import Link from 'next/link';
import Icon from '@/app/component/Icon';

export default function DashboardPage() {
  return (
    <div className="page-inner wide fade-up">
      <div className="between" style={{ marginBottom: 22 }}>
        <div>
          <div className="eyebrow">Overview</div>
          <h1 className="section-title" style={{ marginTop: 4 }}>Dashboard</h1>
        </div>
        <Link href="/insight" className="btn btn-primary">
          <Icon name="plus" size={15} /> New insight
        </Link>
      </div>

      <div className="empty-state">
        <div className="es-ic">
          <Icon name="dashboard" size={22} />
        </div>
        <h3>No data to show yet</h3>
        <p>
          Dashboard metrics will appear here once the analytics backend is
          connected and insights start being generated.
        </p>
      </div>
    </div>
  );
}
