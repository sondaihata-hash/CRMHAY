import os

from app import app, build_customer_from_message, extract_phone_numbers, extract_location, resolve_page_access_tokens, resolve_facebook_pages


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
