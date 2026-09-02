# Hướng dẫn Deploy CRM HAY lên Koyeb (Thay thế Render)

## 1. Ưu điểm của Koyeb
- Khởi động nhanh (fast startup / no prolonged cold starts).
- Tự động cấp **HTTPS (SSL/TLS)** miễn phí.
- Hỗ trợ deploy từ **GitHub repo** hoặc **Dockerfile**.
- Region Singapore (`sin`) latency thấp cho Việt Nam.

---

## 2. Các bước Deploy trên Koyeb

### Bước 1: Đăng ký & Kết nối GitHub
1. Đăng ký tài khoản tại [Koyeb Console](https://app.koyeb.com/).
2. Kết nối tài khoản GitHub với Koyeb.

### Bước 2: Tạo Service mới
1. Click **Create Service**.
2. Chọn **GitHub** làm nguồn.
3. Chọn Repository `CRM HAY` (branch `main`).
4. Koyeb sẽ tự động nhận diện `Dockerfile` hoặc `koyeb.yaml`.
5. Chọn Region: **Singapore (sin)** để đạt tốc độ tốt nhất tại Việt Nam.

### Bước 3: Cấu hình Environment Variables (Biến môi trường)
Thêm các biến môi trường trong Koyeb Console:

| Key | Value (Mẫu) | Ghi chú |
| :--- | :--- | :--- |
| `CRM_SECRET_KEY` | `chuoibaomatngaunhien123` | Bắt buộc cho production |
| `DATABASE_URL` | `postgresql://user:pass@ep-xxx.neon.tech/dbname?sslmode=require` | PostgreSQL từ Supabase/Neon |
| `FACEBOOK_PAGE_ACCESS_TOKEN` | `EAAG...` | Token Fanpage (nếu có) |
| `FLASK_ENV` | `production` | Chế độ production |

### Bước 4: Deploy & Lấy URL
1. Click **Deploy**.
2. Sau khi build hoàn tất (~2 phút), Koyeb sẽ cấp URL: `https://<app-name>-<org>.koyeb.app`.

---

## 3. Cập nhật Android App & Frontend Mobile
Sau khi có URL mới từ Koyeb:
1. Sửa `BASE_URL` trong `mobile-sales/app/build.gradle.kts`:
   ```kotlin
   buildConfigField("String", "BASE_URL", "\"https://your-app-name.koyeb.app\"")
   ```
2. Build lại APK debug/release.