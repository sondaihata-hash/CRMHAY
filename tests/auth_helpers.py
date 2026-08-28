import re

from app import User, app, db
from werkzeug.security import generate_password_hash


ADMIN_USERNAME = 'test_admin'
ADMIN_PASSWORD = 'TestAdminPass123!'


def ensure_test_admin():
    with app.app_context(), db.session.no_autoflush:
        user = User.query.filter_by(username=ADMIN_USERNAME).first()
        if user:
            user.is_active = True
            user.role = 'admin'
        else:
            user = User(
                username=ADMIN_USERNAME,
                password_hash=generate_password_hash(ADMIN_PASSWORD),
                role='admin',
            )
            db.session.add(user)
        db.session.commit()


def login_admin(client):
    ensure_test_admin()
    response = client.post(
        '/login',
        data={'username': ADMIN_USERNAME, 'password': ADMIN_PASSWORD},
    )
    assert response.status_code == 302
    return client


def csrf_token(client, path='/'):
    response = client.get(path)
    assert response.status_code == 200
    match = re.search(r'name="_csrf_token" value="([^"]+)"', response.get_data(as_text=True))
    assert match, f'No CSRF token found in {path}'
    return match.group(1)
