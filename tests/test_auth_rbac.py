import uuid

from app import Customer, CustomerActivity, Organization, User, app, db
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


def test_manager_can_view_and_reassign_team_customers():
    with app.app_context():
        organization = Organization(name=f'Manager Company {uuid.uuid4().hex}', slug=f'manager-company-{uuid.uuid4().hex}')
        db.session.add(organization)
        db.session.flush()
        manager = User(
            username=f'manager_{uuid.uuid4().hex}',
            password_hash=generate_password_hash('ManagerPass123!'),
            role='manager',
            organization_id=organization.id,
        )
        sales_one = User(
            username=f'sales_{uuid.uuid4().hex}',
            password_hash=generate_password_hash('SalesPass123!'),
            role='sales',
            manager=manager,
            organization_id=organization.id,
        )
        sales_two = User(
            username=f'sales_{uuid.uuid4().hex}',
            password_hash=generate_password_hash('SalesPass123!'),
            role='sales',
            manager=manager,
            organization_id=organization.id,
        )
        db.session.add(manager)
        db.session.flush()
        customer = Customer(
            name=f'Team customer {uuid.uuid4().hex}',
            assigned_user=sales_one,
            organization_id=organization.id,
        )
        db.session.add_all([manager, sales_one, sales_two, customer])
        db.session.flush()
        sales_one.manager_id = manager.id
        sales_two.manager_id = manager.id
        customer.assigned_user_id = sales_one.id
        db.session.commit()
        manager_username = manager.username
        organization_id = organization.id
        manager_id, sales_one_id, sales_two_id, customer_id = (
            manager.id, sales_one.id, sales_two.id, customer.id,
        )

    try:
        client = app.test_client()
        assert client.post(
            '/login',
            data={'username': manager_username, 'password': 'ManagerPass123!'},
        ).status_code == 302
        assert client.get(f'/customers/{customer_id}').status_code == 200
        response = client.post(
            f'/customers/{customer_id}/assign',
            data={
                'assigned_user_id': sales_two_id,
                '_csrf_token': csrf_token(client, '/customers'),
            },
        )
        assert response.status_code == 302
        with app.app_context():
            assert db.session.get(Customer, customer_id).assigned_user_id == sales_two_id
    finally:
        with app.app_context():
            for model, item_id in (
                (Customer, customer_id), (User, sales_one_id),
                (User, sales_two_id), (User, manager_id),
            ):
                item = db.session.get(model, item_id)
                if item:
                    db.session.delete(item)
            db.session.delete(db.session.get(Organization, organization_id))
            db.session.commit()


def test_manager_can_view_customers_assigned_directly_to_manager():
    with app.app_context():
        organization = Organization(name=f'Direct Manager Company {uuid.uuid4().hex}', slug=f'direct-manager-{uuid.uuid4().hex}')
        db.session.add(organization)
        db.session.flush()
        manager = User(
            username=f'manager_{uuid.uuid4().hex}',
            password_hash=generate_password_hash('ManagerPass123!'),
            role='manager',
            organization_id=organization.id,
        )
        db.session.add(manager)
        db.session.flush()
        customer = Customer(
            name=f'Direct manager customer {uuid.uuid4().hex}',
            assigned_user_id=manager.id,
            organization_id=organization.id,
        )
        db.session.add(customer)
        db.session.commit()
        manager_username, manager_id = manager.username, manager.id
        customer_id, organization_id = customer.id, organization.id

    try:
        client = app.test_client()
        assert client.post(
            '/login',
            data={'username': manager_username, 'password': 'ManagerPass123!'},
        ).status_code == 302
        assert client.get(f'/customers/{customer_id}').status_code == 200
    finally:
        with app.app_context():
            db.session.delete(db.session.get(Customer, customer_id))
            db.session.delete(db.session.get(User, manager_id))
            db.session.delete(db.session.get(Organization, organization_id))
            db.session.commit()


def test_dashboard_counts_customers_held_by_manager():
    client = login_admin(app.test_client())
    with app.app_context():
        admin = User.query.filter_by(username='test_admin').one()
        manager = User(
            username=f'manager_{uuid.uuid4().hex}',
            password_hash=generate_password_hash('ManagerPass123!'),
            role='manager',
            organization_id=admin.organization_id,
        )
        db.session.add(manager)
        db.session.flush()
        customer = Customer(
            name=f'Manager dashboard customer {uuid.uuid4().hex}',
            assigned_user_id=manager.id,
            organization_id=admin.organization_id,
        )
        db.session.add(customer)
        db.session.commit()
        manager_id, customer_id = manager.id, customer.id

    try:
        response = client.get('/')
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert manager.username in html
        assert '1 khách' in html
    finally:
        with app.app_context():
            db.session.delete(db.session.get(Customer, customer_id))
            db.session.delete(db.session.get(User, manager_id))
            db.session.commit()


def test_admin_customer_list_filters_assignment_status_and_shows_sales():
    client = login_admin(app.test_client())
    with app.app_context():
        admin = User.query.filter_by(username='test_admin').one()
        sales = User(
            username=f'sales_{uuid.uuid4().hex}',
            password_hash=generate_password_hash('SalesPass123!'),
            role='sales',
            organization_id=admin.organization_id,
        )
        assigned = Customer(
            name=f'Assigned list customer {uuid.uuid4().hex}',
            assigned_user=sales,
            organization_id=admin.organization_id,
        )
        unassigned = Customer(
            name=f'Unassigned list customer {uuid.uuid4().hex}',
            organization_id=admin.organization_id,
        )
        db.session.add_all([sales, assigned, unassigned])
        db.session.commit()
        sales_id, assigned_id, unassigned_id = sales.id, assigned.id, unassigned.id

    try:
        assigned_html = client.get('/customers?assignment=assigned').get_data(as_text=True)
        assert 'Đã có Sales' in assigned_html
        assert f'Assigned list customer' in assigned_html
        assert 'Unassigned list customer' not in assigned_html
        assert 'Sales phụ trách' in assigned_html
        unassigned_html = client.get('/customers?assignment=unassigned').get_data(as_text=True)
        assert 'Unassigned list customer' in unassigned_html
        assert 'Assigned list customer' not in unassigned_html
    finally:
        with app.app_context():
            db.session.delete(db.session.get(Customer, assigned_id))
            db.session.delete(db.session.get(Customer, unassigned_id))
            db.session.delete(db.session.get(User, sales_id))
            db.session.commit()


def test_admin_customer_list_filters_call_status_and_caller():
    client = login_admin(app.test_client())
    with app.app_context():
        admin = User.query.filter_by(username='test_admin').one()
        first_sales = User(
            username=f'caller_one_{uuid.uuid4().hex}',
            password_hash=generate_password_hash('SalesPass123!'),
            role='sales',
            organization_id=admin.organization_id,
        )
        second_sales = User(
            username=f'caller_two_{uuid.uuid4().hex}',
            password_hash=generate_password_hash('SalesPass123!'),
            role='sales',
            organization_id=admin.organization_id,
        )
        first_customer = Customer(
            name=f'Called by first {uuid.uuid4().hex}',
            organization_id=admin.organization_id,
        )
        second_customer = Customer(
            name=f'Called by second {uuid.uuid4().hex}',
            organization_id=admin.organization_id,
        )
        uncalled_customer = Customer(
            name=f'Never called {uuid.uuid4().hex}',
            organization_id=admin.organization_id,
        )
        db.session.add_all([first_sales, second_sales, first_customer, second_customer, uncalled_customer])
        db.session.flush()
        db.session.add_all([
            CustomerActivity(
                customer_id=first_customer.id, user_id=first_sales.id,
                activity_type='call', channel='phone', status='completed',
            ),
            CustomerActivity(
                customer_id=second_customer.id, user_id=second_sales.id,
                activity_type='call', channel='phone', status='completed',
            ),
        ])
        db.session.commit()
        first_sales_id = first_sales.id
        first_customer_id = first_customer.id
        second_customer_id = second_customer.id
        uncalled_customer_id = uncalled_customer.id
        second_sales_id = second_sales.id

    try:
        called_html = client.get('/customers?call_status=called').get_data(as_text=True)
        assert f'Called by first' in called_html
        assert f'Called by second' in called_html
        assert f'Never called' not in called_html

        uncalled_html = client.get('/customers?call_status=uncalled').get_data(as_text=True)
        assert f'Never called' in uncalled_html
        assert f'Called by first' not in uncalled_html

        caller_html = client.get(
            f'/customers?call_status=called&caller_user_id={first_sales_id}',
        ).get_data(as_text=True)
        assert f'Called by first' in caller_html
        assert f'Called by second' not in caller_html
    finally:
        with app.app_context():
            CustomerActivity.query.filter(
                CustomerActivity.customer_id.in_(
                    [first_customer_id, second_customer_id, uncalled_customer_id],
                ),
            ).delete(synchronize_session=False)
            for customer_id in (first_customer_id, second_customer_id, uncalled_customer_id):
                db.session.delete(db.session.get(Customer, customer_id))
            for user_id in (first_sales_id, second_sales_id):
                db.session.delete(db.session.get(User, user_id))
            db.session.commit()


def test_customer_table_places_sales_column_before_phone():
    client = login_admin(app.test_client())
    with app.app_context():
        admin = User.query.filter_by(username='test_admin').one()
        sales = User(
            username=f'sales_{uuid.uuid4().hex}',
            password_hash=generate_password_hash('SalesPass123!'),
            role='sales',
            organization_id=admin.organization_id,
        )
        customer = Customer(
            name=f'Column order customer {uuid.uuid4().hex}',
            phone='0900000000',
            page_name='Page Order',
            assigned_user=sales,
            organization_id=admin.organization_id,
        )
        db.session.add_all([sales, customer])
        db.session.commit()
        sales_id, customer_id = sales.id, customer.id

    try:
        html = client.get('/customers').get_data(as_text=True)
        assert html.index('>Page<') < html.index('Sales phụ trách')
        assert html.index('Sales phụ trách') < html.index('Số điện thoại')
    finally:
        with app.app_context():
            db.session.delete(db.session.get(Customer, customer_id))
            db.session.delete(db.session.get(User, sales_id))
            db.session.commit()
