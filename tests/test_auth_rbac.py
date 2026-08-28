import uuid

from app import Customer, User, app, db
from auth_helpers import csrf_token, login_admin
from werkzeug.security import generate_password_hash


def test_protected_routes_require_login():
    response = app.test_client().get('/customers')

    assert response.status_code == 302
    assert '/login' in response.headers['Location']


def test_sales_can_only_access_currently_assigned_customer():
    username = f'sales_{uuid.uuid4().hex}'
    with app.app_context():
        sales = User(
            username=username,
            password_hash=generate_password_hash('SalesPass123!'),
            role='sales',
        )
        db.session.add(sales)
        db.session.flush()
        assigned = Customer(name=f'Assigned {uuid.uuid4().hex}', assigned_user_id=sales.id)
        hidden = Customer(name=f'Hidden {uuid.uuid4().hex}')
        db.session.add_all([assigned, hidden])
        db.session.commit()
        sales_id, assigned_id, hidden_id = sales.id, assigned.id, hidden.id

    try:
        client = app.test_client()
        assert client.post('/login', data={'username': username, 'password': 'SalesPass123!'}).status_code == 302
        assert client.get(f'/customers/{assigned_id}').status_code == 200
        assert client.get(f'/customers/{hidden_id}').status_code == 404
    finally:
        with app.app_context():
            db.session.delete(db.session.get(Customer, assigned_id))
            db.session.delete(db.session.get(Customer, hidden_id))
            db.session.delete(db.session.get(User, sales_id))
            db.session.commit()


def test_sales_cannot_access_admin_area_or_assign_customer():
    username = f'sales_{uuid.uuid4().hex}'
    with app.app_context():
        sales = User(
            username=username,
            password_hash=generate_password_hash('SalesPass123!'),
            role='sales',
        )
        db.session.add(sales)
        db.session.flush()
        customer = Customer(name=f'Assignment {uuid.uuid4().hex}')
        db.session.add(customer)
        db.session.commit()
        sales_id, customer_id = sales.id, customer.id

    try:
        client = app.test_client()
        assert client.post('/login', data={'username': username, 'password': 'SalesPass123!'}).status_code == 302
        assert client.get('/settings').status_code == 403
        assert client.get('/admin/users').status_code == 403
        assert client.post(
            f'/customers/{customer_id}/assign',
            data={'assigned_user_id': sales_id, '_csrf_token': csrf_token(client, '/customers')},
        ).status_code == 403
    finally:
        with app.app_context():
            db.session.delete(db.session.get(Customer, customer_id))
            db.session.delete(db.session.get(User, sales_id))
            db.session.commit()
