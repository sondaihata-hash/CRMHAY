import uuid

from app import Customer, SalesGroup, app, db
from auth_helpers import login_admin


def test_customer_list_contains_zalo_handoff_action():
    with app.app_context():
        customer = Customer(name='Button Test Customer')
        group = SalesGroup(name=f'Button Test Group {uuid.uuid4().hex}', zalo_url='https://zalo.me/g/button-test')
        db.session.add_all([customer, group])
        db.session.commit()

        response = login_admin(app.test_client()).get('/customers')
        html = response.get_data(as_text=True)

        assert response.status_code == 200
        assert 'Gửi Zalo' in html
        assert 'zaloHandoffModal' in html
        assert 'customer-zalo-button' in html
        assert 'onclick="openZaloHandoff' not in html
        assert 'window.location.href = data.destination_url' not in html
        assert 'window.location.assign(\'zalo://\')' in html
        assert 'zalo.me/g/button-test' not in html

        db.session.delete(group)
        db.session.delete(customer)
        db.session.commit()