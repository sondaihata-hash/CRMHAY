# Hướng dẫn sử dụng CRM HAY cho Admin

## 1. Đăng nhập Admin

### Thông tin đăng nhập
- URL: https://crmhay.cloud/login
- Username: được set trong biến môi trường `CRM_ADMIN_USERNAME`
- Password: được set trong biến môi trường `CRM_ADMIN_PASSWORD`

> **Lưu ý**: Thay đổi mật khẩu ngay sau lần đăng nhập đầu tiên

---

## 2. Quản lý Tài khoản Sales

### Vào mục Tài khoản Sales
- Menu chính → Tài khoản Sales

### Tạo tài khoản Sales mới
1. Bấm "Tạo tài khoản mới"
2. Nhập username (không dấu, chữ thường)
3. Tạo password mạnh (tối thiểu 8 ký tự)
4. Bấm "Tạo"
5. Ghi lại username và password để cấp cho sales

### Khóa/Mở khóa tài khoản
1. Tìm sales trong danh sách
2. Bấm "Khóa" nếu sales nghỉ việc
3. Bấm "Mở khóa" khi sales quay lại

### Xóa tài khoản
- Khóa tài khoản thay vì xóa (giữ dữ liệu lịch sử)

---

## 3. Quản lý Khách hàng

### Xem danh sách khách
- Menu → Khách hàng
- Sắp xếp theo:
  - **Mới nhất**: khách được cập nhật gần đây nhất trước
  - **Theo ngày**: khách sắp xếp theo ngày cập nhật lịch sử
  - **Theo page**: nhóm khách hàng theo page/fanpage nguồn

### Thêm khách hàng thủ công
1. Menu → Khách hàng → Thêm khách hàng
2. Nhập thông tin:
   - Họ tên
   - Số điện thoại
   - Email
   - Địa chỉ
   - Lưu ý
   - Page/fanpage nguồn
3. Bấm "Lưu"

### Chỉnh sửa khách hàng
1. Tìm khách trong danh sách
2. Bấm vào hồ sơ khách
3. Sửa thông tin
4. Bấm "Lưu"

### Xác nhận khách hàng
- CRM sẽ tự động loại bỏ các số điện thoại là hotline
- Admin có thể set danh sách hotline trong Settings

---

## 4. Quản lý Đơn hàng

### Xem danh sách đơn hàng
- Menu → Đơn hàng
- Hiển thị tất cả đơn của tất cả sales

### Thêm đơn hàng
1. Tìm khách hàng
2. Vào chi tiết khách
3. Bấm "Thêm đơn hàng"
4. Nhập:
   - Mã đơn
   - Tổng tiền
   - Trạng thái (Mới / Đang xử lý / Hoàn tất / Hủy)
   - Ghi chú
5. Bấm "Lưu"

### Cập nhật trạng thái đơn
1. Mở đơn hàng
2. Thay đổi trạng thái
3. Bấm "Cập nhật"

---

## 5. Quản lý Nhắc việc

### Xem nhắc việc
- Menu → Nhắc việc
- Hiển thị các việc đang chờ và đã hoàn tất

### Admin có thể
- Xem tất cả nhắc việc của toàn team
- Kiểm tra sales nào chưa follow-up
- Đánh dấu hoàn tất theo dõi

---

## 6. Quản lý Nhóm Sales (tùy chọn)

### Dùng khi có nhiều sales
- Menu → Nhóm Sales
- Tạo nhóm để phân loại sales (theo khu vực, sản phẩm, v.v.)

### Tính năng
- Gom khách hàng theo nhóm
- Báo cáo theo nhóm
- Giao việc theo nhóm

---

## 7. Settings (Cài đặt)

### Quản lý danh sách hotline
1. Menu → Settings
2. Vào mục "Hotline cần loại bỏ"
3. Nhập các số hotline (mỗi dòng 1 số)
4. Bấm "Lưu"
5. CRM sẽ tự động loại khách có số hotline khi import Facebook

### Kết nối Zalo (nếu có)
- Thiết lập Zalo OA token (nếu muốn tích hợp OA)
- Lưu ý: PA 1 không dùng OA, chỉ dùng cá nhân

---

## 8. Báo cáo & Thống kê

### Dashboard chính
- Hiển thị:
  - Tổng khách hàng
  - Khách có số điện thoại
  - Tổng đơn hàng
  - Doanh thu tháng này
  - Nguồn khách hàng (biểu đồ)
  - Trạng thái đơn hàng

### Xuất dữ liệu (nếu cần)
- Hiện tại CRM chưa có function xuất, nhưng dữ liệu lưu trong database
- Admin có thể request xuất từ database trực tiếp

---

## 9. Nguyên tắc quản lý tốt

### Ngày đầu
- ✅ Tạo tài khoản cho 2-3 sales để test
- ✅ Đặt danh sách hotline
- ✅ Thêm 10-20 khách test
- ✅ Test flow: khách → CRM → nhắc việc → đơn hàng

### Tuần đầu
- ✅ Cấp tài khoản cho toàn team
- ✅ Import khách hàng từ Facebook nếu có
- ✅ Hướng dẫn sales sử dụng

### Hàng tuần
- ✅ Kiểm tra số liệu dashboard
- ✅ Xem sales nào chưa follow-up
- ✅ Cập nhật hotline list nếu cần
- ✅ Hỗ trợ sales khi có vấn đề

---

## 10. Troubleshooting

### Khách hàng không hiển thị sau import
- Kiểm tra xem có bị loại do hotline không?
- Kiểm tra phone format có đúng không?
- Kiểm tra database có data không?

### Sales không nhìn thấy khách hàng
- Kiểm tra xem admin đã phân công khách cho sales chưa?
- Kiểm tra permission của sales user

### Không thể đăng nhập
- Reset mật khẩu từ env variables
- Kiểm tra database còn sạch không?

---

## Liên hệ hỗ trợ

Nếu có vấn đề:
- Kiểm tra /debug (logs) trên server
- Kiểm tra database connection
- Khởi động lại app nếu cần
