from app import build_customer_from_message, extract_phone_numbers, extract_location


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
