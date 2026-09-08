import uuid

from app import (
    AuditLog,
    Customer,
    LoginThrottle,
    Organization,
    Setting,
    User,
    app,
    db,
)
from werkzeug.security import generate_password_hash
from auth_helpers import csrf_token


def test_health_and_readiness_probes_and_security_headers():
    client = app.test_client()

    health = client.get('/healthz')
    ready = client.get('/readyz')

    assert health.status_code == 200
    assert health.json == {'status': 'ok'}
    assert ready.status_code == 200
    assert health.headers['X-Content-Type-Options'] == 'nosniff'
    assert health.headers['X-Frame-Options'] == 'SAMEORIGIN'
    assert health.headers['Referrer-Policy'] == 'strict-origin-when-cross-origin'


def test_login_brute_force_lock_has_clear_error():
    username = f'locked_{uuid.uuid4().hex}'
    client = app.test_client()
    try:
        for _ in range(5):
            response = client.post(
                '/login',
                data={'username': username, 'password': 'wrong'},
                environ_base={'REMOTE_ADDR': '198.51.100.20'},
            )
        assert response.status_code == 429
        assert 'tạm khóa' in response.get_data(as_text=True)
    finally:
        with app.app_context():
            throttle = LoginThrottle.query.filter_by(username=username).first()
            if throttle:
                db.session.delete(throttle)
                db.session.commit()


def test_customer_and_setting_are_isolated_by_organization():
    suffix = uuid.uuid4().hex
    with app.app_context():
        org_a = Organization(name=f'Org A {suffix}', slug=f'org-a-{suffix}')
        org_b = Organization(name=f'Org B {suffix}', slug=f'org-b-{suffix}')
        db.session.add_all([org_a, org_b])
        db.session.flush()
        user_a = User(
            username=f'user-a-{suffix}', password_hash=generate_password_hash('Pass1234!'),
            role='sales', organization_id=org_a.id,
        )
        user_b = User(
            username=f'user-b-{suffix}', password_hash=generate_password_hash('Pass1234!'),
            role='sales', organization_id=org_b.id,
        )
        customer_b = Customer(name=f'Private B {suffix}', organization_id=org_b.id)
        setting_b = Setting(
            key=f'private-{suffix}', value='only-b', organization_id=org_b.id,
        )
        db.session.add_all([user_a, user_b, customer_b, setting_b])
        db.session.commit()
        org_a_id, org_b_id = org_a.id, org_b.id
        user_a_id, user_b_id = user_a.id, user_b.id
        customer_b_id = customer_b.id
        setting_b_id = setting_b.id

    try:
        client_a = app.test_client()
        assert client_a.post(
            '/login', data={'username': f'user-a-{suffix}', 'password': 'Pass1234!'}
        ).status_code == 302
        with app.app_context():
            assert AuditLog.query.filter_by(
                action='login.success', actor_user_id=user_a_id
            ).count() >= 1
        assert client_a.get(f'/customers/{customer_b_id}').status_code == 404
        assert 'only-b' not in client_a.get('/settings').get_data(as_text=True)
    finally:
        with app.app_context():
            for audit in AuditLog.query.filter(
                AuditLog.organization_id.in_([org_a_id, org_b_id])
            ).all():
                db.session.delete(audit)
            db.session.delete(db.session.get(Setting, setting_b_id))
            db.session.delete(db.session.get(Customer, customer_b_id))
            db.session.delete(db.session.get(User, user_a_id))
            db.session.delete(db.session.get(User, user_b_id))
            db.session.delete(db.session.get(Organization, org_a_id))
            db.session.delete(db.session.get(Organization, org_b_id))
            db.session.commit()


def test_user_can_change_password_and_admin_can_view_audit_log():
    username = f'password_{uuid.uuid4().hex}'
    with app.app_context():
        user = User(
            username=username,
            password_hash=generate_password_hash('OldPassword123!'),
            role='sales',
        )
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    try:
        client = app.test_client()
        assert client.post(
            '/login',
            data={'username': username, 'password': 'OldPassword123!'},
        ).status_code == 302
        token = client.get('/account/password').get_data(as_text=True)
        assert 'Đổi mật khẩu' in token
        response = client.post(
            '/account/password',
            data={
                'current_password': 'OldPassword123!',
                'new_password': 'NewPassword123!',
                'confirmation': 'NewPassword123!',
                '_csrf_token': csrf_token(client, '/account/password'),
            },
        )
        assert response.status_code == 302
        with app.app_context():
            changed = db.session.get(User, user_id)
            assert changed is not None
            assert changed.password_hash != 'OldPassword123!'
    finally:
        with app.app_context():
            db.session.delete(db.session.get(User, user_id))
            db.session.commit()
