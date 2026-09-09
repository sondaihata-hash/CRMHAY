import re
import uuid

from app import DeveloperCommandLog, Organization, User, app, db
from auth_helpers import csrf_token
from werkzeug.security import generate_password_hash


def login_developer(client):
    username = 'test_developer'
    password = 'TestDeveloperPass123!'
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        if not user:
            user = User(
                username=username,
                password_hash=generate_password_hash(password),
                role='dev',
                is_platform_admin=True,
            )
            db.session.add(user)
        else:
            user.role = 'dev'
            user.is_platform_admin = True
            user.is_active = True
        db.session.commit()
    response = client.post('/login', data={'username': username, 'password': password})
    assert response.status_code == 302


def test_developer_console_and_allowlisted_command():
    client = app.test_client()
    login_developer(client)
    response = client.get('/developer')
    assert response.status_code == 200
    token = re.search(
        r'name="_csrf_token" value="([^"]+)"', response.get_data(as_text=True)
    ).group(1)
    response = client.post(
        '/developer/chat',
        data={'_csrf_token': token, 'command': 'cài đặt hệ thống'},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        assert DeveloperCommandLog.query.filter_by(command='cài đặt hệ thống').first()


def test_regular_admin_cannot_open_developer_console():
    from auth_helpers import login_admin

    client = login_admin(app.test_client())
    assert client.get('/developer').status_code == 403


def test_developer_can_manage_accounts_across_organizations():
    client = app.test_client()
    login_developer(client)
    with app.app_context():
        organization = Organization(
            name='Developer Test Company',
            slug=f'developer-test-company-{uuid.uuid4().hex[:8]}',
        )
        db.session.add(organization)
        db.session.commit()
        organization_id = organization.id

    token = csrf_token(client, '/admin/users')
    username = 'developer_managed_user'
    response = client.post(
        '/admin/users/add',
        data={
            '_csrf_token': token,
            'username': username,
            'password': 'ManagedUserPass123!',
            'role': 'admin',
            'organization_id': organization_id,
        },
    )
    assert response.status_code == 302
    with app.app_context():
        user = User.query.filter_by(username=username).one()
        user_id = user.id
        assert user.organization_id == organization_id

    token = csrf_token(client, '/admin/users')
    response = client.post(
        f'/admin/users/{user_id}/edit',
        data={
            '_csrf_token': token,
            'username': username + '_edited',
            'password': 'EditedUserPass123!',
            'role': 'dev',
            'organization_id': organization_id,
        },
    )
    assert response.status_code == 302
    with app.app_context():
        assert User.query.get(user_id).role == 'dev'

    token = csrf_token(client, '/admin/users')
    response = client.post(
        f'/admin/users/{user_id}/delete',
        data={'_csrf_token': token},
    )
    assert response.status_code == 302
    with app.app_context():
        assert User.query.get(user_id) is None


def test_developer_can_create_and_edit_company_information():
    client = app.test_client()
    login_developer(client)
    suffix = uuid.uuid4().hex[:8]
    slug = f'managed-company-{suffix}'
    username = f'managed-company-admin-{suffix}'
    token = csrf_token(client, '/platform/organizations')
    response = client.post(
        '/platform/organizations',
        data={
            '_csrf_token': token,
            'name': 'Managed Company',
            'slug': slug,
            'username': username,
            'password': 'ManagedCompanyPass123!',
            'company_address': '1 Test Street',
            'company_phone': '0900000000',
            'company_email': 'admin@managed.test',
        },
    )
    assert response.status_code == 302
    with app.app_context():
        organization = Organization.query.filter_by(slug=slug).one()
        organization_id = organization.id
        assert organization.company_address == '1 Test Street'

    token = csrf_token(client, '/platform/organizations')
    response = client.post(
        f'/platform/organizations/{organization_id}/edit',
        data={
            '_csrf_token': token,
            'name': 'Managed Company Updated',
            'slug': 'managed-company-updated',
            'company_address': '2 Updated Street',
            'company_phone': '0911111111',
            'company_email': 'updated@managed.test',
        },
    )
    assert response.status_code == 302
    with app.app_context():
        organization = db.session.get(Organization, organization_id)
        assert organization.name == 'Managed Company Updated'
        assert organization.company_address == '2 Updated Street'
