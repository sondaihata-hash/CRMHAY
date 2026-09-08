import Link from 'next/link';

export default function PrivacyPage() {
  return (
    <main className="min-h-screen bg-slate-50 text-slate-900">
      <article className="mx-auto max-w-3xl px-6 py-10">
        <Link href="/" className="text-sm font-semibold text-indigo-600 hover:text-indigo-500">
          ← Về trang chủ
        </Link>
        <h1 className="mt-12 text-4xl font-bold tracking-tight">Chính sách bảo mật</h1>
        <p className="mt-3 text-sm text-slate-500">Cập nhật lần cuối: 08/09/2026</p>
        <div className="prose prose-slate mt-10 max-w-none">
          <h2>1. Dữ liệu chúng tôi xử lý</h2>
          <p>
            CRM Facebook xử lý thông tin lead và dữ liệu tài khoản do khách hàng chủ động
            nhập hoặc kết nối, nhằm cung cấp chức năng quản lý và chăm sóc khách hàng.
          </p>
          <h2>2. Mục đích sử dụng</h2>
          <p>
            Dữ liệu được dùng để vận hành dịch vụ, đồng bộ lead, hỗ trợ người dùng, bảo vệ
            an toàn hệ thống và cải thiện trải nghiệm. Chúng tôi không bán dữ liệu khách hàng.
          </p>
          <h2>3. Bảo vệ và lưu giữ dữ liệu</h2>
          <p>
            Chúng tôi áp dụng các biện pháp kỹ thuật và vận hành phù hợp để hạn chế truy cập
            trái phép. Thời gian lưu giữ phụ thuộc vào hợp đồng, nhu cầu vận hành và nghĩa vụ pháp lý.
          </p>
          <h2>4. Quyền của khách hàng</h2>
          <p>
            Khách hàng có thể yêu cầu truy cập, chỉnh sửa, xuất hoặc xóa dữ liệu của mình,
            trừ trường hợp pháp luật yêu cầu tiếp tục lưu giữ.
          </p>
          <h2>5. Liên hệ</h2>
          <p>
            Câu hỏi về bảo mật vui lòng gửi đến{' '}
            <a href="mailto:support@crm-facebook.vn">support@crm-facebook.vn</a>.
          </p>
        </div>
      </article>
    </main>
  );
}
