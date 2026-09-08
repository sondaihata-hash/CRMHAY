import Link from 'next/link';

const highlights = [
  {
    title: 'Tập trung lead',
    description: 'Quản lý lead từ Facebook trong một quy trình rõ ràng, dễ theo dõi.',
  },
  {
    title: 'Theo sát cơ hội',
    description: 'Cập nhật trạng thái, ghi chú và lịch sử chăm sóc để không bỏ lỡ khách hàng.',
  },
  {
    title: 'Sẵn sàng cho đội sales',
    description: 'Giao diện đơn giản, triển khai nhanh cho doanh nghiệp nhỏ và đội bán hàng.',
  },
];

export default function Home() {
  return (
    <main className="min-h-screen bg-slate-950 text-white">
      <header className="border-b border-white/10">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
          <Link href="/" className="text-xl font-bold tracking-tight">
            CRM Facebook
          </Link>
          <nav className="flex items-center gap-4 text-sm text-slate-300">
            <Link href="/pricing" className="hidden transition hover:text-white sm:block">
              Gói giá
            </Link>
            <Link href="/terms" className="hidden transition hover:text-white sm:block">
              Điều khoản
            </Link>
            <Link
              href="/dashboard"
              className="rounded-lg bg-indigo-500 px-4 py-2 font-semibold text-white transition hover:bg-indigo-400"
            >
              Mở CRM
            </Link>
          </nav>
        </div>
      </header>

      <section className="mx-auto max-w-6xl px-6 pb-20 pt-24 sm:pt-32">
        <div className="max-w-3xl">
          <p className="mb-5 text-sm font-semibold uppercase tracking-[0.2em] text-indigo-300">
            CRM cho đội sales hiện đại
          </p>
          <h1 className="text-4xl font-bold tracking-tight sm:text-6xl">
            Biến lead Facebook thành khách hàng trung thành.
          </h1>
          <p className="mt-6 max-w-2xl text-lg leading-8 text-slate-300">
            CRM Facebook giúp doanh nghiệp tập trung lead, quản lý cơ hội và xây dựng
            quy trình chăm sóc khách hàng nhất quán trong một nơi.
          </p>
          <div className="mt-8 flex flex-wrap gap-4">
            <Link
              href="/dashboard"
              className="rounded-lg bg-indigo-500 px-5 py-3 font-semibold transition hover:bg-indigo-400"
            >
              Bắt đầu quản lý lead
            </Link>
            <Link
              href="/pricing"
              className="rounded-lg border border-white/20 px-5 py-3 font-semibold text-slate-200 transition hover:border-white/40 hover:text-white"
            >
              Xem gói giá
            </Link>
          </div>
        </div>

        <div className="mt-20 grid gap-5 md:grid-cols-3">
          {highlights.map((highlight) => (
            <article key={highlight.title} className="rounded-2xl border border-white/10 bg-white/5 p-6">
              <h2 className="text-lg font-semibold">{highlight.title}</h2>
              <p className="mt-3 leading-7 text-slate-400">{highlight.description}</p>
            </article>
          ))}
        </div>
      </section>

      <footer className="border-t border-white/10">
        <div className="mx-auto flex max-w-6xl flex-col gap-4 px-6 py-8 text-sm text-slate-400 sm:flex-row sm:items-center sm:justify-between">
          <p>© 2026 CRM Facebook. Bảo lưu mọi quyền.</p>
          <nav className="flex flex-wrap gap-x-5 gap-y-2">
            <Link href="/pricing" className="transition hover:text-white">
              Gói giá
            </Link>
            <Link href="/privacy" className="transition hover:text-white">
              Chính sách bảo mật
            </Link>
            <Link href="/terms" className="transition hover:text-white">
              Điều khoản sử dụng
            </Link>
          </nav>
        </div>
      </footer>
    </main>
  );
}