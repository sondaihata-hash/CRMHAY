import os

from app import app, build_customer_from_message, extract_phone_numbers, extract_location, resolve_page_access_tokens, resolve_facebook_pages, get_setting_value, fetch_facebook_json


def test_database_uses_persistent_project_path():
    db_uri = app.config['SQLALCHEMY_DATABASE_URI']
    sqlite_path = db_uri.replace('sqlite:///', '', 1)
    assert os.path.isabs(sqlite_path)
    assert 'instance' in sqlite_path.lower()


def test_extract_phone_numbers_from_message():
    text = "Xin chào, tôi ở Hà Nội, gọi cho tôi qua số 0987654321 hoặc 024 9999 9999"
    numbers = extract_phone_numbers(text)
    assert "0987654321" in numbers
    assert "02499999999" in numbers or "024 9999 9999" in text


def test_extract_location_from_message():
    text = "Mình ở quận 7, thành phố Hồ Chí Minh, cần tư vấn gói dịch vụ"
    location = extract_location(text)
    assert "quận 7" in location.lower() or "hồ chí minh" in location.lower()


def test_build_customer_from_message_uses_facebook_fields():
    payload = {
        "name": "Lan Anh",
        "facebook_id": "123456789",
        "page_name": "Page Cửa Hàng",
        "message": "Mình sống ở Hà Nội, số 0987654321, muốn mua hàng",
        "message_date": "2026-08-13T10:00:00+0000",
    }

    customer = build_customer_from_message(payload)
    assert customer["name"] == "Lan Anh"
    assert customer["facebook_id"] == "123456789"
    assert customer["page_name"] == "Page Cửa Hàng"
    assert customer["phone"] == "0987654321"
    assert "hà nội" in customer["location"].lower()


def test_resolve_page_access_tokens_falls_back_to_page_lookup_without_page_id():
    pages = [{"id": "101", "name": "Page One"}, {"id": "202", "name": "Page Two"}]

    def fake_fetch(endpoint, token, extra_params=None):
        if endpoint == "me/accounts":
            return {"data": pages}
        if endpoint == "101":
            return {"id": "101", "access_token": "page_token_101"}
        if endpoint == "202":
            return {"id": "202", "access_token": "page_token_202"}
        raise AssertionError(f"Unexpected endpoint: {endpoint}")

    resolved = resolve_page_access_tokens("system_user_token", pages, fake_fetch)

    assert resolved[0]["access_token"] == "page_token_101"
    assert resolved[1]["access_token"] == "page_token_202"


def test_facebook_token_can_be_loaded_from_database_settings():
    from app import db, Setting

    with app.app_context():
        Setting.query.filter_by(key='FACEBOOK_PAGE_ACCESS_TOKEN').delete()
        db.session.add(Setting(key='FACEBOOK_PAGE_ACCESS_TOKEN', value='db_token_123'))
        db.session.commit()

        assert get_setting_value('FACEBOOK_PAGE_ACCESS_TOKEN') == 'db_token_123'

        Setting.query.filter_by(key='FACEBOOK_PAGE_ACCESS_TOKEN').delete()
        db.session.commit()


def test_fetch_facebook_json_keeps_pagination_query_string_valid():
    import unittest.mock as mock

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"data": []}'

    def fake_urlopen(request, timeout=25):
        captured['url'] = request.full_url
        return FakeResponse()

    next_url = 'https://graph.facebook.com/v19.0/123/conversations?fields=participants{id,name}&limit=25&after=abc'
    with mock.patch('app.urlopen', side_effect=fake_urlopen):
        result = fetch_facebook_json(next_url, 'token_123', {'limit': '25'})

    assert result == {'data': []}
    assert captured['url'].count('?') == 1
    assert 'after=abc' in captured['url']
    assert 'access_token=token_123' in captured['url']


def test_resolve_facebook_pages_accepts_page_access_token_without_me_accounts():
    calls = []

    def fake_fetch(endpoint, token, extra_params=None):
        calls.append((endpoint, token, extra_params))
        if endpoint == "me":
            return {"id": "page_999", "name": "Page Demo"}
        raise AssertionError(f"Unexpected endpoint: {endpoint}")

    resolved = resolve_facebook_pages("page_token_only", fake_fetch)

    assert resolved == [{"id": "page_999", "name": "Page Demo", "access_token": "page_token_only"}]
    assert calls[0][0] == "me"


def test_fetch_managed_excludes_page_id_from_customer():
    """When latest message sender is the page, customer should come from participants."""
    import unittest.mock as mock
    from app import fetch_managed_facebook_messages

    page_id = "PAGE_111"
    conversations_response = {
        "data": [
            {
                "participants": {"data": [
                    {"id": page_id, "name": "My Page"},
                    {"id": "CUST_A", "name": "Alice"},
                ]},
                "messages": {"data": [
                    {"from": {"id": page_id, "name": "My Page"}, "message": "Cảm ơn bạn", "created_time": "2026-08-01T10:00:00+0000"},
                    {"from": {"id": "CUST_A", "name": "Alice"}, "message": "Số tôi 0912345678", "created_time": "2026-07-31T10:00:00+0000"},
                ]},
            },
            {
                "participants": {"data": [
                    {"id": page_id, "name": "My Page"},
                    {"id": "CUST_B", "name": "Bob"},
                ]},
                "messages": {"data": [
                    {"from": {"id": page_id, "name": "My Page"}, "message": "Chào bạn", "created_time": "2026-08-02T10:00:00+0000"},
                    {"from": {"id": "CUST_B", "name": "Bob"}, "message": "SĐT 0988777666", "created_time": "2026-08-01T10:00:00+0000"},
                ]},
            },
        ],
        "paging": {},
    }

    def fake_fetch(endpoint, token, extra_params=None):
        if endpoint == "me/accounts":
            return {"data": [{"id": page_id, "name": "My Page", "access_token": "tok"}]}
        if endpoint == f"{page_id}/conversations":
            return conversations_response
        if endpoint == page_id:
            return {"id": page_id, "name": "My Page", "access_token": "tok"}
        return {}

    with mock.patch("app.fetch_facebook_json", side_effect=fake_fetch), \
         mock.patch.dict("os.environ", {"FACEBOOK_PAGE_ACCESS_TOKEN": "tok"}):
        results = fetch_managed_facebook_messages(max_pages=1, max_conversations_per_page=10)

    assert len(results) == 2
    ids = {r["facebook_id"] for r in results}
    # Both customers should have distinct IDs, neither should be the page ID
    assert page_id not in ids
    assert "CUST_A" in ids
    assert "CUST_B" in ids


def test_import_does_not_merge_different_fb_users_with_same_name():
    """Two FB users with same name but different facebook_id → 2 records."""
    import unittest.mock as mock
    from app import db, Customer, import_facebook_messages

    messages = [
        {"name": "Nguyễn Văn A", "facebook_id": "FB_001", "message": "Xin chào", "message_date": "2026-08-01T10:00:00+0000", "page_name": "Shop"},
        {"name": "Nguyễn Văn A", "facebook_id": "FB_002", "message": "Tôi cần mua", "message_date": "2026-08-02T10:00:00+0000", "page_name": "Shop"},
    ]

    with app.app_context():
        # Clean slate
        Customer.query.filter(Customer.facebook_id.in_(["FB_001", "FB_002"])).delete()
        Customer.query.filter(Customer.name == "Nguyễn Văn A", Customer.source == "facebook").delete()
        db.session.commit()

        imported, updated = import_facebook_messages(messages)

        customers = Customer.query.filter(Customer.facebook_id.in_(["FB_001", "FB_002"])).all()
        assert len(customers) == 2, f"Expected 2 distinct customers, got {len(customers)}"
        assert imported == 2
        assert updated == 0

        # Cleanup
        for c in customers:
            db.session.delete(c)
        db.session.commit()


def test_conversations_with_image_only_not_skipped():
    """Conversations where latest message is image-only but earlier message has phone → imported."""
    import unittest.mock as mock
    from app import fetch_managed_facebook_messages

    page_id = "PAGE_222"
    conversations_response = {
        "data": [
            {
                "participants": {"data": [
                    {"id": page_id, "name": "My Page"},
                    {"id": "CUST_IMG", "name": "Hình Ảnh User"},
                ]},
                "messages": {"data": [
                    {"from": {"id": "CUST_IMG", "name": "Hình Ảnh User"}, "message": "", "created_time": "2026-08-01T10:00:00+0000"},
                    {"from": {"id": "CUST_IMG", "name": "Hình Ảnh User"}, "message": "SĐT tôi 0966111222", "created_time": "2026-07-31T10:00:00+0000"},
                ]},
            },
        ],
        "paging": {},
    }

    def fake_fetch(endpoint, token, extra_params=None):
        if endpoint == "me/accounts":
            return {"data": [{"id": page_id, "name": "My Page", "access_token": "tok"}]}
        if endpoint == f"{page_id}/conversations":
            return conversations_response
        if endpoint == page_id:
            return {"id": page_id, "name": "My Page", "access_token": "tok"}
        return {}

    with mock.patch("app.fetch_facebook_json", side_effect=fake_fetch), \
         mock.patch.dict("os.environ", {"FACEBOOK_PAGE_ACCESS_TOKEN": "tok"}):
        results = fetch_managed_facebook_messages(max_pages=1, max_conversations_per_page=10)

    assert len(results) == 1
    assert results[0]["facebook_id"] == "CUST_IMG"
    assert results[0]["phone"] == "0966111222"


def test_build_customer_splits_name_and_maps_new_fields():
    """build_customer_from_message maps profile_pic, gender, locale, conversation_id, message_count."""
    from app import build_customer_from_message

    payload = {
        "name": "Nguyễn Văn Bình",
        "facebook_id": "999",
        "message": "Hello",
        "message_date": "2026-08-14T10:00:00+0000",
        "page_name": "Shop",
        "profile_pic": "https://example.com/pic.jpg",
        "gender": "male",
        "locale": "vi_VN",
        "conversation_id": "conv_123",
        "message_count": 5,
    }

    result = build_customer_from_message(payload)
    assert result["first_name"] == "Bình"
    assert result["last_name"] == "Nguyễn Văn"
    assert result["profile_pic"] == "https://example.com/pic.jpg"
    assert result["gender"] == "male"
    assert result["locale"] == "vi_VN"
    assert result["conversation_id"] == "conv_123"
    assert result["message_count"] == 5


def test_import_dedup_by_conversation_id():
    """Same conversation_id → update, not duplicate."""
    from app import db, Customer, import_facebook_messages

    messages = [
        {"name": "User A", "facebook_id": "U1", "conversation_id": "CONV_X", "message": "Hi", "message_date": "2026-08-14T10:00:00+0000", "page_name": "Shop"},
        {"name": "User A Updated", "facebook_id": "U1", "conversation_id": "CONV_X", "message": "Bye", "message_date": "2026-08-14T11:00:00+0000", "page_name": "Shop"},
    ]

    with app.app_context():
        Customer.query.filter(Customer.conversation_id == "CONV_X").delete()
        db.session.commit()

        imported, updated = import_facebook_messages(messages)
        assert imported == 1
        assert updated == 1

        customers = Customer.query.filter(Customer.conversation_id == "CONV_X").all()
        assert len(customers) == 1
        assert customers[0].name == "User A Updated"

        db.session.delete(customers[0])
        db.session.commit()


def test_fetch_managed_facebook_messages_uses_safe_default_limit():
    """Default sync should not process an unbounded conversation list and trigger worker timeouts."""
    import unittest.mock as mock
    from app import fetch_managed_facebook_messages

    page_id = "PAGE_SAFE"
    conversations = []
    for index in range(60):
        conversations.append({
            "id": f"CONV_{index}",
            "participants": {"data": [
                {"id": page_id, "name": "My Page"},
                {"id": f"CUST_{index}", "name": f"Customer {index}"},
            ]},
            "messages": {"data": [
                {"from": {"id": f"CUST_{index}", "name": f"Customer {index}"}, "message": f"Hello {index}", "created_time": "2026-08-01T10:00:00+0000"},
                {"from": {"id": f"CUST_{index}", "name": f"Customer {index}"}, "message": f"Gọi tôi 09{70000000 + index}", "created_time": "2026-07-31T10:00:00+0000"},
            ]},
        })

    def fake_fetch(endpoint, token, extra_params=None):
        if endpoint == "me/accounts":
            return {"data": [{"id": page_id, "name": "My Page", "access_token": "tok"}]}
        if endpoint == f"{page_id}/conversations":
            return {"data": conversations, "paging": {}}
        return {}

    with mock.patch("app.fetch_facebook_json", side_effect=fake_fetch), \
         mock.patch.dict("os.environ", {"FACEBOOK_PAGE_ACCESS_TOKEN": "tok"}, clear=False):
        results = fetch_managed_facebook_messages()

    assert len(results) == 25


def test_fetch_managed_facebook_messages_scans_multiple_pages_and_filters_phone_and_location():
    import unittest.mock as mock
    from app import fetch_managed_facebook_messages

    page_one = "PAGE_ONE"
    page_two = "PAGE_TWO"

    def page_conversations(page_id, customer_name, customer_id, phone, location):
        return {
            "data": [{
                "id": f"CONV_{page_id}",
                "participants": {"data": [
                    {"id": page_id, "name": "My Page"},
                    {"id": customer_id, "name": customer_name},
                ]},
                "messages": {"data": [
                    {"from": {"id": customer_id, "name": customer_name}, "message": f"Xin chào, tôi ở {location}, sđt {phone}", "created_time": "2026-08-01T10:00:00+0000"},
                ]},
            }],
            "paging": {},
        }

    def fake_fetch(endpoint, token, extra_params=None):
        if endpoint == "me/accounts":
            return {"data": [
                {"id": page_one, "name": "Page One", "access_token": "tok_one"},
                {"id": page_two, "name": "Page Two", "access_token": "tok_two"},
            ]}
        if endpoint == f"{page_one}/conversations":
            return page_conversations(page_one, "Khách A", "CUST_A", "0987654321", "Hà Nội")
        if endpoint == f"{page_two}/conversations":
            return page_conversations(page_two, "Khách B", "CUST_B", "0911222333", "Đà Nẵng")
        return {}

    with mock.patch("app.fetch_facebook_json", side_effect=fake_fetch), \
         mock.patch.dict("os.environ", {"FACEBOOK_PAGE_ACCESS_TOKEN": "tok"}, clear=False):
        results = fetch_managed_facebook_messages(max_pages=5, max_conversations_per_page=10)

    assert len(results) == 2
    assert {r["facebook_id"] for r in results} == {"CUST_A", "CUST_B"}
    assert any(r["phone"] == "0987654321" and "hà nội" in (r["location"] or '').lower() for r in results)
    assert any(r["phone"] == "0911222333" and "đà nẵng" in (r["location"] or '').lower() for r in results)


def test_fetch_managed_facebook_messages_skips_conversations_without_phone():
    import unittest.mock as mock
    from app import fetch_managed_facebook_messages

    page_id = "PAGE_NO_PHONE"
    conversations = [{
        "id": "CONV_EMPTY",
        "participants": {"data": [
            {"id": page_id, "name": "My Page"},
            {"id": "CUST_X", "name": "Khách không số"},
        ]},
        "messages": {"data": [
            {"from": {"id": "CUST_X", "name": "Khách không số"}, "message": "Xin chào tôi ở quận 7", "created_time": "2026-08-01T10:00:00+0000"},
        ]},
    }]

    def fake_fetch(endpoint, token, extra_params=None):
        if endpoint == "me/accounts":
            return {"data": [{"id": page_id, "name": "My Page", "access_token": "tok"}]}
        if endpoint == f"{page_id}/conversations":
            return {"data": conversations, "paging": {}}
        return {}

    with mock.patch("app.fetch_facebook_json", side_effect=fake_fetch), \
         mock.patch.dict("os.environ", {"FACEBOOK_PAGE_ACCESS_TOKEN": "tok"}, clear=False):
        results = fetch_managed_facebook_messages(max_pages=1, max_conversations_per_page=10)

    assert results == []


def test_sync_does_not_request_customer_profiles_by_default():
    """PSID profile requests cause Graph API HTTP 400 for normal Page tokens."""
    import unittest.mock as mock
    from app import fetch_managed_facebook_messages

    page_id = "PAGE_PROFILE"
    customer_id = "34683375887973748"
    calls = []

    def fake_fetch(endpoint, token, extra_params=None):
        calls.append(endpoint)
        if endpoint == "me/accounts":
            return {"data": [{"id": page_id, "name": "My Page", "access_token": "tok"}]}
        if endpoint == f"{page_id}/conversations":
            return {"data": [{
                "id": "CONV_PROFILE",
                "participants": {"data": [
                    {"id": page_id, "name": "My Page"},
                    {"id": customer_id, "name": "Customer"},
                ]},
                "messages": {"data": [{
                    "from": {"id": customer_id, "name": "Customer"},
                    "message": "Số điện thoại 0987654321",
                    "created_time": "2026-08-01T10:00:00+0000",
                }]},
            }], "paging": {}}
        if endpoint == customer_id:
            raise AssertionError("Customer profile endpoint must not be called")
        return {}

    with mock.patch("app.fetch_facebook_json", side_effect=fake_fetch), \
         mock.patch.dict("os.environ", {"FACEBOOK_PAGE_ACCESS_TOKEN": "tok"}, clear=False):
        results = fetch_managed_facebook_messages(max_pages=1, max_conversations_per_page=10)

    assert len(results) == 1
    assert customer_id not in calls
