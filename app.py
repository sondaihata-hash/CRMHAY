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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, 'instance')
os.makedirs(INSTANCE_DIR, exist_ok=True)
DB_PATH = os.path.join(INSTANCE_DIR, 'crm.db')

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('CRM_SECRET_KEY', 'dev-secret')

db = SQLAlchemy(app)


class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    first_name = db.Column(db.String(100), nullable=True)
    last_name = db.Column(db.String(100), nullable=True)
    facebook_id = db.Column(db.String(100), nullable=True)
    conversation_id = db.Column(db.String(200), nullable=True)
    profile_pic = db.Column(db.Text, nullable=True)
    gender = db.Column(db.String(20), nullable=True)
    locale = db.Column(db.String(20), nullable=True)
    email = db.Column(db.String(200), nullable=True)
    phone = db.Column(db.String(50), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    page_name = db.Column(db.String(200), nullable=True)
    location = db.Column(db.String(200), nullable=True)
    last_message_date = db.Column(db.DateTime, nullable=True)
    message_excerpt = db.Column(db.Text, nullable=True)
    source = db.Column(db.String(50), default='manual')
    message_count = db.Column(db.Integer, default=0)
    tags = db.Column(db.String(500), nullable=True)
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
        'first_name': 'TEXT',
        'last_name': 'TEXT',
        'conversation_id': 'TEXT',
        'profile_pic': 'TEXT',
        'gender': 'TEXT',
        'locale': 'TEXT',
        'message_count': 'INTEGER DEFAULT 0',
        'tags': 'TEXT',
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

    # Split name into first/last (Vietnamese: last word = first_name)
    name_clean = raw_name.strip() or 'Khách hàng Facebook'
    name_parts = name_clean.split()
    first_name = payload.get('first_name') or (name_parts[-1] if name_parts else '')
    last_name = payload.get('last_name') or (' '.join(name_parts[:-1]) if len(name_parts) > 1 else '')

    return {
        'name': name_clean,
        'first_name': first_name,
        'last_name': last_name,
        'facebook_id': facebook_id,
        'conversation_id': payload.get('conversation_id') or '',
        'profile_pic': payload.get('profile_pic') or '',
        'gender': payload.get('gender') or '',
        'locale': payload.get('locale') or '',
        'email': payload.get('email') or '',
        'phone': phone,
        'notes': f"Page: {raw_page_name}\nVị trí: {location}\nTin nhắn: {message[:500]}",
        'page_name': raw_page_name,
        'location': location,
        'last_message_date': last_message_date,
        'message_excerpt': message[:500],
        'source': 'facebook',
        'message_count': payload.get('message_count') or 0,
        'tags': payload.get('tags') or '',
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
        # Dedup priority: conversation_id > facebook_id > name (no facebook_id only)
        if payload.get('conversation_id'):
            customer = Customer.query.filter(Customer.conversation_id == payload['conversation_id']).first()
        if customer is None and payload.get('facebook_id'):
            customer = Customer.query.filter(Customer.facebook_id == payload['facebook_id']).first()
        if customer is None and not payload.get('facebook_id'):
            # Only dedup by name when no facebook_id — avoid merging distinct FB users
            customer = Customer.query.filter(Customer.name == payload['name'], Customer.source == 'facebook').first()

        if customer is None:
            customer = Customer(
                name=payload['name'],
                first_name=payload['first_name'],
                last_name=payload['last_name'],
                facebook_id=payload['facebook_id'],
                conversation_id=payload['conversation_id'],
                profile_pic=payload['profile_pic'],
                gender=payload['gender'],
                locale=payload['locale'],
                email=payload['email'],
                phone=payload['phone'],
                notes=payload['notes'],
                page_name=payload['page_name'],
                location=payload['location'],
                last_message_date=payload['last_message_date'],
                message_excerpt=payload['message_excerpt'],
                source=payload['source'],
                message_count=payload['message_count'],
                tags=payload['tags'],
            )
            db.session.add(customer)
            imported += 1
        else:
            customer.name = payload['name']
            customer.first_name = payload['first_name'] or customer.first_name
            customer.last_name = payload['last_name'] or customer.last_name
            customer.facebook_id = payload['facebook_id'] or customer.facebook_id
            customer.conversation_id = payload['conversation_id'] or customer.conversation_id
            customer.profile_pic = payload['profile_pic'] or customer.profile_pic
            customer.gender = payload['gender'] or customer.gender
            customer.locale = payload['locale'] or customer.locale
            customer.email = payload['email'] or customer.email
            customer.phone = payload['phone'] or customer.phone
            customer.page_name = payload['page_name'] or customer.page_name
            customer.location = payload['location'] or customer.location
            customer.last_message_date = payload['last_message_date']
            customer.message_excerpt = payload['message_excerpt'] or customer.message_excerpt
            customer.notes = payload['notes']
            customer.source = 'facebook'
            customer.message_count = payload['message_count'] or customer.message_count
            customer.tags = payload['tags'] or customer.tags
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
        payload = json.loads(response.read().decode('utf-8'))

    if isinstance(payload, dict) and 'error' in payload:
        error = payload['error']
        message = error.get('message', 'Facebook Graph API returned an error.')
        raise ValueError(message)

    return payload


def normalize_customer_name(participants, page_name):
    if not participants:
        return page_name or 'Khách hàng Facebook'
    for participant in participants:
        if participant.get('id') and participant.get('name'):
            return participant.get('name')
    return page_name or 'Khách hàng Facebook'


def resolve_page_access_tokens(system_token, page_data, fetcher=None):
    fetcher = fetcher or fetch_facebook_json
    resolved = []
    for page in page_data:
        page_id = page.get('id')
        page_name = page.get('name') or 'Facebook Page'
        page_token = page.get('access_token')
        if page_token:
            resolved.append({**page, 'access_token': page_token})
            continue
        if not page_id:
            continue
        try:
            page_payload = fetcher(page_id, system_token, {'fields': 'id,name,access_token'})
        except (HTTPError, URLError, ValueError, KeyError):
            continue
        access_token = page_payload.get('access_token')
        if not access_token:
            continue
        resolved.append({
            'id': page_id,
            'name': page_name,
            'access_token': access_token,
        })
    if not resolved and page_data:
        resolved = [{
            'id': page.get('id'),
            'name': page.get('name') or 'Facebook Page',
            'access_token': system_token,
        } for page in page_data if page.get('id')]
    return resolved


def resolve_facebook_pages(token, fetcher=None):
    fetcher = fetcher or fetch_facebook_json
    try:
        me_payload = fetcher('me', token, {'fields': 'id,name'})
    except (HTTPError, URLError, ValueError, KeyError):
        return []

    if not me_payload:
        return []

    page_id = me_payload.get('id') or me_payload.get('page_id')
    page_name = me_payload.get('name') or 'Facebook Page'
    if not page_id:
        return []
    return [{
        'id': page_id,
        'name': page_name,
        'access_token': token,
    }]


def fetch_managed_facebook_messages(max_pages=None, max_conversations_per_page=None):
    token = (
        os.environ.get('FACEBOOK_PAGE_ACCESS_TOKEN')
        or os.environ.get('FACEBOOK_SYSTEM_USER_ACCESS_TOKEN')
        or os.environ.get('FACEBOOK_APP_ACCESS_TOKEN')
    )
    if not token:
        return []

    try:
        page_data = []
        try:
            accounts_payload = fetch_facebook_json('me/accounts', token, {'fields': 'id,name,access_token'})
            if accounts_payload.get('data'):
                page_data = resolve_page_access_tokens(token, accounts_payload.get('data'))
        except (HTTPError, URLError, ValueError, KeyError):
            page_data = []

        if not page_data:
            page_data = resolve_facebook_pages(token)

        if not page_data:
            page_id = os.environ.get('FACEBOOK_PAGE_ID')
            if page_id:
                page_data = [{
                    'id': page_id,
                    'name': os.environ.get('FACEBOOK_PAGE_NAME', 'Facebook Page'),
                    'access_token': token,
                }]
            else:
                raise ValueError('Không lấy được danh sách Page từ Facebook. Hãy dùng token doanh nghiệp/page hợp lệ và chắc chắn nó thuộc quyền quản lý page với quyền inbox.')

        page_limit = max_pages if max_pages is not None else int(os.environ.get('FACEBOOK_SYNC_PAGE_LIMIT', '3'))
        page_limit = max(1, min(int(page_limit), 10))
        conversation_limit = max_conversations_per_page if max_conversations_per_page is not None else int(os.environ.get('FACEBOOK_SYNC_CONVERSATION_LIMIT', '500'))
        conversation_limit = max(1, min(int(conversation_limit), 2000))

        facebook_messages = []
        request_delay_seconds = 0.5
        for page_index, page in enumerate(page_data[:page_limit]):
            page_id = page.get('id')
            page_name = page.get('name') or os.environ.get('FACEBOOK_PAGE_NAME', 'Facebook Page')
            page_token = page.get('access_token') or token
            if not page_id:
                continue

            endpoint = f'{page_id}/conversations'
            next_url = None
            collected_for_page = 0

            while True:
                params = {'fields': 'participants{id,name},messages{from{id,name},message,created_time}', 'limit': '25'}
                payload = fetch_facebook_json(next_url or endpoint, page_token, params)
                conversations = payload.get('data', [])
                if not conversations:
                    break

                for conversation in conversations:
                    if collected_for_page >= conversation_limit:
                        break
                    participants = conversation.get('participants', {}).get('data', [])
                    messages = conversation.get('messages', {}).get('data', [])
                    if not messages:
                        continue
                    latest_message = messages[0]
                    sender = latest_message.get('from', {})
                    # Find the actual customer, not the page itself
                    customer_participants = [p for p in participants if p.get('id') != page_id]
                    if sender.get('id') and sender.get('id') != page_id:
                        customer_name = sender.get('name') or normalize_customer_name(customer_participants, page_name)
                        customer_id = sender.get('id')
                    elif customer_participants:
                        customer_name = customer_participants[0].get('name') or normalize_customer_name(customer_participants, page_name)
                        customer_id = customer_participants[0].get('id') or ''
                    else:
                        customer_name = sender.get('name') or normalize_customer_name(participants, page_name)
                        customer_id = sender.get('id') or (participants[0].get('id') if participants else '')
                    message_text = latest_message.get('message') or latest_message.get('story') or ''
                    if not message_text:
                        message_text = '[Hình ảnh/sticker]'

                    # Fetch user profile (profile_pic, first_name, last_name, gender, locale)
                    profile_pic = ''
                    first_name = ''
                    last_name = ''
                    gender = ''
                    locale = ''
                    if customer_id:
                        try:
                            profile = fetch_facebook_json(
                                customer_id, page_token,
                                {'fields': 'first_name,last_name,profile_pic,gender,locale'}
                            )
                            profile_pic = profile.get('profile_pic') or ''
                            first_name = profile.get('first_name') or ''
                            last_name = profile.get('last_name') or ''
                            gender = profile.get('gender') or ''
                            locale = profile.get('locale') or ''
                        except Exception:
                            pass  # Profile API may not be available

                    conversation_id = conversation.get('id') or ''
                    message_count = len(messages)

                    facebook_messages.append({
                        'name': customer_name,
                        'first_name': first_name,
                        'last_name': last_name,
                        'facebook_id': customer_id,
                        'conversation_id': conversation_id,
                        'profile_pic': profile_pic,
                        'gender': gender,
                        'locale': locale,
                        'message': message_text,
                        'message_date': latest_message.get('created_time'),
                        'page_name': page_name,
                        'message_count': message_count,
                    })
                    collected_for_page += 1

                paging = payload.get('paging', {})
                next_page = paging.get('next')
                if collected_for_page >= conversation_limit or not next_page:
                    break
                next_url = next_page
                time.sleep(request_delay_seconds)

            time.sleep(request_delay_seconds)

        return facebook_messages
    except (HTTPError, URLError, ValueError, KeyError) as exc:
        message = str(exc)
        if message.startswith('HTTP Error'):
            raise ValueError('Facebook API trả về lỗi HTTP. Kiểm tra token và quyền truy cập Page.')
        if 'Invalid OAuth access token' in message or 'OAuthException' in message:
            raise ValueError('Token Facebook không hợp lệ hoặc hết hạn. Hãy kiểm tra lại FACEBOOK_PAGE_ACCESS_TOKEN / FACEBOOK_SYSTEM_USER_ACCESS_TOKEN.')
        if 'does not have permission' in message or 'permission' in message.lower():
            raise ValueError('Token hiện tại không có quyền đọc inbox / page conversations. Cần Page Access Token hoặc token có quyền page messaging.')
        raise ValueError(f'Facebook API lỗi: {message}')


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
        items = Customer.query.filter(
            db.or_(
                Customer.name.contains(q),
                Customer.phone.contains(q),
                Customer.facebook_id.contains(q),
                Customer.email.contains(q),
                Customer.tags.contains(q),
            )
        ).all()
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
        location = request.form.get('location')
        tags = request.form.get('tags')
        if not name:
            flash('Tên là bắt buộc', 'danger')
            return redirect(url_for('add_customer'))
        c = Customer(name=name, facebook_id=facebook_id, email=email, phone=phone, notes=notes, location=location, tags=tags)
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
        c.location = request.form.get('location')
        c.tags = request.form.get('tags')
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

    try:
        messages = fetch_managed_facebook_messages(
            max_pages=int(os.environ.get('FACEBOOK_SYNC_PAGE_LIMIT', '3')),
            max_conversations_per_page=int(os.environ.get('FACEBOOK_SYNC_CONVERSATION_LIMIT', '500')),
        )
    except Exception as exc:
        app.logger.exception('Facebook sync failed')
        flash('Đồng bộ Facebook thất bại: token không hợp lệ hoặc thiếu quyền truy cập page/inbox. Vui lòng kiểm tra lại cài đặt Facebook.', 'danger')
        return redirect(url_for('customers'))

    if not messages:
        flash('Không tìm thấy khách hàng nào từ Facebook cho token hiện tại. Hãy kiểm tra quyền page/inbox hoặc token doanh nghiệp.', 'warning')
        return redirect(url_for('customers'))

    try:
        imported, updated = import_facebook_messages(messages)
    except Exception as exc:
        app.logger.exception('Import facebook customers failed')
        flash('Đồng bộ dữ liệu Facebook xong nhưng lưu trữ CRM gặp lỗi. Vui lòng thử lại sau.', 'danger')
        return redirect(url_for('customers'))

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
    writer.writerow(['id', 'name', 'first_name', 'last_name', 'facebook_id', 'conversation_id', 'email', 'phone', 'location', 'page_name', 'gender', 'locale', 'message_count', 'tags', 'last_message_date', 'source', 'profile_pic'])

    customers = Customer.query.order_by(Customer.created_at.desc()).all()
    for customer in customers:
        writer.writerow([
            customer.id,
            customer.name,
            customer.first_name or '',
            customer.last_name or '',
            customer.facebook_id or '',
            customer.conversation_id or '',
            customer.email or '',
            customer.phone or '',
            customer.location or '',
            customer.page_name or '',
            customer.gender or '',
            customer.locale or '',
            customer.message_count or 0,
            customer.tags or '',
            customer.last_message_date.strftime('%Y-%m-%d %H:%M:%S') if customer.last_message_date else '',
            customer.source or '',
            customer.profile_pic or '',
        ])

    csv_data = output.getvalue()
    return Response(csv_data, mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=facebook_customers.csv'})


if __name__ == '__main__':
    init_db()
    app.run(debug=True)
else:
    init_db()
