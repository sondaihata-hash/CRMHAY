# CRMHAY - CRM quản lý lead và bán hàng

Ứng dụng CRM cho đội Sales: quản lý lead, phân công khách hàng, nhắc việc,
đơn hàng và đồng bộ hội thoại Facebook.

Yêu cầu:
- Python 3.11+
- PostgreSQL cho môi trường production
- Redis nếu chạy đồng bộ Facebook bằng worker Celery

Cài đặt và chạy:

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Mở trình duyệt `http://127.0.0.1:5000`

Ghi chú:
## Chạy test

```bash
pytest -q
```

## Production

Không dùng SQLite hoặc `dev-secret` cho môi trường public. Xem
[README_DEPLOY.md](README_DEPLOY.md) để cấu hình PostgreSQL, secret, backup,
worker và health check.

## Mobile

Mã nguồn Capacitor nằm trong thư mục `../crmhay-mobile` khi checkout theo cấu
trúc workspace hiện tại. Quy trình build Android được mô tả trong
[APP_MOBILE_SETUP.md](APP_MOBILE_SETUP.md).
