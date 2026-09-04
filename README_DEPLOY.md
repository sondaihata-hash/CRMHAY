# CRM Facebook - Deployment Guide (Render + Mắt Bào)

Hướng dẫn chi tiết deploy CRM lên Render và cấu hình domain `crmhay.cloud`.

## Chạy CRM trên chính PC Windows

PC cần bật liên tục, không sleep, và nên dùng Cloudflare Tunnel để domain
truy cập được từ Internet mà không phải mở port trên router.

### 1. Cài và chạy ứng dụng cục bộ

Mở PowerShell tại thư mục dự án:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:CRM_SECRET_KEY = '<chuoi-ngau-nhien-it-nhat-32-ky-tu>'
$env:CRM_ADMIN_USERNAME = '<tai-khoan-admin>'
$env:CRM_ADMIN_PASSWORD = '<mat-khau-manh>'
.\scripts\start-vps.ps1
```

Kiểm tra trên PC bằng `http://127.0.0.1:5000`. Script dùng Waitress thay cho
`gunicorn` vì gunicorn không chạy native trên Windows.

### 2. Đưa domain về PC bằng Cloudflare Tunnel

1. Cài `cloudflared` trên PC và đăng nhập: `cloudflared tunnel login`.
2. Tạo tunnel: `cloudflared tunnel create crmhay`.
3. Tạo file cấu hình `C:\Users\<user>\.cloudflared\config.yml`:

```yaml
tunnel: <tunnel-uuid>
credentials-file: C:\Users\<user>\.cloudflared\<tunnel-uuid>.json
ingress:
  - hostname: crmhay.cloud
    service: http://127.0.0.1:5000
  - hostname: www.crmhay.cloud
    service: http://127.0.0.1:5000
  - service: http_status:404
```

4. Trỏ DNS tunnel:

```powershell
cloudflared tunnel route dns crmhay crmhay.cloud
cloudflared tunnel route dns crmhay www.crmhay.cloud
cloudflared tunnel run crmhay
```

Trong DNS provider hiện tại, xóa record `www` đang trỏ tới
`crmhay.onrender.com`; Cloudflare sẽ tạo CNAME tới tunnel. Chỉ đổi DNS sau khi
ứng dụng đã chạy và kiểm tra được ở `127.0.0.1:5000`.

## Bước 1: Deploy lên Render

> Bản `render.yaml` chỉ tạo Web Service để tương thích Render Free. Hãy dùng
> một PostgreSQL bên ngoài (Neon, Supabase hoặc Render Database trả phí) và
> đặt connection string vào `DATABASE_URL`. PostgreSQL là nơi lưu dữ liệu
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

### 1.4 Tạo tài khoản Admin lần đầu

Trước khi deploy, thêm các biến môi trường sau trong Render Web Service:

```text
CRM_SECRET_KEY=<chuỗi ngẫu nhiên dài, tối thiểu 32 ký tự>
CRM_ADMIN_USERNAME=<tên đăng nhập Admin>
CRM_ADMIN_PASSWORD=<mật khẩu mạnh, tối thiểu 8 ký tự>
CRM_HOTLINE_NUMBERS=0707866676,0901234567,0281234567
```

Lần khởi động đầu tiên sẽ tạo tài khoản Admin từ các biến này. Mật khẩu chỉ
được lưu dưới dạng hash trong database. Sau khi đăng nhập và tạo các tài khoản
Sales, có thể xoá hoặc thay đổi biến `CRM_ADMIN_PASSWORD`; ứng dụng không ghi
đè tài khoản Admin đã tồn tại.

Sales đăng nhập tại cùng địa chỉ CRM. Admin vào **Tài khoản Sales** để tạo,
khóa/mở khóa tài khoản và vào hồ sơ khách để phân công. Khách chưa phân công
chỉ hiển thị cho Admin; khi chuyển khách, Sales cũ mất quyền xem khách và các
đơn hàng lịch sử của khách đó.

`CRM_HOTLINE_NUMBERS` là danh sách số tổng đài/số cửa hàng, phân cách bằng dấu
phẩy. Khi đồng bộ Facebook, CRM chỉ đọc số trong message có sender là khách và
loại các số trong danh sách hotline. Mỗi lần ứng dụng khởi động, CRM cũng tự
dọn các số hotline từng bị lưu nhầm trên hồ sơ khách. Nếu số hotline thay đổi,
cập nhật biến môi trường rồi khởi động lại dịch vụ hoặc chạy lại **Đồng bộ dữ
liệu Facebook**.

Mặc định CRM quét lần lượt toàn bộ cuộc hội thoại và tự đi qua tất cả các trang
phân trang của Facebook cho mọi Page đã tìm thấy. CRM lấy 25 cuộc hội thoại mỗi
lượt gọi API; các tin nhắn trong từng hội thoại vẫn được phân trang đầy đủ.
Nếu cần giới hạn một lần chạy vì quota hoặc chạy thử, có thể đặt
`FACEBOOK_SYNC_CONVERSATION_LIMIT`, `FACEBOOK_SYNC_PAGE_LIMIT` (tối đa 20) hoặc
`FACEBOOK_SYNC_API_CALL_LIMIT` (tối đa 10.000) trong Environment. Khi không đặt
các biến này, không có giới hạn nhân tạo theo số cuộc hội thoại hay số API call.

### 1.5 Đặt lại mật khẩu Admin trên Render Free

Nếu Admin đã tồn tại và quên mật khẩu, vào **Render → Web Service → Environment**
và thêm tạm thời:

```bash
CRM_ADMIN_RESET_PASSWORD=<mat-khau-moi>
```

Sau khi deploy lại, đăng nhập bằng mật khẩu mới rồi **xoá ngay** biến
`CRM_ADMIN_RESET_PASSWORD` và deploy lại lần nữa. Biến này chỉ áp dụng cho
username trong `CRM_ADMIN_USERNAME`.

Không cần Render Shell. Nếu username là `admin`, cấu hình sẽ gồm:

```bash
CRM_ADMIN_USERNAME=admin
CRM_ADMIN_RESET_PASSWORD=<mat-khau-moi>
```

### 1.6 Bổ sung nơi ở từ Facebook Profile (tuỳ quyền ứng dụng)

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

### Kết nối nhóm Zalo cho Sale

CRM không thể tự ý thêm thành viên hoặc gửi tin vào nhóm Zalo cá nhân thông qua
Zalo cá nhân. Zalo OA/Open API cũng chỉ hỗ trợ các nghiệp vụ được cấp quyền,
không phải API gửi tuỳ ý vào mọi nhóm cá nhân.

Quy trình được hỗ trợ ổn định:

1. Mở nhóm Zalo cần nhận lead và sao chép link mời/link mở nhóm.
2. Vào **Nhóm Sales Zalo**, dán link vào nhóm tương ứng rồi bấm **Lưu**.
3. Vào hồ sơ khách, chọn nhóm Sales và bấm **Sao chép & mở Zalo**.
4. CRM ghi lịch sử chuyển khách, sao chép nội dung lead và mở đúng link nhóm;
   Sale chỉ cần dán và gửi.

Link nhóm phải có dạng `https://zalo.me/g/...`. Nếu không cấu hình link, CRM
vẫn mở Zalo chung như trước.

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
- [ ] Kiểm tra `DATABASE_URL` trỏ tới PostgreSQL bên ngoài và không dùng SQLite production
- [ ] Kiểm tra đăng nhập Admin và tạo tài khoản Sales
- [ ] Tích hợp Facebook API khi sẵn sàng

---

## Tham khảo

- [Render Docs](https://render.com/docs)
- [Cloudflare DNS](https://www.cloudflare.com/learning/dns/)
- [Mắt Bào Support](https://support.matbao.vn)
