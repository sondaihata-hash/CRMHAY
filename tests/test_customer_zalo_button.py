from app import Customer, SalesGroup, app, db


def test_customer_list_contains_zalo_handoff_action():
    with app.app_context():
        customer = Customer(name='Button Test Customer')
        group = SalesGroup(name='Button Test Group', zalo_url='https://zalo.me/g/button-test')
        db.session.add_all([customer, group])
        db.session.commit()

        response = app.test_client().get('/customers')
        html = response.get_data(as_text=True)

        assert response.status_code == 200
        assert 'Gửi Zalo' in html
        assert 'zaloHandoffModal' in html
        assert 'zalo.me/g/button-test' not in html

        db.session.delete(group)
        db.session.delete(customer)
        db.session.commit()