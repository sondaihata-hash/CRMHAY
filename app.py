from flask import Flask, render_template, request, redirect, url_for, flash, Response
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from sqlalchemy import text
import ast
import csv
import io
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import time

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///crm.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('CRM_SECRET_KEY', 'dev-secret')

db = SQLAlchemy(app)


class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    facebook_id = db.Column(db.String(100), nullable=True)
    email = db.Column(db.String(200), nullable=True)
    phone = db.Column(db.String(50), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    page_name = db.Column(db.String(200), nullable=True)
    location = db.Column(db.String(200), nullable=True)
    last_message_date = db.Column(db.DateTime, nullable=True)
    message_excerpt = db.Column(db.Text, nullable=True)
    source = db.Column(db.String(50), default='manual')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Setting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(200), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=True)
    description = db.Column(db.String(400), nullable=True)


def ensure_customer_columns():
    columns = [row[1] for row in db.session.execute(text('PRAGMA table_info(customer)'))]
    new_columns = {
        'page_name': 'TEXT',
        'location': 'TEXT',
        'last_message_date': 'DATETIME',
        'message_excerpt': 'TEXT',
        'source': 'TEXT',
    }
    for column_name, column_type in new_columns.items():
        if column_name not in columns:
            db.session.execute(text(f'ALTER TABLE customer ADD COLUMN {column_name} {column_type}'))
    db.session.commit()


def extract_phone_numbers(text_value):
    if not text_value:
        return []

    patterns = [
        r'(?<!\d)(?:\+?84\d{9,10}|0\d{9,10})(?!\d)',
        r'(?<!\d)0\d{3}[\s.-]?\d{3}[\s.-]?\d{3,4}(?!\d)',
    ]

    found = []
    for pattern in patterns:
        for match in re.finditer(pattern, text_value):
            found.append(match.group(0))

    normalized = []
    for value in found:
        cleaned = re.sub(r'\D', '', value)
        if cleaned.startswith('+84'):
            cleaned = '0' + cleaned[3:]
        elif cleaned.startswith('84'):
            cleaned = '0' + cleaned[2:]
        if cleaned.startswith('0') and len(cleaned) >= 10:
            cleaned = cleaned[:11]
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)

    return normalized


def extract_location(text_value):
    if not text_value:
        return ''
    lower = text_value.lower()
    location_keywords = [
        'hà nội', 'hồ chí minh', 'đà nẵng', 'hải phòng', 'cần thơ', 'biên hòa',
        'quận 1', 'quận 2', 'quận 3', 'quận 4', 'quận 5', 'quận 6', 'quận 7',
        'quận 8', 'quận 9', 'quận 10', 'quận 11', 'quận 12', 'huyện', 'tỉnh',
        'thành phố', 'phường', 'xã', 'thị trấn'
    ]
    for keyword in location_keywords:
        if keyword in lower:
            return keyword.capitalize()
    match = re.search(r'(?:ở|tại|sống|live|reside|resident)\s+([^,.\n;]+)', lower, re.IGNORECASE)
    if match:
        return match.group(1).strip().title()
    return ''


def build_customer_from_message(payload):
    message = payload.get('message') or payload.get('content') or payload.get('text') or ''
    raw_name = payload.get('name') or payload.get('customer_name') or payload.get('sender_name') or 'Khách hàng Facebook'
    raw_page_name = payload.get('page_name') or payload.get('page') or payload.get('page_title') or ''
    phone = payload.get('phone') or ''
    if not phone:
        numbers = extract_phone_numbers(message)
        phone = numbers[0] if numbers else ''
    facebook_id = str(payload.get('facebook_id') or payload.get('id') or payload.get('sender_id') or '').strip()
    location = payload.get('location') or extract_location(message)
    date_value = payload.get('message_date') or payload.get('date') or payload.get('created_time') or payload.get('updated_time')
    last_message_date = datetime.utcnow()
    if date_value:
        try:
            if date_value.endswith('Z'):
                date_value = date_value[:-1] + '+00:00'
            last_message_date = datetime.fromisoformat(date_value)
        except ValueError:
            try:
                last_message_date = datetime.strptime(date_value, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                last_message_date = datetime.utcnow()

    return {
        'name': raw_name.strip() or 'Khách hàng Facebook',
        'facebook_id': facebook_id,
        'email': payload.get('email') or '',
        'phone': phone,
        'notes': f"Page: {raw_page_name}\nVị trí: {location}\nTin nhắn: {message[:500]}",
        'page_name': raw_page_name,
        'location': location,
        'last_message_date': last_message_date,
        'message_excerpt': message[:500],
        'source': 'facebook',
    }


def parse_facebook_messages(raw_text):
    if not raw_text or not raw_text.strip():
        return []

    candidate_texts = [raw_text.strip()]
    if '\n' in raw_text:
        candidate_texts.extend(line.strip() for line in raw_text.splitlines() if line.strip())

    for candidate in candidate_texts:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                return [parsed]
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

        try:
            parsed = ast.literal_eval(candidate)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                return [parsed]
        except (ValueError, SyntaxError):
            pass

    items = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(line)
                if isinstance(parsed, dict):
                    items.append(parsed)
                elif isinstance(parsed, list):
                    items.extend(parsed)
                break
            except (TypeError, ValueError, SyntaxError):
                continue
    return items


def import_facebook_messages(messages):
    imported = 0
    updated = 0
    for item in messages:
        payload = build_customer_from_message(item)
        if not payload['name']:
            continue

        customer = None
        if payload.get('facebook_id'):
            customer = Customer.query.filter(Customer.facebook_id == payload['facebook_id']).first()
        if customer is None:
            customer = Customer.query.filter(Customer.name == payload['name']).first()

        if customer is None:
            customer = Customer(
                name=payload['name'],
                facebook_id=payload['facebook_id'],
                email=payload['email'],
                phone=payload['phone'],
                notes=payload['notes'],
                page_name=payload['page_name'],
                location=payload['location'],
                last_message_date=payload['last_message_date'],
                message_excerpt=payload['message_excerpt'],
                source=payload['source'],
            )
            db.session.add(customer)
            imported += 1
        else:
            customer.name = payload['name']
            customer.facebook_id = payload['facebook_id'] or customer.facebook_id
            customer.email = payload['email'] or customer.email
            customer.phone = payload['phone'] or customer.phone
            customer.page_name = payload['page_name'] or customer.page_name
            customer.location = payload['location'] or customer.location
            customer.last_message_date = payload['last_message_date']
            customer.message_excerpt = payload['message_excerpt'] or customer.message_excerpt
            customer.notes = payload['notes']
            customer.source = 'facebook'
            updated += 1
    db.session.commit()
    return imported, updated


def fetch_facebook_json(endpoint, access_token, extra_params=None):
    base_url = endpoint if endpoint.startswith('http') else f'https://graph.facebook.com/v19.0/{endpoint}'
    params = {'access_token': access_token}
    if extra_params:
        params.update(extra_params)
    url = f'{base_url}?{urlencode(params)}'
    request = Request(url, headers={'User-Agent': 'CRM-HAY/1.0'})
    with urlopen(request, timeout=25) as response:
        return json.loads(response.read().decode('utf-8'))


def normalize_customer_name(participants, page_name):
    if not participants:
        return page_name or 'Khách hàng Facebook'
    for participant in participants:
        if participant.get('id') and participant.get('name'):
            return participant.get('name')
    return page_name or 'Khách hàng Facebook'


def fetch_managed_facebook_messages():
    token = (
        os.environ.get('FACEBOOK_PAGE_ACCESS_TOKEN')
        or os.environ.get('FACEBOOK_SYSTEM_USER_ACCESS_TOKEN')
        or os.environ.get('FACEBOOK_APP_ACCESS_TOKEN')
    )
    if not token:
        return []

    try:
        page_data = []
        accounts_payload = fetch_facebook_json('me/accounts', token, {'fields': 'id,name'})
        if accounts_payload.get('data'):
            page_data = accounts_payload.get('data')
        else:
            page_id = os.environ.get('FACEBOOK_PAGE_ID')
            if page_id:
                page_data = [{'id': page_id, 'name': os.environ.get('FACEBOOK_PAGE_NAME', 'Facebook Page')}]
            else:
                page_data = [{'id': 'me', 'name': os.environ.get('FACEBOOK_PAGE_NAME', 'Facebook Page')}]

        facebook_messages = []
        request_delay_seconds = 1.5
        for page in page_data:
            page_id = page.get('id')
            page_name = page.get('name') or os.environ.get('FACEBOOK_PAGE_NAME', 'Facebook Page')
            endpoint = f'{page_id}/conversations' if page_id != 'me' else 'me/conversations'
            next_url = None

            while True:
                params = {'fields': 'participants{id,name},messages{from{id,name},message,created_time}', 'limit': '25'}
                payload = fetch_facebook_json(next_url or endpoint, token, params)
                conversations = payload.get('data', [])
                for conversation in conversations:
                    participants = conversation.get('participants', {}).get('data', [])
                    messages = conversation.get('messages', {}).get('data', [])
                    if not messages:
                        continue
                    latest_message = messages[0]
                    sender = latest_message.get('from', {})
                    customer_name = sender.get('name') or normalize_customer_name(participants, page_name)
                    customer_id = sender.get('id') or (participants[0].get('id') if participants else '')
                    message_text = latest_message.get('message') or latest_message.get('story') or ''
                    if not message_text:
                        continue
                    facebook_messages.append({
                        'name': customer_name,
                        'facebook_id': customer_id,
                        'message': message_text,
                        'message_date': latest_message.get('created_time'),
                        'page_name': page_name,
                    })

                paging = payload.get('paging', {})
                next_page = paging.get('next')
                if not next_page:
                    break
                next_url = next_page
                time.sleep(request_delay_seconds)

            time.sleep(request_delay_seconds)

        return facebook_messages
    except (HTTPError, URLError, ValueError, KeyError):
        return []


def init_db():
    with app.app_context():
        db.create_all()
        ensure_customer_columns()
        if not Customer.query.first():
            sample = Customer(
                name='Nguyen Van A',
                facebook_id='fb_12345',
                email='a@example.com',
                phone='0123456789',
                notes='Khách hàng tiềm năng',
                page_name='Demo Page',
                location='Hà Nội',
                source='demo',
            )
            db.session.add(sample)
            db.session.commit()


@app.route('/')
def index():
    return redirect(url_for('customers'))


@app.route('/customers')
def customers():
    q = request.args.get('q', '')
    if q:
        items = Customer.query.filter(Customer.name.contains(q)).all()
    else:
        items = Customer.query.order_by(Customer.created_at.desc()).all()
    return render_template('customers.html', customers=items, q=q)


@app.route('/customers/add', methods=['GET', 'POST'])
def add_customer():
    if request.method == 'POST':
        name = request.form.get('name')
        facebook_id = request.form.get('facebook_id')
        email = request.form.get('email')
        phone = request.form.get('phone')
        notes = request.form.get('notes')
        if not name:
            flash('Tên là bắt buộc', 'danger')
            return redirect(url_for('add_customer'))
        c = Customer(name=name, facebook_id=facebook_id, email=email, phone=phone, notes=notes)
        db.session.add(c)
        db.session.commit()
        flash('Đã thêm khách hàng', 'success')
        return redirect(url_for('customers'))
    return render_template('customer_form.html', action='add')


@app.route('/customers/<int:c_id>')
def customer_detail(c_id):
    c = Customer.query.get_or_404(c_id)
    return render_template('customer_detail.html', c=c)


@app.route('/customers/<int:c_id>/edit', methods=['GET', 'POST'])
def edit_customer(c_id):
    c = Customer.query.get_or_404(c_id)
    if request.method == 'POST':
        c.name = request.form.get('name')
        c.facebook_id = request.form.get('facebook_id')
        c.email = request.form.get('email')
        c.phone = request.form.get('phone')
        c.notes = request.form.get('notes')
        db.session.commit()
        flash('Đã cập nhật khách hàng', 'success')
        return redirect(url_for('customer_detail', c_id=c.id))
    return render_template('customer_form.html', action='edit', c=c)


@app.route('/customers/<int:c_id>/delete', methods=['POST'])
def delete_customer(c_id):
    c = Customer.query.get_or_404(c_id)
    db.session.delete(c)
    db.session.commit()
    flash('Đã xóa khách hàng', 'info')
    return redirect(url_for('customers'))


@app.route('/settings')
def settings():
    items = Setting.query.order_by(Setting.key).all()
    return render_template('settings.html', items=items)


@app.route('/settings/add', methods=['POST'])
def add_setting():
    key = request.form.get('key')
    value = request.form.get('value')
    description = request.form.get('description')
    if not key:
        flash('Key là bắt buộc', 'danger')
        return redirect(url_for('settings'))
    s = Setting(key=key, value=value, description=description)
    db.session.add(s)
    try:
        db.session.commit()
        flash('Đã thêm setting', 'success')
    except Exception:
        db.session.rollback()
        flash('Lỗi: có thể key đã tồn tại', 'danger')
    return redirect(url_for('settings'))


@app.route('/settings/<int:s_id>/delete', methods=['POST'])
def delete_setting(s_id):
    s = Setting.query.get_or_404(s_id)
    db.session.delete(s)
    db.session.commit()
    flash('Đã xóa setting', 'info')
    return redirect(url_for('settings'))


@app.route('/settings/<int:s_id>/edit', methods=['GET', 'POST'])
def edit_setting(s_id):
    s = Setting.query.get_or_404(s_id)
    if request.method == 'POST':
        s.value = request.form.get('value')
        s.description = request.form.get('description')
        db.session.commit()
        flash('Đã cập nhật setting', 'success')
        return redirect(url_for('settings'))
    return render_template('setting_form.html', s=s)


@app.route('/customers/sync-facebook')
def sync_facebook_customers():
    configured = bool(
        os.environ.get('FACEBOOK_PAGE_ACCESS_TOKEN')
        or os.environ.get('FACEBOOK_SYSTEM_USER_ACCESS_TOKEN')
        or os.environ.get('FACEBOOK_APP_ACCESS_TOKEN')
    )

    if not configured:
        flash('Chưa cấu hình token Facebook hợp lệ. Thêm FACEBOOK_PAGE_ACCESS_TOKEN hoặc FACEBOOK_SYSTEM_USER_ACCESS_TOKEN.', 'danger')
        return redirect(url_for('customers'))

    messages = fetch_managed_facebook_messages()
    if not messages:
        flash('Không tìm thấy khách hàng nào từ Facebook hoặc token chưa được cấp quyền đủ.', 'warning')
        return redirect(url_for('customers'))

    imported, updated = import_facebook_messages(messages)
    flash(f'Đã đồng bộ {imported} khách mới và cập nhật {updated} khách từ Facebook.', 'success')
    return redirect(url_for('customers'))


@app.route('/facebook/import')
def facebook_import_legacy():
    flash('Chức năng đồng bộ Facebook đã được chuyển sang tab Khách hàng.', 'info')
    return redirect(url_for('customers'))


@app.route('/facebook/export')
def facebook_export():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['id', 'name', 'facebook_id', 'email', 'phone', 'location', 'page_name', 'last_message_date', 'source'])

    customers = Customer.query.order_by(Customer.created_at.desc()).all()
    for customer in customers:
        writer.writerow([
            customer.id,
            customer.name,
            customer.facebook_id or '',
            customer.email or '',
            customer.phone or '',
            customer.location or '',
            customer.page_name or '',
            customer.last_message_date.strftime('%Y-%m-%d %H:%M:%S') if customer.last_message_date else '',
            customer.source or '',
        ])

    csv_data = output.getvalue()
    return Response(csv_data, mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=facebook_customers.csv'})


if __name__ == '__main__':
    init_db()
    app.run(debug=True)
else:
    init_db()
