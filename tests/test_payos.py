import json
import uuid
from unittest import mock

from app import (
    Payment, Subscription, User, Organization, _payos_signature, PLAN_FEATURES,
    SALES_SEAT_MONTHLY_PRICE, app, db,
)
from werkzeug.security import generate_password_hash


def test_checkout_fails_clearly_when_payos_is_not_configured():
    client = app.test_client()
    with mock.patch.dict('os.environ', {'PAYOS_CLIENT_ID': '', 'PAYOS_API_KEY': ''}, clear=False):
        response = client.post('/checkout/growth', json={
            'organization_name': 'Test company',
            'username': f'new_checkout_user_{uuid.uuid4().hex}',
            'password': 'Password123!',
        })
    assert response.status_code == 503
    assert 'PAYOS_CLIENT_ID' in response.get_json()['message']


def test_webhook_fails_closed_when_secret_is_not_configured():
    with mock.patch.dict('os.environ', {'PAYOS_WEBHOOK_SECRET': '', 'PAYOS_API_KEY': ''}, clear=False):
        response = app.test_client().post('/api/payos/webhook', json={'code': '00', 'data': {}})
    assert response.status_code == 503
    assert 'secret' in response.get_json()['message'].lower()


def test_checkout_assigns_organization_before_persisting_subscription():
    username = f'checkout_user_{uuid.uuid4().hex}'
    with mock.patch.dict('os.environ', {
        'PAYOS_CLIENT_ID': 'client',
        'PAYOS_API_KEY': 'key',
        'PAYOS_CHECKSUM_KEY': 'checksum',
    }, clear=False):
        with mock.patch('app._payos_create_link', return_value=('https://payos.test/checkout', {'code': '00'})):
            response = app.test_client().post('/checkout/growth', json={
                'organization_name': 'Checkout company',
                'username': username,
                'password': 'Password123!',
                'billing_interval': 'monthly',
            })
    assert response.status_code == 200
    with app.app_context():
        user = User.query.filter_by(username=username).one()
        subscription = Subscription.query.filter_by(organization_id=user.organization_id).order_by(
            Subscription.id.desc(),
        ).first()
        assert subscription.organization_id == user.organization_id
        assert subscription.billing_interval == 'monthly'
        payment = Payment.query.filter_by(user_id=user.id).one()
        assert payment.billing_interval == 'monthly'
        assert payment.amount == 349500
        organization_id = user.organization_id
        db.session.delete(payment)
        db.session.delete(subscription)
        db.session.delete(user)
        db.session.delete(db.session.get(Organization, organization_id))
        db.session.commit()


def test_verified_success_webhook_activates_account_once():
    with app.app_context():
        organization = Organization(name='Pending', slug=f'pending-payments-{uuid.uuid4().hex}', is_active=False)
        db.session.add(organization)
        db.session.flush()
        user = User(username=f'pending_payment_user_{uuid.uuid4().hex}', password_hash=generate_password_hash('Password123!'),
                    role='admin', organization_id=organization.id, is_active=False)
        subscription = Subscription(organization_id=organization.id, plan='growth', status='pending')
        db.session.add_all([user, subscription])
        db.session.flush()
        order_code = 900000000 + (uuid.uuid4().int % 9999999)
        payment = Payment(order_code=order_code, organization_id=organization.id, user_id=user.id,
                          subscription_id=subscription.id, plan='growth', amount=3495000)
        db.session.add(payment)
        db.session.commit()
        payment_id, subscription_id, user_id, organization_id = payment.id, subscription.id, user.id, organization.id
    try:
        data = {'orderCode': order_code, 'amount': 3495000, 'code': '00'}
        payload = {'code': '00', 'success': True, 'data': data,
                   'signature': _payos_signature(data, 'test-secret')}
        with mock.patch.dict('os.environ', {'PAYOS_WEBHOOK_SECRET': 'test-secret'}, clear=False):
            response = app.test_client().post('/api/payos/webhook', data=json.dumps(payload),
                                              content_type='application/json')
        assert response.status_code == 200
        with app.app_context():
            assert db.session.get(Payment, payment_id).status == 'paid'
            assert db.session.get(Organization, organization_id).is_active is True
            assert db.session.get(User, user_id).is_active is True
    finally:
        with app.app_context():
            db.session.delete(db.session.get(Payment, payment_id))
            db.session.delete(db.session.get(Subscription, subscription_id))
            db.session.delete(db.session.get(User, user_id))
            db.session.delete(db.session.get(Organization, organization_id))
            db.session.commit()


def test_plan_features_limit_export_and_hourly_sync():
    assert PLAN_FEATURES['basic']['manual_sync'] is True
    assert PLAN_FEATURES['basic']['export'] is False
    assert PLAN_FEATURES['growth']['export'] is True
    assert PLAN_FEATURES['business']['hourly_sync'] is True


def test_sales_seat_purchase_and_webhook_activation():
    from auth_helpers import csrf_token

    with app.app_context():
        organization = Organization(name='Seat Company', slug=f'seat-company-{uuid.uuid4().hex}')
        db.session.add(organization)
        db.session.flush()
        admin = User(
            username=f'seat-admin-{uuid.uuid4().hex}',
            password_hash=generate_password_hash('Password123!'),
            role='admin',
            organization_id=organization.id,
        )
        subscription = Subscription(
            organization_id=organization.id, plan='basic', status='active',
            billing_interval='monthly',
        )
        db.session.add_all([admin, subscription])
        db.session.flush()
        admin_id, subscription_id, organization_id = admin.id, subscription.id, organization.id
        db.session.commit()
    client = app.test_client()
    client.post('/login', data={'username': admin.username, 'password': 'Password123!'})
    token = csrf_token(client, '/admin/sales-seats')
    with mock.patch('app._payos_create_link', return_value=('https://payos.test/seats', {'code': '00'})):
        response = client.post('/admin/sales-seats', data={'_csrf_token': token, 'quantity': '2'})
    assert response.status_code == 302
    with app.app_context():
        payment = Payment.query.filter_by(organization_id=organization_id, plan='sales_seats').order_by(Payment.id.desc()).first()
        assert payment.amount == 2 * SALES_SEAT_MONTHLY_PRICE
        data = {'orderCode': payment.order_code, 'amount': payment.amount, 'code': '00'}
        payload = {'code': '00', 'success': True, 'data': data, 'signature': _payos_signature(data, 'seat-secret')}
    with mock.patch.dict('os.environ', {'PAYOS_WEBHOOK_SECRET': 'seat-secret'}, clear=False):
        response = app.test_client().post('/api/payos/webhook', json=payload)
    assert response.status_code == 200
    with app.app_context():
        organization = db.session.get(Organization, organization_id)
        assert organization.sales_seat_addons == 2
        db.session.delete(db.session.get(Payment, payment.id))
        db.session.delete(db.session.get(Subscription, subscription_id))
        db.session.delete(db.session.get(User, admin_id))
        db.session.delete(organization)
        db.session.commit()
