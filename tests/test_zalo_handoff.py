import uuid

from app import Customer, SalesGroup, SalesHandoff, app, db


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