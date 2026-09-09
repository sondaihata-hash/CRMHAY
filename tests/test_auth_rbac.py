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


def test_admin_can_promote_sales_to_manager():
    username = f'sales_{uuid.uuid4().hex}'
    client = login_admin(app.test_client())
    with app.app_context():
        admin = User.query.filter_by(username='test_admin').one()
        sales = User(
            username=username,
            password_hash=generate_password_hash('SalesPass123!'),
            role='sales',
            organization_id=admin.organization_id,
        )
        db.session.add(sales)
        db.session.commit()
        sales_id = sales.id

    try:
        response = client.post(
            f'/admin/users/{sales_id}/edit',
            data={
                'username': username,
                'role': 'manager',
                '_csrf_token': csrf_token(client, '/admin/users'),
            },
        )
        assert response.status_code == 302
        with app.app_context():
            assert db.session.get(User, sales_id).role == 'manager'
    finally:
        with app.app_context():
            db.session.delete(db.session.get(User, sales_id))
            db.session.commit()


def test_admin_can_assign_all_customers_from_page_to_sales():
    client = login_admin(app.test_client())
    with app.app_context():
        admin = User.query.filter_by(username='test_admin').one()
        sales = User(
            username=f'sales_{uuid.uuid4().hex}',
            password_hash=generate_password_hash('SalesPass123!'),
            role='sales',
            organization_id=admin.organization_id,
        )
        customers = [
            Customer(name=f'Page customer {uuid.uuid4().hex}', page_name='Page A', organization_id=admin.organization_id),
            Customer(name=f'Page customer {uuid.uuid4().hex}', page_name='Page A', organization_id=admin.organization_id),
            Customer(name=f'Other page customer {uuid.uuid4().hex}', page_name='Page B', organization_id=admin.organization_id),
        ]
        db.session.add(sales)
        db.session.add_all(customers)
        db.session.commit()
        sales_id = sales.id
        customer_ids = [customer.id for customer in customers]

    try:
        response = client.post(
            '/customers/assign-bulk',
            data={
                'assigned_user_id': sales_id,
                'page_name': 'Page A',
                '_csrf_token': csrf_token(client, '/customers'),
            },
        )
        assert response.status_code == 302
        with app.app_context():
            assigned = [db.session.get(Customer, customer_id) for customer_id in customer_ids]
            assert assigned[0].assigned_user_id == sales_id
            assert assigned[1].assigned_user_id == sales_id
            assert assigned[2].assigned_user_id is None
    finally:
        with app.app_context():
            for customer_id in customer_ids:
                customer = db.session.get(Customer, customer_id)
                if customer:
                    db.session.delete(customer)
            db.session.delete(db.session.get(User, sales_id))
            db.session.commit()
