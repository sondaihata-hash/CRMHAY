from functools import wraps
import importlib.util
import pkgutil
import secrets
import werkzeug
from flask import Flask, render_template, request, redirect, url_for, flash, Response, send_file, send_from_directory, session, has_request_context
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from sqlalchemy import text, inspect, func
from sqlalchemy.exc import IntegrityError, OperationalError
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
try:
    import psutil
except ImportError:  # Optional locally; disk/database checks remain available.
    psutil = None
import ast
import csv
import io
import json
import hashlib
import hmac
import logging
import os
import re
import tempfile
import shutil
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import time
import threading
import uuid
from contextvars import ContextVar
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import event, or_
from sqlalchemy.orm import Session, with_loader_criteria

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, 'instance')
os.makedirs(INSTANCE_DIR, exist_ok=True)
DB_PATH = os.path.join(INSTANCE_DIR, 'crm.db')
# The database is the source of truth. This JSON file is a recovery copy
# written after each successful Facebook sync.
CUSTOMER_SNAPSHOT_PATH = os.environ.get(
    'CUSTOMER_SNAPSHOT_PATH', os.path.join(INSTANCE_DIR, 'customers-backup.json')
)
APP_VERSION = os.environ.get('CRM_MOBILE_VERSION', '1.4.1')
APP_DOWNLOAD_DIR = os.path.join(BASE_DIR, 'downloads')
APP_DOWNLOAD_FILE = 'crmhay-mobile.apk'
APP_UPDATE_URL = os.environ.get('CRM_MOBILE_UPDATE_URL', f'https://crmhay.cloud/downloads/{APP_DOWNLOAD_FILE}')
APP_MIN_VERSION = os.environ.get('CRM_MOBILE_MIN_VERSION', APP_VERSION)
MAX_API_CALLS_PER_SYNC = 1000
MAX_CONVERSATION_PAGES = 1000
AUTH_RATE_WINDOW_SECONDS = 300
AUTH_RATE_LIMIT = 10


def mobile_release_metadata():
    release_path = os.path.join(APP_DOWNLOAD_DIR, 'mobile-release.json')
    metadata = {
        'version': APP_VERSION,
        'version_code': int(os.environ.get('CRM_MOBILE_VERSION_CODE', '0') or 0),
        'download_url': APP_UPDATE_URL or None,
    }
    if os.path.isfile(release_path):
        try:
            with open(release_path, encoding='utf-8') as release_file:
                published = json.load(release_file)
            if published.get('version'):
                metadata['version'] = published['version']
            if published.get('version_code') is not None:
                metadata['version_code'] = published['version_code']
            if published.get('download_url'):
                metadata['download_url'] = published['download_url']
        except (OSError, ValueError, TypeError):
            logging.warning('Khong doc duoc mobile-release.json; dung cau hinh moi truong.')
    return metadata
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
app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=is_production,
)

MOBILE_CORS_ORIGINS = {
    'capacitor://localhost',
    'http://localhost',
    'https://localhost',
    'https://crmhay.cloud',
}


@app.after_request
def add_mobile_cors_headers(response):
    origin = request.headers.get('Origin')
    if origin in MOBILE_CORS_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, OPTIONS'
        response.headers['Access-Control-Max-Age'] = '600'
        response.headers.add('Vary', 'Origin')
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    if current_user():
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
    return response

db = SQLAlchemy(app)

# Configure these in Render for durable background jobs.  When absent, the
# local-thread fallback keeps development/demo deployments usable.
CELERY_BROKER_URL = os.environ.get('REDIS_URL') or os.environ.get('CELERY_BROKER_URL')
CELERY_ENABLED = os.environ.get('CRM_USE_CELERY', '').strip().lower() in {
    '1', 'true', 'yes', 'on'
}
REQUIRE_DURABLE_JOBS = os.environ.get('CRM_REQUIRE_DURABLE_JOBS', '').strip().lower() in {
    '1', 'true', 'yes', 'on'
}
celery = None
if CELERY_ENABLED and CELERY_BROKER_URL and Celery:
    celery = Celery(app.import_name, broker=CELERY_BROKER_URL, backend=CELERY_BROKER_URL)
    celery.conf.update(task_track_started=True, task_acks_late=True, worker_prefetch_multiplier=1)

logger = logging.getLogger(__name__)
_auth_attempts = {}
_auth_attempts_lock = threading.Lock()

ADMIN_ENDPOINTS = {
    'settings', 'add_setting', 'edit_setting', 'sales_groups',
    'add_sales_group', 'delete_sales_group', 'update_sales_group_link',
    'sync_facebook_customers', 'sync_facebook_status', 'save_facebook_token', 'facebook_export',
    'delete_customer',
    'facebook_import_legacy', 'reminders', 'complete_reminder',
}
USER_ROLES = {
    'dev': 'Developer',
    'admin': 'Quản trị viên',
    'manager': 'Quản lý',
    'employee': 'Nhân viên',
    'sales': 'Sales',
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

# Optional deployment limits. When unset, Facebook pagination runs until the
# API reports that there is no next page.
FACEBOOK_API_TIMEOUT = 30  # seconds per HTTP request
API_RATE_DELAY = 0.25  # seconds between Facebook API calls
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
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True, index=True)
    name = db.Column(db.String(200), nullable=False)
    first_name = db.Column(db.String(100), nullable=True)
    last_name = db.Column(db.String(100), nullable=True)
    facebook_id = db.Column(db.String(100), nullable=True)
    facebook_lead_id = db.Column(db.String(150), nullable=True, index=True)
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
    last_customer_message_at = db.Column(db.DateTime, nullable=True)
    message_excerpt = db.Column(db.Text, nullable=True)
    source = db.Column(db.String(50), default='manual')
    message_count = db.Column(db.Integer, default=0)
    tags = db.Column(db.String(500), nullable=True)
    points = db.Column(db.Integer, nullable=False, default=0)
    assigned_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    phone_added_at = db.Column(db.DateTime, nullable=True, index=True)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True, index=True)
    username = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='sales')
    manager_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
    is_platform_admin = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)
    customers = db.relationship('Customer', backref='assigned_user', lazy=True)
    manager = db.relationship('User', remote_side=[id], backref=db.backref('managed_users', lazy=True))
    organization = db.relationship('Organization', backref=db.backref('users', lazy=True))


class DeveloperAlert(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    severity = db.Column(db.String(20), nullable=False, default='warning', index=True)
    category = db.Column(db.String(40), nullable=False, index=True)
    message = db.Column(db.Text, nullable=False)
    fingerprint = db.Column(db.String(200), nullable=False, index=True)
    is_resolved = db.Column(db.Boolean, nullable=False, default=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    resolved_at = db.Column(db.DateTime, nullable=True)


class DeveloperCommandLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    command = db.Column(db.String(500), nullable=False)
    result = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    user = db.relationship('User')


class Organization(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    slug = db.Column(db.String(120), unique=True, nullable=False, index=True)
    company_address = db.Column(db.String(400), nullable=True)
    company_phone = db.Column(db.String(50), nullable=True)
    company_email = db.Column(db.String(200), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sales_seat_addons = db.Column(db.Integer, nullable=False, default=0)
    sales_seat_addons_expires_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class Subscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    plan = db.Column(db.String(40), nullable=False)
    billing_interval = db.Column(db.String(20), nullable=False, default='yearly')
    status = db.Column(db.String(20), nullable=False, default='pending', index=True)
    starts_at = db.Column(db.DateTime, nullable=True)
    ends_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    organization = db.relationship('Organization', backref=db.backref('subscriptions', lazy=True))


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_code = db.Column(db.BigInteger, unique=True, nullable=False, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    subscription_id = db.Column(db.Integer, db.ForeignKey('subscription.id'), nullable=False)
    plan = db.Column(db.String(40), nullable=False)
    billing_interval = db.Column(db.String(20), nullable=False, default='yearly')
    amount = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='pending', index=True)
    checkout_url = db.Column(db.Text, nullable=True)
    provider_payload = db.Column(db.Text, nullable=True)
    paid_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    organization = db.relationship('Organization')
    user = db.relationship('User')
    subscription = db.relationship('Subscription')


class Setting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True, index=True)
    key = db.Column(db.String(200), nullable=False, index=True)
    value = db.Column(db.Text, nullable=True)
    description = db.Column(db.String(400), nullable=True)


class SyncJob(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True, index=True)
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
    incremental = db.Column(db.Boolean, nullable=False, default=False)


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False, index=True)
    code = db.Column(db.String(40), nullable=False, unique=True)
    total_amount = db.Column(db.Float, nullable=False, default=0)
    status = db.Column(db.String(30), nullable=False, default='Mới')
    note = db.Column(db.Text, nullable=True)
    delivery_address = db.Column(db.String(400), nullable=True)
    discount_amount = db.Column(db.Float, nullable=False, default=0)
    vat_amount = db.Column(db.Float, nullable=False, default=0)
    payment_details = db.Column(db.String(400), nullable=True)
    sales_phone = db.Column(db.String(50), nullable=True)
    sales_bank_account = db.Column(db.String(200), nullable=True)
    points_awarded = db.Column(db.Integer, nullable=False, default=0)
    points_redeemed = db.Column(db.Integer, nullable=False, default=0)
    points_value = db.Column(db.Float, nullable=False, default=1000)
    points_discount = db.Column(db.Float, nullable=False, default=0)
    production_sent_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    customer = db.relationship('Customer', backref=db.backref('orders', lazy=True))


class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True, index=True)
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
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True, index=True)
    name = db.Column(db.String(200), nullable=False, index=True)
    description = db.Column(db.String(400), nullable=True)
    zalo_url = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class SalesHandoff(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True, index=True)
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
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False, index=True)
    sender_type = db.Column(db.String(30), nullable=False, default='customer')
    channel = db.Column(db.String(30), nullable=False, default='zalo')
    message = db.Column(db.Text, nullable=False)
    media_url = db.Column(db.Text, nullable=True)
    media_type = db.Column(db.String(30), nullable=True)
    external_message_id = db.Column(db.String(200), nullable=True)
    sent_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    customer = db.relationship('Customer', backref=db.backref('message_logs', lazy=True, cascade='all, delete-orphan'))


class CustomerActivity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    activity_type = db.Column(db.String(30), nullable=False)
    channel = db.Column(db.String(30), nullable=False, default='crm')
    note = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='completed')
    started_at = db.Column(db.DateTime, nullable=True)
    ended_at = db.Column(db.DateTime, nullable=True)
    duration_seconds = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    customer = db.relationship('Customer', backref=db.backref('activities', lazy=True, cascade='all, delete-orphan'))
    user = db.relationship('User', backref=db.backref('customer_activities', lazy=True))


class Reminder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True, index=True)
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
        'facebook_lead_id': 'VARCHAR(150)',
        'phone_added_at': 'TIMESTAMP' if db.engine.dialect.name == 'postgresql' else 'DATETIME',
        'location': 'TEXT',
        'last_message_date': 'DATETIME',
        'last_customer_message_at': 'TIMESTAMP' if db.engine.dialect.name == 'postgresql' else 'DATETIME',
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


def ensure_organization_contact_columns():
    columns = {column['name'] for column in inspect(db.engine).get_columns('organization')}
    new_columns = {
        'company_address': 'VARCHAR(400)',
        'company_phone': 'VARCHAR(50)',
        'company_email': 'VARCHAR(200)',
    }
    for column_name, column_type in new_columns.items():
        if column_name not in columns:
            db.session.execute(text(
                f'ALTER TABLE organization ADD COLUMN {column_name} {column_type}'
            ))
    db.session.commit()


def ensure_sales_seat_columns():
    columns = {column['name'] for column in inspect(db.engine).get_columns('organization')}
    new_columns = {
        'sales_seat_addons': 'INTEGER NOT NULL DEFAULT 0',
        'sales_seat_addons_expires_at': 'TIMESTAMP' if db.engine.dialect.name == 'postgresql' else 'DATETIME',
    }
    for column_name, column_type in new_columns.items():
        if column_name not in columns:
            db.session.execute(text(
                f'ALTER TABLE organization ADD COLUMN {column_name} {column_type}'
            ))
    db.session.commit()


def ensure_message_log_columns():
    columns = {column['name'] for column in inspect(db.engine).get_columns('message_log')}
    media_type = {'media_url': 'TEXT', 'media_type': 'VARCHAR(30)'}
    for column_name, column_type in media_type.items():
        if column_name not in columns:
            db.session.execute(text(f'ALTER TABLE message_log ADD COLUMN {column_name} {column_type}'))
    db.session.commit()


def ensure_customer_activity_columns():
    columns = {column['name'] for column in inspect(db.engine).get_columns('customer_activity')}
    timestamp_type = 'TIMESTAMP' if db.engine.dialect.name == 'postgresql' else 'DATETIME'
    new_columns = {
        'status': "VARCHAR(20) NOT NULL DEFAULT 'completed'",
        'started_at': timestamp_type,
        'ended_at': timestamp_type,
        'duration_seconds': 'INTEGER',
    }
    for column_name, column_type in new_columns.items():
        if column_name not in columns:
            db.session.execute(text(f'ALTER TABLE customer_activity ADD COLUMN {column_name} {column_type}'))
    db.session.commit()


def ensure_sync_job_columns():
    columns = {column['name'] for column in inspect(db.engine).get_columns('sync_job')}
    timestamp_type = 'TIMESTAMP' if db.engine.dialect.name == 'postgresql' else 'DATETIME'
    new_columns = {
        'progress': 'INTEGER DEFAULT 0',
        'processed': 'INTEGER DEFAULT 0',
        'total': 'INTEGER DEFAULT 0',
        'last_activity_at': timestamp_type,
        'incremental': 'BOOLEAN DEFAULT FALSE' if db.engine.dialect.name == 'postgresql' else 'INTEGER DEFAULT 0',
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


def ensure_subscription_payment_columns():
    for table_name in ('subscription', 'payment'):
        columns = {column['name'] for column in inspect(db.engine).get_columns(table_name)}
        if 'billing_interval' not in columns:
            db.session.execute(
                text(
                    f"ALTER TABLE {table_name} ADD COLUMN billing_interval "
                    "VARCHAR(20) NOT NULL DEFAULT 'yearly'"
                )
            )
    db.session.commit()


def current_user():
    user_id = session.get('user_id')
    return db.session.get(User, user_id) if user_id else None


def developer_required(view):
    @wraps(view)
    @login_required
    def wrapped_view(*args, **kwargs):
        user = current_user()
        if user.role != 'dev':
            return 'Chỉ tài khoản Developer mới có quyền thực hiện thao tác này.', 403
        return view(*args, **kwargs)
    return wrapped_view


@app.context_processor
def inject_current_user():
    return {'current_user': current_user, 'csrf_token': csrf_token}


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        user = current_user()
        if not user or not user.is_active:
            session.clear()
            if request.path.startswith('/api/') or request.args.get('format') == 'json':
                return {'ok': False, 'message': 'Phiên đăng nhập đã hết hạn.'}, 401
            return redirect(url_for('login', next=request.full_path))
        return view(*args, **kwargs)
    return wrapped_view


def auth_rate_limited(identifier):
    now = time.monotonic()
    with _auth_attempts_lock:
        attempts = [timestamp for timestamp in _auth_attempts.get(identifier, []) if now - timestamp < AUTH_RATE_WINDOW_SECONDS]
        if len(attempts) >= AUTH_RATE_LIMIT:
            _auth_attempts[identifier] = attempts
            return True
        attempts.append(now)
        _auth_attempts[identifier] = attempts
    return False


def clear_auth_attempts(identifier):
    with _auth_attempts_lock:
        _auth_attempts.pop(identifier, None)


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped_view(*args, **kwargs):
        if current_user().role not in {'admin', 'dev'}:
            return 'Bạn không có quyền thực hiện thao tác này.', 403
        return view(*args, **kwargs)
    return wrapped_view


def team_manager_required(view):
    @login_required
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if current_user().role not in {'admin', 'dev', 'manager'}:
            return 'Bạn không có quyền thực hiện thao tác này.', 403
        return view(*args, **kwargs)
    return wrapped_view


def customer_assignment_required(view):
    @login_required
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if current_user().role not in {'admin', 'dev', 'manager'}:
            return 'Bạn không có quyền phân công khách hàng.', 403
        return view(*args, **kwargs)
    return wrapped_view


def platform_admin_required(view):
    @wraps(view)
    @login_required
    def wrapped_view(*args, **kwargs):
        if not current_user().is_platform_admin and current_user().role != 'dev':
            return 'Chỉ Platform Admin mới có quyền thực hiện thao tác này.', 403
        return view(*args, **kwargs)
    return wrapped_view


def visible_customer_query():
    user = current_user()
    query = Customer.query
    if not user.is_platform_admin and user.organization_id is not None:
        query = query.filter(Customer.organization_id == user.organization_id)
    if user.role == 'manager':
        managed_ids = User.query.filter(User.manager_id == user.id).with_entities(User.id)
        query = query.filter(
            db.or_(
                Customer.assigned_user_id == user.id,
                Customer.assigned_user_id.in_(managed_ids),
            )
        )
    elif user.role != 'admin':
        query = query.filter(Customer.assigned_user_id == user.id)
    return query


def get_visible_customer(customer_id):
    return visible_customer_query().filter(Customer.id == customer_id).first_or_404()


def assignable_sales_user(user_id):
    actor = current_user()
    query = User.query.filter(
        User.id == user_id,
        User.role.in_(('sales', 'employee', 'manager')),
        User.is_active.is_(True),
    )
    if actor.role == 'manager':
        query = query.filter(User.role.in_(('sales', 'employee')), User.manager_id == actor.id)
    return query.first()


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        rate_key = f'web:{request.remote_addr or "unknown"}'
        if auth_rate_limited(rate_key):
            flash('Quá nhiều lần đăng nhập không thành công. Vui lòng thử lại sau ít phút.', 'danger')
            return render_template('login.html'), 429
        username = (request.form.get('username') or '').strip().lower()
        password = request.form.get('password') or ''
        user = User.query.filter_by(username=username, is_active=True).first()
        if not user or not check_password_hash(user.password_hash, password):
            if request.is_json or request.accept_mimetypes.best == 'application/json':
                return {'ok': False, 'message': 'Tên đăng nhập hoặc mật khẩu không đúng.'}, 401
            flash('Tên đăng nhập hoặc mật khẩu không đúng.', 'danger')
            return render_template('login.html')
        clear_auth_attempts(rate_key)
        if user.organization_id is None:
            default_organization = Organization.query.filter_by(slug='default').first()
            if default_organization:
                user.organization_id = default_organization.id
                db.session.commit()
        set_tenant_context(None if user.is_platform_admin else user.organization_id)
        session.clear()
        session['user_id'] = user.id
        user.last_login_at = datetime.utcnow()
        db.session.commit()
        if request.is_json or request.accept_mimetypes.best == 'application/json':
            return {'ok': True, 'user': {
                'id': user.id, 'username': user.username, 'role': user.role,
                'organization_id': user.organization_id,
                'organization_name': user.organization.name if user.organization else None,
            }}
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


@app.route('/privacy-policy')
def privacy_policy():
    return render_template('privacy_policy.html')


@app.route('/pricing')
def pricing():
    return render_template('pricing.html')


PAYOS_PLANS = {
    'basic': {'name': 'Basic', 'monthly_amount': 149500, 'yearly_amount': 1495000},
    'growth': {'name': 'Growth', 'monthly_amount': 349500, 'yearly_amount': 3495000},
    'business': {'name': 'Business', 'monthly_amount': 745000, 'yearly_amount': 7450000},
}
SALES_SEATS_PER_PLAN = {'basic': 6, 'growth': 12, 'business': 30}
SALES_SEAT_MONTHLY_PRICE = 30000
PLAN_RANK = {'basic': 1, 'growth': 2, 'business': 3}
PLAN_FEATURES = {
    'basic': {'manual_sync': True, 'export': False, 'hourly_sync': False},
    'growth': {'manual_sync': True, 'export': True, 'hourly_sync': False},
    'business': {'manual_sync': True, 'export': True, 'hourly_sync': True},
}


def active_subscription(organization_id):
    return Subscription.query.filter(
        Subscription.organization_id == organization_id,
        Subscription.status == 'active',
        db.or_(Subscription.ends_at.is_(None), Subscription.ends_at >= datetime.utcnow()),
    ).order_by(Subscription.created_at.desc()).first()


def organization_plan(user=None):
    user = user or current_user()
    if user.is_platform_admin:
        return 'business'
    subscription = active_subscription(user.organization_id)
    return subscription.plan if subscription and subscription.plan in PLAN_RANK else 'basic'


def is_default_admin(user=None):
    user = user or current_user()
    if not user or user.role != 'admin':
        return False
    default_organization = Organization.query.filter_by(slug='default').first()
    configured_username = os.environ.get('CRM_ADMIN_USERNAME', '').strip().lower()
    return bool(
        default_organization
        and user.organization_id == default_organization.id
        and configured_username
        and user.username == configured_username
    )


def plan_allows(feature, user=None):
    if feature == 'hourly_sync' and is_default_admin(user):
        return True
    return PLAN_FEATURES[organization_plan(user)].get(feature, False)


def sales_seat_limit(user=None):
    user = user or current_user()
    if user.is_platform_admin:
        return None
    organization = db.session.get(Organization, user.organization_id)
    base_limit = SALES_SEATS_PER_PLAN.get(organization_plan(user), SALES_SEATS_PER_PLAN['basic'])
    addon_limit = 0
    if organization and organization.sales_seat_addons_expires_at and organization.sales_seat_addons_expires_at >= datetime.utcnow():
        addon_limit = organization.sales_seat_addons
    return base_limit + addon_limit


def active_sales_count(organization_id):
    return User.query.filter_by(
        organization_id=organization_id, role='sales',
    ).count()


def _payos_config():
    client_id = os.environ.get('PAYOS_CLIENT_ID', '').strip()
    api_key = os.environ.get('PAYOS_API_KEY', '').strip()
    checksum_key = (
        os.environ.get('PAYOS_CHECKSUM_KEY', '').strip()
        or os.environ.get('PAYOS_WEBHOOK_SECRET', '').strip()
    )
    if not client_id or not api_key or not checksum_key:
        raise RuntimeError(
            'PAYOS_CLIENT_ID, PAYOS_API_KEY and PAYOS_CHECKSUM_KEY must be configured.'
        )
    return client_id, api_key, checksum_key


def _payos_signature(data, secret):
    """PayOS signs alphabetically sorted key=value pairs (nested values are JSON)."""
    values = []
    for key in sorted(data):
        if key == 'signature':
            continue
        value = data[key]
        if isinstance(value, bool):
            value = str(value).lower()
        elif isinstance(value, (dict, list)):
            value = json.dumps(value, separators=(',', ':'), ensure_ascii=False)
        values.append(f'{key}={value if value is not None else ""}')
    return hmac.new(secret.encode(), '&'.join(values).encode(), hashlib.sha256).hexdigest()


def _payos_create_link(payment, plan_name, billing_interval='yearly'):
    client_id, api_key, checksum_key = _payos_config()
    base = os.environ.get('PAYOS_API_URL', 'https://api-merchant.payos.vn').rstrip('/')
    status_url = url_for('payment_status', order_code=payment.order_code, _external=True)
    payload = {
        'orderCode': payment.order_code,
        'amount': payment.amount,
        'description': f'CRM HAY {plan_name} {billing_interval}'[:25],
        'cancelUrl': status_url,
        'returnUrl': status_url,
        'signature': hmac.new(
            checksum_key.encode(),
            (
                f'amount={payment.amount}&cancelUrl={status_url}'
                f'&description={f"CRM HAY {plan_name} {billing_interval}"[:25]}'
                f'&orderCode={payment.order_code}&returnUrl={status_url}'
            ).encode(),
            hashlib.sha256,
        ).hexdigest(),
    }
    request_obj = Request(
        f'{base}/v2/payment-requests',
        data=json.dumps(payload).encode(),
        headers={
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'User-Agent': 'CRM-HAY-PayOS/1.0',
            'x-client-id': client_id,
            'x-api-key': api_key,
        },
        method='POST',
    )
    try:
        with urlopen(request_obj, timeout=15) as response:
            result = json.loads(response.read().decode())
    except HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')[:500]
        raise RuntimeError(
            f'PayOS từ chối tạo payment link (HTTP {exc.code}): {detail}'
        ) from exc
    if result.get('code') not in (None, '00') or not result.get('data', {}).get('checkoutUrl'):
        raise RuntimeError(result.get('desc') or 'PayOS không trả về liên kết thanh toán.')
    return result['data']['checkoutUrl'], result


def _payos_upgrade_link(organization, user, target_plan):
    current = active_subscription(organization.id)
    if not current or target_plan not in PLAN_RANK or PLAN_RANK[target_plan] <= PLAN_RANK.get(current.plan, 1):
        raise RuntimeError('Gói hiện tại đã đủ hoặc không hợp lệ.')
    interval = current.billing_interval if current.billing_interval in ('monthly', 'yearly') else 'yearly'
    amount = PAYOS_PLANS[target_plan][f'{interval}_amount'] - PAYOS_PLANS[current.plan][f'{interval}_amount']
    if amount <= 0:
        raise RuntimeError('Không có khoản chênh lệch cần thanh toán.')
    payment = Payment(
        order_code=int(datetime.utcnow().timestamp() * 1000) % 900000000 + 100000000,
        organization_id=organization.id,
        user_id=user.id,
        subscription_id=current.id,
        plan=target_plan,
        billing_interval=interval,
        amount=amount,
    )
    db.session.add(payment)
    db.session.flush()
    checkout_url, result = _payos_create_link(payment, f'Nang cap {target_plan}', interval)
    payment.checkout_url = checkout_url
    payment.provider_payload = json.dumps(result, ensure_ascii=False)
    db.session.commit()
    return checkout_url


def _payos_sales_seat_link(organization, user, quantity):
    subscription = active_subscription(organization.id)
    if not subscription:
        raise RuntimeError('Workspace chưa có gói thuê bao đang hoạt động.')
    amount = quantity * SALES_SEAT_MONTHLY_PRICE
    payment = Payment(
        order_code=int(datetime.utcnow().timestamp() * 1000) % 900000000 + 100000000,
        organization_id=organization.id,
        user_id=user.id,
        subscription_id=subscription.id,
        plan='sales_seats',
        billing_interval='monthly',
        amount=amount,
    )
    db.session.add(payment)
    db.session.flush()
    checkout_url, result = _payos_create_link(
        payment, f'{quantity} Sales seats', 'monthly'
    )
    payment.checkout_url = checkout_url
    payment.provider_payload = json.dumps(
        {'quantity': quantity, 'payos': result}, ensure_ascii=False
    )
    db.session.commit()
    return checkout_url


@app.route('/admin/sales-seats', methods=['GET', 'POST'])
@admin_required
def purchase_sales_seats():
    if current_user().role != 'dev' and current_user().role != 'admin':
        return 'Chỉ Company Admin mới có quyền mua thêm tài khoản Sales.', 403
    organization = db.session.get(Organization, current_user().organization_id)
    if request.method == 'GET':
        return render_template(
            'sales_seats.html',
            sales_count=active_sales_count(organization.id),
            seat_limit=sales_seat_limit(),
            seat_price=SALES_SEAT_MONTHLY_PRICE,
        )
    quantity = request.form.get('quantity', type=int)
    if not quantity or quantity < 1 or quantity > 100:
        return {'ok': False, 'message': 'Số tài khoản Sales phải từ 1 đến 100.'}, 400
    try:
        checkout_url = _payos_sales_seat_link(organization, current_user(), quantity)
    except RuntimeError as exc:
        return {'ok': False, 'message': str(exc)}, 400
    return redirect(checkout_url)


@app.route('/upgrade/<plan>', methods=['GET', 'POST'])
@admin_required
def upgrade_plan(plan):
    plan = plan.lower()
    if plan not in PLAN_RANK:
        return {'ok': False, 'message': 'Gói nâng cấp không hợp lệ.'}, 404
    organization = db.session.get(Organization, current_user().organization_id)
    if request.method == 'GET':
        return render_template('upgrade.html', target_plan=plan, current_plan=organization_plan())
    try:
        checkout_url = _payos_upgrade_link(organization, current_user(), plan)
    except RuntimeError as exc:
        return {'ok': False, 'message': str(exc)}, 400
    return redirect(checkout_url)


@app.route('/checkout', methods=['GET', 'POST'])
@app.route('/checkout/<plan>', methods=['GET', 'POST'])
def checkout(plan='growth'):
    plan = (plan or 'growth').lower()
    selected = PAYOS_PLANS.get(plan)
    if not selected:
        return {'ok': False, 'message': 'Gói thanh toán không hợp lệ.'}, 404
    if request.method == 'GET':
        billing_interval = request.args.get('interval', 'yearly').lower()
        if billing_interval not in ('monthly', 'yearly'):
            billing_interval = 'yearly'
        return render_template(
            'checkout.html',
            plan=plan,
            selected=selected,
            billing_interval=billing_interval,
            amount=selected[f'{billing_interval}_amount'],
        )
    def checkout_error(message, status):
        if request.is_json:
            return {'ok': False, 'message': message}, status
        return render_template(
            'checkout.html',
            plan=plan,
            selected=selected,
            billing_interval=(request.form.get('billing_interval') or 'yearly'),
            amount=selected.get(
                f'{request.form.get("billing_interval", "yearly")}_amount',
                selected['yearly_amount'],
            ),
            error=message,
        ), status

    if not request.is_json:
        csrf_error = validate_csrf_token()
        if csrf_error:
            return checkout_error(csrf_error[0], csrf_error[1])
    values = request.get_json(silent=True) or request.form
    billing_interval = (values.get('billing_interval') or 'yearly').strip().lower()
    if billing_interval not in ('monthly', 'yearly'):
        return checkout_error('Chu kỳ thanh toán không hợp lệ.', 400)
    organization_name = (values.get('organization_name') or values.get('name') or '').strip()
    username = (values.get('username') or '').strip().lower()
    password = values.get('password') or ''
    if not organization_name or not username or len(password) < 8:
        return checkout_error(
            'Tên doanh nghiệp, tài khoản và mật khẩu tối thiểu 8 ký tự là bắt buộc.',
            400,
        )
    if User.query.filter_by(username=username).first():
        return checkout_error('Tên đăng nhập đã tồn tại.', 409)
    try:
        _payos_config()
    except RuntimeError as exc:
        return checkout_error(str(exc), 503)
    slug = re.sub(r'[^a-z0-9]+', '-', organization_name.lower()).strip('-') or f'company-{uuid.uuid4().hex[:8]}'
    if Organization.query.filter_by(slug=slug).first():
        slug = f'{slug}-{uuid.uuid4().hex[:6]}'
    organization = Organization(name=organization_name, slug=slug, is_active=False)
    db.session.add(organization)
    db.session.flush()
    user = User(
        username=username,
        password_hash=generate_password_hash(password),
        role='admin',
        organization_id=organization.id,
        is_active=False,
    )
    subscription = Subscription(
        plan=plan,
        billing_interval=billing_interval,
        status='pending',
        organization_id=organization.id,
    )
    db.session.add_all([user, subscription])
    db.session.flush()
    order_code = int(datetime.utcnow().timestamp() * 1000) % 900000000 + 100000000
    payment = Payment(order_code=order_code, organization_id=organization.id, user_id=user.id,
                      subscription_id=subscription.id, plan=plan,
                      billing_interval=billing_interval,
                      amount=selected[f'{billing_interval}_amount'])
    db.session.add(payment)
    try:
        checkout_url, provider_result = _payos_create_link(
            payment, selected['name'], billing_interval
        )
        payment.checkout_url = checkout_url
        payment.provider_payload = json.dumps(provider_result, ensure_ascii=False)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.exception('PayOS create payment link failed')
        return checkout_error(f'Không thể tạo liên kết thanh toán PayOS: {exc}', 502)
    if request.is_json:
        return {'ok': True, 'order_code': payment.order_code, 'checkout_url': payment.checkout_url}
    return redirect(payment.checkout_url)


@app.route('/payment/status/<int:order_code>')
@app.route('/checkout/status/<int:order_code>')
def payment_status(order_code):
    payment = Payment.query.filter_by(order_code=order_code).first_or_404()
    return render_template('payment_status.html', payment=payment)


@app.route('/api/payos/webhook', methods=['GET', 'POST'])
@app.route('/api/payos/webhook/', methods=['GET', 'POST'])
@app.route('/payos/webhook', methods=['GET', 'POST'])
def payos_webhook():
    if request.method == 'GET':
        return {'ok': True, 'service': 'payos-webhook'}
    payload = request.get_json(silent=True) or {}
    data = payload.get('data') if isinstance(payload.get('data'), dict) else payload
    signature = payload.get('signature') or data.get('signature')
    secret = os.environ.get('PAYOS_WEBHOOK_SECRET', '').strip() or os.environ.get('PAYOS_API_KEY', '').strip()
    if not secret:
        return {'ok': False, 'message': 'PayOS webhook secret is not configured.'}, 503
    if not signature and not data.get('orderCode'):
        return {'code': 0, 'desc': 'success'}
    if not signature or not hmac.compare_digest(signature, _payos_signature(data, secret)):
        return {'ok': False, 'message': 'Invalid PayOS signature.'}, 403
    order_code = data.get('orderCode')
    payment = Payment.query.filter_by(order_code=int(order_code)).first() if order_code else None
    if not payment:
        return {'ok': False, 'message': 'Payment not found.'}, 404
    if payment.status != 'paid':
        successful = str(payload.get('code', data.get('code', '00'))) == '00' and payload.get('success', True) is not False
        amount = int(data.get('amount', payment.amount) or 0)
        if successful and amount == payment.amount:
            payment.status = 'paid'
            payment.paid_at = datetime.utcnow()
            payment.organization.is_active = True
            payment.user.is_active = True
            if payment.plan == 'sales_seats':
                try:
                    quantity = json.loads(payment.provider_payload or '{}').get('quantity', 0)
                except (TypeError, ValueError):
                    quantity = 0
                if not isinstance(quantity, int) or quantity < 1:
                    return {'ok': False, 'message': 'Invalid Sales seat quantity.'}, 400
                existing_quantity = payment.organization.sales_seat_addons or 0
                if (
                    payment.organization.sales_seat_addons_expires_at
                    and payment.organization.sales_seat_addons_expires_at >= payment.paid_at
                ):
                    existing_quantity = payment.organization.sales_seat_addons
                else:
                    existing_quantity = 0
                payment.organization.sales_seat_addons = existing_quantity + quantity
                payment.organization.sales_seat_addons_expires_at = payment.paid_at + timedelta(days=30)
            else:
                payment.subscription.status = 'active'
                payment.subscription.plan = payment.plan
                payment.subscription.starts_at = payment.paid_at
                payment.subscription.ends_at = payment.paid_at + timedelta(
                    days=30 if payment.billing_interval == 'monthly' else 365
                )
            payment.provider_payload = json.dumps(payload, ensure_ascii=False)
            db.session.commit()
    return {'ok': True}


@app.route('/terms')
def terms_of_service():
    return render_template('terms.html')


@app.route('/data-deletion')
def data_deletion():
    return render_template('data_deletion.html')


@app.route('/healthz', methods=['GET', 'POST'])
def healthz():
    if request.method == 'POST':
        return payos_webhook()
    return {'ok': True, 'service': 'crmhay'}


@app.route('/readyz')
def readyz():
    try:
        db.session.execute(text('SELECT 1'))
    except OperationalError:
        return {'ok': False, 'service': 'crmhay', 'database': 'unavailable'}, 503
    return {'ok': True, 'service': 'crmhay', 'database': 'ready'}


def record_developer_alert(severity, category, message):
    fingerprint = hashlib.sha256(f'{category}:{message}'.encode('utf-8')).hexdigest()
    existing = DeveloperAlert.query.filter_by(
        fingerprint=fingerprint, is_resolved=False,
    ).first()
    if existing:
        return existing
    alert = DeveloperAlert(
        severity=severity, category=category, message=message,
        fingerprint=fingerprint,
    )
    db.session.add(alert)
    db.session.commit()
    logger.warning('Developer alert [%s] %s: %s', severity, category, message)
    return alert


def run_system_health_check():
    """Run inexpensive checks that are safe to execute from the web process."""
    with app.app_context():
        try:
            db.session.execute(text('SELECT 1'))
        except OperationalError as exc:
            db.session.rollback()
            record_developer_alert('critical', 'database', f'Cơ sở dữ liệu không phản hồi: {exc}')

        threshold_setting = Setting.query.filter_by(
            organization_id=None, key='developer.storage_warning_percent'
        ).first()
        warning_percent = int(threshold_setting.value) if threshold_setting and threshold_setting.value.isdigit() else 20
        critical_percent = max(5, warning_percent // 2)
        usage = shutil.disk_usage(BASE_DIR)
        free_percent = (usage.free / usage.total * 100) if usage.total else 0
        if free_percent < critical_percent:
            record_developer_alert(
                'critical', 'storage',
                f'Ổ đĩa còn {free_percent:.1f}% dung lượng trống ({usage.free / 1024**3:.1f} GB).',
            )
        elif free_percent < warning_percent:
            record_developer_alert(
                'warning', 'storage',
                f'Ổ đĩa còn {free_percent:.1f}% dung lượng trống; cần dọn dẹp hoặc nâng cấp.',
            )

        if psutil:
            memory = psutil.virtual_memory()
            cpu = psutil.cpu_percent(interval=0.2)
            if memory.percent >= 90:
                record_developer_alert('critical', 'hardware', f'RAM đang sử dụng {memory.percent:.1f}%.')
            elif memory.percent >= 80:
                record_developer_alert('warning', 'hardware', f'RAM đang sử dụng {memory.percent:.1f}%.')
            if cpu >= 95:
                record_developer_alert('critical', 'hardware', f'CPU đang sử dụng {cpu:.1f}%.')
            elif cpu >= 85:
                record_developer_alert('warning', 'hardware', f'CPU đang sử dụng {cpu:.1f}%.')
        else:
            record_developer_alert(
                'info', 'hardware',
                'Chưa cài psutil nên chưa đo được CPU/RAM; hãy cài dependencies của ứng dụng.',
            )

        log_path = os.path.join(BASE_DIR, 'logs', 'vps-supervisor.log')
        if os.path.isfile(log_path):
            try:
                with open(log_path, 'rb') as log_file:
                    log_file.seek(max(0, os.path.getsize(log_path) - 65536))
                    recent_log = log_file.read().decode('utf-8', errors='replace')
                if 'Traceback (most recent call last)' in recent_log or re.search(r'\b(ERROR|CRITICAL)\b', recent_log):
                    record_developer_alert(
                        'warning', 'application',
                        'Nhật ký supervisor có lỗi ERROR/CRITICAL hoặc traceback gần đây.',
                    )
            except OSError as exc:
                logger.warning('Không đọc được supervisor log: %s', exc)


def _developer_monitor_loop():
    while True:
        try:
            run_system_health_check()
        except Exception:
            logger.exception('Developer health monitor failed')
        time.sleep(300)


def developer_command_response(command):
    normalized = re.sub(r'\s+', ' ', (command or '').strip().lower())
    if not normalized:
        return 'Hãy nhập lệnh. Ví dụ: “kiểm tra hệ thống” hoặc “xem cảnh báo”.'
    if normalized in {'kiểm tra hệ thống', 'kiem tra he thong', 'status', 'health'}:
        run_system_health_check()
        unresolved = DeveloperAlert.query.filter_by(is_resolved=False).order_by(
            DeveloperAlert.created_at.desc()
        ).all()
        if not unresolved:
            return 'Hệ thống đang hoạt động và chưa có cảnh báo chưa xử lý.'
        return 'Có {} cảnh báo chưa xử lý:\n{}'.format(
            len(unresolved),
            '\n'.join(f'- [{alert.severity}] {alert.message}' for alert in unresolved[:10]),
        )
    if normalized in {'xem cảnh báo', 'xem canh bao', 'alerts'}:
        alerts = DeveloperAlert.query.filter_by(is_resolved=False).order_by(
            DeveloperAlert.created_at.desc()
        ).all()
        return '\n'.join(f'- [{alert.severity}] {alert.message}' for alert in alerts[:20]) or 'Chưa có cảnh báo.'
    if normalized in {'cài đặt hệ thống', 'cai dat he thong', 'setup'}:
        return (
            'Các lệnh an toàn được hỗ trợ: “kiểm tra hệ thống”, “xem cảnh báo”, '
            '“đặt ngưỡng lưu trữ 15”, “bật đồng bộ mỗi giờ”, “tắt đồng bộ mỗi giờ”. '
            'Chatbot không chạy lệnh PowerShell/shell tùy ý.'
        )
    storage_match = re.fullmatch(r'(?:đặt|dat) ngưỡng lưu trữ (\d{1,2})', normalized)
    if storage_match:
        threshold = int(storage_match.group(1))
        if threshold < 5 or threshold > 50:
            return 'Ngưỡng lưu trữ phải từ 5% đến 50%.'
        setting = Setting.query.filter_by(organization_id=None, key='developer.storage_warning_percent').first()
        if not setting:
            setting = Setting(organization_id=None, key='developer.storage_warning_percent')
            db.session.add(setting)
        setting.value = str(threshold)
        db.session.commit()
        return f'Đã đặt cảnh báo khi dung lượng trống dưới {threshold}%.'
    if normalized in {'bật đồng bộ mỗi giờ', 'bat dong bo moi gio'}:
        setting = Setting.query.filter_by(organization_id=None, key='developer.hourly_sync_enabled').first()
        if not setting:
            setting = Setting(organization_id=None, key='developer.hourly_sync_enabled')
            db.session.add(setting)
        setting.value = 'true'
        db.session.commit()
        return 'Đã bật cờ cấu hình đồng bộ mỗi giờ. Gói Business vẫn là điều kiện bắt buộc.'
    if normalized in {'tắt đồng bộ mỗi giờ', 'tat dong bo moi gio'}:
        setting = Setting.query.filter_by(organization_id=None, key='developer.hourly_sync_enabled').first()
        if not setting:
            setting = Setting(organization_id=None, key='developer.hourly_sync_enabled')
            db.session.add(setting)
        setting.value = 'false'
        db.session.commit()
        return 'Đã tắt cờ cấu hình đồng bộ mỗi giờ.'
    return 'Tôi chưa hiểu lệnh này. Gõ “cài đặt hệ thống” để xem các lệnh được hỗ trợ.'


@app.route('/developer')
@developer_required
def developer_console():
    run_system_health_check()
    return render_template(
        'developer.html',
        alerts=DeveloperAlert.query.filter_by(is_resolved=False).order_by(
            DeveloperAlert.created_at.desc()
        ).all(),
        commands=DeveloperCommandLog.query.order_by(
            DeveloperCommandLog.created_at.desc()
        ).limit(20).all(),
    )


@app.route('/developer/chat', methods=['POST'])
@developer_required
def developer_chat():
    command = (request.form.get('command') or '').strip()
    result = developer_command_response(command)
    db.session.add(DeveloperCommandLog(
        user_id=current_user().id, command=command[:500], result=result,
    ))
    db.session.commit()
    return redirect(url_for('developer_console'))


@app.route('/developer/alerts/<int:alert_id>/resolve', methods=['POST'])
@developer_required
def resolve_developer_alert(alert_id):
    alert = DeveloperAlert.query.get_or_404(alert_id)
    alert.is_resolved = True
    alert.resolved_at = datetime.utcnow()
    db.session.commit()
    flash('Đã đánh dấu cảnh báo là đã xử lý.', 'success')
    return redirect(url_for('developer_console'))


@app.route('/downloads/<path:filename>')
def mobile_download(filename):
    if filename != APP_DOWNLOAD_FILE:
        return {'error': 'Không tìm thấy tệp cập nhật.'}, 404
    return send_from_directory(APP_DOWNLOAD_DIR, filename, as_attachment=True)


@app.route('/api/mobile/version')
def api_mobile_version():
    release = mobile_release_metadata()
    return {
        'version': release['version'],
        'minimum_version': APP_MIN_VERSION,
        'version_code': release['version_code'],
        'update_available': bool(release['download_url']),
        'download_url': release['download_url'],
        'message': 'Có phiên bản CRM Mobile mới.' if release['download_url'] else 'Bạn đang dùng phiên bản mới nhất.',
    }


@app.route('/logout', methods=['POST'])
@login_required
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/admin/users')
@team_manager_required
def users():
    actor = current_user()
    user_query = User.query
    if actor.role == 'manager':
        user_query = user_query.filter(User.manager_id == actor.id)
    return render_template(
        'users.html',
        users=user_query.order_by(User.role, User.username).all(),
        role_options=USER_ROLES,
        role_labels=USER_ROLES,
        managers=User.query.filter_by(role='manager', is_active=True).order_by(User.username).all(),
        organizations=Organization.query.order_by(Organization.name).all() if actor.role == 'dev' else [],
        active_sales_count=active_sales_count(actor.organization_id),
        sales_seat_limit=sales_seat_limit(actor),
        actor=actor,
    )


@app.route('/platform/organizations', methods=['GET', 'POST'])
@platform_admin_required
def platform_organizations():
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        username = (request.form.get('username') or '').strip().lower()
        password = request.form.get('password') or ''
        slug = (request.form.get('slug') or '').strip().lower()
        slug = re.sub(r'[^a-z0-9-]+', '-', slug).strip('-') or re.sub(
            r'[^a-z0-9]+', '-', name.lower()
        ).strip('-') or f'company-{uuid.uuid4().hex[:8]}'
        company_address = (request.form.get('company_address') or '').strip()
        company_phone = (request.form.get('company_phone') or '').strip()
        company_email = (request.form.get('company_email') or '').strip()
        if not name or not username or len(password) < 8:
            flash('Tên công ty, tài khoản Admin và mật khẩu tối thiểu 8 ký tự là bắt buộc.', 'danger')
        elif Organization.query.filter_by(slug=slug).first():
            flash('Tên công ty này đã tồn tại.', 'warning')
        elif User.query.filter_by(username=username).first():
            flash('Tên đăng nhập Admin đã tồn tại.', 'warning')
        else:
            organization = Organization(
                name=name, slug=slug, company_address=company_address,
                company_phone=company_phone, company_email=company_email,
            )
            db.session.add(organization)
            db.session.flush()
            db.session.add(User(
                organization_id=organization.id,
                username=username,
                password_hash=generate_password_hash(password),
                role='admin',
                is_platform_admin=False,
            ))
            db.session.commit()
            flash(f'Đã tạo workspace {name} và tài khoản Company Admin.', 'success')
        return redirect(url_for('platform_organizations'))
    return render_template(
        'organizations.html',
        organizations=Organization.query.order_by(Organization.created_at.desc()).all(),
    )


@app.route('/platform/organizations/<int:organization_id>/edit', methods=['POST'])
@platform_admin_required
def edit_organization(organization_id):
    organization = db.session.get(Organization, organization_id)
    if not organization:
        return 'Không tìm thấy công ty.', 404
    name = (request.form.get('name') or '').strip()
    slug = re.sub(r'[^a-z0-9-]+', '-', (request.form.get('slug') or '').strip().lower()).strip('-')
    duplicate = Organization.query.filter(
        Organization.slug == slug, Organization.id != organization.id,
    ).first()
    if not name or not slug or duplicate:
        flash('Tên công ty và slug hợp lệ, không trùng là bắt buộc.', 'danger')
    else:
        organization.name = name
        organization.slug = slug
        organization.company_address = (request.form.get('company_address') or '').strip()
        organization.company_phone = (request.form.get('company_phone') or '').strip()
        organization.company_email = (request.form.get('company_email') or '').strip()
        db.session.commit()
        flash(f'Đã cập nhật thông tin công ty {organization.name}.', 'success')
    return redirect(url_for('platform_organizations'))


@app.route('/platform/organizations/<int:organization_id>/toggle', methods=['POST'])
@platform_admin_required
def toggle_organization(organization_id):
    organization = db.session.get(Organization, organization_id)
    if not organization:
        return 'Không tìm thấy công ty.', 404
    if organization.slug == 'default':
        flash('Không thể khóa workspace mặc định.', 'warning')
    else:
        organization.is_active = not organization.is_active
        db.session.commit()
        flash(f"Đã {'mở khóa' if organization.is_active else 'khóa'} công ty {organization.name}.", 'success')
    return redirect(url_for('platform_organizations'))


@app.route('/admin/users/add', methods=['POST'])
@team_manager_required
def add_user():
    username = (request.form.get('username') or '').strip().lower()
    password = request.form.get('password') or ''
    role = (request.form.get('role') or 'employee').strip().lower()
    actor = current_user()
    manager_id = request.form.get('manager_id', type=int)
    organization_id = actor.organization_id
    if actor.role == 'dev':
        organization_id = request.form.get('organization_id', type=int)
        if not organization_id or not db.session.get(Organization, organization_id):
            organization_id = None
    if actor.role == 'manager':
        role = 'sales'
        manager_id = actor.id
    elif role in USER_ROLES and manager_id:
        manager = User.query.filter_by(
            id=manager_id, role='manager', is_active=True, organization_id=organization_id,
        ).first()
        if not manager:
            manager_id = None
    if not username or len(password) < 8:
        flash('Tên đăng nhập và mật khẩu tối thiểu 8 ký tự là bắt buộc.', 'danger')
    elif role not in USER_ROLES or (role == 'dev' and actor.role != 'dev') or not organization_id:
        flash('Vai trò tài khoản không hợp lệ.', 'danger')
    elif role == 'sales' and actor.role != 'dev' and sales_seat_limit(actor) is not None and active_sales_count(organization_id) >= sales_seat_limit(actor):
        flash('Workspace đã đạt giới hạn tài khoản Sales. Hãy mua thêm quyền Sales theo tháng.', 'warning')
    elif User.query.filter_by(username=username).first():
        flash('Tên đăng nhập đã tồn tại.', 'warning')
    else:
        db.session.add(User(
            username=username, password_hash=generate_password_hash(password),
            role=role, manager_id=manager_id, organization_id=organization_id,
            is_platform_admin=(role == 'dev'),
        ))
        db.session.commit()
        flash(f"Đã tạo tài khoản {USER_ROLES[role]}.", 'success')
    return redirect(url_for('users'))


@app.route('/admin/users/<int:user_id>/toggle', methods=['POST'])
@team_manager_required
def toggle_user(user_id):
    user = User.query.get_or_404(user_id)
    actor = current_user()
    if actor.role == 'manager' and user.manager_id != actor.id:
        return 'Bạn không có quyền quản lý tài khoản này.', 403
    if actor.role != 'dev' and (
        user.role in {'admin', 'dev'} or (actor.role == 'manager' and user.role == 'manager')
    ):
        flash('Không thể khóa tài khoản Admin từ màn hình này.', 'warning')
    else:
        user.is_active = not user.is_active
        db.session.commit()
        flash(f"Đã {'mở khóa' if user.is_active else 'khóa'} tài khoản {USER_ROLES.get(user.role, user.role)}.", 'success')
    return redirect(url_for('users'))


@app.route('/admin/users/<int:user_id>/edit', methods=['POST'])
@team_manager_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)
    actor = current_user()
    if actor.role == 'manager' and user.manager_id != actor.id:
        return 'Bạn không có quyền quản lý tài khoản này.', 403
    if actor.role != 'dev' and user.role in {'admin', 'dev'}:
        flash('Không thể sửa tài khoản Admin từ màn hình này.', 'warning')
        return redirect(url_for('users'))
    username = (request.form.get('username') or '').strip().lower()
    role = (request.form.get('role') or user.role).strip().lower()
    password = request.form.get('password') or ''
    organization_id = user.organization_id
    if actor.role == 'dev':
        organization_id = request.form.get('organization_id', type=int)
    duplicate = User.query.filter(User.username == username, User.id != user.id).first()
    allowed_roles = set(USER_ROLES) if actor.role == 'dev' else {'sales', 'employee', 'manager'}
    if (
        not username or duplicate or role not in allowed_roles
        or not organization_id or not db.session.get(Organization, organization_id)
    ):
        flash('Thông tin tài khoản hoặc vai trò không hợp lệ.', 'danger')
    else:
        user.username = username
        user.role = 'sales' if actor.role == 'manager' else role
        user.organization_id = organization_id
        user.is_platform_admin = actor.role == 'dev' and role == 'dev'
        if user.role == 'manager':
            user.manager_id = None
        if actor.role == 'dev':
            manager_id = request.form.get('manager_id', type=int)
            user.manager_id = manager_id if User.query.filter_by(
                id=manager_id, role='manager', is_active=True, organization_id=organization_id,
            ).first() else None
        if password:
            if len(password) < 8:
                flash('Mật khẩu mới tối thiểu 8 ký tự.', 'danger')
                return redirect(url_for('users'))
            user.password_hash = generate_password_hash(password)
        db.session.commit()
        flash('Đã cập nhật tài khoản.', 'success')
    return redirect(url_for('users'))


@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@team_manager_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    actor = current_user()
    if actor.role == 'manager' and user.manager_id != actor.id:
        return 'Bạn không có quyền quản lý tài khoản này.', 403
    if user.id == actor.id:
        flash('Không thể xóa tài khoản Admin hoặc tài khoản đang đăng nhập.', 'warning')
    elif actor.role != 'dev' and user.role in {'admin', 'dev'}:
        flash('Không thể xóa tài khoản Admin từ màn hình này.', 'warning')
    elif (
        Customer.query.filter_by(assigned_user_id=user.id).first()
        or CustomerActivity.query.filter_by(user_id=user.id).first()
        or Reminder.query.filter_by(assigned_user_id=user.id).first()
        or Payment.query.filter_by(user_id=user.id).first()
        or ApiToken.query.filter_by(user_id=user.id).first()
        or DeveloperCommandLog.query.filter_by(user_id=user.id).first()
    ):
        flash('Không thể xóa tài khoản đã có dữ liệu; hãy khóa tài khoản thay thế.', 'warning')
    else:
        User.query.filter_by(manager_id=user.id).update(
            {User.manager_id: None}, synchronize_session=False,
        )
        try:
            db.session.delete(user)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('Không thể xóa tài khoản vì vẫn còn dữ liệu liên kết; hãy khóa tài khoản thay thế.', 'warning')
        else:
            flash('Đã xóa tài khoản.', 'success')
    return redirect(url_for('users'))


@app.route('/customers/<int:c_id>/assign', methods=['POST'])
@customer_assignment_required
def assign_customer(c_id):
    customer = get_visible_customer(c_id)
    user_id = request.form.get('assigned_user_id', type=int)
    user = assignable_sales_user(user_id) if user_id else None
    if user_id and not user:
        flash('Sales được chọn không hợp lệ hoặc đã bị khóa.', 'danger')
    else:
        customer.assigned_user_id = user.id if user else None
        db.session.commit()
        flash('Đã cập nhật Sales phụ trách.', 'success')
    return redirect(url_for('customer_detail', c_id=customer.id))


@app.route('/customers/assign-bulk', methods=['POST'])
@customer_assignment_required
def assign_customers_bulk():
    customer_ids = request.form.getlist('customer_ids', type=int)
    user_id = request.form.get('assigned_user_id', type=int)
    page_name = (request.form.get('page_name') or '').strip()
    user = assignable_sales_user(user_id) if user_id else None
    if not customer_ids and not page_name:
        flash('Hãy chọn ít nhất một khách hàng để chuyển.', 'warning')
        return redirect(url_for('customers'))
    if user_id and not user:
        flash('Sales được chọn không hợp lệ hoặc đã bị khóa.', 'danger')
        return redirect(url_for('customers'))

    customer_query = visible_customer_query()
    if page_name:
        customer_query = customer_query.filter(Customer.page_name == page_name)
    elif customer_ids:
        customer_query = customer_query.filter(Customer.id.in_(customer_ids))
    customers_to_assign = customer_query.all()
    for customer in customers_to_assign:
        customer.assigned_user_id = user.id if user else None
    db.session.commit()
    assignment = user.username if user else 'chưa phân công'
    flash(f'Đã chuyển {len(customers_to_assign)} khách cho {assignment}.', 'success')
    return redirect(url_for('customers'))


def ensure_sales_group_columns():
    columns = {column['name'] for column in inspect(db.engine).get_columns('sales_group')}
    if 'zalo_url' not in columns:
        db.session.execute(text('ALTER TABLE sales_group ADD COLUMN zalo_url TEXT'))
        db.session.commit()


def ensure_user_columns():
    columns = {column['name'] for column in inspect(db.engine).get_columns('user')}
    boolean_default = 'TRUE' if db.engine.dialect.name == 'postgresql' else '1'
    new_columns = {
        'role': "TEXT DEFAULT 'sales'",
        'manager_id': 'INTEGER',
        'organization_id': 'INTEGER',
        'is_platform_admin': f'BOOLEAN DEFAULT {boolean_default}',
        'is_active': f'BOOLEAN DEFAULT {boolean_default}',
        'last_login_at': 'DATETIME',
    }
    for column_name, column_type in new_columns.items():
        if column_name not in columns:
            db.session.execute(text(f'ALTER TABLE "user" ADD COLUMN {column_name} {column_type}'))
    db.session.commit()


def ensure_tenant_columns():
    tables = {
        'customer': 'organization_id',
        'setting': 'organization_id',
        'sync_job': 'organization_id',
        'order': 'organization_id',
        'order_item': 'organization_id',
        'sales_group': 'organization_id',
        'sales_handoff': 'organization_id',
        'message_log': 'organization_id',
        'customer_activity': 'organization_id',
        'reminder': 'organization_id',
        'api_token': 'organization_id',
    }
    for table_name, column_name in tables.items():
        columns = {column['name'] for column in inspect(db.engine).get_columns(table_name)}
        if column_name not in columns:
            quoted_table = f'"{table_name}"' if table_name in {'order', 'user'} else table_name
            db.session.execute(text(f'ALTER TABLE {quoted_table} ADD COLUMN {column_name} INTEGER'))
    db.session.commit()


def ensure_setting_key_is_tenant_scoped():
    if db.engine.dialect.name == 'sqlite':
        indexes = db.session.execute(text('PRAGMA index_list("setting")')).fetchall()
        key_unique_indexes = []
        for index in indexes:
            if not index[2]:
                continue
            columns = db.session.execute(text(f'PRAGMA index_info("{index[1]}")')).fetchall()
            if [column[2] for column in columns] == ['key']:
                key_unique_indexes.append(index[1])
        if key_unique_indexes:
            db.session.execute(text(
                'CREATE TABLE setting_tenant_migration '
                '(id INTEGER PRIMARY KEY, organization_id INTEGER, key VARCHAR(200) NOT NULL, '
                'value TEXT, description VARCHAR(400))',
            ))
            db.session.execute(text(
                'INSERT INTO setting_tenant_migration (id, organization_id, key, value, description) '
                'SELECT id, organization_id, key, value, description FROM setting',
            ))
            db.session.execute(text('DROP TABLE setting'))
            db.session.execute(text('ALTER TABLE setting_tenant_migration RENAME TO setting'))
            db.session.commit()
    elif db.engine.dialect.name == 'postgresql':
        constraints = db.session.execute(text(
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE table_name='setting' AND constraint_type='UNIQUE'",
        )).fetchall()
        for (constraint_name,) in constraints:
            db.session.execute(text(f'ALTER TABLE setting DROP CONSTRAINT "{constraint_name}"'))
        db.session.commit()


def ensure_sales_group_name_is_tenant_scoped():
    if db.engine.dialect.name == 'sqlite':
        indexes = db.session.execute(text('PRAGMA index_list("sales_group")')).fetchall()
        name_unique_indexes = []
        for index in indexes:
            if not index[2]:
                continue
            columns = db.session.execute(text(f'PRAGMA index_info("{index[1]}")')).fetchall()
            if [column[2] for column in columns] == ['name']:
                name_unique_indexes.append(index[1])
        if name_unique_indexes:
            db.session.execute(text(
                'CREATE TABLE sales_group_tenant_migration '
                '(id INTEGER PRIMARY KEY, organization_id INTEGER, name VARCHAR(200) NOT NULL, '
                'description VARCHAR(400), zalo_url VARCHAR(500), created_at DATETIME NOT NULL)',
            ))
            db.session.execute(text(
                'INSERT INTO sales_group_tenant_migration '
                '(id, organization_id, name, description, zalo_url, created_at) '
                'SELECT id, organization_id, name, description, zalo_url, created_at FROM sales_group',
            ))
            db.session.execute(text('DROP TABLE sales_group'))
            db.session.execute(text('ALTER TABLE sales_group_tenant_migration RENAME TO sales_group'))
            db.session.commit()
    elif db.engine.dialect.name == 'postgresql':
        constraints = db.session.execute(text(
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE table_name='sales_group' AND constraint_type='UNIQUE'",
        )).fetchall()
        for (constraint_name,) in constraints:
            db.session.execute(text(f'ALTER TABLE sales_group DROP CONSTRAINT "{constraint_name}"'))
        db.session.commit()


def ensure_default_organization():
    organization = Organization.query.filter_by(slug='default').first()
    if not organization:
        organization = Organization(name='CRM HAY mặc định', slug='default')
        db.session.add(organization)
        db.session.flush()
    tenant_models = (
        User, Customer, Setting, SyncJob, Order, OrderItem, SalesGroup,
        SalesHandoff, MessageLog, CustomerActivity, Reminder, ApiToken,
    )
    for model in tenant_models:
        model.query.filter(model.organization_id.is_(None)).update(
            {'organization_id': organization.id}, synchronize_session=False,
        )
    User.query.filter_by(organization_id=organization.id, role='admin').update(
        {'is_platform_admin': True}, synchronize_session=False,
    )
    db.session.commit()
    return organization


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
    if request.endpoint in {
        'login', 'index', 'static', 'mobile_download', 'privacy_policy',
        'pricing', 'checkout', 'payment_status', 'terms_of_service', 'data_deletion', 'healthz', 'readyz',
        'facebook_webhook', 'zalo_webhook', 'payos_webhook',
    } or (request.endpoint or '').startswith('api_'):
        return None
    user = current_user()
    if not user or not user.is_active:
        session.clear()
        return redirect(url_for('login', next=request.full_path))
    set_tenant_context(None if user.is_platform_admin else user.organization_id)
    if request.endpoint in ADMIN_ENDPOINTS and user.role not in {'admin', 'dev'}:
        return 'Bạn không có quyền thực hiện thao tác này.', 403
    if request.method == 'POST':
        return validate_csrf_token()
    return None


@app.teardown_request
def clear_tenant_context(exception=None):
    set_tenant_context(None)


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
            customer.phone_added_at = None
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
    phone_added_at = payload.get('phone_added_at')
    if isinstance(phone_added_at, str):
        try:
            phone_added_at = datetime.fromisoformat(phone_added_at.replace('Z', '+00:00')).replace(tzinfo=None)
        except ValueError:
            phone_added_at = None

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
        'phone_added_at': phone_added_at,
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
    setting_query = Setting.query.filter_by(key=key)
    tenant_id = current_tenant_id()
    if tenant_id is not None:
        setting_query = setting_query.filter(Setting.organization_id == tenant_id)
    setting = setting_query.first()
    if setting and setting.value:
        return setting.value
    if tenant_id is None:
        value = os.environ.get(key)
        if value:
            return value
    return default


def get_facebook_token():
    for key in (
        'FACEBOOK_SYSTEM_USER_ACCESS_TOKEN',
        'FACEBOOK_PAGE_ACCESS_TOKEN',
        'FACEBOOK_APP_ACCESS_TOKEN',
    ):
        token = get_setting_value(key)
        if token:
            return token.strip()
    return None


def get_facebook_sync_limits(max_pages=None, max_conversations_per_page=None):
    configured_conversation_limit = max_conversations_per_page
    if configured_conversation_limit is None:
        configured_conversation_limit = os.environ.get('FACEBOOK_SYNC_CONVERSATION_LIMIT')

    # Production sync always scans every page returned by the token. Keep the
    # explicit argument for focused tests and controlled one-off imports.
    page_limit = min(MAX_CONVERSATION_PAGES, max(1, int(max_pages))) if max_pages else None
    conversation_limit = None
    if configured_conversation_limit:
        conversation_limit = max(1, int(configured_conversation_limit))
    return page_limit, conversation_limit


def get_facebook_api_call_limit():
    """Return an optional API budget for deployments that need one."""
    configured_limit = os.environ.get('FACEBOOK_SYNC_API_CALL_LIMIT')
    if not configured_limit:
        return MAX_API_CALLS_PER_SYNC
    return min(MAX_API_CALLS_PER_SYNC, max(1, int(configured_limit)))


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
        if customer is None and payload.get('phone'):
            customer = Customer.query.filter(Customer.phone == payload['phone']).first()
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
                phone_added_at=payload['phone_added_at'] or (datetime.utcnow() if payload['phone'] else None),
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
            had_phone = bool((customer.phone or '').strip())
            customer.name = payload['name'] or customer.name
            customer.first_name = payload['first_name'] or customer.first_name
            customer.last_name = payload['last_name'] or customer.last_name
            customer.facebook_id = payload['facebook_id'] or customer.facebook_id
            customer.conversation_id = payload['conversation_id'] or customer.conversation_id
            customer.profile_pic = payload['profile_pic'] or customer.profile_pic
            customer.gender = payload['gender'] or customer.gender
            customer.locale = payload['locale'] or customer.locale
            customer.phone = payload['phone']
            if payload['phone'] and (
                    not had_phone
                    or not customer.phone_added_at
                    or (
                        payload['phone_added_at']
                        and payload['phone_added_at'] < customer.phone_added_at
                    )
            ):
                customer.phone_added_at = payload['phone_added_at'] or datetime.utcnow()
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
        last_error = None
        for attempt in range(3):
            try:
                with urlopen(req, timeout=FACEBOOK_API_TIMEOUT) as response:
                    payload = json.loads(response.read().decode('utf-8'))
                break
            except HTTPError as exc:
                last_error = exc
                if exc.code < 500 and exc.code != 429:
                    raise
            except (URLError, TimeoutError, OSError) as exc:
                last_error = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
        else:
            raise last_error
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
    seen_next_urls = set()
    while payload:
        for page in (payload.get('data') or []):
            all_pages.append(page)
        next_url = (payload.get('paging') or {}).get('next')
        if not next_url:
            break
        if next_url in seen_next_urls:
            logger.warning("Facebook returned a repeated page list URL; stopping page discovery")
            break
        seen_next_urls.add(next_url)
        try:
            payload = fetcher(next_url, token)
        except (HTTPError, URLError, ValueError, KeyError):
            break
    return all_pages


def fetch_managed_facebook_messages(
    max_pages=None,
    max_conversations_per_page=None,
    progress_callback=None,
    incremental=False,
):
    token = get_facebook_token()
    if not token:
        return []

    try:
        if progress_callback:
            progress_callback(0, 0, 'Đang kết nối và xác thực Facebook...')
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
            page_id = os.environ.get('FACEBOOK_PAGE_ID') if current_tenant_id() is None else None
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
        known_conversations = set()
        if incremental:
            known_conversations = {
                conversation_id for (conversation_id,) in Customer.query.with_entities(
                    Customer.conversation_id
                ).filter(
                    Customer.conversation_id.isnot(None),
                    Customer.conversation_id != '',
                ).all()
            }
        api_call_count = 0
        api_call_limit = get_facebook_api_call_limit()
        t_sync_start = time.time()

        pages_to_sync = page_data if page_limit is None else page_data[:page_limit]
        progress_total = len(pages_to_sync) * conversation_limit if conversation_limit else 0
        if progress_callback:
            progress_callback(0, progress_total, f'Đã kết nối Facebook, tìm thấy {len(pages_to_sync)} Page. Bắt đầu quét dữ liệu...')
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

            seen_page_urls = set()
            while True:
                pagination_round += 1
                if pagination_round > MAX_CONVERSATION_PAGES:
                    logger.warning("Hit conversation page limit=%d, stopping page scan", MAX_CONVERSATION_PAGES)
                    break
                if api_call_limit is not None and api_call_count >= api_call_limit:
                    logger.warning("Hit API call limit=%d, stopping sync", api_call_limit)
                    break

                params = {
                    'fields': 'participants{id,name},messages.limit(100){from{id,name},message,created_time}',
                    'limit': str(CONVERSATIONS_PER_REQUEST),
                }
                try:
                    payload = fetch_facebook_json(next_url or endpoint, page_token, params)
                except (HTTPError, URLError, ValueError, KeyError, TimeoutError, OSError) as exc:
                    logger.exception(
                        "Skipping page %d/%d after conversations request failed: %s",
                        page_index + 1, len(pages_to_sync), exc,
                    )
                    break
                api_call_count += 1
                conversations = payload.get('data', [])
                if not conversations:
                    break

                if API_RATE_DELAY:
                    time.sleep(API_RATE_DELAY)

                for conversation in conversations:
                    if conversation_limit and scanned_for_page >= conversation_limit:
                        break
                    scanned_for_page += 1
                    conversation_id = conversation.get('id') or ''
                    if incremental and conversation_id in known_conversations:
                        continue
                    if progress_callback:
                        progress_callback(
                            ((page_index * conversation_limit) + scanned_for_page)
                            if conversation_limit else scanned_for_page,
                            progress_total,
                            f'Đang quét Page {page_index + 1}/{len(pages_to_sync)}: '
                            f'{scanned_for_page}'
                            + (f'/{conversation_limit}' if conversation_limit else ' hội thoại'),
                        )
                    participants = conversation.get('participants', {}).get('data', [])
                    messages_payload = conversation.get('messages', {})
                    messages = list(messages_payload.get('data', []))
                    message_next_url = (messages_payload.get('paging') or {}).get('next')
                    while message_next_url and (
                            api_call_limit is None or api_call_count < api_call_limit):
                        try:
                            message_payload = fetch_facebook_json(message_next_url, page_token)
                        except (HTTPError, URLError, ValueError, KeyError, TimeoutError, OSError) as exc:
                            logger.exception(
                                "Skipping remaining messages in page %s conversation after request failed: %s",
                                page_name, exc,
                            )
                            break
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
                    phone_message_dates = []
                    for msg in customer_messages:
                        msg_text = msg.get('message') or msg.get('story') or ''
                        msg_numbers = extract_phone_numbers(msg_text)
                        if any(number not in configured_hotline_numbers() for number in msg_numbers):
                            created_time = msg.get('created_time')
                            if created_time:
                                try:
                                    phone_message_dates.append(
                                        datetime.fromisoformat(
                                            created_time.replace('Z', '+00:00')
                                        ).replace(tzinfo=None)
                                    )
                                except (TypeError, ValueError):
                                    pass

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
                            and (api_call_limit is None or api_call_count < api_call_limit)):
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
                        'phone_added_at': min(phone_message_dates) if phone_message_dates else None,
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
                if (conversation_limit and scanned_for_page >= conversation_limit) or not next_page:
                    break
                if next_page in seen_page_urls:
                    logger.warning("Facebook returned a repeated conversation page URL; stopping page scan")
                    break
                seen_page_urls.add(next_page)
                next_url = next_page

            logger.info("END sync page %s collected=%d in %.2fs api_calls=%d",
                        page_name, collected_for_page, time.time() - t_page_start, api_call_count)

            if api_call_limit is not None and api_call_count >= api_call_limit:
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
        ensure_tenant_columns()
        ensure_customer_columns()
        ensure_message_log_columns()
        ensure_customer_activity_columns()
        ensure_order_columns()
        ensure_reminder_columns()
        ensure_sales_group_columns()
        ensure_sync_job_columns()
        ensure_subscription_payment_columns()
        ensure_organization_contact_columns()
        ensure_sales_seat_columns()
        ensure_setting_key_is_tenant_scoped()
        ensure_api_token_columns()
        ensure_sales_group_name_is_tenant_scoped()
        default_organization = ensure_default_organization()
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
                organization_id=default_organization.id,
                is_platform_admin=True,
            ))
            db.session.commit()
        if configured_admin and admin_reset_password:
            if len(admin_reset_password) < 8:
                raise RuntimeError('CRM_ADMIN_RESET_PASSWORD must be at least 8 characters.')
            configured_admin.password_hash = generate_password_hash(admin_reset_password)
            db.session.commit()
        dev_username = os.environ.get('CRM_DEV_USERNAME', '').strip().lower()
        dev_password = os.environ.get('CRM_DEV_PASSWORD', '')
        if dev_username and len(dev_password) < 12:
            raise RuntimeError('CRM_DEV_PASSWORD must be at least 12 characters.')
        configured_dev = User.query.filter_by(username=dev_username).first() if dev_username else None
        if dev_username and dev_password and not configured_dev:
            db.session.add(User(
                username=dev_username,
                password_hash=generate_password_hash(dev_password),
                role='dev',
                organization_id=default_organization.id,
                is_platform_admin=True,
            ))
            db.session.commit()
        elif configured_dev:
            changed = False
            if configured_dev.role != 'dev' or not configured_dev.is_platform_admin:
                configured_dev.role = 'dev'
                configured_dev.is_platform_admin = True
                changed = True
            if os.environ.get('CRM_DEV_RESET_PASSWORD'):
                reset_password = os.environ['CRM_DEV_RESET_PASSWORD']
                if len(reset_password) < 12:
                    raise RuntimeError('CRM_DEV_RESET_PASSWORD must be at least 12 characters.')
                configured_dev.password_hash = generate_password_hash(reset_password)
                changed = True
            if changed:
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
            phone_added_at=datetime.utcnow() if phone else None,
            facebook_id=sender_id or None,
            source='zalo',
            page_name=payload.get('page_name') or 'Zalo',
            message_count=1,
        )
        db.session.add(customer)
        db.session.flush()

    customer.name = customer.name or name
    if phone:
        if not customer.phone or not customer.phone_added_at:
            customer.phone_added_at = datetime.utcnow()
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
    actor = current_user() if has_request_context() else User.query.filter_by(role='admin').first()
    if actor:
        db.session.add(CustomerActivity(
            customer_id=customer.id, user_id=actor.id,
            activity_type='message', channel='zalo', note=text,
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


FACEBOOK_MEDIA_TYPES = {
    'image/': 'image',
    'video/': 'video',
    'audio/': 'audio',
}


def facebook_media_type(mimetype):
    for prefix, media_type in FACEBOOK_MEDIA_TYPES.items():
        if (mimetype or '').startswith(prefix):
            return media_type
    return None


def upload_facebook_attachment(page_id, page_token, media_file, media_type):
    boundary = f'----CRMHAY{uuid.uuid4().hex}'
    file_data = media_file.read()
    filename = os.path.basename(media_file.filename or f'upload-{uuid.uuid4().hex}')
    filename = filename.replace('"', '')
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="message"\r\n\r\n'
        f'{{"attachment":{{"type":"{media_type}","payload":{{"is_reusable":false}}}}}}\r\n',
        f'--{boundary}\r\nContent-Disposition: form-data; name="filedata"; '
        f'filename="{filename}"\r\nContent-Type: {media_file.mimetype or "application/octet-stream"}\r\n\r\n',
        file_data,
        f'\r\n--{boundary}--\r\n',
    ]
    body = b''.join(part.encode('utf-8') if isinstance(part, str) else part for part in parts)
    params = urlencode({'access_token': page_token})
    try:
        with urlopen(Request(
            f'https://graph.facebook.com/v19.0/{page_id}/message_attachments?{params}',
            data=body,
            headers={
                'Content-Type': f'multipart/form-data; boundary={boundary}',
                'User-Agent': 'CRM-HAY/1.0',
            },
        ), timeout=FACEBOOK_API_TIMEOUT) as response:
            result = json.loads(response.read().decode('utf-8'))
    except HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        logger.error('Facebook attachment upload FAIL page=%s: %s', page_id, detail)
        raise ValueError('Facebook không nhận được tệp đính kèm.') from exc
    except (URLError, ValueError) as exc:
        raise ValueError('Không thể tải tệp lên Facebook.') from exc
    attachment_id = result.get('attachment_id')
    if not attachment_id:
        raise ValueError('Facebook không trả về mã tệp đính kèm.')
    return attachment_id


def send_facebook_message(customer, text, media_file=None):
    """Send a Messenger reply using the Page token for the customer's Page."""
    if not customer or not customer.facebook_id:
        raise ValueError('Khách hàng chưa có Facebook ID để gửi tin.')
    system_token = get_facebook_token()
    if not system_token:
        raise ValueError('Chưa cấu hình System User Token Facebook.')

    pages = resolve_page_access_tokens(system_token, fetch_all_facebook_pages(system_token))
    page = next(
        (item for item in pages if item.get('name') == customer.page_name),
        None,
    )
    if page is None and len(pages) == 1:
        page = pages[0]
    if not page:
        raise ValueError('Không tìm thấy Page của khách hàng hoặc Page chưa cấp quyền nhắn tin.')

    endpoint = f"https://graph.facebook.com/v19.0/{page['id']}/messages"
    payload = {
        'recipient': {'id': customer.facebook_id},
        'messaging_type': 'RESPONSE',
    }
    media_type = None
    if media_file and media_file.filename:
        media_type = facebook_media_type(media_file.mimetype)
        if not media_type:
            raise ValueError('Chỉ hỗ trợ tệp hình ảnh, video hoặc âm thanh.')
        attachment_id = upload_facebook_attachment(
            page['id'], page['access_token'], media_file, media_type
        )
        if text:
            send_facebook_message(customer, text)
        payload['message'] = {
            'attachment': {'type': media_type, 'payload': {'attachment_id': attachment_id}}
        }
    else:
        payload['message'] = {'text': text}
    params = urlencode({'access_token': page['access_token']})
    request = Request(
        f'{endpoint}?{params}',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'User-Agent': 'CRM-HAY/1.0'},
    )
    try:
        with urlopen(request, timeout=FACEBOOK_API_TIMEOUT) as response:
            result = json.loads(response.read().decode('utf-8'))
    except HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        logger.error('Facebook send FAIL customer=%s: %s', customer.id, detail)
        try:
            error_payload = json.loads(detail).get('error', {})
            error_message = error_payload.get('message') or ''
            error_code = error_payload.get('code')
        except (TypeError, ValueError):
            error_message = ''
            error_code = None
        if error_message:
            suffix = f' (mã {error_code})' if error_code is not None else ''
            raise ValueError(f'Facebook không gửi được tin: {error_message}{suffix}') from exc
        raise ValueError('Facebook không gửi được tin. Kiểm tra token, quyền Page hoặc thời hạn phản hồi.') from exc
    except (URLError, ValueError) as exc:
        logger.error('Facebook send FAIL customer=%s: %s', customer.id, exc)
        raise ValueError('Không thể kết nối Facebook để gửi tin.') from exc
    logger.info('Facebook send OK customer=%s', customer.id)
    return result.get('message_id') or result.get('id') or '', media_type


def parse_facebook_timestamp(value):
    if isinstance(value, (int, float)):
        return datetime.utcfromtimestamp(value / 1000 if value > 10_000_000_000 else value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def facebook_reply_window_open(customer):
    last_customer_message = customer.last_customer_message_at
    return bool(
        last_customer_message
        and (datetime.utcnow() - last_customer_message).total_seconds() < 24 * 60 * 60
    )


def sync_facebook_webhook_message(page_id, messaging):
    sender = messaging.get('sender') or {}
    recipient = messaging.get('recipient') or {}
    message = messaging.get('message') or {}
    sender_id = str(sender.get('id') or '').strip()
    conversation_id = str(
        messaging.get('conversation', {}).get('id')
        or messaging.get('thread', {}).get('id')
        or ''
    ).strip()
    text = (message.get('text') or '').strip()
    attachments = message.get('attachments') or []
    if not sender_id or sender_id == str(page_id) or (not text and not attachments):
        return None
    attachment = attachments[0] if attachments else {}
    attachment_payload = attachment.get('payload') or {}
    media_url = attachment_payload.get('url') or attachment_payload.get('src')
    media_type = attachment.get('type')
    display_text = text or f'[{media_type or "Tệp đính kèm"}]'

    customer = Customer.query.filter(Customer.facebook_id == sender_id).first()
    if customer is None and conversation_id:
        customer = Customer.query.filter(Customer.conversation_id == conversation_id).first()
    if customer is None:
        customer = Customer(
            name='Khách hàng Facebook',
            facebook_id=sender_id,
            conversation_id=conversation_id or None,
            source='facebook',
            page_name=str(page_id),
        )
        db.session.add(customer)
        db.session.flush()
    customer.facebook_id = sender_id
    if conversation_id:
        customer.conversation_id = conversation_id
    customer.source = 'facebook'
    customer.message_count = (customer.message_count or 0) + 1
    customer.message_excerpt = display_text[:500]
    customer.last_customer_message_at = parse_facebook_timestamp(messaging.get('timestamp')) or datetime.utcnow()
    customer.last_message_date = customer.last_customer_message_at
    message_id = str(message.get('mid') or '').strip()
    if message_id and MessageLog.query.filter_by(external_message_id=message_id).first():
        return customer


    db.session.add(MessageLog(
        customer_id=customer.id,
        sender_type='customer',
        channel='facebook',
        message=display_text,
        media_url=media_url,
        media_type=media_type,
        external_message_id=message_id,
        sent_at=datetime.utcnow(),
    ))
    db.session.add(CustomerActivity(
        customer_id=customer.id, user_id=current_user().id,
        activity_type='message', channel='facebook', note=message or f'[{media_type}]',
    ))
    db.session.commit()
    return customer


def verify_facebook_webhook_signature(raw_body):
    app_secret = os.environ.get('FACEBOOK_APP_SECRET', '').strip()
    if not app_secret:
        logger.error('FACEBOOK_APP_SECRET is not configured; rejecting webhook request.')
        return False
    signature = request.headers.get('X-Hub-Signature-256', '')
    if not signature.startswith('sha256='):
        return False
    expected = hmac.new(app_secret.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature[7:], expected)


def sync_facebook_lead(lead_id, page_id):
    token = get_facebook_token()
    if not token:
        raise ValueError('Chưa cấu hình Facebook access token để đọc Lead Ads.')
    lead = fetch_facebook_json(
        str(lead_id),
        token,
        {'fields': 'id,created_time,field_data,ad_id,form_id,campaign_name,ad_name,adset_name'},
    )
    fields = {}
    for field in lead.get('field_data') or []:
        name = str(field.get('name') or '').strip().lower()
        values = field.get('values') or []
        if name and values:
            fields[name] = str(values[0]).strip()

    def field_value(*names):
        for name in names:
            if fields.get(name):
                return fields[name]
        return ''

    phone = sanitize_customer_phone(field_value('phone_number', 'phone', 'số điện thoại', 'mobile_phone'))
    email = field_value('email', 'e-mail')
    name = field_value('full_name', 'name', 'full name', 'ho_ten', 'họ và tên') or f'Facebook Lead {lead_id}'
    customer = Customer.query.filter_by(facebook_lead_id=str(lead_id)).first()
    if customer is None and phone:
        customer = Customer.query.filter(Customer.phone == phone).first()
    lead_date = datetime.utcnow()
    if isinstance(lead.get('created_time'), str):
        try:
            lead_date = datetime.fromisoformat(lead['created_time'].replace('Z', '+00:00')).replace(tzinfo=None)
        except ValueError:
            pass
    details = [
        f"Lead ID: {lead_id}",
        f"Page ID: {page_id}",
        f"Form ID: {lead.get('form_id') or '—'}",
        f"Ad ID: {lead.get('ad_id') or '—'}",
    ]
    if lead.get('campaign_name'):
        details.append(f"Chiến dịch: {lead['campaign_name']}")
    if customer is None:
        customer = Customer(
            name=name, facebook_lead_id=str(lead_id), phone=phone,
            phone_added_at=lead_date if phone else None, email=email,
            notes='\n'.join(details), page_name=str(page_id),
            last_message_date=lead_date, message_excerpt='Lead Ads Facebook',
            source='facebook_lead',
        )
        db.session.add(customer)
    else:
        customer.facebook_lead_id = str(lead_id)
        customer.name = name or customer.name
        customer.phone = phone or customer.phone
        customer.email = email or customer.email
        if f'Lead ID: {lead_id}' not in (customer.notes or ''):
            customer.notes = (customer.notes or '') + '\n' + '\n'.join(details)
        customer.page_name = str(page_id) if page_id else customer.page_name
        customer.source = 'facebook_lead'
        customer.last_message_date = lead_date
    db.session.commit()
    return customer


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
    day_start = datetime(now.year, now.month, now.day)
    tomorrow = day_start + timedelta(days=1)
    week_start = day_start - timedelta(days=day_start.weekday())
    next_month_start = (month_start + timedelta(days=32)).replace(day=1)
    year_start = datetime(now.year, 1, 1)
    customer_query = visible_customer_query()
    customer_with_phone_query = customer_query.filter(
        Customer.phone.isnot(None),
        Customer.phone != '',
    )

    def customer_period_stat(query, start, end, previous_start, previous_end):
        current = query.filter(Customer.last_message_date >= start, Customer.last_message_date < end).count()
        previous = query.filter(Customer.last_message_date >= previous_start, Customer.last_message_date < previous_end).count()
        return {
            'count': current,
            'previous': previous,
            'delta': current - previous,
            'growth': ((current - previous) / previous * 100) if previous else (100 if current else 0),
        }

    customer_period_stats = {
        'day': customer_period_stat(customer_with_phone_query, day_start, tomorrow, day_start - timedelta(days=1), day_start),
        'week': customer_period_stat(customer_with_phone_query, week_start, week_start + timedelta(days=7), week_start - timedelta(days=7), week_start),
        'month': customer_period_stat(customer_with_phone_query, month_start, next_month_start, month_start - timedelta(days=32), month_start),
        'year': customer_period_stat(customer_with_phone_query, year_start, datetime(now.year + 1, 1, 1), datetime(now.year - 1, 1, 1), year_start),
    }
    order_query = Order.query.join(Customer).filter(Customer.id.in_(customer_query.with_entities(Customer.id)))
    customer_count = customer_query.count()
    phone_count = customer_query.filter(Customer.phone.isnot(None), Customer.phone != '').count()
    location_summary = customer_query.filter(
        Customer.location.isnot(None),
        db.func.trim(Customer.location) != '',
    ).with_entities(
        Customer.location,
        func.count(Customer.id).label('customer_count'),
    ).group_by(
        Customer.location,
    ).order_by(
        func.count(Customer.id).desc(),
        Customer.location.asc(),
    ).limit(10).all()
    location_unknown_count = customer_query.filter(
        db.or_(
            Customer.location.is_(None),
            db.func.trim(Customer.location) == '',
        )
    ).count()
    sales_user_query = User.query.filter(User.role.in_(('sales', 'employee')))
    if current_user().role == 'manager':
        sales_user_query = sales_user_query.filter(User.manager_id == current_user().id)
    sales_users = sales_user_query.order_by(User.username.asc()).all()
    holding_users = list(sales_users)
    manager_query = User.query.filter(User.role == 'manager', User.is_active.is_(True))
    if current_user().role == 'manager':
        manager_query = manager_query.filter(User.id == current_user().id)
    holding_users.extend(manager_query.order_by(User.username.asc()).all())
    sales_customer_counts = dict(
        customer_query.filter(Customer.assigned_user_id.isnot(None)).with_entities(
            Customer.assigned_user_id,
            func.count(Customer.id),
        ).group_by(Customer.assigned_user_id).all()
    )
    sales_revenues = dict(
        Order.query.join(Customer, Order.customer_id == Customer.id).filter(
            Customer.id.in_(customer_query.with_entities(Customer.id)),
            Customer.assigned_user_id.isnot(None),
        ).with_entities(
            Customer.assigned_user_id,
            func.coalesce(func.sum(Order.total_amount), 0),
        ).group_by(Customer.assigned_user_id).all()
    )
    sales_customer_stats = sorted(
        [
            {
                'name': user.username,
                'customer_count': sales_customer_counts.get(user.id, 0),
                'revenue': sales_revenues.get(user.id, 0) or 0,
            }
            for user in holding_users
        ],
        key=lambda item: item['customer_count'],
        reverse=True,
    )
    sales_revenue_stats = sorted(
        sales_customer_stats,
        key=lambda item: item['revenue'],
        reverse=True,
    )
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
        customer_period_stats=customer_period_stats,
        location_summary=location_summary,
        location_unknown_count=location_unknown_count,
        sales_customer_stats=sales_customer_stats,
        sales_revenue_stats=sales_revenue_stats,
    )


def customer_sort_order(sort_key):
    sort_key = (sort_key or 'newest').lower()
    if sort_key == 'date':
        return [Customer.last_message_date.desc(), Customer.created_at.desc(), Customer.id.desc()]
    if sort_key == 'page':
        return [Customer.page_name.asc(), Customer.name.asc(), Customer.id.desc()]
    if sort_key == 'location':
        return [
            Customer.location.is_(None).asc(),
            Customer.location.asc(),
            Customer.name.asc(),
            Customer.id.desc(),
        ]
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
@login_required
def customers():
    q = request.args.get('q', '')
    sort = request.args.get('sort', 'newest')
    if sort not in {'date', 'newest', 'page'}:
        sort = 'newest'
    selected_page_name = request.args.get('page_name', '').strip()
    assignment_filter = request.args.get('assignment', 'all').strip().lower()
    if assignment_filter not in {'all', 'assigned', 'unassigned'}:
        assignment_filter = 'all'
    sync_job_id = request.args.get('sync_job', '')
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (TypeError, ValueError):
        page = 1
    per_page = 100
    base_query = visible_customer_query()
    page_names = [
        name for (name,) in base_query.with_entities(Customer.page_name)
        .filter(Customer.page_name.isnot(None), db.func.trim(Customer.page_name) != '')
        .distinct()
        .order_by(Customer.page_name.asc())
        .all()
    ]

    if q:
        base_query = base_query.filter(
            db.or_(
                Customer.name.contains(q),
                Customer.phone.contains(q),
                Customer.facebook_id.contains(q),
                Customer.email.contains(q),
                Customer.tags.contains(q),
            )
        )
    if selected_page_name:
        base_query = base_query.filter(Customer.page_name == selected_page_name)
    if assignment_filter == 'assigned':
        base_query = base_query.filter(Customer.assigned_user_id.isnot(None))
    elif assignment_filter == 'unassigned':
        base_query = base_query.filter(Customer.assigned_user_id.is_(None))
    ordered_query = base_query.order_by(*customer_sort_order(sort))
    total_count = ordered_query.count()
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = min(page, total_pages)
    items = ordered_query.offset((page - 1) * per_page).limit(per_page).all()
    item_ids = [customer.id for customer in items]
    latest_call_at = {}
    contacted_customer_ids = set()
    if item_ids:
        activity_rows = CustomerActivity.query.filter(
            CustomerActivity.customer_id.in_(item_ids),
        ).with_entities(
            CustomerActivity.customer_id, CustomerActivity.activity_type,
            CustomerActivity.created_at,
        ).all()
        for customer_id, activity_type, created_at in activity_rows:
            contacted_customer_ids.add(customer_id)
            if activity_type == 'call' and (
                customer_id not in latest_call_at
                or created_at > latest_call_at[customer_id]
            ):
                latest_call_at[customer_id] = created_at
    for customer in items:
        customer.last_call_at = latest_call_at.get(customer.id)
        customer.has_call = customer.last_call_at is not None
        customer.has_contact = customer.id in contacted_customer_ids
        customer.needs_call = not customer.has_call
        customer.message_needs_attention = bool(
            customer.last_customer_message_at
            and customer.has_call
            and customer.last_customer_message_at > customer.last_call_at
        )

    if request.args.get('format') == 'json':
        return {
            'ok': True,
            'customers': [{
                'id': customer.id,
                'name': customer.name,
                'phone': customer.phone or '',
                'email': customer.email or '',
                'location': customer.location or '',
                'page_name': customer.page_name or '',
                'source': customer.source or '',
                'assigned_user_id': customer.assigned_user_id,
                'last_message_date': customer.last_message_date.isoformat() if customer.last_message_date else None,
            } for customer in items],
        }

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
        total_count=total_count, page=page, total_pages=total_pages,
        q=q, sync_job_id=sync_job_id, sort=sort, selected_page_name=selected_page_name,
        assignment_filter=assignment_filter,
        page_names=page_names,
        sales_groups=SalesGroup.query.order_by(SalesGroup.name).all(),
        sales_users=User.query.filter(
            User.role.in_(('sales', 'employee', 'manager') if current_user().role in {'admin', 'dev'} else ('sales', 'employee')),
            User.is_active.is_(True),
            *([User.manager_id == current_user().id] if current_user().role == 'manager' else []),
        ).order_by(User.username).all(),
        can_export=plan_allows('export'),
        current_plan=organization_plan(),
    )


@app.route('/customers/<int:c_id>/call-start', methods=['POST'])
@login_required
def start_customer_call(c_id):
    csrf_error = validate_csrf_token()
    if csrf_error:
        return csrf_error
    customer = visible_customer_query().filter(Customer.id == c_id).first()
    if not customer:
        return {'error': 'Không tìm thấy khách hàng hoặc bạn không có quyền.'}, 404
    channel = (request.form.get('channel') or 'phone').strip().lower()
    if channel not in {'phone', 'zalo', 'whatsapp'}:
        return {'error': 'Kênh gọi không hợp lệ.'}, 400
    activity = CustomerActivity(
        customer_id=customer.id,
        user_id=current_user().id,
        activity_type='call',
        channel=channel,
        status='initiated',
        started_at=datetime.utcnow(),
    )
    db.session.add(activity)
    db.session.commit()
    return {'activity_id': activity.id, 'started_at': activity.started_at.isoformat()}


@app.route('/customers/<int:c_id>/call-end', methods=['POST'])
@login_required
def end_customer_call(c_id):
    csrf_error = validate_csrf_token()
    if csrf_error:
        return csrf_error
    customer = visible_customer_query().filter(Customer.id == c_id).first()
    activity = CustomerActivity.query.filter_by(
        id=request.form.get('activity_id', type=int),
        customer_id=c_id,
        user_id=current_user().id,
        activity_type='call',
        status='initiated',
    ).first()
    if not customer or not activity:
        return {'error': 'Không tìm thấy cuộc gọi đang thực hiện.'}, 404
    activity.ended_at = datetime.utcnow()
    activity.duration_seconds = max(0, int((activity.ended_at - (activity.started_at or activity.created_at)).total_seconds()))
    activity.status = 'completed'
    db.session.commit()
    return {'duration_seconds': activity.duration_seconds}


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
        c = Customer(name=name, facebook_id=facebook_id, email=email, phone=phone,
                     phone_added_at=datetime.utcnow() if phone else None,
                     notes=notes, location=location, tags=tags,
                     assigned_user_id=current_user().id if current_user().role == 'sales' else None)
        db.session.add(c)
        db.session.commit()
        flash('Đã thêm khách hàng', 'success')
        return redirect(url_for('customers'))
    return render_template('customer_form.html', action='add')


@app.route('/customers/<int:c_id>')
@login_required
def customer_detail(c_id):
    c = get_visible_customer(c_id)
    if request.args.get('format') == 'json':
        return {
            'ok': True,
            'customer': {
                'id': c.id,
                'name': c.name,
                'phone': c.phone or '',
                'email': c.email or '',
                'location': c.location or '',
                'page_name': c.page_name or '',
                'source': c.source or '',
                'notes': c.notes or '',
                'assigned_user_id': c.assigned_user_id,
            },
        }
    groups = SalesGroup.query.order_by(SalesGroup.name).all()
    handoffs = SalesHandoff.query.filter_by(customer_id=c.id).order_by(SalesHandoff.created_at.desc()).limit(5).all()
    activities = CustomerActivity.query.filter_by(customer_id=c.id).order_by(CustomerActivity.created_at.desc()).all()
    sales_user_query = User.query.filter(
        User.role.in_(('sales', 'employee')),
        User.is_active.is_(True),
    )
    if current_user().role == 'manager':
        sales_user_query = sales_user_query.filter(User.manager_id == current_user().id)
    sales_users = sales_user_query.order_by(User.username).all()
    return render_template(
        'customer_detail.html',
        c=c,
        sales_groups=groups,
        handoffs=handoffs,
        activities=activities,
        sales_users=sales_users,
    )


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
        'group_url': group.zalo_url if is_valid_zalo_group_url(group.zalo_url) else None,
        'desktop_app_url': group.zalo_url if is_valid_zalo_group_url(group.zalo_url) else 'zalo://',
    }


def ensure_order_columns():
    columns = {column['name'] for column in inspect(db.engine).get_columns('order')}
    new_columns = {'delivery_address': 'TEXT', 'discount_amount': 'FLOAT DEFAULT 0', 'vat_amount': 'FLOAT DEFAULT 0', 'payment_details': 'TEXT', 'sales_phone': 'VARCHAR(50)', 'sales_bank_account': 'VARCHAR(200)', 'points_awarded': 'INTEGER DEFAULT 0', 'points_redeemed': 'INTEGER DEFAULT 0', 'points_value': 'FLOAT DEFAULT 1000', 'points_discount': 'FLOAT DEFAULT 0', 'production_sent_at': 'TIMESTAMP' if db.engine.dialect.name == 'postgresql' else 'DATETIME'}
    for column_name, column_type in new_columns.items():
        if column_name not in columns:
            db.session.execute(text(f'ALTER TABLE "order" ADD COLUMN {column_name} {column_type}'))
    db.session.commit()


def update_customer_points(customer_id):
    c = db.session.get(Customer, customer_id)
    if not c:
        return
    completed_orders = Order.query.filter_by(customer_id=c.id, status='Hoàn tất').all()
    earned_pts = 0
    redeemed_pts = 0
    for o in completed_orders:
        pts = int((o.total_amount or 0) // 1000000)
        o.points_awarded = pts
        earned_pts += pts
    redeemed_pts = sum((o.points_redeemed or 0) for o in Order.query.filter_by(customer_id=c.id).all())
    c.points = max(earned_pts - redeemed_pts, 0)
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
            points_redeemed = int(request.form.get('points_redeemed') or 0)
            points_value = float(request.form.get('points_value') or 1000)
        except ValueError:
            flash('Chiết khấu, VAT, điểm đổi và giá trị điểm cần là số hợp lệ.', 'danger')
            return redirect(url_for('create_order', customer_id=customer.id))
        points_redeemed = max(points_redeemed, 0)
        if points_value <= 0:
            flash('Giá trị quy đổi 1 điểm phải lớn hơn 0.', 'danger')
            return redirect(url_for('create_order', customer_id=customer.id))
        if points_redeemed > (customer.points or 0):
            flash(f'Khách chỉ còn {customer.points or 0} điểm, không thể đổi {points_redeemed} điểm.', 'danger')
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
        points_discount = points_redeemed * points_value
        total_amount = max(sum(item.quantity * item.unit_price for item in items) - max(discount_amount, 0) - points_discount + max(vat_amount, 0), 0)
        order = Order(
            customer_id=customer.id,
            code=f"DH{datetime.utcnow():%Y%m%d%H%M%S}{customer.id}",
            total_amount=total_amount,
            status=request.form.get('status') or 'Mới',
            note=request.form.get('note', '').strip(),
            delivery_address=request.form.get('delivery_address', '').strip(), payment_details=request.form.get('payment_details', '').strip(),
            sales_phone=request.form.get('sales_phone', '').strip(),
            sales_bank_account=request.form.get('sales_bank_account', '').strip(),
            points_redeemed=points_redeemed,
            points_value=points_value,
            points_discount=points_discount,
            discount_amount=max(discount_amount, 0), vat_amount=max(vat_amount, 0),
        )
        db.session.add(order)
        order.items.extend(items)
        db.session.commit()
        update_customer_points(customer.id)
        flash(f'Đã tạo đơn {order.code} cho {customer.name}.', 'success')
        return redirect(url_for('order_document', order_id=order.id))
    organization = db.session.get(Organization, current_user().organization_id)
    return render_template(
        'order_form.html',
        customer=customer,
        organization=organization,
        now=datetime.utcnow,
    )




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


@app.route('/orders/<int:order_id>/delete', methods=['POST'])
def delete_order(order_id):
    order = Order.query.join(Customer).filter(
        Order.id == order_id,
        Customer.id.in_(visible_customer_query().with_entities(Customer.id)),
    ).first_or_404()
    db.session.delete(order)
    db.session.commit()
    flash(f'Đã xóa đơn {order.code}.', 'success')
    return redirect(url_for('orders'))


@app.route('/orders/<int:order_id>/edit', methods=['GET', 'POST'])
def edit_order(order_id):
    order = Order.query.join(Customer).filter(
        Order.id == order_id,
        Customer.id.in_(visible_customer_query().with_entities(Customer.id)),
    ).first_or_404()
    if request.method == 'POST':
        order.status = (request.form.get('status') or order.status).strip()
        order.note = (request.form.get('note') or '').strip()
        order.delivery_address = (request.form.get('delivery_address') or '').strip()
        order.sales_phone = (request.form.get('sales_phone') or '').strip()
        order.sales_bank_account = (request.form.get('sales_bank_account') or '').strip()
        try:
            order.points_value = float(request.form.get('points_value') or order.points_value or 1000)
            order.points_redeemed = max(int(request.form.get('points_redeemed') or 0), 0)
        except ValueError:
            flash('Giá trị điểm và số điểm đổi phải là số hợp lệ.', 'danger')
            return redirect(url_for('edit_order', order_id=order.id))
        if order.points_value <= 0:
            flash('Giá trị quy đổi 1 điểm phải lớn hơn 0.', 'danger')
            return redirect(url_for('edit_order', order_id=order.id))
        if order.points_redeemed > (order.customer.points or 0) + (order.points_redeemed or 0):
            flash('Số điểm đổi vượt quá số dư của khách hàng.', 'danger')
            return redirect(url_for('edit_order', order_id=order.id))
        order.points_discount = order.points_redeemed * order.points_value
        order.items.clear()
        for code, name, unit, qty, price in zip(
                request.form.getlist('product_code'),
                request.form.getlist('product_name'),
                request.form.getlist('unit'),
                request.form.getlist('quantity'),
                request.form.getlist('unit_price')):
            if name.strip():
                order.items.append(OrderItem(
                    product_code=code.strip(), product_name=name.strip(),
                    unit=unit.strip(), quantity=max(float(qty or 0), 0),
                    unit_price=max(float(price or 0), 0),
                ))
        order.total_amount = max(
            sum(item.quantity * item.unit_price for item in order.items)
            - (order.discount_amount or 0)
            - (order.points_discount or 0)
            + (order.vat_amount or 0),
            0,
        )
        db.session.commit()
        flash(f'Đã cập nhật đơn {order.code}.', 'success')
        return redirect(url_for('order_document', order_id=order.id))
    return render_template('order_edit.html', order=order)


@app.route('/orders/<int:order_id>/production', methods=['POST'])
def send_order_to_production(order_id):
    order = Order.query.join(Customer).filter(
        Order.id == order_id,
        Customer.id.in_(visible_customer_query().with_entities(Customer.id)),
    ).first_or_404()
    order.production_sent_at = datetime.utcnow()
    order.status = 'Đã gửi sản xuất'
    db.session.commit()
    return render_template(
        'production_order.html',
        order=order,
        production_message=production_order_message(order),
    )


@app.route('/orders/<int:order_id>')
def order_document(order_id):
    order = Order.query.join(Customer).filter(
        Order.id == order_id,
        Customer.id.in_(visible_customer_query().with_entities(Customer.id)),
    ).first()
    if not order:
        return 'Không tìm thấy đơn hàng.', 404
    organization = db.session.get(Organization, current_user().organization_id)
    return render_template('order_document.html', order=order, organization=organization)


@app.route('/orders/<int:order_id>/export.pdf')
def export_order_pdf(order_id):
    order = Order.query.join(Customer).filter(
        Order.id == order_id,
        Customer.id.in_(visible_customer_query().with_entities(Customer.id)),
    ).first()
    if not order:
        return 'Không tìm thấy đơn hàng.', 404
    from order_pdf import build_order_pdf
    organization = db.session.get(Organization, current_user().organization_id)
    return send_file(build_order_pdf(order, organization), mimetype='application/pdf', as_attachment=True, download_name=f'don-dat-hang-{order.code}.pdf')


@app.route('/customers/<int:c_id>/edit', methods=['GET', 'POST'])
def edit_customer(c_id):
    c = get_visible_customer(c_id)
    if request.method == 'POST':
        c.name = request.form.get('name')
        c.facebook_id = request.form.get('facebook_id')
        c.email = request.form.get('email')
        previous_phone = c.phone
        c.phone = sanitize_customer_phone(request.form.get('phone'))
        if not previous_phone and c.phone:
            c.phone_added_at = datetime.utcnow()
        elif previous_phone and not c.phone:
            c.phone_added_at = None
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


@app.route('/customers/<int:c_id>/send-facebook', methods=['POST'])
@login_required
def send_customer_facebook(c_id):
    customer = get_visible_customer(c_id)
    message = (request.form.get('facebook_message') or '').strip()
    media_file = request.files.get('facebook_attachment')
    if not message and not (media_file and media_file.filename):
        flash('Hãy nhập nội dung hoặc chọn tệp đính kèm.', 'danger')
        return redirect(url_for('customer_detail', c_id=customer.id))
    try:
        external_message_id, media_type = send_facebook_message(customer, message, media_file)
    except ValueError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('customer_detail', c_id=customer.id))
    db.session.add(MessageLog(
        customer_id=customer.id,
        sender_type='sales',
        channel='facebook',
        message=message or f'[{media_type}]',
        media_type=media_type,
        external_message_id=external_message_id,
        sent_at=datetime.utcnow(),
    ))
    customer.last_message_date = datetime.utcnow()
    customer.message_excerpt = message[:500]
    db.session.commit()
    flash('Đã gửi tin nhắn qua Facebook Page.', 'success')
    return redirect(url_for('customer_detail', c_id=customer.id))


@app.route('/api/zalo/webhook', methods=['GET', 'POST'])
def zalo_webhook():
    if request.method == 'GET':
        return {'ok': True, 'status': 'ready'}
    webhook_secret = os.environ.get('ZALO_WEBHOOK_SECRET', '').strip()
    if not webhook_secret:
        return {'ok': False, 'message': 'Zalo webhook secret is not configured.'}, 503
    signature = request.headers.get('X-Zalo-Signature', '')
    expected = hmac.new(webhook_secret.encode('utf-8'), request.get_data(), hashlib.sha256).hexdigest()
    if signature.startswith('sha256='):
        signature = signature[7:]
    if not signature or not hmac.compare_digest(signature, expected):
        return {'ok': False, 'message': 'Invalid Zalo webhook signature.'}, 403
    payload = request.get_json(silent=True) or {}
    if not payload:
        payload = request.form.to_dict(flat=True)
    customer = sync_zalo_customer_message(payload)
    return {'ok': True, 'customer_id': customer.id if customer else None}


@app.route('/api/facebook/webhook', methods=['GET', 'POST'])
def facebook_webhook():
    if request.method == 'GET':
        verify_token = get_setting_value('FACEBOOK_WEBHOOK_VERIFY_TOKEN')
        if (
            request.args.get('hub.mode') == 'subscribe'
            and verify_token
            and request.args.get('hub.verify_token') == verify_token
        ):
            return request.args.get('hub.challenge', ''), 200
        return 'Invalid verification token', 403

    if not verify_facebook_webhook_signature(request.get_data()):
        return {'ok': False, 'message': 'Invalid Facebook webhook signature.'}, 403
    payload = request.get_json(silent=True) or {}
    if payload.get('object') != 'page':
        return {'ok': False, 'message': 'Unsupported webhook object.'}, 400
    processed = 0
    for entry in payload.get('entry') or []:
        page_id = str(entry.get('id') or '').strip()
        for change in entry.get('changes') or []:
            if change.get('field') != 'leadgen':
                continue
            value = change.get('value') or {}
            lead_id = value.get('leadgen_id') or value.get('lead_id')
            if lead_id:
                try:
                    sync_facebook_lead(lead_id, page_id)
                    processed += 1
                except (HTTPError, URLError, ValueError, KeyError) as exc:
                    logger.error('Facebook Lead Ads sync failed for %s: %s', lead_id, exc)
                    return {'ok': False, 'message': 'Không thể lấy dữ liệu Lead Ads từ Facebook.'}, 502
        for messaging in entry.get('messaging') or []:
            if sync_facebook_webhook_message(page_id, messaging):
                processed += 1
    return {'ok': True, 'processed': processed}


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
    facebook_token = get_setting_value('FACEBOOK_SYSTEM_USER_ACCESS_TOKEN')
    webhook_verify_token = get_setting_value('FACEBOOK_WEBHOOK_VERIFY_TOKEN')
    organization = db.session.get(Organization, current_user().organization_id)
    return render_template(
        'settings.html',
        items=items,
        facebook_token_configured=bool(facebook_token),
        webhook_verify_token_configured=bool(webhook_verify_token),
        organization=organization,
    )


@app.route('/settings/company-profile', methods=['POST'])
@admin_required
def save_company_profile():
    organization = db.session.get(Organization, current_user().organization_id)
    if not organization:
        return 'Không tìm thấy thông tin công ty.', 404
    organization.company_address = (request.form.get('company_address') or '').strip() or None
    organization.company_phone = (request.form.get('company_phone') or '').strip() or None
    organization.company_email = (request.form.get('company_email') or '').strip() or None
    db.session.commit()
    flash('Đã cập nhật thông tin công ty trên mẫu đơn hàng.', 'success')
    return redirect(url_for('settings'))


@app.route('/settings/facebook-token', methods=['POST'])
def save_facebook_token():
    token = (request.form.get('facebook_system_user_token') or '').strip()
    verify_token = (request.form.get('facebook_webhook_verify_token') or '').strip()
    if not token and not verify_token:
        flash('Hãy nhập System User Token hoặc mã xác thực Webhook.', 'danger')
        return redirect(url_for('settings'))

    if token:
        setting = Setting.query.filter_by(key='FACEBOOK_SYSTEM_USER_ACCESS_TOKEN').first()
        if setting:
            setting.value = token
            setting.description = 'Facebook System User access token dùng để đồng bộ inbox.'
        else:
            db.session.add(Setting(
                key='FACEBOOK_SYSTEM_USER_ACCESS_TOKEN',
                value=token,
                description='Facebook System User access token dùng để đồng bộ inbox.',
            ))
    if verify_token:
        setting = Setting.query.filter_by(key='FACEBOOK_WEBHOOK_VERIFY_TOKEN').first()
        if setting:
            setting.value = verify_token
            setting.description = 'Mã xác thực Facebook Webhook.'
        else:
            db.session.add(Setting(
                key='FACEBOOK_WEBHOOK_VERIFY_TOKEN',
                value=verify_token,
                description='Mã xác thực Facebook Webhook.',
            ))
    db.session.commit()
    flash('Đã lưu cấu hình Facebook. Khi khách nhắn lại Page, CRM sẽ mở lại quyền trả lời trong 24 giờ.', 'success')
    return redirect(url_for('settings'))


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
                set_tenant_context(job.organization_id)
            if job:
                job.status = 'running'
                job.started_at = datetime.utcnow()
                job.message = 'Đang đồng bộ Facebook...'
                job.progress = 0
                job.processed = 0
                job.total = 0
                job.incremental = bool(job.incremental)
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

            if job:
                job.last_activity_at = datetime.utcnow()
                job.message = 'Đang chuẩn bị kết nối Facebook...'
                db.session.commit()

            messages = fetch_managed_facebook_messages(
                *get_facebook_sync_limits(),
                progress_callback=update_progress,
                incremental=bool(job and job.incremental),
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
                    job.total = max(job.total or 0, job.processed or 0)
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
                job.total = max(job.total or 0, job.processed or 0)
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


def _scheduled_business_sync_loop():
    while True:
        try:
            with app.app_context():
                sync_organizations = {
                    subscription.organization_id
                    for subscription in Subscription.query.filter_by(
                        plan='business', status='active'
                    ).all()
                }
                default_admin = User.query.filter_by(
                    username=os.environ.get('CRM_ADMIN_USERNAME', '').strip().lower(),
                    role='admin', is_active=True,
                ).join(Organization).filter(Organization.slug == 'default').first()
                if default_admin and plan_allows('hourly_sync', default_admin):
                    sync_organizations.add(default_admin.organization_id)
                logger.info(
                    'Hourly Facebook sync scheduler: eligible organizations=%s',
                    sorted(sync_organizations),
                )
                for organization_id in sync_organizations:
                    set_tenant_context(organization_id)
                    try:
                        active_job = SyncJob.query.filter(
                            SyncJob.organization_id == organization_id,
                            SyncJob.status.in_(('queued', 'running')),
                        ).first()
                        if active_job:
                            last_activity = (
                                active_job.last_activity_at
                                or active_job.started_at
                                or active_job.created_at
                            )
                            if (
                                last_activity
                                and (datetime.utcnow() - last_activity).total_seconds() > 120
                            ):
                                active_job.status = 'error'
                                active_job.message = (
                                    'Tác vụ đồng bộ trước đã bị treo và được đóng tự động '
                                    'để bắt đầu lượt quét mới.'
                                )
                                active_job.finished_at = datetime.utcnow()
                                active_job.last_activity_at = datetime.utcnow()
                                db.session.commit()
                                logger.warning(
                                    'Closed stale Facebook sync job %s for organization %s',
                                    active_job.id,
                                    organization_id,
                                )
                            else:
                                continue
                        job = SyncJob(
                            id=str(uuid.uuid4()),
                            organization_id=organization_id,
                            status='queued',
                            message='Đồng bộ tự động theo giờ đang chờ xử lý...',
                            incremental=True,
                        )
                        db.session.add(job)
                        db.session.commit()
                        if celery:
                            facebook_sync_task.delay(job.id)
                        else:
                            threading.Thread(
                                target=_run_facebook_sync,
                                args=(job.id,),
                                daemon=True,
                            ).start()
                    finally:
                        set_tenant_context(None)
        except Exception:
            logger.exception('Hourly Business sync scheduler failed')
        time.sleep(3 * 60 * 60)


if celery:
    @celery.task(name='crmhay.facebook_sync', bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
    def facebook_sync_task(self, job_id):
        _run_facebook_sync(job_id)


@app.route('/customers/sync-facebook', methods=['POST'])
def sync_facebook_customers():
    if not plan_allows('manual_sync'):
        flash('Gói hiện tại không có quyền đồng bộ Facebook.', 'danger')
        return redirect(url_for('customers'))
    configured = bool(get_facebook_token())

    if not configured:
        flash('Chưa cấu hình token Facebook hợp lệ. Thêm FACEBOOK_PAGE_ACCESS_TOKEN hoặc FACEBOOK_SYSTEM_USER_ACCESS_TOKEN.', 'danger')
        return redirect(url_for('customers'))
    if is_production and REQUIRE_DURABLE_JOBS and not celery:
        flash('Production cần Redis/Celery để đồng bộ Facebook an toàn. Hãy cấu hình REDIS_URL và CRM_USE_CELERY=true.', 'danger')
        return redirect(url_for('customers'))

    active_job = SyncJob.query.filter(SyncJob.status.in_(('queued', 'running'))).first()
    if active_job:
        last_activity = active_job.last_activity_at or active_job.started_at or active_job.created_at
        if last_activity and (datetime.utcnow() - last_activity).total_seconds() > 120:
            active_job.status = 'error'
            active_job.message = 'Tác vụ đồng bộ trước đã bị treo và được đóng tự động.'
            active_job.finished_at = datetime.utcnow()
            db.session.commit()
        else:
            flash('Đang có một tác vụ đồng bộ Facebook. Vui lòng chờ hoàn tất.', 'info')
            return redirect(url_for('customers', sync_job=active_job.id))

    job = SyncJob(
        id=str(uuid.uuid4()),
        status='queued',
        message='Đang xếp hàng đồng bộ Facebook...',
        progress=0,
        processed=0,
        total=0,
        incremental=Customer.query.filter(
            Customer.conversation_id.isnot(None),
            Customer.conversation_id != '',
        ).first() is not None,
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
    last_activity = job.last_activity_at or job.started_at or job.created_at
    stale = (
        job.status in ('queued', 'running')
        and last_activity is not None
        and (datetime.utcnow() - last_activity).total_seconds() > 120
    )
    message = job.message or ''
    if stale:
        if job.status == 'queued':
            message = 'Tác vụ đang chờ worker; CRM chưa bắt đầu quét Facebook. Kiểm tra tiến trình CRM.'
        else:
            message = 'Tác vụ đồng bộ đã mất kết nối hoặc bị treo; hãy chạy lại đồng bộ Facebook.'
    return {'running': job.status in ('queued', 'running') and not stale, 'result': job.status,
            'message': message, 'imported': job.imported, 'updated': job.updated,
            'progress': job.progress or 0, 'processed': job.processed or 0, 'total': job.total or 0,
            'last_activity_at': job.last_activity_at.isoformat() if job.last_activity_at else None,
            'stale': stale,
            'started_at': job.started_at.isoformat() if job.started_at else None,
            'finished_at': job.finished_at.isoformat() if job.finished_at else None}


@app.route('/facebook/import', methods=['GET', 'POST'])
def facebook_import_legacy():
    flash('Chức năng đồng bộ Facebook đã được chuyển sang tab Khách hàng.', 'info')
    return redirect(url_for('customers'))


CUSTOMER_EXPORT_HEADERS = [
        'STT', 'Mã khách hàng', 'Họ và tên', 'Tên', 'Họ', 'Số điện thoại',
        'Email', 'Địa chỉ', 'Facebook ID', 'Conversation ID', 'Page',
        'Sales phụ trách', 'Vai trò phụ trách', 'Nguồn', 'Trạng thái chăm sóc',
        'Số tin nhắn', 'Tin nhắn cuối', 'Thời gian tin nhắn cuối', 'Ngày tạo',
        'Nhãn', 'Giới tính', 'Ngôn ngữ', 'Facebook Lead ID', 'Link ảnh đại diện',
        'Ghi chú',
]

def customer_export_rows():
    customers_list = visible_customer_query().order_by(
        Customer.page_name.asc(),
        Customer.assigned_user_id.is_(None).asc(),
        Customer.assigned_user_id.asc(),
        Customer.name.asc(),
        Customer.id.asc(),
    ).all()
    assigned_user_ids = {
        customer.assigned_user_id for customer in customers_list
        if customer.assigned_user_id is not None
    }
    assigned_users = {
        user.id: user for user in User.query.filter(User.id.in_(assigned_user_ids)).all()
    } if assigned_user_ids else {}
    contacted_customer_ids = {
        customer_id for (customer_id,) in CustomerActivity.query.filter(
            CustomerActivity.customer_id.in_([customer.id for customer in customers_list])
        ).with_entities(CustomerActivity.customer_id).distinct().all()
    } if customers_list else set()

    rows = []
    for index, customer in enumerate(customers_list, start=1):
        assigned_user = assigned_users.get(customer.assigned_user_id)
        rows.append([
            index,
            customer.id,
            customer.name or '',
            customer.first_name or '',
            customer.last_name or '',
            customer.phone or '',
            customer.email or '',
            customer.location or '',
            customer.facebook_id or '',
            customer.conversation_id or '',
            customer.page_name or '',
            assigned_user.username if assigned_user else '',
            assigned_user.role if assigned_user else '',
            customer.source or '',
            'Đã liên hệ' if customer.id in contacted_customer_ids else 'Chưa liên hệ',
            customer.message_count or 0,
            customer.message_excerpt or '',
            customer.last_message_date.strftime('%Y-%m-%d %H:%M:%S') if customer.last_message_date else '',
            customer.created_at.strftime('%Y-%m-%d %H:%M:%S') if customer.created_at else '',
            customer.tags or '',
            customer.gender or '',
            customer.locale or '',
            customer.facebook_lead_id or '',
            customer.profile_pic or '',
            customer.notes or '',
        ])
    return rows


@app.route('/facebook/export')
def facebook_export():
    if not plan_allows('export'):
        return 'Tính năng tải danh sách khách hàng cần nâng cấp lên gói Growth hoặc Business.', 403
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\r\n')
    writer.writerow(CUSTOMER_EXPORT_HEADERS)
    for row in customer_export_rows():
        writer.writerow([
            str(value).replace('\r\n', ' | ').replace('\n', ' | ').replace('\r', ' | ')
            if isinstance(value, str) else value
            for value in row
        ])

    csv_data = '\ufeff' + output.getvalue()
    return Response(
        csv_data,
        mimetype='text/csv; charset=utf-8',
        headers={
            'Content-Disposition': 'attachment; filename=facebook_customers.csv',
            'X-Content-Type-Options': 'nosniff',
        },
    )


@app.route('/facebook/export.xlsx')
def facebook_export_xlsx():
    if not plan_allows('export'):
        return 'Tính năng tải danh sách khách hàng cần nâng cấp lên gói Growth hoặc Business.', 403
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Khách hàng'
    sheet.append(CUSTOMER_EXPORT_HEADERS)
    for row in customer_export_rows():
        sheet.append(row)

    header_fill = PatternFill('solid', fgColor='1F4E78')
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = Font(color='FFFFFF', bold=True)
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    sheet.row_dimensions[1].height = 32
    widths = [7, 14, 24, 16, 16, 16, 28, 28, 22, 28, 28, 20, 18, 14, 20, 12, 42, 21, 21, 24, 14, 14, 24, 42, 42]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical='top', wrap_text=True)
    for row in range(2, sheet.max_row + 1):
        sheet.cell(row, 1).alignment = Alignment(horizontal='center', vertical='top')
        sheet.row_dimensions[row].height = 36

    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name='khach_hang_crmhay.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


# ---------------------------------------------------------------------------
# Mobile REST API — token-based auth, no CSRF
# ---------------------------------------------------------------------------

class ApiToken(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    token_hash = db.Column(db.String(64), unique=True, nullable=True, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    user = db.relationship('User', backref=db.backref('api_tokens', lazy=True))


_tenant_context = ContextVar('crm_tenant_context', default=None)
TENANT_SCOPED_MODELS = (
    User, Customer, Setting, SyncJob, Order, OrderItem, SalesGroup,
    SalesHandoff, MessageLog, CustomerActivity, Reminder, ApiToken,
)


def set_tenant_context(organization_id):
    _tenant_context.set(organization_id)


def current_tenant_id():
    return _tenant_context.get()


@event.listens_for(Session, 'do_orm_execute')
def apply_tenant_scope(execute_state):
    tenant_id = current_tenant_id()
    if tenant_id is None or not execute_state.is_select:
        return
    for model in TENANT_SCOPED_MODELS:
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(
                model,
                lambda cls: cls.organization_id == tenant_id,
                include_aliases=True,
            ),
        )


@event.listens_for(Session, 'before_flush')
def assign_new_rows_to_tenant(session, flush_context, instances):
    tenant_id = current_tenant_id()
    if tenant_id is None:
        return
    for obj in session.new:
        if isinstance(obj, TENANT_SCOPED_MODELS) and getattr(obj, 'organization_id', None) is None:
            obj.organization_id = tenant_id


def ensure_api_token_columns():
    columns = {column['name'] for column in inspect(db.engine).get_columns('api_token')}
    if 'token_hash' not in columns:
        db.session.execute(text('ALTER TABLE api_token ADD COLUMN token_hash VARCHAR(64)'))
        db.session.commit()


def hash_api_token(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def api_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return {'error': 'Token bắt buộc.'}, 401
        token_str = auth[7:]
        token_obj = ApiToken.query.filter_by(token_hash=hash_api_token(token_str)).first()
        if not token_obj:
            token_obj = ApiToken.query.filter_by(token=token_str).first()
        if not token_obj or not token_obj.user.is_active:
            return {'error': 'Token không hợp lệ hoặc tài khoản bị khóa.'}, 401
        request._api_user = token_obj.user
        set_tenant_context(None if token_obj.user.is_platform_admin else token_obj.user.organization_id)
        return view(*args, **kwargs)
    return wrapped


@app.route('/api/admin/sync-facebook', methods=['POST'])
@api_login_required
def api_sync_facebook_customers():
    if api_current_user().role != 'admin':
        return {'error': 'Chỉ Admin mới có quyền đồng bộ Facebook.'}, 403
    if not plan_allows('manual_sync', api_current_user()):
        return {'error': 'Gói hiện tại không có quyền đồng bộ Facebook.'}, 403
    if not get_facebook_token():
        return {'error': 'Chưa cấu hình token Facebook hợp lệ.'}, 400
    if is_production and REQUIRE_DURABLE_JOBS and not celery:
        return {'error': 'Production cần Redis/Celery để đồng bộ Facebook an toàn.'}, 503
    active_job = SyncJob.query.filter(SyncJob.status.in_(('queued', 'running'))).first()
    if active_job:
        return {'job_id': active_job.id, 'message': 'Đang có tác vụ đồng bộ Facebook.'}
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
        threading.Thread(target=_run_facebook_sync, args=(job.id,), daemon=True).start()
    return {'job_id': job.id, 'message': 'Đã bắt đầu đồng bộ Facebook.'}, 202


def api_current_user():
    return getattr(request, '_api_user', None)


def api_visible_customer_query():
    user = api_current_user()
    query = Customer.query
    if user.role == 'manager':
        managed_ids = User.query.filter(User.manager_id == user.id).with_entities(User.id)
        query = query.filter(
            db.or_(
                Customer.assigned_user_id == user.id,
                Customer.assigned_user_id.in_(managed_ids),
            )
        )
    elif user.role != 'admin':
        query = query.filter(Customer.assigned_user_id == user.id)
    return query


def serialize_customer(c):
    return {
        'id': c.id, 'name': c.name, 'first_name': c.first_name,
        'last_name': c.last_name, 'facebook_id': c.facebook_id,
        'facebook_lead_id': c.facebook_lead_id,
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
        'assigned_user_name': c.assigned_user.username if c.assigned_user else None,
    }


def serialize_order(o):
    return {
        'id': o.id, 'code': o.code, 'total_amount': o.total_amount,
        'status': o.status, 'note': o.note,
        'delivery_address': o.delivery_address,
        'sales_phone': o.sales_phone,
        'sales_bank_account': o.sales_bank_account,
        'discount_amount': o.discount_amount, 'vat_amount': o.vat_amount,
        'payment_details': o.payment_details,
        'points_awarded': o.points_awarded or 0,
        'points_redeemed': o.points_redeemed or 0,
        'points_value': o.points_value or 1000,
        'points_discount': o.points_discount or 0,
        'production_sent_at': o.production_sent_at.isoformat() if o.production_sent_at else None,
        'customer_name': o.customer.name if o.customer else None,
        'customer_id': o.customer_id,
        'created_at': o.created_at.isoformat() if o.created_at else None,
        'items': [{
            'id': item.id, 'product_code': item.product_code,
            'product_name': item.product_name, 'unit': item.unit,
            'quantity': item.quantity, 'unit_price': item.unit_price,
        } for item in o.items],
    }


def serialize_activity(activity):
    return {
        'id': activity.id,
        'type': activity.activity_type,
        'channel': activity.channel,
        'note': activity.note or '',
        'status': activity.status,
        'started_at': activity.started_at.isoformat() if activity.started_at else None,
        'ended_at': activity.ended_at.isoformat() if activity.ended_at else None,
        'duration_seconds': activity.duration_seconds,
        'user_id': activity.user_id,
        'username': activity.user.username if activity.user else '',
        'created_at': activity.created_at.isoformat() if activity.created_at else None,
    }


def serialize_message(message):
    return {
        'id': message.id,
        'sender_type': message.sender_type,
        'channel': message.channel,
        'message': message.message,
        'media_url': message.media_url,
        'media_type': message.media_type,
        'sent_at': message.sent_at.isoformat() if message.sent_at else None,
    }


@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json(silent=True) or {}
    rate_key = f'api:{request.remote_addr or "unknown"}'
    if auth_rate_limited(rate_key):
        return {'error': 'Quá nhiều lần đăng nhập không thành công. Vui lòng thử lại sau ít phút.'}, 429
    username = (data.get('username') or '').strip().lower()
    password = data.get('password') or ''
    user = User.query.filter_by(username=username, is_active=True).first()
    if not user or not check_password_hash(user.password_hash, password):
        return {'error': 'Tên đăng nhập hoặc mật khẩu không đúng.'}, 401
    clear_auth_attempts(rate_key)
    token_str = secrets.token_urlsafe(48)
    token_hash = hash_api_token(token_str)
    db.session.add(ApiToken(token=token_hash, token_hash=token_hash, user_id=user.id))
    user.last_login_at = datetime.utcnow()
    db.session.commit()
    return {'token': token_str, 'user': {
        'id': user.id, 'username': user.username, 'role': user.role,
        'organization_id': user.organization_id,
        'organization_name': user.organization.name if user.organization else None,
    }}


@app.route('/api/logout', methods=['POST'])
@api_login_required
def api_logout():
    auth = request.headers.get('Authorization', '')[7:]
    ApiToken.query.filter(
        db.or_(ApiToken.token_hash == hash_api_token(auth), ApiToken.token == auth),
    ).delete(synchronize_session=False)
    db.session.commit()
    return {'ok': True}


@app.route('/api/profile')
@api_login_required
def api_profile():
    user = api_current_user()
    return {'user': {
        'id': user.id, 'username': user.username, 'role': user.role,
        'organization_id': user.organization_id,
        'organization_name': user.organization.name if user.organization else None,
    }}


@app.route('/api/dashboard')
@api_login_required
def api_dashboard():
    user = api_current_user()
    now = datetime.utcnow()
    month_start = datetime(now.year, now.month, 1)
    cq = api_visible_customer_query()
    oq = Order.query.join(Customer).filter(Customer.id.in_(cq.with_entities(Customer.id)))
    result = {
        'customer_count': cq.count(),
        'phone_count': cq.filter(Customer.phone.isnot(None), Customer.phone != '').count(),
        'order_count': oq.count(),
        'revenue': float(oq.with_entities(func.coalesce(func.sum(Order.total_amount), 0)).scalar() or 0),
        'month_revenue': float(oq.with_entities(func.coalesce(func.sum(Order.total_amount), 0)).filter(
            Order.created_at >= month_start).scalar() or 0),
        'status_summary': [{'status': s, 'count': c} for s, c in oq.with_entities(
            Order.status, func.count(Order.id)).group_by(Order.status).all()],
    }
    if user.role in {'admin', 'manager'}:
        day_start = datetime(now.year, now.month, now.day)
        tomorrow = day_start + timedelta(days=1)
        week_start = day_start - timedelta(days=day_start.weekday())
        next_month_start = (month_start + timedelta(days=32)).replace(day=1)
        year_start = datetime(now.year, 1, 1)

        def period_stat(start, end, previous_start, previous_end):
            current = cq.filter(
                Customer.phone.isnot(None), Customer.phone != '',
                Customer.last_message_date >= start, Customer.last_message_date < end,
            ).count()
            previous = cq.filter(
                Customer.phone.isnot(None), Customer.phone != '',
                Customer.last_message_date >= previous_start, Customer.last_message_date < previous_end,
            ).count()
            return {
                'count': current,
                'previous': previous,
                'delta': current - previous,
                'growth': ((current - previous) / previous * 100) if previous else (100 if current else 0),
            }

        location_rows = cq.filter(
            Customer.location.isnot(None), db.func.trim(Customer.location) != '',
        ).with_entities(
            Customer.location, func.count(Customer.id),
        ).group_by(Customer.location).order_by(func.count(Customer.id).desc(), Customer.location.asc()).limit(10).all()
        location_unknown = cq.filter(db.or_(
            Customer.location.is_(None), db.func.trim(Customer.location) == '',
        )).count()
        sales_users_query = User.query.filter(User.role.in_(('sales', 'employee')))
        if user.role == 'manager':
            sales_users_query = sales_users_query.filter(User.manager_id == user.id)
        sales_users = sales_users_query.order_by(User.username.asc()).all()
        sales_customer_counts = dict(cq.filter(
            Customer.assigned_user_id.isnot(None),
        ).with_entities(Customer.assigned_user_id, func.count(Customer.id)).group_by(Customer.assigned_user_id).all())
        sales_revenues = dict(Order.query.join(
            Customer, Order.customer_id == Customer.id,
        ).filter(
            Customer.id.in_(cq.with_entities(Customer.id)),
            Customer.assigned_user_id.isnot(None),
        ).with_entities(
            Customer.assigned_user_id, func.coalesce(func.sum(Order.total_amount), 0),
        ).group_by(Customer.assigned_user_id).all())
        sales_stats = [{
            'id': sales_user.id,
            'name': sales_user.username,
            'customer_count': sales_customer_counts.get(sales_user.id, 0),
            'revenue': float(sales_revenues.get(sales_user.id, 0) or 0),
        } for sales_user in sales_users]
        result.update({
            'customer_period_stats': {
                'day': period_stat(day_start, tomorrow, day_start - timedelta(days=1), day_start),
                'week': period_stat(week_start, week_start + timedelta(days=7), week_start - timedelta(days=7), week_start),
                'month': period_stat(month_start, next_month_start, month_start - timedelta(days=32), month_start),
                'year': period_stat(year_start, datetime(now.year + 1, 1, 1), datetime(now.year - 1, 1, 1), year_start),
            },
            'location_summary': [{'location': location, 'count': count} for location, count in location_rows],
            'location_unknown_count': location_unknown,
            'sales_customer_stats': sorted(sales_stats, key=lambda item: item['customer_count'], reverse=True),
            'sales_revenue_stats': sorted(sales_stats, key=lambda item: item['revenue'], reverse=True),
        })
    return result


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
    activities = CustomerActivity.query.filter_by(customer_id=c.id).order_by(CustomerActivity.created_at.desc()).all()
    messages = MessageLog.query.filter_by(customer_id=c.id).order_by(MessageLog.sent_at.desc()).limit(100).all()
    return {
        'customer': serialize_customer(c),
        'orders': [serialize_order(o) for o in orders_list],
        'activities': [serialize_activity(activity) for activity in activities],
        'messages': [serialize_message(message) for message in messages],
    }


@app.route('/api/customers/<int:c_id>/activities', methods=['POST'])
@api_login_required
def api_create_customer_activity(c_id):
    customer = api_visible_customer_query().filter(Customer.id == c_id).first()
    if not customer:
        return {'error': 'Không tìm thấy khách hàng hoặc bạn không được phân công khách này.'}, 404
    data = request.get_json(silent=True) or {}
    activity_type = (data.get('type') or '').strip().lower()
    channel = (data.get('channel') or 'crm').strip().lower()
    if activity_type not in {'call', 'message'}:
        return {'error': 'Loại hoạt động phải là call hoặc message.'}, 400
    if channel not in {'phone', 'facebook', 'zalo', 'sms', 'crm'}:
        return {'error': 'Kênh hoạt động không hợp lệ.'}, 400
    status = (data.get('status') or 'completed').strip().lower()
    if status not in {'initiated', 'completed', 'missed', 'rejected', 'failed'}:
        return {'error': 'Trạng thái hoạt động không hợp lệ.'}, 400
    def parse_activity_time(value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace('Z', '+00:00')).replace(tzinfo=None)
        except ValueError:
            return None
    try:
        duration_seconds = max(0, int(data['duration_seconds'])) if data.get('duration_seconds') is not None else None
    except (TypeError, ValueError):
        return {'error': 'Thời lượng cuộc gọi không hợp lệ.'}, 400
    activity = CustomerActivity(
        customer_id=customer.id,
        user_id=api_current_user().id,
        activity_type=activity_type,
        channel=channel,
        note=(data.get('note') or '').strip()[:2000],
        status=status,
        started_at=parse_activity_time(data.get('started_at')),
        ended_at=parse_activity_time(data.get('ended_at')),
        duration_seconds=duration_seconds,
    )
    db.session.add(activity)
    db.session.commit()
    return {'activity': serialize_activity(activity)}, 201


@app.route('/api/customers/<int:c_id>/activities/<int:activity_id>', methods=['PATCH'])
@api_login_required
def api_update_customer_activity(c_id, activity_id):
    customer = api_visible_customer_query().filter(Customer.id == c_id).first()
    activity = CustomerActivity.query.filter_by(id=activity_id, customer_id=c_id).first()
    if not customer or not activity:
        return {'error': 'Không tìm thấy hoạt động hoặc bạn không có quyền.'}, 404
    data = request.get_json(silent=True) or {}
    status = (data.get('status') or activity.status).strip().lower()
    if status not in {'initiated', 'completed', 'missed', 'rejected', 'failed'}:
        return {'error': 'Trạng thái hoạt động không hợp lệ.'}, 400
    activity.status = status
    if data.get('ended_at'):
        try:
            activity.ended_at = datetime.fromisoformat(str(data['ended_at']).replace('Z', '+00:00')).replace(tzinfo=None)
        except ValueError:
            return {'error': 'Thời gian kết thúc không hợp lệ.'}, 400
    if data.get('started_at'):
        try:
            activity.started_at = datetime.fromisoformat(str(data['started_at']).replace('Z', '+00:00')).replace(tzinfo=None)
        except ValueError:
            return {'error': 'Thời gian bắt đầu không hợp lệ.'}, 400
    if data.get('duration_seconds') is not None:
        try:
            activity.duration_seconds = max(0, int(data['duration_seconds']))
        except (TypeError, ValueError):
            return {'error': 'Thời lượng cuộc gọi không hợp lệ.'}, 400
    db.session.commit()
    return {'activity': serialize_activity(activity)}


@app.route('/api/customers/<int:c_id>/messages', methods=['POST'])
@api_login_required
def api_send_customer_message(c_id):
    customer = api_visible_customer_query().filter(Customer.id == c_id).first()
    if not customer:
        return {'error': 'Không tìm thấy khách hàng hoặc bạn không có quyền.'}, 404
    data = request.get_json(silent=True) or {}
    channel = (data.get('channel') or '').strip().lower()
    message = (data.get('message') or '').strip()
    if channel not in {'facebook', 'zalo'}:
        return {'error': 'Kênh nhắn tin phải là facebook hoặc zalo.'}, 400
    if not message:
        return {'error': 'Nội dung tin nhắn không được để trống.'}, 400
    try:
        if channel == 'facebook':
            external_id, media_type = send_facebook_message(customer, message, None)
        else:
            external_id = send_zalo_message(customer, message)
            media_type = None
    except ValueError as exc:
        return {'error': str(exc)}, 400
    except Exception:
        logger.exception('Unable to send %s message for customer %s', channel, customer.id)
        return {'error': 'Không thể gửi tin nhắn lúc này.'}, 502
    sent_at = datetime.utcnow()
    message_log = MessageLog(
        customer_id=customer.id,
        sender_type='sales',
        channel=channel,
        message=message,
        media_type=media_type,
        external_message_id=external_id,
        sent_at=sent_at,
    )
    db.session.add(message_log)
    db.session.add(CustomerActivity(
        customer_id=customer.id,
        user_id=api_current_user().id,
        activity_type='message',
        channel=channel,
        note=message,
        status='completed',
        started_at=sent_at,
        ended_at=sent_at,
    ))
    customer.last_message_date = sent_at
    customer.message_excerpt = message[:500]
    db.session.commit()
    return {'message': serialize_message(message_log)}, 201


@app.route('/api/customers/<int:c_id>', methods=['PUT'])
@api_login_required
def api_update_customer(c_id):
    c = api_visible_customer_query().filter(Customer.id == c_id).first()
    if not c:
        return {'error': 'Không tìm thấy khách hàng.'}, 404
    data = request.get_json(silent=True) or {}
    for field in ('name', 'phone', 'email', 'notes', 'location', 'tags', 'facebook_id'):
        if field in data:
            if field == 'phone':
                previous_phone = c.phone
                data[field] = sanitize_customer_phone(data[field])
                if not previous_phone and data[field]:
                    c.phone_added_at = datetime.utcnow()
                elif previous_phone and not data[field]:
                    c.phone_added_at = None
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
        phone_added_at=datetime.utcnow() if (data.get('phone') or '').strip() else None,
        notes=data.get('notes'),
        location=data.get('location'),
        tags=data.get('tags'),
        assigned_user_id=user.id if user.role in {'sales', 'employee'} else None,
    )
    db.session.add(c)
    db.session.commit()
    return {'customer': serialize_customer(c)}, 201


@app.route('/api/admin/users')
@api_login_required
def api_admin_users():
    if api_current_user().role != 'admin':
        return {'error': 'Chỉ Admin mới có quyền xem danh sách nhân viên.'}, 403
    users = User.query.filter(User.role.in_(('sales', 'employee')), User.is_active.is_(True)).order_by(User.username.asc()).all()
    return {'users': [{'id': user.id, 'username': user.username, 'role': user.role, 'role_label': USER_ROLES.get(user.role, user.role)} for user in users]}


@app.route('/api/admin/customers/<int:c_id>/assign', methods=['PATCH'])
@api_login_required
def api_assign_customer(c_id):
    if api_current_user().role != 'admin':
        return {'error': 'Chỉ Admin mới có quyền phân công khách hàng.'}, 403
    customer = Customer.query.filter_by(id=c_id).first()
    if not customer:
        return {'error': 'Không tìm thấy khách hàng.'}, 404
    data = request.get_json(silent=True) or {}
    user_id = data.get('assigned_user_id')
    user = db.session.get(User, user_id) if user_id else None
    if user_id and (not user or user.role not in {'sales', 'employee'} or not user.is_active):
        return {'error': 'Sales được chọn không hợp lệ hoặc đã bị khóa.'}, 400
    customer.assigned_user_id = user.id if user else None
    db.session.commit()
    return {'customer': serialize_customer(customer)}


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
    user = api_current_user()
    phone = sanitize_customer_phone(data.get('phone') or '')
    phone_numbers = extract_phone_numbers(phone)
    normalized_phone = phone_numbers[0] if phone_numbers else ''
    c = Customer.query.filter(Customer.id == customer_id).first() if customer_id else None
    if c is None and normalized_phone:
        for candidate in Customer.query.filter(Customer.phone.isnot(None), Customer.phone != '').all():
            candidate_phone = extract_phone_numbers(candidate.phone)
            if candidate_phone and candidate_phone[0] == normalized_phone:
                c = candidate
                break
    if c is None and normalized_phone:
        name = (data.get('customer_name') or '').strip()
        if not name:
            return {'error': 'Cần nhập tên khách hàng khi tạo khách mới.'}, 400
        c = Customer(
            name=name,
            phone=normalized_phone,
            phone_added_at=datetime.utcnow(),
            email=(data.get('customer_email') or '').strip(),
            location=(data.get('customer_location') or '').strip(),
            source='manual',
        )
        db.session.add(c)
        db.session.flush()
    if not c:
        return {'error': 'Cần chọn khách hàng hoặc nhập số điện thoại.'}, 400
    if normalized_phone:
        if not c.phone_added_at:
            c.phone_added_at = datetime.utcnow()
        c.phone = normalized_phone
    if data.get('customer_name'):
        c.name = data['customer_name'].strip()
    if data.get('customer_email'):
        c.email = data['customer_email'].strip()
    if data.get('customer_location'):
        c.location = data['customer_location'].strip()
    if user.role == 'sales':
        c.assigned_user_id = user.id
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
    points_redeemed = max(int(data.get('points_redeemed') or 0), 0)
    points_value = float(data.get('points_value') or 1000)
    if points_value <= 0:
        return {'error': 'Giá trị quy đổi 1 điểm phải lớn hơn 0.'}, 400
    if points_redeemed > (c.points or 0):
        return {'error': f'Khách chỉ còn {c.points or 0} điểm.'}, 400
    points_discount = points_redeemed * points_value
    total = max(sum(i.quantity * i.unit_price for i in items) - discount - points_discount + vat, 0)
    order = Order(
        customer_id=c.id,
        code=f"DH{datetime.utcnow():%Y%m%d%H%M%S}{c.id}",
        total_amount=total, status=data.get('status') or 'Mới',
        note=(data.get('note') or '').strip(),
        delivery_address=(data.get('delivery_address') or '').strip(),
        payment_details=(data.get('payment_details') or '').strip(),
        sales_phone=(data.get('sales_phone') or '').strip(),
        sales_bank_account=(data.get('sales_bank_account') or '').strip(),
        points_redeemed=points_redeemed,
        points_value=points_value,
        points_discount=points_discount,
        discount_amount=discount, vat_amount=vat,
    )
    db.session.add(order)
    order.items.extend(items)
    db.session.commit()
    update_customer_points(c.id)
    return {'order': serialize_order(order)}, 201


def production_order_message(order):
    lines = [
        f'ĐƠN SẢN XUẤT: {order.code}',
        f'Trạng thái: {order.status}',
        '',
        'Sản phẩm:',
    ]
    lines.extend(
        f'- {item.product_name} | ĐVT: {item.unit or "—"} | SL: {item.quantity:g}'
        for item in order.items
    )
    if order.note:
        lines.extend(['', f'Ghi chú sản xuất: {order.note}'])
    return '\n'.join(lines)


@app.route('/api/orders/<int:order_id>', methods=['PUT', 'DELETE'])
@api_login_required
def api_modify_order(order_id):
    order = Order.query.join(Customer).filter(
        Order.id == order_id,
        Customer.id.in_(api_visible_customer_query().with_entities(Customer.id)),
    ).first()
    if not order:
        return {'error': 'Không tìm thấy đơn hàng hoặc bạn không có quyền.'}, 404
    if request.method == 'DELETE':
        db.session.delete(order)
        db.session.commit()
        return {'ok': True}
    data = request.get_json(silent=True) or {}
    if 'status' in data and data['status']:
        order.status = str(data['status']).strip()
    for field in ('note', 'delivery_address', 'payment_details', 'sales_phone', 'sales_bank_account'):
        if field in data:
            setattr(order, field, (data[field] or '').strip())
    if 'points_value' in data or 'points_redeemed' in data:
        try:
            order.points_value = float(data.get('points_value') or order.points_value or 1000)
            order.points_redeemed = max(int(data.get('points_redeemed') or 0), 0)
        except (TypeError, ValueError):
            return {'error': 'Giá trị điểm và số điểm đổi không hợp lệ.'}, 400
        if order.points_value <= 0:
            return {'error': 'Giá trị quy đổi 1 điểm phải lớn hơn 0.'}, 400
        if order.points_redeemed > (order.customer.points or 0) + (order.points_redeemed or 0):
            return {'error': 'Số điểm đổi vượt quá số dư của khách hàng.'}, 400
        order.points_discount = order.points_redeemed * order.points_value
    if 'items' in data:
        if not isinstance(data['items'], list) or not data['items']:
            return {'error': 'Đơn hàng cần ít nhất một sản phẩm.'}, 400
        order.items.clear()
        for raw_item in data['items']:
            try:
                order.items.append(OrderItem(
                    product_code=(raw_item.get('product_code') or '').strip(),
                    product_name=(raw_item.get('product_name') or '').strip(),
                    unit=(raw_item.get('unit') or '').strip(),
                    quantity=max(float(raw_item.get('quantity') or 0), 0),
                    unit_price=max(float(raw_item.get('unit_price') or 0), 0),
                ))
            except (TypeError, ValueError):
                return {'error': 'Số lượng / đơn giá không hợp lệ.'}, 400
        order.discount_amount = max(float(data.get('discount_amount') or 0), 0)
        order.vat_amount = max(float(data.get('vat_amount') or 0), 0)
        order.total_amount = max(
            sum(item.quantity * item.unit_price for item in order.items)
            - order.discount_amount - (order.points_discount or 0) + order.vat_amount, 0,
        )
    db.session.commit()
    return {'order': serialize_order(order)}


@app.route('/api/orders/<int:order_id>/production', methods=['POST'])
@api_login_required
def api_send_order_to_production(order_id):
    order = Order.query.join(Customer).filter(
        Order.id == order_id,
        Customer.id.in_(api_visible_customer_query().with_entities(Customer.id)),
    ).first()
    if not order:
        return {'error': 'Không tìm thấy đơn hàng hoặc bạn không có quyền.'}, 404
    message = production_order_message(order)
    order.production_sent_at = datetime.utcnow()
    order.status = 'Đã gửi sản xuất'
    db.session.commit()
    return {
        'order': serialize_order(order),
        'message': message,
        'zalo_url': 'https://zalo.me',
    }


if __name__ == '__main__':
    init_db()
    app.run(debug=True)
else:
    init_db()
    if is_production:
        threading.Thread(target=_scheduled_business_sync_loop, daemon=True).start()
        threading.Thread(target=_developer_monitor_loop, daemon=True).start()
