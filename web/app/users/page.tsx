import Icon from '@/app/component/Icon';

export default function UsersPage() {
  return (
    <div className="page-inner fade-up">
      <div className="between" style={{ marginBottom: 22 }}>
        <div>
          <div className="eyebrow">Access management</div>
          <h1 className="section-title" style={{ marginTop: 4 }}>Users</h1>
        </div>
        <button className="btn btn-primary">
          <Icon name="plus" size={15} /> Invite user
        </button>
      </div>

      <div className="row" style={{ marginBottom: 16, gap: 10 }}>
        <div className="input" style={{ display: 'flex', alignItems: 'center', gap: 8, maxWidth: 280, padding: '8px 12px' }}>
          <Icon name="search" size={15} style={{ color: 'var(--text-faint)' }} />
          <input
            style={{ border: 'none', outline: 'none', background: 'none', flex: 1, fontFamily: 'inherit', fontSize: 13.5, color: 'var(--text)' }}
            placeholder="Search users…"
          />
        </div>
        <button className="btn btn-ghost"><Icon name="filter" size={14} /> Role</button>
      </div>

      <div className="empty-state">
        <div className="es-ic">
          <Icon name="users" size={22} />
        </div>
        <h3>No users yet</h3>
        <p>Invite your first user to manage access and permissions.</p>
      </div>
    </div>
  );
}
