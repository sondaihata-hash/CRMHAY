import uuid

from app import Customer, SalesGroup, SalesHandoff, app, db, is_valid_zalo_group_url


def test_zalo_handoff_returns_configured_group_link():
    group_name = f'Sales Test {uuid.uuid4().hex}'
    customer = Customer(name=f'Customer Test {uuid.uuid4().hex}', phone='0987654321')
    group = SalesGroup(
        name=group_name,
        description='Test group',
        zalo_url='https://zalo.me/g/example-group',
    )

    with app.app_context():
        db.session.add_all([customer, group])
        db.session.commit()
        customer_id = customer.id
        group_id = group.id

        response = app.test_client().post(
            f'/customers/{customer_id}/handoff-zalo',
            data={'group_id': group_id},
        )

        assert response.status_code == 200
        payload = response.get_json()
        assert payload['destination_url'] == 'https://zalo.me/g/example-group'

        db.session.query(SalesHandoff).filter_by(customer_id=customer.id).delete()
        db.session.delete(group)
        db.session.delete(customer)
        db.session.commit()


def test_sales_group_link_update_accepts_zalo_links_only():
    group = SalesGroup(name=f'Link Test {uuid.uuid4().hex}')

    with app.app_context():
        db.session.add(group)
        db.session.commit()

        client = app.test_client()
        response = client.post(
            f'/sales-groups/{group.id}/link',
            data={'zalo_url': 'https://zalo.me/g/sales-group'},
        )
        assert response.status_code == 302
        assert db.session.get(SalesGroup, group.id).zalo_url == 'https://zalo.me/g/sales-group'

        response = client.post(
            f'/sales-groups/{group.id}/link',
            data={'zalo_url': 'https://evil.example/phishing'},
        )
        assert response.status_code == 302
        assert db.session.get(SalesGroup, group.id).zalo_url == 'https://zalo.me/g/sales-group'

        db.session.delete(group)
        db.session.commit()


def test_zalo_group_url_validation_allows_only_group_links():
    assert is_valid_zalo_group_url('https://zalo.me/g/sales-group')
    assert is_valid_zalo_group_url('')
    assert not is_valid_zalo_group_url('https://evil.example/g/sales-group')
    assert not is_valid_zalo_group_url('zalo://open/group')
    assert not is_valid_zalo_group_url('https://zalo.me/g/../phishing')
    assert not is_valid_zalo_group_url('https://zalo.me/g/sales-group?next=evil.example')
    assert not is_valid_zalo_group_url('https://[bad')


def test_handoff_ignores_legacy_invalid_group_destination():
    group = SalesGroup(
        name=f'Legacy Link Test {uuid.uuid4().hex}',
        zalo_url='https://evil.example/phishing',
    )
    customer = Customer(name=f'Legacy Customer {uuid.uuid4().hex}')

    with app.app_context():
        db.session.add_all([group, customer])
        db.session.commit()
        response = app.test_client().post(
            f'/customers/{customer.id}/handoff-zalo',
            data={'group_id': group.id},
        )
        assert response.get_json()['destination_url'] == 'zalo://'
        db.session.query(SalesHandoff).filter_by(customer_id=customer.id).delete()
        db.session.delete(group)
        db.session.delete(customer)
        db.session.commit()