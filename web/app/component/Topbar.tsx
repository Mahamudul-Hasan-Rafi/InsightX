'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import Icon from './Icon';

const PAGE_META: Record<string, { title: string; crumb: string }> = {
  insight:    { title: 'Insight',     crumb: 'Ask your data' },
  dashboard:  { title: 'Dashboard',   crumb: 'Overview' },
  datasource: { title: 'Data Source', crumb: 'Connections' },
  users:      { title: 'Users',       crumb: 'Access management' },
  glossary:   { title: 'Glossary',    crumb: 'Shared vocabulary' },
  developers: { title: 'Developers',  crumb: 'API & integration' },
};

export default function Topbar() {
  const pathname = usePathname();
  const page = pathname.replace('/', '').split('/')[0] || 'insight';
  const meta = PAGE_META[page] ?? { title: 'InsightX', crumb: '' };

  return (
    <header className="topbar">
      <h1>{meta.title}</h1>
      {meta.crumb && <span className="crumb">/ {meta.crumb}</span>}
      <div className="topbar-actions">
        {page === 'dashboard' && (
          <button className="btn btn-ghost btn-sm">
            <Icon name="download" size={14} /> Export
          </button>
        )}
        {page === 'insight' && (
          <Link href="/insight" className="btn btn-subtle btn-sm">
            <Icon name="plus" size={14} /> New chat
          </Link>
        )}
      </div>
    </header>
  );
}
