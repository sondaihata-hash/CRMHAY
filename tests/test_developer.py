import re

from app import DeveloperCommandLog, User, app, db
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
