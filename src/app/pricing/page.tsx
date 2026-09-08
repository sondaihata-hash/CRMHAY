import Link from 'next/link';

const plans = [
  {
    name: 'Pilot',
    price: 'Liên hệ',
    description: 'Dành cho đội sales nhỏ muốn bắt đầu nhanh.',
    features: ['Quản lý lead Facebook', 'Theo dõi trạng thái và ghi chú', 'Hỗ trợ cài đặt ban đầu'],
  },
  {
    name: 'Business',
    price: 'Liên hệ',
    description: 'Dành cho doanh nghiệp cần quy trình sales ổn định.',
    features: ['Tất cả tính năng Pilot', 'Nhiều người dùng và phân quyền', 'Báo cáo và hỗ trợ ưu tiên'],
  },
  {
    name: 'Custom',
    price: 'Trao đổi',
    description: 'Dành cho nhu cầu tích hợp và vận hành riêng.',
    features: ['Tùy chỉnh theo quy trình', 'Tích hợp dữ liệu theo nhu cầu', 'Cam kết hỗ trợ theo thỏa thuận'],
  },
];

export default function PricingPage() {
  return (
    <main className="min-h-screen bg-slate-50 text-slate-900">
      <div className="mx-auto max-w-6xl px-6 py-10">
        <Link href="/" className="text-sm font-semibold text-indigo-600 hover:text-indigo-500">
          ← Về trang chủ
        </Link>
        <div className="mt-12 max-w-2xl">
          <p className="text-sm font-semibold uppercase tracking-[0.2em] text-indigo-600">Gói giá</p>
          <h1 className="mt-3 text-4xl font-bold tracking-tight">Chọn gói phù hợp với đội sales</h1>
          <p className="mt-4 text-lg leading-8 text-slate-600">
            Chúng tôi tư vấn theo số lượng người dùng, nhu cầu tích hợp và quy mô dữ liệu.
            Liên hệ để nhận báo giá chính thức cho doanh nghiệp của bạn.
          </p>
        </div>
        <div className="mt-12 grid gap-6 md:grid-cols-3">
          {plans.map((plan) => (
            <article key={plan.name} className="flex flex-col rounded-2xl border border-slate-200 bg-white p-7 shadow-sm">
              <h2 className="text-xl font-bold">{plan.name}</h2>
              <p className="mt-4 text-3xl font-bold text-indigo-600">{plan.price}</p>
              <p className="mt-3 min-h-14 text-slate-600">{plan.description}</p>
              <ul className="mt-6 space-y-3 border-t border-slate-100 pt-6 text-sm text-slate-700">
                {plan.features.map((feature) => (
                  <li key={feature}>✓ {feature}</li>
                ))}
              </ul>
              <a
                href="mailto:support@crm-facebook.vn?subject=T%C6%B0%20v%E1%BA%A5n%20g%C3%B3i%20CRM"
                className="mt-8 rounded-lg bg-indigo-600 px-4 py-3 text-center font-semibold text-white hover:bg-indigo-500"
              >
                Nhận tư vấn
              </a>
            </article>
          ))}
        </div>
        <p className="mt-10 text-sm text-slate-500">
          Giá và giới hạn sử dụng sẽ được xác nhận trong báo giá hoặc hợp đồng riêng trước khi thanh toán.
        </p>
      </div>
    </main>
  );
}
