"""Reset an existing Admin password without exposing it in shell history."""
import argparse
from getpass import getpass

from app import User, app, db
from werkzeug.security import generate_password_hash


def main():
    parser = argparse.ArgumentParser(description='Reset a CRM Admin password.')
    parser.add_argument('username', help='Existing Admin username')
    args = parser.parse_args()
    password = getpass('New Admin password (min 8 characters): ')
    confirmation = getpass('Confirm new password: ')
    if len(password) < 8:
        raise SystemExit('Password must be at least 8 characters.')
    if password != confirmation:
        raise SystemExit('Passwords do not match.')

    with app.app_context():
        user = User.query.filter_by(username=args.username.strip().lower(), role='admin').first()
        if not user:
            raise SystemExit('Admin account not found.')
        user.password_hash = generate_password_hash(password)
        user.is_active = True
        db.session.commit()
    print('Admin password reset successfully.')


if __name__ == '__main__':
    main()
