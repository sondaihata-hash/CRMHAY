from functools import wraps
import importlib.util
import pkgutil
import secrets
import werkzeug
from flask import Flask, render_template, request, redirect, url_for, flash, Response, send_file, session
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from sqlalchemy import text, inspect, func
from sqlalchemy.exc import OperationalError
from werkzeug.security import check_password_hash, generate_password_hash

if not hasattr(pkgutil, 'get_loader'):
    def _compat_get_loader(module_name):
        if module_name == '__main__':
            return None
        try:
            spec = importlib.util.find_spec(module_name)
        except (ImportError, ValueError, AttributeError):
            return None
        if spec is None:
            return None
        return spec.loader
    pkgutil.get_loader = _compat_get_loader

if not hasattr(werkzeug, '__version__'):
    werkzeug.__version__ = '3.0.0'
try:
    from celery import Celery
except ImportError:  # Allows local development before optional worker deps install.
    Celery = None
import ast
import csv
import io
import json
import logging
import os
import re
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import time
import threading
import uuid

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, 'instance')
os.makedirs(INSTANCE_DIR, exist_ok=True)
DB_PATH = os.path.join(INSTANCE_DIR, 'crm.db')
# The database is the source of truth. This JSON file is a recovery copy
# written after each successful Facebook sync.
CUSTOMER_SNAPSHOT_PATH = os.environ.get(
    'CUSTOMER_SNAPSHOT_PATH', os.path.join(INSTANCE_DIR, 'customers-backup.json')
)
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL or f'sqlite:///{DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
secret_key = os.environ.get('CRM_SECRET_KEY')
is_production = os.environ.get('KOYEB', '').lower() == 'true' or os.environ.get('RENDER', '').lower() == 'true' or os.environ.get('FLASK_ENV') == 'production'
if not secret_key and is_production:
    raise RuntimeError('CRM_SECRET_KEY must be configured in production.')
app.config['SECRET_KEY'] = secret_key or 'dev-secret'
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=is_production,
)

db = SQLAlchemy(app)

# Configure these in Render for durable background jobs.  When absent, the
# local-thread fallback keeps development/demo deployments usable.
CELERY_BROKER_URL = os.environ.get('REDIS_URL') or os.environ.get('CELERY_BROKER_URL')
celery = None
if CELERY_BROKER_URL and Celery:
    celery = Celery(app.import_name, broker=CELERY_BROKER_URL, backend=CELERY_BROKER_URL)
    celery.conf.update(task_track_started=True, task_acks_late=True, worker_prefetch_multiplier=1)

logger = logging.getLogger(__name__)

ADMIN_ENDPOINTS = {
    'settings', 'add_setting', 'edit_setting', 'sales_groups',
    'add_sales_group', 'delete_sales_group', 'update_sales_group_link',
    'sync_facebook_customers', 'sync_facebook_status', 'facebook_export',
    'delete_customer', 'users', 'add_user', 'toggle_user', 'assign_customer',
    'facebook_import_legacy', 'reminders', 'complete_reminder',
}

# Background sync state — single-worker safe
# ponytail: upgrade to Redis/Celery when moving to multi-worker
_sync_state = {
    'running': False,
    'result': None,
    'message': '',
    'imported': 0,
    'updated': 0,
    'started_at': None,
    'finished_at': None,
}
_sync_lock = threading.Lock()

# Bounded high-water marks: enough for a full historical import while still
# preventing a broken Facebook pagination cursor from running forever.
MAX_API_CALLS_PER_SYNC = 10000
MAX_CONVERSATION_PAGES = 1000
MAX_PAGE_PAGINATION_ROUNDS = 20
FACEBOOK_API_TIMEOUT = 15  # seconds per HTTP request
API_RATE_DELAY = 0.25  # seconds between Facebook API calls
DEFAULT_SYNC_CONVERSATION_LIMIT = 5000
MAX_SYNC_CONVERSATION_LIMIT = 10000
CONVERSATIONS_PER_REQUEST = 25
DEFAULT_HOTLINE_NUMBERS = frozenset({
    '0707866676',
    '0794753133',
    '0918374251',
    '0918374257',
    '0978587075',
})


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
    points = db.Column(db.Integer, nullable=False, default=0)
    assigned_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='sales')
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)
    customers = db.relationship('Customer', backref='assigned_user', lazy=True)


class Setting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(200), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=True)
    description = db.Column(db.String(400), nullable=True)


class SyncJob(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    status = db.Column(db.String(20), nullable=False, default='queued', index=True)
    message = db.Column(db.Text, nullable=True)
    imported = db.Column(db.Integer, nullable=False, default=0)
    updated = db.Column(db.Integer, nullable=False, default=0)
    progress = db.Column(db.Integer, nullable=False, default=0)
    processed = db.Column(db.Integer, nullable=False, default=0)
    total = db.Column(db.Integer, nullable=False, default=0)
    last_activity_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False, index=True)
    code = db.Column(db.String(40), nullable=False, unique=True)
    total_amount = db.Column(db.Float, nullable=False, default=0)
    status = db.Column(db.String(30), nullable=False, default='Mới')
    note = db.Column(db.Text, nullable=True)
    delivery_address = db.Column(db.String(400), nullable=True)
    discount_amount = db.Column(db.Float, nullable=False, default=0)
    vat_amount = db.Column(db.Float, nullable=False, default=0)
    payment_details = db.Column(db.String(400), nullable=True)
    points_awarded = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    customer = db.relationship('Customer', backref=db.backref('orders', lazy=True))


class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False, index=True)
    product_code = db.Column(db.String(100), nullable=True)
    product_name = db.Column(db.String(300), nullable=False)
    unit = db.Column(db.String(50), nullable=True)
    quantity = db.Column(db.Float, nullable=False, default=1)
    unit_price = db.Column(db.Float, nullable=False, default=0)
    order = db.relationship('Order', backref=db.backref('items', lazy=True, cascade='all, delete-orphan'))


class SalesGroup(db.Model):
    """A manually maintained destination list for the user's Zalo groups."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, unique=True)
    description = db.Column(db.String(400), nullable=True)
    zalo_url = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class SalesHandoff(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False, index=True)
    group_id = db.Column(db.Integer, db.ForeignKey('sales_group.id'), nullable=False, index=True)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    customer = db.relationship(
        'Customer',
        backref=db.backref('sales_handoffs', lazy=True, cascade='all, delete-orphan'),
    )
    group = db.relationship('SalesGroup', backref=db.backref('handoffs', lazy=True))


class MessageLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False, index=True)
    sender_type = db.Column(db.String(30), nullable=False, default='customer')
    channel = db.Column(db.String(30), nullable=False, default='zalo')
    message = db.Column(db.Text, nullable=False)
    external_message_id = db.Column(db.String(200), nullable=True)
    sent_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    customer = db.relationship('Customer', backref=db.backref('message_logs', lazy=True, cascade='all, delete-orphan'))


class Reminder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False, index=True)
    assigned_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    title = db.Column(db.String(200), nullable=False)
    note = db.Column(db.Text, nullable=True)
    due_at = db.Column(db.DateTime, nullable=True)
    priority = db.Column(db.String(20), nullable=False, default='medium')
    status = db.Column(db.String(20), nullable=False, default='pending', index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)
    customer = db.relationship('Customer', backref=db.backref('reminders', lazy=True, cascade='all, delete-orphan'))
    assigned_user = db.relationship('User', backref=db.backref('reminders', lazy=True))


def write_customer_snapshot():
    """Atomically save imported customer data to a JSON recovery file."""
    customers = Customer.query.order_by(Customer.id).all()
    fields = (
        'id', 'name', 'first_name', 'last_name', 'facebook_id',
        'conversation_id', 'profile_pic', 'gender', 'locale', 'email', 'phone',
        'notes', 'page_name', 'location', 'message_excerpt', 'source',
        'message_count', 'tags', 'created_at', 'last_message_date',
    )
    payload = {
        'generated_at': datetime.utcnow().isoformat() + 'Z',
        'customer_count': len(customers),
        'customers': [{
            field: (getattr(customer, field).isoformat() if isinstance(
                getattr(customer, field), datetime) else getattr(customer, field)
            ) for field in fields
        } for customer in customers],
    }
    snapshot_dir = os.path.dirname(os.path.abspath(CUSTOMER_SNAPSHOT_PATH))
    os.makedirs(snapshot_dir, exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(prefix='.customers-', suffix='.json', dir=snapshot_dir)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as snapshot_file:
            json.dump(payload, snapshot_file, ensure_ascii=False, indent=2)
        os.replace(temporary_path, CUSTOMER_SNAPSHOT_PATH)
        logger.info('Customer snapshot saved: %s (%d customers)', CUSTOMER_SNAPSHOT_PATH, len(customers))
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


def ensure_customer_columns():
    columns = {column['name'] for column in inspect(db.engine).get_columns('customer')}
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
        'points': 'INTEGER DEFAULT 0',
        'assigned_user_id': 'INTEGER',
    }
    for column_name, column_type in new_columns.items():
        if column_name not in columns:
            db.session.execute(text(f'ALTER TABLE customer ADD COLUMN {column_name} {column_type}'))
    db.session.commit()


def ensure_sync_job_columns():
    columns = {column['name'] for column in inspect(db.engine).get_columns('sync_job')}
    timestamp_type = 'TIMESTAMP' if db.engine.dialect.name == 'postgresql' else 'DATETIME'
    new_columns = {
        'progress': 'INTEGER DEFAULT 0',
        'processed': 'INTEGER DEFAULT 0',
        'total': 'INTEGER DEFAULT 0',
        'last_activity_at': timestamp_type,
    }
    for column_name, column_type in new_columns.items():
        if column_name not in columns:
            try:
                db.session.execute(text(f'ALTER TABLE sync_job ADD COLUMN {column_name} {column_type}'))
            except OperationalError as exc:
                db.session.rollback()
                if 'duplicate column name' not in str(exc).lower():
                    raise
    db.session.commit()


def current_user():
    user_id = session.get('user_id')
    return db.session.get(User, user_id) if user_id else None


@app.context_processor
def inject_current_user():
    return {'current_user': current_user, 'csrf_token': csrf_token}


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        user = current_user()
        if not user or not user.is_active:
            session.clear()
            return redirect(url_for('login', next=request.full_path))
        return view(*args, **kwargs)
    return wrapped_view


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped_view(*args, **kwargs):
        if current_user().role != 'admin':
            return 'Bạn không có quyền thực hiện thao tác này.', 403
        return view(*args, **kwargs)
    return wrapped_view


def visible_customer_query():
    user = current_user()
    query = Customer.query
    if user.role != 'admin':
        query = query.filter(Customer.assigned_user_id == user.id)
    return query


def get_visible_customer(customer_id):
    return visible_customer_query().filter(Customer.id == customer_id).first_or_404()


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip().lower()
        password = request.form.get('password') or ''
        user = User.query.filter_by(username=username, is_active=True).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash('Tên đăng nhập hoặc mật khẩu không đúng.', 'danger')
            return render_template('login.html'), 401
        session.clear()
        session['user_id'] = user.id
        user.last_login_at = datetime.utcnow()
        db.session.commit()
        next_url = request.args.get('next') or url_for('index')
        parsed_next = urlsplit(next_url)
        if (
            not next_url.startswith('/') or next_url.startswith('//')
            or parsed_next.scheme or parsed_next.netloc or '\\' in next_url
            or any(ord(character) < 32 for character in next_url)
        ):
            next_url = url_for('index')
        return redirect(next_url)
    return render_template('login.html')


@app.route('/logout', methods=['POST'])
@login_required
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/admin/users')
@admin_required
def users():
    return render_template('users.html', users=User.query.order_by(User.role, User.username).all())


@app.route('/admin/users/add', methods=['POST'])
@admin_required
def add_user():
    username = (request.form.get('username') or '').strip().lower()
    password = request.form.get('password') or ''
    if not username or len(password) < 8:
        flash('Tên đăng nhập và mật khẩu tối thiểu 8 ký tự là bắt buộc.', 'danger')
    elif User.query.filter_by(username=username).first():
        flash('Tên đăng nhập đã tồn tại.', 'warning')
    else:
        db.session.add(User(username=username, password_hash=generate_password_hash(password), role='sales'))
        db.session.commit()
        flash('Đã tạo tài khoản Sales.', 'success')
    return redirect(url_for('users'))


@app.route('/admin/users/<int:user_id>/toggle', methods=['POST'])
@admin_required
def toggle_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.role == 'admin':
        flash('Không thể khóa tài khoản Admin từ màn hình này.', 'warning')
    else:
        user.is_active = not user.is_active
        db.session.commit()
        flash(f"Đã {'mở khóa' if user.is_active else 'khóa'} tài khoản Sales.", 'success')
    return redirect(url_for('users'))


@app.route('/customers/<int:c_id>/assign', methods=['POST'])
@admin_required
def assign_customer(c_id):
    customer = Customer.query.get_or_404(c_id)
    user_id = request.form.get('assigned_user_id', type=int)
    user = db.session.get(User, user_id) if user_id else None
    if user_id and (not user or user.role != 'sales' or not user.is_active):
        flash('Sales được chọn không hợp lệ hoặc đã bị khóa.', 'danger')
    else:
        customer.assigned_user_id = user.id if user else None
        db.session.commit()
        flash('Đã cập nhật Sales phụ trách.', 'success')
    return redirect(url_for('customer_detail', c_id=customer.id))


def ensure_sales_group_columns():
    columns = {column['name'] for column in inspect(db.engine).get_columns('sales_group')}
    if 'zalo_url' not in columns:
        db.session.execute(text('ALTER TABLE sales_group ADD COLUMN zalo_url TEXT'))
        db.session.commit()


def ensure_user_columns():
    columns = {column['name'] for column in inspect(db.engine).get_columns('user')}
    new_columns = {'role': "TEXT DEFAULT 'sales'", 'is_active': 'BOOLEAN DEFAULT 1', 'last_login_at': 'DATETIME'}
    for column_name, column_type in new_columns.items():
        if column_name not in columns:
            db.session.execute(text(f'ALTER TABLE "user" ADD COLUMN {column_name} {column_type}'))
    db.session.commit()


def csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_urlsafe(32)
        session['_csrf_token'] = token
    return token


def validate_csrf_token():
    submitted = request.form.get('_csrf_token') or request.headers.get('X-CSRF-Token')
    expected = session.get('_csrf_token')
    if not expected or not submitted or not secrets.compare_digest(submitted, expected):
        return 'CSRF token không hợp lệ.', 400
    return None


@app.before_request
def require_authentication():
    if request.endpoint in {'login', 'index', 'static'} or (request.endpoint or '').startswith('api_'):
        return None
    user = current_user()
    if not user or not user.is_active:
        session.clear()
        return redirect(url_for('login', next=request.full_path))
    if request.endpoint in ADMIN_ENDPOINTS and user.role != 'admin':
        return 'Bạn không có quyền thực hiện thao tác này.', 403
    if request.method == 'POST':
        return validate_csrf_token()
    return None


def extract_phone_numbers(text_value):
    if not text_value:
        return []

    patterns = [
        r'(?<!\d)(?:\+?84\d{9,10}|0\d{9,10})(?!\d)',
        r'(?<!\d)\+?84[\s.-]?\d{3}[\s.-]?\d{3}[\s.-]?\d{3}(?!\d)',
        r'(?<!\d)\+?84[\s.-]?\d{2}[\s.-]?\d{4}[\s.-]?\d{4}(?!\d)',
        r'(?<!\d)0\d{3}[\s.-]?\d{3}[\s.-]?\d{3,4}(?!\d)',
    ]

    found = []
    for pattern in patterns:
        for match in re.finditer(pattern, text_value):
            found.append(match.group(0))

    normalized = []
    for value in found:
        cleaned = re.sub(r'[\s.-]', '', value)
        if cleaned.startswith('+84'):
            cleaned = '0' + cleaned[3:]
        elif cleaned.startswith('84') and len(cleaned) in {11, 12}:
            cleaned = '0' + cleaned[2:]
        if 10 <= len(cleaned) <= 12 and cleaned not in normalized:
            normalized.append(cleaned)

    return normalized


def configured_hotline_numbers():
    configured = os.environ.get('CRM_HOTLINE_NUMBERS', '')
    return DEFAULT_HOTLINE_NUMBERS | {
        number
        for value in configured.split(',')
        for number in extract_phone_numbers(value.strip())
    }


def is_configured_hotline_number(phone):
    """Return whether a stored or submitted phone value is one of our hotlines."""
    if not phone:
        return False
    return any(number in configured_hotline_numbers()
               for number in extract_phone_numbers(str(phone)))


def sanitize_customer_phone(phone):
    """Never persist a company hotline as a customer's phone number."""
    return '' if is_configured_hotline_number(phone) else phone


def clear_configured_hotlines_from_customers():
    """Remove stale company hotline values saved before the phone filter existed."""
    affected = 0
    for customer in Customer.query.filter(
        Customer.phone.isnot(None), Customer.phone != ''
    ).all():
        if is_configured_hotline_number(customer.phone):
            customer.phone = ''
            affected += 1
    if affected:
        db.session.commit()
        logger.info('Cleared configured hotline from %d customer record(s)', affected)
    return affected


def extract_customer_phone_numbers(messages, page_id):
    customer_texts = [
        msg.get('message') or msg.get('story') or ''
        for msg in messages
        if (msg.get('from') or {}).get('id') and (msg.get('from') or {}).get('id') != page_id
    ]
    hotline_numbers = configured_hotline_numbers()
    return [
        number
        for text_value in customer_texts
        for number in extract_phone_numbers(text_value)
        if number not in hotline_numbers
    ]


def normalize_location_name(location):
    if not isinstance(location, str):
        return ''
    normalized = location.strip().title()
    if re.fullmatch(r'tp\.?\s*hcm', location.strip(), re.IGNORECASE):
        return 'Hồ Chí Minh'
    return normalized.replace('Thành Phố', 'Thành phố')


def extract_location(text_value):
    if not text_value:
        return ''
    text = re.sub(r'[ \t]+', ' ', text_value).strip()
    location_prefix = re.compile(
        r'(?:địa\s+chỉ\s+(?:là\s+|ở\s+|tại\s+)?|khu\s+vực\s+|'
        r'quê\s+(?:ở|tại)\s+|đang\s+(?:ở|tại)\s+|'
        r'sống\s+(?:ở|tại)\s+|(?:ở|tại)\s+)'
        r'(?P<location>.+?)(?=(?:,\s*|\s+)(?:cần|muốn|sđt|số|điện thoại|liên hệ|gọi|để)\b|[;\n]|\.(?=\s|$|(?:cần|muốn|sđt|số|điện thoại|liên hệ|gọi|để)\b)|$)',
        re.IGNORECASE,
    )
    match = location_prefix.search(text)
    if match:
        location = match.group('location').strip(' ,:-')
        if location and location.lower() not in {'nhà', 'đây', 'đó', 'chỗ này', 'nước ngoài'}:
            return normalize_location_name(location)

    location_keywords = [
        'thành phố hồ chí minh', 'hồ chí minh', 'hà nội', 'đà nẵng', 'hải phòng',
        'cần thơ', 'biên hòa', 'buôn ma thuột', 'thái nguyên', 'nam định',
        'nha trang', 'đà lạt', 'vũng tàu', 'quy nhơn', 'hạ long', 'thanh hóa',
        'bắc ninh', 'quận 10', 'quận 11', 'quận 12', 'quận 1', 'quận 2',
        'quận 3', 'quận 4', 'quận 5', 'quận 6', 'quận 7', 'quận 8', 'quận 9',
        'huế', 'vinh', 'tp.hcm', 'tp hcm', 'tphcm', 'sài gòn',
    ]
    lower = text.lower()
    for keyword in location_keywords:
        if re.search(rf'(?<!\w){re.escape(keyword)}(?!\w)', lower):
            return normalize_location_name(keyword)
    return ''


def build_customer_from_message(payload):
    message = payload.get('message') or payload.get('content') or payload.get('text') or ''
    raw_name = payload.get('name') or payload.get('customer_name') or payload.get('sender_name') or 'Khách hàng Facebook'
    raw_page_name = payload.get('page_name') or payload.get('page') or payload.get('page_title') or ''
    phone = sanitize_customer_phone(payload.get('phone') or '')
    if not phone:
        numbers = extract_phone_numbers(message)
        phone = next((number for number in numbers if number not in configured_hotline_numbers()), '')
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


def get_setting_value(key, default=None):
    value = os.environ.get(key)
    if value:
        return value
    with app.app_context():
        setting = Setting.query.filter_by(key=key).first()
        if setting and setting.value:
            return setting.value
    return default


def get_facebook_token():
    return (
        get_setting_value('FACEBOOK_PAGE_ACCESS_TOKEN')
        or get_setting_value('FACEBOOK_SYSTEM_USER_ACCESS_TOKEN')
        or get_setting_value('FACEBOOK_APP_ACCESS_TOKEN')
    )


def get_facebook_sync_limits(max_pages=None, max_conversations_per_page=None):
    configured_page_limit = max_pages
    if configured_page_limit is None:
        configured_page_limit = os.environ.get('FACEBOOK_SYNC_PAGE_LIMIT')
    configured_conversation_limit = max_conversations_per_page
    if configured_conversation_limit is None:
        configured_conversation_limit = os.environ.get(
            'FACEBOOK_SYNC_CONVERSATION_LIMIT',
            str(DEFAULT_SYNC_CONVERSATION_LIMIT),
        )

    page_limit = None
    if configured_page_limit:
        page_limit = max(1, min(int(configured_page_limit), 20))
    conversation_limit = max(
        1, min(int(configured_conversation_limit), MAX_SYNC_CONVERSATION_LIMIT)
    )
    return page_limit, conversation_limit


def get_facebook_api_call_limit():
    """Allow a deployment to reduce one sync's budget without capping it at 100."""
    configured_limit = os.environ.get('FACEBOOK_SYNC_API_CALL_LIMIT')
    if not configured_limit:
        return MAX_API_CALLS_PER_SYNC
    return max(1, min(int(configured_limit), MAX_API_CALLS_PER_SYNC))


def should_fetch_facebook_profiles():
    """Profile lookups are not available for most Messenger Page tokens.

    Facebook exposes a customer's display name in the conversation payload, but
    querying the customer's PSID as a Graph object is commonly rejected with a
    400 response.  Keep this off unless the connected app has explicitly been
    granted the required profile access.
    """
    return os.environ.get('FACEBOOK_FETCH_PROFILE', '').strip().lower() in {
        '1', 'true', 'yes', 'on'
    }


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
            if not payload['phone'] and item.get('from_facebook_sync'):
                continue
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
                source='facebook',
                message_count=payload['message_count'],
                tags=payload['tags'],
            )
            db.session.add(customer)
            imported += 1
        else:
            customer.name = payload['name'] or customer.name
            customer.first_name = payload['first_name'] or customer.first_name
            customer.last_name = payload['last_name'] or customer.last_name
            customer.facebook_id = payload['facebook_id'] or customer.facebook_id
            customer.conversation_id = payload['conversation_id'] or customer.conversation_id
            customer.profile_pic = payload['profile_pic'] or customer.profile_pic
            customer.gender = payload['gender'] or customer.gender
            customer.locale = payload['locale'] or customer.locale
            customer.phone = payload['phone']
            customer.location = payload['location'] or customer.location
            customer.page_name = payload['page_name'] or customer.page_name
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
    if endpoint.startswith('http'):
        parsed = urlsplit(endpoint)
        params = parse_qsl(parsed.query, keep_blank_values=True)
        params = [(key, value) for key, value in params if key != 'access_token']
        params.append(('access_token', access_token))
        if extra_params:
            for key, value in extra_params.items():
                if value is None:
                    continue
                params = [(k, v) for k, v in params if k != key]
                params.append((str(key), str(value)))
        url = urlunsplit(parsed._replace(query=urlencode(params)))
    else:
        base_url = f'https://graph.facebook.com/v19.0/{endpoint}'
        params = {'access_token': access_token}
        if extra_params:
            params.update({key: str(value) for key, value in extra_params.items() if value is not None})
        url = f'{base_url}?{urlencode(params)}'

    safe_endpoint = endpoint if not endpoint.startswith('http') else urlsplit(endpoint).path
    t0 = time.time()
    req = Request(url, headers={'User-Agent': 'CRM-HAY/1.0'})
    try:
        with urlopen(req, timeout=FACEBOOK_API_TIMEOUT) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except Exception as exc:
        elapsed = time.time() - t0
        logger.error("Facebook API FAIL %s %.2fs: %s", safe_endpoint, elapsed, exc)
        raise
    elapsed = time.time() - t0
    logger.info("Facebook API OK %s %.2fs", safe_endpoint, elapsed)

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


def fetch_all_facebook_pages(token, fetcher=None):
    """Fetch ALL pages from me/accounts, following pagination with cap."""
    fetcher = fetcher or fetch_facebook_json
    all_pages = []
    try:
        payload = fetcher('me/accounts', token, {'fields': 'id,name,access_token', 'limit': '100'})
    except (HTTPError, URLError, ValueError, KeyError):
        return []
    rounds = 0
    while payload and rounds < MAX_PAGE_PAGINATION_ROUNDS:
        rounds += 1
        for page in (payload.get('data') or []):
            all_pages.append(page)
        next_url = (payload.get('paging') or {}).get('next')
        if not next_url:
            break
        try:
            payload = fetcher(next_url, token)
        except (HTTPError, URLError, ValueError, KeyError):
            break
    return all_pages


def fetch_managed_facebook_messages(
    max_pages=None,
    max_conversations_per_page=None,
    progress_callback=None,
):
    token = get_facebook_token()
    if not token:
        return []

    try:
        page_data = []
        try:
            all_account_pages = fetch_all_facebook_pages(token)
            if all_account_pages:
                page_data = resolve_page_access_tokens(token, all_account_pages)
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

        page_limit, conversation_limit = get_facebook_sync_limits(max_pages, max_conversations_per_page)

        facebook_messages = []
        api_call_count = 0
        api_call_limit = get_facebook_api_call_limit()
        t_sync_start = time.time()

        pages_to_sync = page_data if page_limit is None else page_data[:page_limit]
        progress_total = len(pages_to_sync) * conversation_limit
        if progress_callback:
            progress_callback(0, progress_total, 'Đã kết nối Facebook, bắt đầu quét dữ liệu...')
        for page_index, page in enumerate(pages_to_sync):
            page_id = page.get('id')
            page_name = page.get('name') or os.environ.get('FACEBOOK_PAGE_NAME', 'Facebook Page')
            page_token = page.get('access_token') or token
            if not page_id:
                continue

            logger.info("START sync page %d/%d id=%s name=%s",
                        page_index + 1, len(pages_to_sync), page_id, page_name)
            t_page_start = time.time()
            endpoint = f'{page_id}/conversations'
            next_url = None
            collected_for_page = 0
            scanned_for_page = 0
            pagination_round = 0

            while pagination_round < MAX_CONVERSATION_PAGES:
                pagination_round += 1
                if api_call_count >= api_call_limit:
                    logger.warning("Hit API call limit=%d, stopping sync", api_call_limit)
                    break

                params = {
                    'fields': 'participants{id,name},messages.limit(100){from{id,name},message,created_time}',
                    'limit': str(CONVERSATIONS_PER_REQUEST),
                }
                payload = fetch_facebook_json(next_url or endpoint, page_token, params)
                api_call_count += 1
                conversations = payload.get('data', [])
                if not conversations:
                    break

                if API_RATE_DELAY:
                    time.sleep(API_RATE_DELAY)

                for conversation in conversations:
                    if scanned_for_page >= conversation_limit:
                        break
                    scanned_for_page += 1
                    if progress_callback:
                        progress_callback(
                            (page_index * conversation_limit) + scanned_for_page,
                            progress_total,
                            f'Đang quét Page {page_index + 1}/{len(pages_to_sync)}: {scanned_for_page}/{conversation_limit} hội thoại',
                        )
                    participants = conversation.get('participants', {}).get('data', [])
                    messages_payload = conversation.get('messages', {})
                    messages = list(messages_payload.get('data', []))
                    message_next_url = (messages_payload.get('paging') or {}).get('next')
                    while message_next_url and api_call_count < api_call_limit:
                        message_payload = fetch_facebook_json(message_next_url, page_token)
                        api_call_count += 1
                        messages.extend(message_payload.get('data', []))
                        message_next_url = (message_payload.get('paging') or {}).get('next')
                    if message_next_url:
                        continue
                    if not messages:
                        continue
                    latest_message = messages[0]
                    customer_messages = [
                        msg for msg in messages
                        if (msg.get('from') or {}).get('id') and (msg.get('from') or {}).get('id') != page_id
                    ]
                    if not customer_messages:
                        continue
                    latest_customer_message = customer_messages[0]
                    sender = latest_customer_message.get('from', {})
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

                    all_texts = []
                    for msg in customer_messages:
                        txt = msg.get('message') or msg.get('story') or ''
                        if txt:
                            all_texts.append(txt)
                    combined_text = '\n'.join(all_texts)

                    phone_numbers = extract_customer_phone_numbers(messages, page_id)
                    # Keep phone-less existing conversations in the rescan result
                    # so stale hotline/admin numbers can be cleared.
                    phone = phone_numbers[0] if phone_numbers else ''

                    location = extract_location(combined_text) or ''
                    message_text = latest_customer_message.get('message') or latest_customer_message.get('story') or '[Hình ảnh/sticker]'

                    # The conversation payload already contains the sender name.
                    # Do not query /{PSID} by default: Messenger Page tokens are
                    # normally not allowed to read customer profile fields and
                    # Facebook returns HTTP 400 for every customer.
                    profile_pic = ''
                    first_name = sender.get('name') or (customer_name.split()[-1] if customer_name else '')
                    last_name = ''
                    gender = ''
                    locale = ''
                    profile_location = ''
                    if (customer_id and should_fetch_facebook_profiles()
                            and api_call_count < api_call_limit):
                        api_call_count += 1
                        try:
                            profile = fetch_facebook_json(
                                customer_id, page_token,
                                {'fields': 'first_name,last_name,profile_pic,gender,locale,location{name}'}
                            )
                            if not isinstance(profile, dict):
                                profile = {}
                            profile_pic = profile.get('profile_pic') or ''
                            first_name = profile.get('first_name') or ''
                            last_name = profile.get('last_name') or ''
                            gender = profile.get('gender') or ''
                            locale = profile.get('locale') or ''
                            profile_location_data = profile.get('location') or {}
                            if isinstance(profile_location_data, dict):
                                profile_location = profile_location_data.get('name') or ''
                            elif isinstance(profile_location_data, str):
                                profile_location = profile_location_data
                            if API_RATE_DELAY:
                                time.sleep(API_RATE_DELAY)
                        except (HTTPError, URLError, ValueError, KeyError):
                            # Profile access is optional; importing conversations
                            # must continue when the connected app cannot read it.
                            pass
                    if not location and profile_location:
                        location = normalize_location_name(profile_location)

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
                        'phone': phone,
                        'location': location,
                        'message': combined_text[:500] if combined_text else message_text,
                        'message_date': latest_customer_message.get('created_time'),
                        'page_name': page_name,
                        'message_count': message_count,
                        'from_facebook_sync': True,
                    })
                    collected_for_page += 1

                paging = payload.get('paging', {})
                next_page = paging.get('next')
                if scanned_for_page >= conversation_limit or not next_page:
                    break
                next_url = next_page

            logger.info("END sync page %s collected=%d in %.2fs api_calls=%d",
                        page_name, collected_for_page, time.time() - t_page_start, api_call_count)

            if api_call_count >= api_call_limit:
                break

        logger.info("END Facebook sync total=%d api_calls=%d time=%.2fs",
                     len(facebook_messages), api_call_count, time.time() - t_sync_start)
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
        ensure_user_columns()
        ensure_customer_columns()
        ensure_order_columns()
        ensure_reminder_columns()
        ensure_sales_group_columns()
        ensure_sync_job_columns()
        clear_configured_hotlines_from_customers()
        admin_username = os.environ.get('CRM_ADMIN_USERNAME', '').strip().lower()
        admin_password = os.environ.get('CRM_ADMIN_PASSWORD', '')
        admin_reset_password = os.environ.get('CRM_ADMIN_RESET_PASSWORD', '')
        if admin_username and len(admin_password) < 8:
            raise RuntimeError('CRM_ADMIN_PASSWORD must be at least 8 characters.')
        configured_admin = User.query.filter_by(username=admin_username).first() if admin_username else None
        if admin_username and admin_password and not configured_admin:
            db.session.add(User(
                username=admin_username,
                password_hash=generate_password_hash(admin_password),
                role='admin',
            ))
            db.session.commit()
        if configured_admin and admin_reset_password:
            if len(admin_reset_password) < 8:
                raise RuntimeError('CRM_ADMIN_RESET_PASSWORD must be at least 8 characters.')
            configured_admin.password_hash = generate_password_hash(admin_reset_password)
            db.session.commit()
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


def build_zalo_handoff_message(customer):
    """Build the minimal customer handoff message for the sales team."""
    return '\n'.join([
        f'Họ tên: {customer.name}',
        f'Số điện thoại: {customer.phone or "Chưa có"}',
        f'Nơi ở: {customer.location or "Chưa rõ"}',
    ])


def normalize_zalo_sender_id(value):
    if value is None:
        return ''
    if isinstance(value, dict):
        value = value.get('id') or value.get('user_id') or value.get('phone') or value.get('sender_id') or ''
    return str(value).strip()


def sync_zalo_customer_message(payload):
    """Map an incoming Zalo/Facebook message into the CRM customer and return that customer."""
    if not isinstance(payload, dict):
        return None
    text = payload.get('message') or payload.get('text') or payload.get('content') or payload.get('msg') or ''
    sender_id = normalize_zalo_sender_id(payload.get('sender_id') or payload.get('user_id') or payload.get('from_user_id') or payload.get('from'))
    phone = sanitize_customer_phone(payload.get('phone') or payload.get('customer_phone') or payload.get('user_phone') or '')
    name = (payload.get('name') or payload.get('customer_name') or payload.get('sender_name') or '').strip() or 'Khách hàng Zalo'
    message_time = payload.get('sent_at') or payload.get('created_at') or payload.get('timestamp')
    customer = None

    if phone:
        customer = Customer.query.filter(Customer.phone == phone).first()
    if customer is None and sender_id:
        customer = Customer.query.filter(db.or_(Customer.facebook_id == sender_id, Customer.conversation_id == sender_id)).first()
    if customer is None and name:
        customer = Customer.query.filter(Customer.name == name, Customer.source.in_(['facebook', 'zalo', 'manual'])).first()
    if customer is None:
        customer = Customer(
            name=name,
            phone=phone or None,
            facebook_id=sender_id or None,
            source='zalo',
            page_name=payload.get('page_name') or 'Zalo',
            message_count=1,
        )
        db.session.add(customer)
        db.session.flush()

    customer.name = customer.name or name
    if phone:
        customer.phone = phone
    if sender_id and not customer.facebook_id:
        customer.facebook_id = sender_id
    customer.source = 'zalo'
    customer.page_name = customer.page_name or payload.get('page_name') or 'Zalo'
    customer.message_count = (customer.message_count or 0) + 1
    customer.message_excerpt = (text or customer.message_excerpt or '')[:500]
    if message_time:
        try:
            if isinstance(message_time, str) and message_time.endswith('Z'):
                message_time = message_time[:-1] + '+00:00'
            customer.last_message_date = datetime.fromisoformat(message_time)
        except ValueError:
            pass
    if not customer.last_message_date:
        customer.last_message_date = datetime.utcnow()

    db.session.add(MessageLog(
        customer_id=customer.id,
        sender_type='customer',
        channel='zalo',
        message=text or 'Không có nội dung',
        external_message_id=str(payload.get('message_id') or payload.get('id') or ''),
        sent_at=datetime.utcnow(),
    ))
    db.session.commit()
    return customer


def send_zalo_message(customer, text):
    """Send a customer message over Zalo when an OA token is configured. Without token, keep it as a safe local mock."""
    token = os.environ.get('ZALO_OA_ACCESS_TOKEN') or os.environ.get('ZALO_ACCESS_TOKEN')
    if not text or not customer:
        return None
    if not token:
        logger.info('Zalo send mocked for customer %s: %s', customer.id, text)
        return f'mock-zalo-{uuid.uuid4().hex[:10]}'

    endpoint = 'https://openapi.zalo.me/v3.0/oa/message/send'
    payload = {
        'recipient': {'user_id': customer.facebook_id or customer.phone or str(customer.id)},
        'message': {'text': text},
    }
    data = json.dumps(payload).encode('utf-8')
    request = Request(endpoint, data=data, headers={'Content-Type': 'application/json', 'access_token': token})
    try:
        with urlopen(request, timeout=15) as response:
            body = response.read().decode('utf-8')
        logger.info('Zalo send ok: %s', body)
        return json.loads(body).get('data', {}).get('message_id') or body
    except Exception as exc:
        logger.warning('Zalo send failed: %s', exc)
        return f'failed-zalo-{uuid.uuid4().hex[:10]}'


def is_valid_zalo_group_url(value):
    if not value:
        return True
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == 'https'
        and parsed.hostname in {'zalo.me', 'www.zalo.me'}
        and port is None
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
        and re.fullmatch(r'/g/[A-Za-z0-9_-]+/?', parsed.path)
    )


@app.route('/')
def index():
    if not current_user():
        return render_template('landing.html')

    now = datetime.utcnow()
    month_start = datetime(now.year, now.month, 1)
    customer_query = visible_customer_query()
    order_query = Order.query.join(Customer).filter(Customer.id.in_(customer_query.with_entities(Customer.id)))
    customer_count = customer_query.count()
    phone_count = customer_query.filter(Customer.phone.isnot(None), Customer.phone != '').count()
    order_count = order_query.count()
    revenue = order_query.with_entities(func.coalesce(func.sum(Order.total_amount), 0)).scalar() or 0
    month_revenue = order_query.with_entities(func.coalesce(func.sum(Order.total_amount), 0)).filter(
        Order.created_at >= month_start,
    ).scalar() or 0
    status_summary = order_query.with_entities(
        Order.status, func.count(Order.id).label('total'),
    ).group_by(Order.status).order_by(func.count(Order.id).desc()).all()
    source_summary = customer_query.with_entities(
        Customer.source, func.count(Customer.id).label('total'),
    ).group_by(Customer.source).order_by(func.count(Customer.id).desc()).all()
    recent_customers = customer_query.order_by(Customer.created_at.desc()).limit(7).all()
    recent_orders = order_query.order_by(Order.created_at.desc()).limit(7).all()
    reminder_count = 0
    pending_reminders = []
    if 'reminder' in inspect(db.engine).get_table_names():
        reminder_query = visible_reminder_query().filter(Reminder.status == 'pending')
        reminder_count = reminder_query.count()
        pending_reminders = reminder_query.order_by(Reminder.due_at.is_(None), Reminder.due_at.asc(), Reminder.created_at.desc()).limit(5).all()
    return render_template(
        'dashboard.html', customer_count=customer_count, phone_count=phone_count,
        order_count=order_count, revenue=revenue, month_revenue=month_revenue,
        status_summary=status_summary, source_summary=source_summary,
        recent_customers=recent_customers, recent_orders=recent_orders,
        reminder_count=reminder_count, pending_reminders=pending_reminders,
    )


def customer_sort_order(sort_key):
    sort_key = (sort_key or 'newest').lower()
    if sort_key == 'date':
        return [Customer.last_message_date.desc(), Customer.created_at.desc(), Customer.id.desc()]
    if sort_key == 'page':
        return [Customer.page_name.asc(), Customer.name.asc(), Customer.id.desc()]
    return [Customer.created_at.desc(), Customer.id.desc()]


@app.route('/reminders')
def reminders():
    query = visible_reminder_query().order_by(Reminder.due_at.is_(None), Reminder.due_at.asc(), Reminder.created_at.desc())
    reminder_stats = {
        'pending': query.filter(Reminder.status == 'pending').count(),
        'done': query.filter(Reminder.status == 'done').count(),
    }
    return render_template('reminders.html', reminders=query.all(), reminder_stats=reminder_stats)


@app.route('/reminders/<int:reminder_id>/complete', methods=['POST'])
def complete_reminder(reminder_id):
    reminder = visible_reminder_query().filter(Reminder.id == reminder_id).first_or_404()
    reminder.status = 'done' if reminder.status != 'done' else 'pending'
    reminder.completed_at = datetime.utcnow() if reminder.status == 'done' else None
    db.session.commit()
    flash('Đã cập nhật trạng thái nhắc việc.', 'success')
    return redirect(request.referrer or url_for('reminders'))


@app.route('/customers')
def customers():
    q = request.args.get('q', '')
    sort = request.args.get('sort', 'newest')
    if sort not in {'date', 'newest', 'page'}:
        sort = 'newest'
    sync_job_id = request.args.get('sync_job', '')
    base_query = visible_customer_query()

    if q:
        items = base_query.filter(
            db.or_(
                Customer.name.contains(q),
                Customer.phone.contains(q),
                Customer.facebook_id.contains(q),
                Customer.email.contains(q),
                Customer.tags.contains(q),
            )
        ).order_by(*customer_sort_order(sort)).all()
    else:
        items = base_query.order_by(*customer_sort_order(sort)).all()

    customer_stats = base_query.with_entities(
        Customer.source,
        Customer.page_name,
        func.count(Customer.id).label('total_customers'),
        func.count(Customer.phone).label('customers_with_phone'),
        func.max(Customer.last_message_date).label('latest_message_at'),
    ).group_by(
        Customer.source, Customer.page_name,
    ).order_by(
        func.count(Customer.id).desc(), Customer.source.asc(), Customer.page_name.asc(),
    ).all()
    return render_template(
        'customers.html', customers=items, customer_stats=customer_stats,
        q=q, sync_job_id=sync_job_id, sort=sort,
        sales_groups=SalesGroup.query.order_by(SalesGroup.name).all(),
    )


@app.route('/customers/add', methods=['GET', 'POST'])
def add_customer():
    if request.method == 'POST':
        name = request.form.get('name')
        facebook_id = request.form.get('facebook_id')
        email = request.form.get('email')
        phone = sanitize_customer_phone(request.form.get('phone'))
        notes = request.form.get('notes')
        location = request.form.get('location')
        tags = request.form.get('tags')
        if not name:
            flash('Tên là bắt buộc', 'danger')
            return redirect(url_for('add_customer'))
        c = Customer(name=name, facebook_id=facebook_id, email=email, phone=phone, notes=notes, location=location, tags=tags, assigned_user_id=current_user().id if current_user().role == 'sales' else None)
        db.session.add(c)
        db.session.commit()
        flash('Đã thêm khách hàng', 'success')
        return redirect(url_for('customers'))
    return render_template('customer_form.html', action='add')


@app.route('/customers/<int:c_id>')
def customer_detail(c_id):
    c = get_visible_customer(c_id)
    groups = SalesGroup.query.order_by(SalesGroup.name).all()
    handoffs = SalesHandoff.query.filter_by(customer_id=c.id).order_by(SalesHandoff.created_at.desc()).limit(5).all()
    sales_users = User.query.filter_by(role='sales', is_active=True).order_by(User.username).all()
    return render_template('customer_detail.html', c=c, sales_groups=groups, handoffs=handoffs, sales_users=sales_users)


@app.route('/customers/<int:c_id>/handoff-zalo', methods=['POST'])
def handoff_customer_to_zalo(c_id):
    customer = get_visible_customer(c_id)
    group_id = request.form.get('group_id', type=int)
    group = db.session.get(SalesGroup, group_id) if group_id else None
    if not group:
        return {'ok': False, 'message': 'Vui lòng chọn nhóm Sales.'}, 400

    message = build_zalo_handoff_message(customer)
    db.session.add(SalesHandoff(customer_id=customer.id, group_id=group.id, message=message))
    db.session.commit()
    return {
        'ok': True,
        'message': message,
        'group_name': group.name,
        'desktop_app_url': 'zalo://',
    }


def ensure_order_columns():
    columns = {column['name'] for column in inspect(db.engine).get_columns('order')}
    new_columns = {'delivery_address': 'TEXT', 'discount_amount': 'FLOAT DEFAULT 0', 'vat_amount': 'FLOAT DEFAULT 0', 'payment_details': 'TEXT', 'points_awarded': 'INTEGER DEFAULT 0'}
    for column_name, column_type in new_columns.items():
        if column_name not in columns:
            db.session.execute(text(f'ALTER TABLE "order" ADD COLUMN {column_name} {column_type}'))
    db.session.commit()


def update_customer_points(customer_id):
    c = db.session.get(Customer, customer_id)
    if not c:
        return
    completed_orders = Order.query.filter_by(customer_id=c.id, status='Hoàn tất').all()
    total_pts = 0
    for o in completed_orders:
        pts = int((o.total_amount or 0) // 1000000)
        o.points_awarded = pts
        total_pts += pts
    c.points = total_pts
    db.session.commit()


def recalculate_all_points():
    for c in Customer.query.all():
        update_customer_points(c.id)


def ensure_reminder_columns():
    tables = {table_name for table_name in inspect(db.engine).get_table_names()}
    if 'reminder' not in tables:
        db.session.execute(text('CREATE TABLE reminder (id INTEGER NOT NULL, customer_id INTEGER NOT NULL, assigned_user_id INTEGER, title VARCHAR(200) NOT NULL, note TEXT, due_at DATETIME, priority VARCHAR(20) NOT NULL DEFAULT "medium", status VARCHAR(20) NOT NULL DEFAULT "pending", created_at DATETIME NOT NULL, completed_at DATETIME, PRIMARY KEY (id), FOREIGN KEY(customer_id) REFERENCES customer (id), FOREIGN KEY(assigned_user_id) REFERENCES "user" (id))'))
        db.session.commit()
    columns = {column['name'] for column in inspect(db.engine).get_columns('reminder')}
    required_columns = {
        'customer_id': 'INTEGER',
        'assigned_user_id': 'INTEGER',
        'title': 'VARCHAR(200)',
        'note': 'TEXT',
        'due_at': 'DATETIME',
        'priority': 'VARCHAR(20)',
        'status': 'VARCHAR(20)',
        'created_at': 'DATETIME',
        'completed_at': 'DATETIME',
    }
    for column_name, column_type in required_columns.items():
        if column_name not in columns:
            db.session.execute(text(f'ALTER TABLE reminder ADD COLUMN {column_name} {column_type}'))
    db.session.commit()


def visible_reminder_query():
    query = Reminder.query.join(Customer).filter(Customer.id.in_(visible_customer_query().with_entities(Customer.id)))
    user = current_user()
    if user and user.role != 'admin':
        query = query.filter(db.or_(Reminder.assigned_user_id == user.id, Reminder.assigned_user_id.is_(None)))
    return query


@app.route('/sales-groups')
def sales_groups():
    return render_template('sales_groups.html', groups=SalesGroup.query.order_by(SalesGroup.name).all())


@app.route('/sales-groups/add', methods=['POST'])
def add_sales_group():
    name = (request.form.get('name') or '').strip()
    description = (request.form.get('description') or '').strip()
    zalo_url = (request.form.get('zalo_url') or '').strip()
    if not name:
        flash('Tên nhóm Sales là bắt buộc.', 'danger')
    elif SalesGroup.query.filter_by(name=name).first():
        flash('Nhóm Sales này đã tồn tại.', 'warning')
    elif not is_valid_zalo_group_url(zalo_url):
        flash('Link nhóm phải có dạng https://zalo.me/g/....', 'danger')
    else:
        db.session.add(SalesGroup(name=name, description=description or None, zalo_url=zalo_url or None))
        db.session.commit()
        flash('Đã thêm nhóm Sales.', 'success')
    return redirect(url_for('sales_groups'))


@app.route('/sales-groups/<int:group_id>/delete', methods=['POST'])
def delete_sales_group(group_id):
    group = db.session.get(SalesGroup, group_id)
    if not group:
        flash('Không tìm thấy nhóm Sales.', 'danger')
    elif group.handoffs:
        flash('Không thể xóa nhóm đã có lịch sử chuyển khách.', 'warning')
    else:
        db.session.delete(group)
        db.session.commit()
        flash('Đã xóa nhóm Sales.', 'info')
    return redirect(url_for('sales_groups'))


@app.route('/sales-groups/<int:group_id>/link', methods=['POST'])
def update_sales_group_link(group_id):
    group = db.session.get(SalesGroup, group_id)
    if not group:
        flash('Không tìm thấy nhóm Sales.', 'danger')
        return redirect(url_for('sales_groups'))
    zalo_url = (request.form.get('zalo_url') or '').strip()
    if not is_valid_zalo_group_url(zalo_url):
        flash('Link nhóm phải có dạng https://zalo.me/g/....', 'danger')
    else:
        group.zalo_url = zalo_url or None
        db.session.commit()
        flash('Đã cập nhật link nhóm Zalo.', 'success')
    return redirect(url_for('sales_groups'))


@app.route('/orders')
def orders():
    status = (request.args.get('status') or '').strip()
    q = (request.args.get('q') or '').strip()
    query = Order.query.join(Customer).filter(Customer.id.in_(visible_customer_query().with_entities(Customer.id)))
    if status:
        query = query.filter(Order.status == status)
    if q:
        query = query.filter(db.or_(Order.code.contains(q), Customer.name.contains(q), Customer.phone.contains(q)))
    order_items = query.order_by(Order.created_at.desc()).all()
    statuses = [item[0] for item in db.session.query(Order.status).distinct().order_by(Order.status).all()]
    return render_template('orders.html', orders=order_items, statuses=statuses, status=status, q=q)


@app.route('/orders/create/<int:customer_id>', methods=['GET', 'POST'])
def create_order(customer_id):
    customer = get_visible_customer(customer_id)
    if request.method == 'POST':
        try:
            discount_amount = float(request.form.get('discount_amount') or 0)
            vat_amount = float(request.form.get('vat_amount') or 0)
        except ValueError:
            flash('Chiết khấu và VAT cần là số hợp lệ.', 'danger')
            return redirect(url_for('create_order', customer_id=customer.id))
        items = []
        for code, name, unit, qty, price in zip(request.form.getlist('product_code'), request.form.getlist('product_name'), request.form.getlist('unit'), request.form.getlist('quantity'), request.form.getlist('unit_price')):
            if not name.strip():
                continue
            try:
                item = OrderItem(product_code=code.strip(), product_name=name.strip(), unit=unit.strip(), quantity=max(float(qty or 0), 0), unit_price=max(float(price or 0), 0))
            except ValueError:
                flash('Số lượng và đơn giá phải là số hợp lệ.', 'danger')
                return redirect(url_for('create_order', customer_id=customer.id))
            items.append(item)
        if not items:
            flash('Hãy nhập ít nhất một sản phẩm.', 'danger')
            return redirect(url_for('create_order', customer_id=customer.id))
        total_amount = max(sum(item.quantity * item.unit_price for item in items) - max(discount_amount, 0) + max(vat_amount, 0), 0)
        order = Order(
            customer_id=customer.id,
            code=f"DH{datetime.utcnow():%Y%m%d%H%M%S}{customer.id}",
            total_amount=total_amount,
            status=request.form.get('status') or 'Mới',
            note=request.form.get('note', '').strip(),
            delivery_address=request.form.get('delivery_address', '').strip(), payment_details=request.form.get('payment_details', '').strip(),
            discount_amount=max(discount_amount, 0), vat_amount=max(vat_amount, 0),
        )
        db.session.add(order)
        order.items.extend(items)
        db.session.commit()
        update_customer_points(customer.id)
        flash(f'Đã tạo đơn {order.code} cho {customer.name}.', 'success')
        return redirect(url_for('order_document', order_id=order.id))
    return render_template('order_form.html', customer=customer, now=datetime.utcnow)




@app.route('/orders/<int:order_id>/status', methods=['POST'])
def update_order_status(order_id):
    order = Order.query.join(Customer).filter(
        Order.id == order_id,
        Customer.id.in_(visible_customer_query().with_entities(Customer.id)),
    ).first_or_404()
    new_status = request.form.get('status', '').strip()
    if new_status:
        order.status = new_status
        db.session.commit()
        update_customer_points(order.customer_id)
        flash(f'Đã cập nhật trạng thái đơn {order.code} thành "{new_status}".', 'success')
    return redirect(request.referrer or url_for('orders'))


@app.route('/orders/<int:order_id>')
def order_document(order_id):
    order = Order.query.join(Customer).filter(
        Order.id == order_id,
        Customer.id.in_(visible_customer_query().with_entities(Customer.id)),
    ).first()
    if not order:
        return 'Không tìm thấy đơn hàng.', 404
    return render_template('order_document.html', order=order)


@app.route('/orders/<int:order_id>/export.pdf')
def export_order_pdf(order_id):
    order = Order.query.join(Customer).filter(
        Order.id == order_id,
        Customer.id.in_(visible_customer_query().with_entities(Customer.id)),
    ).first()
    if not order:
        return 'Không tìm thấy đơn hàng.', 404
    from order_pdf import build_order_pdf
    return send_file(build_order_pdf(order), mimetype='application/pdf', as_attachment=True, download_name=f'don-dat-hang-{order.code}.pdf')


@app.route('/customers/<int:c_id>/edit', methods=['GET', 'POST'])
def edit_customer(c_id):
    c = get_visible_customer(c_id)
    if request.method == 'POST':
        c.name = request.form.get('name')
        c.facebook_id = request.form.get('facebook_id')
        c.email = request.form.get('email')
        c.phone = sanitize_customer_phone(request.form.get('phone'))
        c.notes = request.form.get('notes')
        c.location = request.form.get('location')
        c.tags = request.form.get('tags')
        db.session.commit()
        flash('Đã cập nhật khách hàng', 'success')
        return redirect(url_for('customer_detail', c_id=c.id))
    return render_template('customer_form.html', action='edit', c=c)


@app.route('/customers/<int:c_id>/reminders/add', methods=['POST'])
def add_customer_reminder(c_id):
    customer = get_visible_customer(c_id)
    title = (request.form.get('title') or '').strip()
    note = (request.form.get('note') or '').strip()
    due_at_raw = (request.form.get('due_at') or '').strip()
    priority = (request.form.get('priority') or 'medium').strip().lower()
    if not title:
        flash('Tên nhắc việc là bắt buộc.', 'danger')
        return redirect(url_for('customer_detail', c_id=customer.id))
    try:
        due_at = datetime.fromisoformat(due_at_raw) if due_at_raw else datetime.utcnow() + timedelta(days=1)
    except ValueError:
        due_at = datetime.utcnow() + timedelta(days=1)
    reminder = Reminder(
        customer_id=customer.id,
        assigned_user_id=current_user().id if current_user() else None,
        title=title,
        note=note or None,
        due_at=due_at,
        priority=priority if priority in {'low', 'medium', 'high'} else 'medium',
        status='pending',
    )
    db.session.add(reminder)
    db.session.commit()
    flash('Đã thêm nhắc việc cho khách hàng.', 'success')
    return redirect(url_for('customer_detail', c_id=customer.id))


@app.route('/customers/<int:c_id>/send-zalo', methods=['POST'])
@login_required
def send_customer_zalo(c_id):
    customer = get_visible_customer(c_id)
    message = (request.form.get('zalo_message') or '').strip()
    if not message:
        flash('Nội dung tin nhắn không được để trống.', 'danger')
        return redirect(url_for('customer_detail', c_id=customer.id))
    external_message_id = send_zalo_message(customer, message)
    db.session.add(MessageLog(
        customer_id=customer.id,
        sender_type='sales',
        channel='zalo',
        message=message,
        external_message_id=external_message_id,
        sent_at=datetime.utcnow(),
    ))
    customer.last_message_date = datetime.utcnow()
    customer.message_excerpt = message[:500]
    db.session.commit()
    flash('Đã gửi tin nhắn qua Zalo từ CRM.', 'success')
    return redirect(url_for('customer_detail', c_id=customer.id))


@app.route('/api/zalo/webhook', methods=['GET', 'POST'])
def zalo_webhook():
    if request.method == 'GET':
        return {'ok': True, 'status': 'ready'}
    payload = request.get_json(silent=True) or {}
    if not payload:
        payload = request.form.to_dict(flat=True)
    customer = sync_zalo_customer_message(payload)
    return {'ok': True, 'customer_id': customer.id if customer else None}


@app.route('/customers/<int:c_id>/delete', methods=['POST'])
def delete_customer(c_id):
    c = Customer.query.get_or_404(c_id)
    if c.orders:
        flash('Không thể xóa khách đã có đơn hàng. Hãy lưu trữ hoặc xử lý đơn trước.', 'warning')
        return redirect(url_for('customer_detail', c_id=c.id))
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
    existing = Setting.query.filter_by(key=key).first()
    if existing:
        existing.value = value
        existing.description = description
    else:
        db.session.add(Setting(key=key, value=value, description=description))
    db.session.commit()
    flash('Đã lưu setting', 'success')
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


# ---------------------------------------------------------------------------
# Facebook sync — durable job when PostgreSQL + Redis/Celery are configured
# ---------------------------------------------------------------------------

def _run_facebook_sync(job_id=None):
    """Run one sync and save its visible state in the database."""
    try:
        with app.app_context():
            job = SyncJob.query.get(job_id) if job_id else None
            if job:
                job.status = 'running'
                job.started_at = datetime.utcnow()
                job.message = 'Đang đồng bộ Facebook...'
                job.progress = 0
                job.processed = 0
                job.total = 0
                job.last_activity_at = datetime.utcnow()
                db.session.commit()
            t0 = time.time()
            logger.info("START background Facebook sync")
            last_progress_write = [0]

            def update_progress(processed, total, message):
                if not job:
                    return
                now = time.time()
                if processed < total and processed - last_progress_write[0] < 10 and now - t0 < 5:
                    return
                last_progress_write[0] = processed
                job.processed = processed
                job.total = total
                job.progress = min(99, int(processed * 100 / total)) if total else 1
                job.message = message
                job.last_activity_at = datetime.utcnow()
                db.session.commit()

            messages = fetch_managed_facebook_messages(
                *get_facebook_sync_limits(),
                progress_callback=update_progress,
            )
            if not messages:
                # Still refresh the recovery copy: an empty API result must
                # never erase customers already stored in the database.
                try:
                    write_customer_snapshot()
                except Exception:
                    logger.exception('Could not write customer snapshot')
                if job:
                    job.status = 'warning'
                    job.progress = 100
                    job.processed = job.total
                    job.message = 'Không tìm thấy khách nào đã cung cấp SĐT trong các hội thoại được quét.'
                    job.finished_at = datetime.utcnow()
                    job.last_activity_at = datetime.utcnow()
                    db.session.commit()
                logger.info("END background Facebook sync: no customers (%.2fs)", time.time() - t0)
                return

            imported, updated = import_facebook_messages(messages)
            # A snapshot failure must not mark an already committed import as failed.
            try:
                write_customer_snapshot()
            except Exception:
                logger.exception('Could not write customer snapshot')
            if job:
                job.status = 'success'
                job.progress = 100
                job.processed = job.total
                job.imported = imported
                job.updated = updated
                job.message = f'Đã đồng bộ {imported} khách mới và cập nhật {updated} khách.'
                job.finished_at = datetime.utcnow()
                job.last_activity_at = datetime.utcnow()
                db.session.commit()
            logger.info("END background Facebook sync: imported=%d updated=%d (%.2fs)",
                        imported, updated, time.time() - t0)
    except Exception as exc:
        logger.exception("Background Facebook sync FAILED")
        with app.app_context():
            if job_id:
                db.session.rollback()
                job = db.session.get(SyncJob, job_id)
                if job:
                    job.status = 'error'
                    job.progress = min(job.progress or 0, 99)
                    job.message = f'Đồng bộ thất bại: {exc}'
                    job.finished_at = datetime.utcnow()
                    job.last_activity_at = datetime.utcnow()
                    db.session.commit()


if celery:
    @celery.task(name='crmhay.facebook_sync', bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
    def facebook_sync_task(self, job_id):
        _run_facebook_sync(job_id)


@app.route('/customers/sync-facebook', methods=['POST'])
def sync_facebook_customers():
    configured = bool(get_facebook_token())

    if not configured:
        flash('Chưa cấu hình token Facebook hợp lệ. Thêm FACEBOOK_PAGE_ACCESS_TOKEN hoặc FACEBOOK_SYSTEM_USER_ACCESS_TOKEN.', 'danger')
        return redirect(url_for('customers'))

    active_job = SyncJob.query.filter(SyncJob.status.in_(('queued', 'running'))).first()
    if active_job:
        flash('Đang có một tác vụ đồng bộ Facebook. Vui lòng chờ hoàn tất.', 'info')
        return redirect(url_for('customers', sync_job=active_job.id))

    job = SyncJob(
        id=str(uuid.uuid4()),
        status='queued',
        message='Đang xếp hàng đồng bộ Facebook...',
        progress=0,
        processed=0,
        total=0,
    )
    db.session.add(job)
    db.session.commit()

    if celery:
        facebook_sync_task.delay(job.id)
    else:
        # Fallback for local development only. Render production should set REDIS_URL.
        thread = threading.Thread(target=_run_facebook_sync, args=(job.id,), daemon=True)
        thread.start()

    flash('Đã bắt đầu đồng bộ Facebook. Trang sẽ tự cập nhật khi hoàn tất.', 'info')
    return redirect(url_for('customers', sync_job=job.id))


@app.route('/customers/sync-facebook/status')
def sync_facebook_status():
    """JSON endpoint for polling the current or requested durable job."""
    job_id = request.args.get('job_id')
    job = db.session.get(SyncJob, job_id) if job_id else SyncJob.query.order_by(SyncJob.created_at.desc()).first()
    if not job:
        return {'running': False, 'result': None, 'message': ''}
    return {'running': job.status in ('queued', 'running'), 'result': job.status,
            'message': job.message or '', 'imported': job.imported, 'updated': job.updated,
            'progress': job.progress or 0, 'processed': job.processed or 0, 'total': job.total or 0,
            'last_activity_at': job.last_activity_at.isoformat() if job.last_activity_at else None,
            'started_at': job.started_at.isoformat() if job.started_at else None,
            'finished_at': job.finished_at.isoformat() if job.finished_at else None}


@app.route('/facebook/import', methods=['GET', 'POST'])
def facebook_import_legacy():
    flash('Chức năng đồng bộ Facebook đã được chuyển sang tab Khách hàng.', 'info')
    return redirect(url_for('customers'))


@app.route('/facebook/export')
def facebook_export():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['id', 'name', 'first_name', 'last_name', 'facebook_id', 'conversation_id', 'email', 'phone', 'location', 'page_name', 'gender', 'locale', 'message_count', 'tags', 'last_message_date', 'source', 'profile_pic'])

    customers_list = Customer.query.order_by(Customer.created_at.desc()).all()
    for customer in customers_list:
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


# ---------------------------------------------------------------------------
# Mobile REST API — token-based auth, no CSRF
# ---------------------------------------------------------------------------

class ApiToken(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    user = db.relationship('User', backref=db.backref('api_tokens', lazy=True))


def api_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return {'error': 'Token bắt buộc.'}, 401
        token_str = auth[7:]
        token_obj = ApiToken.query.filter_by(token=token_str).first()
        if not token_obj or not token_obj.user.is_active:
            return {'error': 'Token không hợp lệ hoặc tài khoản bị khóa.'}, 401
        request._api_user = token_obj.user
        return view(*args, **kwargs)
    return wrapped


def api_current_user():
    return getattr(request, '_api_user', None)


def api_visible_customer_query():
    user = api_current_user()
    query = Customer.query
    if user.role != 'admin':
        query = query.filter(Customer.assigned_user_id == user.id)
    return query


def serialize_customer(c):
    return {
        'id': c.id, 'name': c.name, 'first_name': c.first_name,
        'last_name': c.last_name, 'facebook_id': c.facebook_id,
        'email': c.email, 'phone': c.phone, 'notes': c.notes,
        'location': c.location, 'page_name': c.page_name,
        'tags': c.tags, 'source': c.source,
        'profile_pic': c.profile_pic,
        'message_count': c.message_count or 0,
        'message_excerpt': c.message_excerpt,
        'points': c.points or 0,
        'last_message_date': c.last_message_date.isoformat() if c.last_message_date else None,
        'created_at': c.created_at.isoformat() if c.created_at else None,
        'assigned_user_id': c.assigned_user_id,
    }


def serialize_order(o):
    return {
        'id': o.id, 'code': o.code, 'total_amount': o.total_amount,
        'status': o.status, 'note': o.note,
        'delivery_address': o.delivery_address,
        'discount_amount': o.discount_amount, 'vat_amount': o.vat_amount,
        'payment_details': o.payment_details,
        'points_awarded': o.points_awarded or 0,
        'customer_name': o.customer.name if o.customer else None,
        'customer_id': o.customer_id,
        'created_at': o.created_at.isoformat() if o.created_at else None,
        'items': [{
            'id': item.id, 'product_code': item.product_code,
            'product_name': item.product_name, 'unit': item.unit,
            'quantity': item.quantity, 'unit_price': item.unit_price,
        } for item in o.items],
    }


@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip().lower()
    password = data.get('password') or ''
    user = User.query.filter_by(username=username, is_active=True).first()
    if not user or not check_password_hash(user.password_hash, password):
        return {'error': 'Tên đăng nhập hoặc mật khẩu không đúng.'}, 401
    token_str = secrets.token_urlsafe(48)
    db.session.add(ApiToken(token=token_str, user_id=user.id))
    user.last_login_at = datetime.utcnow()
    db.session.commit()
    return {'token': token_str, 'user': {'id': user.id, 'username': user.username, 'role': user.role}}


@app.route('/api/logout', methods=['POST'])
@api_login_required
def api_logout():
    auth = request.headers.get('Authorization', '')[7:]
    ApiToken.query.filter_by(token=auth).delete()
    db.session.commit()
    return {'ok': True}


@app.route('/api/dashboard')
@api_login_required
def api_dashboard():
    user = api_current_user()
    # Temporarily set session user for visible_customer_query reuse
    now = datetime.utcnow()
    month_start = datetime(now.year, now.month, 1)
    cq = api_visible_customer_query()
    oq = Order.query.join(Customer).filter(Customer.id.in_(cq.with_entities(Customer.id)))
    return {
        'customer_count': cq.count(),
        'phone_count': cq.filter(Customer.phone.isnot(None), Customer.phone != '').count(),
        'order_count': oq.count(),
        'revenue': float(oq.with_entities(func.coalesce(func.sum(Order.total_amount), 0)).scalar() or 0),
        'month_revenue': float(oq.with_entities(func.coalesce(func.sum(Order.total_amount), 0)).filter(
            Order.created_at >= month_start).scalar() or 0),
        'status_summary': [{'status': s, 'count': c} for s, c in oq.with_entities(
            Order.status, func.count(Order.id)).group_by(Order.status).all()],
    }


@app.route('/api/customers')
@api_login_required
def api_customers():
    q = request.args.get('q', '')
    query = api_visible_customer_query()
    if q:
        query = query.filter(db.or_(
            Customer.name.contains(q), Customer.phone.contains(q),
            Customer.facebook_id.contains(q), Customer.email.contains(q),
            Customer.tags.contains(q),
        ))
    items = query.order_by(Customer.created_at.desc()).limit(200).all()
    return {'customers': [serialize_customer(c) for c in items]}


@app.route('/api/customers/<int:c_id>')
@api_login_required
def api_customer_detail(c_id):
    c = api_visible_customer_query().filter(Customer.id == c_id).first()
    if not c:
        return {'error': 'Không tìm thấy khách hàng.'}, 404
    orders_list = Order.query.filter_by(customer_id=c.id).order_by(Order.created_at.desc()).all()
    return {'customer': serialize_customer(c), 'orders': [serialize_order(o) for o in orders_list]}


@app.route('/api/customers/<int:c_id>', methods=['PUT'])
@api_login_required
def api_update_customer(c_id):
    c = api_visible_customer_query().filter(Customer.id == c_id).first()
    if not c:
        return {'error': 'Không tìm thấy khách hàng.'}, 404
    data = request.get_json(silent=True) or {}
    for field in ('name', 'phone', 'email', 'notes', 'location', 'tags', 'facebook_id'):
        if field in data:
            setattr(c, field, data[field])
    db.session.commit()
    return {'customer': serialize_customer(c)}


@app.route('/api/customers', methods=['POST'])
@api_login_required
def api_add_customer():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return {'error': 'Tên là bắt buộc.'}, 400
    user = api_current_user()
    c = Customer(
        name=name,
        facebook_id=data.get('facebook_id'),
        email=data.get('email'),
        phone=data.get('phone'),
        notes=data.get('notes'),
        location=data.get('location'),
        tags=data.get('tags'),
        assigned_user_id=user.id if user.role == 'sales' else None,
    )
    db.session.add(c)
    db.session.commit()
    return {'customer': serialize_customer(c)}, 201


@app.route('/api/orders')
@api_login_required
def api_orders():
    cq = api_visible_customer_query()
    query = Order.query.join(Customer).filter(Customer.id.in_(cq.with_entities(Customer.id)))
    status = request.args.get('status', '').strip()
    if status:
        query = query.filter(Order.status == status)
    items = query.order_by(Order.created_at.desc()).limit(200).all()
    return {'orders': [serialize_order(o) for o in items]}


@app.route('/api/orders', methods=['POST'])
@api_login_required
def api_create_order():
    data = request.get_json(silent=True) or {}
    customer_id = data.get('customer_id')
    c = api_visible_customer_query().filter(Customer.id == customer_id).first()
    if not c:
        return {'error': 'Khách hàng không hợp lệ.'}, 400
    raw_items = data.get('items', [])
    if not raw_items:
        return {'error': 'Cần ít nhất một sản phẩm.'}, 400
    items = []
    for ri in raw_items:
        try:
            item = OrderItem(
                product_code=(ri.get('product_code') or '').strip(),
                product_name=(ri.get('product_name') or '').strip(),
                unit=(ri.get('unit') or '').strip(),
                quantity=max(float(ri.get('quantity') or 0), 0),
                unit_price=max(float(ri.get('unit_price') or 0), 0),
            )
        except (ValueError, TypeError):
            return {'error': 'Số lượng / đơn giá không hợp lệ.'}, 400
        items.append(item)
    discount = max(float(data.get('discount_amount') or 0), 0)
    vat = max(float(data.get('vat_amount') or 0), 0)
    total = max(sum(i.quantity * i.unit_price for i in items) - discount + vat, 0)
    order = Order(
        customer_id=c.id,
        code=f"DH{datetime.utcnow():%Y%m%d%H%M%S}{c.id}",
        total_amount=total, status=data.get('status') or 'Mới',
        note=(data.get('note') or '').strip(),
        delivery_address=(data.get('delivery_address') or '').strip(),
        payment_details=(data.get('payment_details') or '').strip(),
        discount_amount=discount, vat_amount=vat,
    )
    db.session.add(order)
    order.items.extend(items)
    db.session.commit()
    update_customer_points(c.id)
    return {'order': serialize_order(order)}, 201


if __name__ == '__main__':
    init_db()
    app.run(debug=True)
else:
    init_db()
