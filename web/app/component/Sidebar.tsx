'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import Icon from './Icon';
import { useAuth } from './AuthProvider';
import { NAV, HISTORY } from '@/lib/dummy-data';

interface SidebarProps {
  onOpenModal: (modal: 'settings' | 'notifications' | 'profile') => void;
}

/**
 * Parse a role string like "feat:insight:view" into its three segments.
 * Returns null for roles that don't follow the feat:<feature>:<permission> pattern.
 */
function parseRole(role: string): { prefix: string; feature: string; permission: string } | null {
  const parts = role.split(':');
  if (parts.length !== 3) return null;
  return { prefix: parts[0], feature: parts[1], permission: parts[2] };
}

/**
 * Derive the set of features a user may VIEW from their Keycloak roles.
 * Only roles matching "feat:<feature>:view" are considered.
 * Admins (insightx-admin role) can view everything.
 */
function getAllowedFeatures(roles: string[]): Set<string> | 'all' {
  if (roles.includes('insightx-admin')) return 'all';

  const features = new Set<string>();
  for (const role of roles) {
    const parsed = parseRole(role);
    if (parsed?.prefix === 'feat' && parsed.permission === 'view') {
      features.add(parsed.feature);
    }
  }
  return features;
}

function initials(name?: string, email?: string): string {
  const source = (name ?? email ?? '?').trim();
  const parts = source.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  return source.slice(0, 2).toUpperCase();
}

export default function Sidebar({ onOpenModal }: SidebarProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { user } = useAuth();

  const page = pathname.replace('/', '').split('?')[0] || 'insight';
  const isInsight = page === 'insight';

  const allowed = getAllowedFeatures(user.roles ?? []);
  const visibleNav = NAV.filter(n =>
    !n.feature || allowed === 'all' || allowed.has(n.feature),
  );

  const displayName = user.name ?? user.username ?? user.email ?? 'User';
  const avatarText  = initials(user.name, user.email);

  return (
    <aside className="sidebar">
      <div className="sb-brand">
        <div className="brand-mark">
          <Icon name="insight" size={17} stroke={2} />
        </div>
        <div>
          <div className="brand-name">Insight<span>X</span></div>
          <div className="brand-sub">Bank Intelligence</div>
        </div>
      </div>

      <div className="sb-section-label">Workspace</div>
      <nav className="sb-nav">
        {visibleNav.map(n => (
          <Link
            key={n.id}
            href={`/${n.id}`}
            className={'nav-item' + (page === n.id ? ' active' : '')}
          >
            <Icon name={n.icon} />
            {n.label}
            {n.id === 'datasource' && <span className="nav-badge">3</span>}
            {n.id === 'users' && <span className="nav-badge">7</span>}
          </Link>
        ))}
      </nav>

      {isInsight ? (
        <div className="sb-context">
          <div className="sb-context-head">
            <h4>Chat history</h4>
            <button className="sb-newchat" onClick={() => router.push('/insight')}>
              <Icon name="plus" /> New
            </button>
          </div>
          <div className="sb-history">
            {HISTORY.map((grp, gi) => (
              <div key={gi}>
                <div className="history-day">{grp.day}</div>
                {grp.items.map(item => (
                  <Link
                    key={item.id}
                    href={`/insight?chat=${item.id}`}
                    className={'history-item' + (pathname.includes(`chat=${item.id}`) ? ' active' : '')}
                  >
                    {item.title}
                  </Link>
                ))}
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="sb-context">
          <div className="sb-context-empty">
            <Icon name="history" />
            <p>Chat history appears here while you&apos;re on the Insight page.</p>
          </div>
        </div>
      )}

      <div className="sb-footer">
        <button className="sb-profile" onClick={() => onOpenModal('profile')}>
          <div className="avatar">{avatarText}</div>
          <div className="who">
            <b>{displayName}</b>
            <small>{user.email ?? ''}</small>
          </div>
        </button>
        <div className="sb-foot-actions">
          <button
            className="sb-foot-btn has-dot"
            title="Notifications"
            onClick={() => onOpenModal('notifications')}
          >
            <Icon name="bell" />
          </button>
          <button
            className="sb-foot-btn"
            title="Settings"
            onClick={() => onOpenModal('settings')}
          >
            <Icon name="dots" />
          </button>
        </div>
      </div>
    </aside>
  );
}
