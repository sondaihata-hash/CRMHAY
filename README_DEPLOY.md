# CRM Facebook - Deployment Guide (Render + Mắt Bào)

Hướng dẫn chi tiết deploy CRM lên Render và cấu hình domain `crmhay.cloud`.

## Bước 1: Deploy lên Render

> Bản `render.yaml` hiện tạo sẵn PostgreSQL `crmhay-db` và một tác vụ quét
> Facebook tự động vào đầu mỗi khoảng 3 giờ. PostgreSQL là nơi lưu dữ liệu
> bền vững: không dùng SQLite nội bộ của Render vì nó bị mất sau redeploy/restart.
> Sau mỗi lần quét, ứng dụng cũng xuất `customers-backup.json` để có bản sao
> phục hồi. Trên Render, bản JSON này chỉ là bản sao; dữ liệu chính là PostgreSQL.

### 1.1 Commit và push Procfile
Ensure `Procfile` và `requirements.txt` đã push lên GitHub:

```bash
git add Procfile requirements.txt
git commit -m "Add deployment config"
git push
```

### 1.2 Tạo Web Service trên Render
1. Đăng nhập [Render](https://render.com)
2. Dashboard → **New** → **Web Service**
3. Connect GitHub → chọn repo `sondaihata-hash/CRMHAY`
4. Điền:
   - **Name**: tên service (VD: `crmhay` hoặc `crmhay-demo`)
   - **Branch**: `main`
   - **Build Command**: (để trống — Render tự detect `pip install`)
   - **Start Command**: `gunicorn app:app --bind 0.0.0.0:$PORT`
   - **Instance Type**: Free (OK cho test)
5. Click **Create Web Service** → chờ deploy (2–3 phút)

### 1.3 Sau deploy thành công
- Render sẽ cung cấp URL tạm: `https://your-service-name.onrender.com`
- Ghi lại **tên service** (VD: `crmhay`) — bạn sẽ dùng trong bước DNS.

**Checkpoint**: Mở `https://your-service-name.onrender.com` trong trình duyệt → kiểm tra CRM chạy được chưa.

### 1.4 Bổ sung nơi ở từ Facebook Profile (tuỳ quyền ứng dụng)

CRM luôn ưu tiên nơi ở khách tự ghi trong tin nhắn. Với khách không ghi nơi ở,
có thể bật fallback từ Facebook Profile API bằng biến môi trường:

```text
FACEBOOK_FETCH_PROFILE=true
```

Trên Render, thêm biến này tại **Environment** của Web Service rồi redeploy.
Ứng dụng sẽ yêu cầu thêm trường `location{name}` khi đọc hồ sơ khách và chỉ dùng
dữ liệu đó nếu tin nhắn không có nơi ở. Tính năng này cần token/Page và ứng dụng
Facebook được cấp quyền phù hợp; nếu Facebook trả lỗi quyền, CRM vẫn đồng bộ
khách bình thường và để trống nơi ở, không tự đoán theo số điện thoại.

---

## Bước 2: Cấu hình DNS (Domain Pointing)

Bạn có 2 lựa chọn quản lý DNS:

### **Lựa Chọn A: Dùng Mắt Bào DNS (đơn giản, nhanh)**

1. Đăng nhập [Mắt Bào](https://manage.matbao.net)
2. Chọn domain `crmhay.cloud` → tab **Bản ghi DNS**
3. Tạo record CNAME:
   - **Type**: CNAME
   - **Host**: `www`
   - **Value**: `your-service-name.onrender.com` (thay thế `your-service-name`)
   - **TTL**: 3600
   - Click **Lưu**

4. (Optional) Trỏ apex (`crmhay.cloud`):
   - Tạo A record hoặc ANAME (nếu Mắt Bào hỗ trợ), hoặc
   - Sử dụng redirect từ apex → www (trong DNS management Mắt Bào)
   - Hoặc dùng Cloudflare (Lựa Chọn B)

5. **Chờ DNS propagate** (5–30 phút)

### **Lựa Chọn B: Dùng Cloudflare DNS (flexible, miễn phí)**

1. [Tạo tài khoản Cloudflare](https://www.cloudflare.com) (nếu chưa có)
2. Add site → chọn `crmhay.cloud`
3. Cloudflare sẽ cung cấp 2 Nameservers (VD: `xxx.ns.cloudflare.com`, `yyy.ns.cloudflare.com`)
4. Đăng nhập Mắt Bào → `crmhay.cloud` → tab **Name Server**
   - Chọn **Sử dụng Name Server tuỳ chỉnh**
   - Nhập 2 nameserver của Cloudflare
   - Click **Lưu thay đổi**
   - **Chờ propagate** (có thể mất 24h)

5. Trở lại Cloudflare → DNS → tạo records:
   - **CNAME** cho www:
     - Type: CNAME
     - Name: www
     - Target: `your-service-name.onrender.com`
     - Proxy: OFF (chọn DNS only — gray cloud ⚪, không orange)
   - **(Optional) CNAME cho apex:**
     - Type: CNAME
     - Name: @
     - Target: `your-service-name.onrender.com`
     - Proxy: OFF (DNS only)
   - Click **Save** và chờ propagate

---

## Bước 3: Thêm Custom Domain vào Render Service

1. Trên Render → chọn Web Service của bạn → tab **Settings**
2. **Custom Domains** → **Add Custom Domain**
3. Nhập domain:
   - `www.crmhay.cloud` (nếu dùng www)
   - hoặc `crmhay.cloud` (apex — nếu DNS hỗ trợ)
4. Click **Add** → Render sẽ hiển thị trạng thái
5. Click **Verify DNS** (hoặc chờ tự động verify)
6. Khi xác nhận ✓ → Render tự cấp **HTTPS/TLS certificate** (tự động, ~5 phút)

---

## Bước 4: Kiểm tra

- [ ] Mở trình duyệt → `http://www.crmhay.cloud` hoặc `https://crmhay.cloud`
- [ ] Kiểm tra CRM load được, không lỗi
- [ ] Kiểm tra HTTPS (nếu trực tiếp gõ http sẽ redirect sang https)
- [ ] Thử các tính năng: thêm khách hàng, vào Settings → xóa, sửa

---

## Troubleshooting

| Vấn đề | Cách xử lý |
|--------|-----------|
| DNS không resolve | Chờ 24h, hoặc kiểm tra Nameserver đúng, kiểm tra record DNS |
| SSL certificate lỗi | Kiểm tra Cloudflare proxy = DNS only (gray), không orange. Nếu vẫn lỗi khởi động lại domain trên Render. |
| 502 Bad Gateway | Kiểm tra app đang chạy (Render logs), hoặc kết nối database. |
| Render sleep | Gói Free Render tự động sleep sau 15 phút idle. Deploy service luôn mở có phí. |

---

## Switching to New Domain (Sau này)

Khi muốn đổi domain mới (VD: crmhay.vn):

1. **Mua domain mới** (hoặc cấu hình sẵn có)
2. **Render**: Custom Domains → Add `www.crmhay.vn`
3. **DNS mới**: Thêm CNAME/A record trỏ tới Render
4. **Chờ propagate** (5–30 phút)
5. **Render**: Remove domain cũ nếu muốn (tuỳ chọn — để vậy vẫn chạy)
6. **Done** — không cần deploy lại

---

## Next Steps

- [ ] Deploy xong, kiểm tra trên https://www.sslshopper.com/ssl-checker.html để xác nhận certificate
- [ ] Kiểm tra PostgreSQL `crmhay-db` và cron `crmhay-facebook-sync` đã được tạo từ Blueprint
- [ ] Thêm authentication/login nếu muốn (bảo vệ CRM)
- [ ] Tích hợp Facebook API khi sẵn sàng

---

## Tham khảo

- [Render Docs](https://render.com/docs)
- [Cloudflare DNS](https://www.cloudflare.com/learning/dns/)
- [Mắt Bào Support](https://support.matbao.vn)
