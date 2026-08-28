"""Reset an existing Admin password without exposing it in shell history."""
import argparse
import os
from getpass import getpass

from werkzeug.security import generate_password_hash


def main():
    parser = argparse.ArgumentParser(description='Reset a CRM Admin password.')
    parser.add_argument('username', help='Existing Admin username')
    parser.add_argument('--local', action='store_true', help='Allow resetting the local SQLite database.')
    args = parser.parse_args()
    if not args.local and not os.environ.get('DATABASE_URL'):
        raise SystemExit('DATABASE_URL is required. Use --local only for the local database.')
    from app import User, app, db

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
        db.session.commit()
    print('Admin password reset successfully.')


if __name__ == '__main__':
    main()
