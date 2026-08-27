import uuid

from app import Customer, SalesGroup, SalesHandoff, app, build_zalo_handoff_message, db, is_valid_zalo_group_url


def test_zalo_handoff_message_contains_only_required_customer_fields():
    customer = Customer(
        name='Lan Anh',
        phone='0987654321',
        location='Hà Nội',
        page_name='Page Cửa Hàng',
        facebook_id='facebook-123',
        notes='Ghi chú nội bộ',
        message_excerpt='Tin nhắn gần nhất',
    )

    message = build_zalo_handoff_message(customer)

    assert message == 'Họ tên: Lan Anh\nSố điện thoại: 0987654321\nNơi ở: Hà Nội'
    assert 'Page Cửa Hàng' not in message
    assert 'facebook-123' not in message
    assert 'Ghi chú nội bộ' not in message
    assert 'Tin nhắn gần nhất' not in message


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
    assert not is_valid_zalo_group_url('https://zalo.me:444/g/sales-group')
    assert not is_valid_zalo_group_url('https://zalo.me:abc/g/sales-group')
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


def test_customer_delete_cascades_zalo_handoff_history():
    group = SalesGroup(name=f'Delete Test {uuid.uuid4().hex}')
    customer = Customer(name=f'Delete Customer {uuid.uuid4().hex}')

    with app.app_context():
        db.session.add_all([group, customer])
        db.session.flush()
        handoff = SalesHandoff(customer_id=customer.id, group_id=group.id, message='Lead')
        db.session.add(handoff)
        db.session.commit()
        customer_id = customer.id
        handoff_id = handoff.id

        db.session.delete(customer)
        db.session.commit()

        assert db.session.get(Customer, customer_id) is None
        assert db.session.get(SalesHandoff, handoff_id) is None
        db.session.delete(group)
        db.session.commit()