import uuid

from app import Customer, Organization, Setting, User, app, db, get_facebook_token, set_tenant_context
from auth_helpers import ensure_test_admin, login_admin, csrf_token
from werkzeug.security import generate_password_hash


def test_company_users_cannot_see_another_company_customers():
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        first = Organization(name=f'Company One {suffix}', slug=f'company-one-{suffix}')
        second = Organization(name=f'Company Two {suffix}', slug=f'company-two-{suffix}')
        db.session.add_all([first, second])
        db.session.flush()
        first_user = User(
            organization_id=first.id,
            username=f'company_one_{suffix}',
            password_hash=generate_password_hash('CompanyPass123!'),
            role='admin',
        )
        second_user = User(
            organization_id=second.id,
            username=f'company_two_{suffix}',
            password_hash=generate_password_hash('CompanyPass123!'),
            role='admin',
        )
        db.session.add_all([
            first_user,
            second_user,
            Customer(organization_id=first.id, name=f'Private One {suffix}'),
            Customer(organization_id=second.id, name=f'Private Two {suffix}'),
        ])
        db.session.commit()
        first_id, second_id = first.id, second.id

    try:
        client = app.test_client()
        assert client.post('/login', data={
            'username': f'company_one_{suffix}',
            'password': 'CompanyPass123!',
        }).status_code == 302
        response = client.get('/customers')
        html = response.get_data(as_text=True)
        assert response.status_code == 200
        assert f'Private One {suffix}' in html
        assert f'Private Two {suffix}' not in html
    finally:
        with app.app_context():
            Customer.query.filter(Customer.organization_id.in_([first_id, second_id])).delete(
                synchronize_session=False,
            )
            User.query.filter(User.organization_id.in_([first_id, second_id])).delete(
                synchronize_session=False,
            )
            Organization.query.filter(Organization.id.in_([first_id, second_id])).delete(
                synchronize_session=False,
            )
            db.session.commit()


def test_platform_admin_can_create_company_workspace():
    suffix = uuid.uuid4().hex[:8]
    username = f'new_company_admin_{suffix}'
    ensure_test_admin()
    with app.app_context():
        User.query.filter_by(username='test_admin').one().is_platform_admin = True
        db.session.commit()
    client = login_admin(app.test_client())
    response = client.post('/platform/organizations', data={
        'name': f'New Company {suffix}',
        'username': username,
        'password': 'CompanyPass123!',
        '_csrf_token': csrf_token(client, '/'),
    })
    assert response.status_code == 302
    with app.app_context():
        organization = Organization.query.filter_by(slug=f'new-company-{suffix}').one()
        company_admin = User.query.filter_by(username=username).one()
        assert company_admin.organization_id == organization.id
        assert company_admin.is_platform_admin is False
        db.session.delete(company_admin)
        db.session.delete(organization)
        db.session.commit()


def test_tenant_cannot_see_unscoped_legacy_customers_or_other_settings():
    suffix = uuid.uuid4().hex[:8]
    with app.app_context():
        organization = Organization(name=f'Isolated Company {suffix}', slug=f'isolated-{suffix}')
        db.session.add(organization)
        db.session.flush()
        db.session.add_all([
        Customer(name=f'Legacy Shared {suffix}'),
        Customer(organization_id=organization.id, name=f'Private {suffix}'),
        Setting(key='FACEBOOK_SYSTEM_USER_ACCESS_TOKEN', value=f'token-{suffix}',
                organization_id=organization.id),
        ])
        db.session.commit()
        organization_id = organization.id
        set_tenant_context(organization_id)
        try:
            names = {customer.name for customer in Customer.query.all()}
            assert names == {f'Private {suffix}'}
            assert get_facebook_token() == f'token-{suffix}'
        finally:
            set_tenant_context(None)
            Customer.query.filter(Customer.name.in_([f'Legacy Shared {suffix}', f'Private {suffix}'])).delete(
                synchronize_session=False,
            )
            Setting.query.filter_by(organization_id=organization_id).delete(synchronize_session=False)
            db.session.delete(organization)
            db.session.commit()
