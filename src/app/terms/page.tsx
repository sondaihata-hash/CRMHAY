import Link from 'next/link';

export default function TermsPage() {
  return (
    <main className="min-h-screen bg-slate-50 text-slate-900">
      <article className="mx-auto max-w-3xl px-6 py-10">
        <Link href="/" className="text-sm font-semibold text-indigo-600 hover:text-indigo-500">
          ← Về trang chủ
        </Link>
        <h1 className="mt-12 text-4xl font-bold tracking-tight">Điều khoản sử dụng</h1>
        <p className="mt-3 text-sm text-slate-500">Cập nhật lần cuối: 08/09/2026</p>
        <div className="prose prose-slate mt-10 max-w-none">
          <h2>1. Chấp nhận điều khoản</h2>
          <p>
            Khi truy cập hoặc sử dụng CRM Facebook, bạn xác nhận đã đọc và đồng ý với các
            điều khoản này. Nếu sử dụng thay mặt doanh nghiệp, bạn cần có thẩm quyền đại diện.
          </p>
          <h2>2. Trách nhiệm của khách hàng</h2>
          <p>
            Khách hàng chịu trách nhiệm về tính hợp pháp, chính xác và quyền sử dụng của dữ
            liệu đưa vào hệ thống; đồng thời phải bảo mật thông tin đăng nhập của mình.
          </p>
          <h2>3. Sử dụng hợp lệ</h2>
          <p>
            Không được sử dụng dịch vụ để truy cập trái phép, phát tán mã độc, vi phạm quyền
            riêng tư hoặc vi phạm pháp luật. Chúng tôi có thể tạm ngừng truy cập khi phát hiện rủi ro.
          </p>
          <h2>4. Dịch vụ và thanh toán</h2>
          <p>
            Phạm vi tính năng, giá, thời hạn và cam kết hỗ trợ được xác định trong gói dịch vụ
            hoặc hợp đồng tương ứng. Các thay đổi quan trọng sẽ được thông báo trước theo thỏa thuận.
          </p>
          <h2>5. Liên hệ</h2>
          <p>
            Để được hỗ trợ về tài khoản hoặc điều khoản, vui lòng gửi email đến{' '}
            <a href="mailto:support@crm-facebook.vn">support@crm-facebook.vn</a>.
          </p>
        </div>
      </article>
    </main>
  );
}
