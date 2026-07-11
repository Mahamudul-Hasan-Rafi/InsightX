'use client';

import { useState } from 'react';
import Icon from './Icon';
import { MODELS, NOTIFICATIONS } from '@/lib/dummy-data';
import { useAuth } from './AuthProvider';

function initials(name?: string, email?: string): string {
  const source = (name ?? email ?? '?').trim();
  const parts = source.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return source.slice(0, 2).toUpperCase();
}

/* ─── Settings Modal ─── */
export function SettingsModal({ onClose }: { onClose: () => void }) {
  const [model, setModel] = useState('ix-analyst-3');
  const [cloud, setCloud] = useState(false);
  const selected = MODELS.find(m => m.id === model);
  const isCloud = selected?.hosted === 'Cloud';

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()} style={{ maxWidth: 600 }}>
        <div className="modal-head">
          <Icon name="settings" size={19} style={{ color: 'var(--accent-text)' }} />
          <h2>Settings</h2>
          <button className="icon-btn x" onClick={onClose}><Icon name="x" /></button>
        </div>
        <div className="modal-body">
          <div className="eyebrow" style={{ marginBottom: 12 }}>Model for chat</div>
          <div className="grid" style={{ gap: 10 }}>
            {MODELS.map(m => (
              <div
                key={m.id}
                className={'model-row' + (model === m.id ? ' sel' : '')}
                onClick={() => setModel(m.id)}
              >
                <div className="model-radio" />
                <div className="model-info">
                  <b>{m.name}</b>
                  <small>{m.desc}</small>
                </div>
                <span className={'pill ' + (m.tag === 'Recommended' ? 'pill-green' : m.tag === 'Cloud' ? 'pill-purple' : 'pill-blue')}>
                  {m.tag}
                </span>
              </div>
            ))}
          </div>

          {isCloud && (
            <div className="card card-pad fade-up" style={{ marginTop: 18, background: 'var(--surface-2)' }}>
              <div className="row" style={{ alignItems: 'center', gap: 9, marginBottom: 14 }}>
                <Icon name="cloud" size={18} style={{ color: 'var(--purple)' }} />
                <b style={{ fontSize: 13.5 }}>Cloud model credentials</b>
                <span className="pill pill-purple" style={{ marginLeft: 'auto' }}>Required</span>
              </div>
              <div className="grid" style={{ gap: 12 }}>
                <div className="field">
                  <label>Provider API key</label>
                  <input className="input mono" type="password" placeholder="sk-••••••••••••••••••••" />
                </div>
                <div className="row">
                  <div className="field" style={{ flex: 1 }}>
                    <label>Endpoint (optional)</label>
                    <input className="input mono" placeholder="https://api.provider.com/v1" />
                  </div>
                  <div className="field" style={{ flex: 1 }}>
                    <label>Region</label>
                    <select className="select">
                      <option>us-east-1</option>
                      <option>eu-west-1</option>
                      <option>ap-south-1</option>
                    </select>
                  </div>
                </div>
                <p className="faint" style={{ fontSize: 11.5, margin: 0, lineHeight: 1.5 }}>
                  Credentials are encrypted at rest. Data sent to a cloud model leaves your on-prem environment — confirm this complies with your data policy.
                </p>
              </div>
            </div>
          )}

          <div className="between" style={{ marginTop: 20, paddingTop: 18, borderTop: '1px solid var(--border-soft)' }}>
            <div>
              <b style={{ fontSize: 13.5 }}>Use cloud-hosted model</b>
              <p className="muted" style={{ margin: '2px 0 0', fontSize: 12.5 }}>Allow selecting cloud providers above</p>
            </div>
            <button
              className={'toggle' + (cloud ? ' on' : '')}
              onClick={() => setCloud(c => !c)}
              aria-label="toggle cloud"
            >
              <span className="toggle-knob" />
            </button>
          </div>
        </div>
        <div className="modal-foot">
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={onClose}>Save settings</button>
        </div>
      </div>
    </div>
  );
}

/* ─── Notifications Drawer ─── */
export function NotificationsDrawer({ onClose }: { onClose: () => void }) {
  const unreadCount = NOTIFICATIONS.filter(n => n.unread).length;

  return (
    <div className="drawer-wrap">
      <div className="drawer-scrim" onClick={onClose} />
      <div className="drawer">
        <div className="modal-head">
          <Icon name="bell" size={19} style={{ color: 'var(--accent-text)' }} />
          <h2>Notifications</h2>
          {unreadCount > 0 && (
            <span className="pill pill-red" style={{ marginLeft: 4 }}>{unreadCount} new</span>
          )}
          <button className="icon-btn x" onClick={onClose} style={{ marginLeft: 'auto' }}>
            <Icon name="x" />
          </button>
        </div>
        <div style={{ overflowY: 'auto', flex: 1 }}>
          {NOTIFICATIONS.map((n, i) => (
            <div className={'notif-item' + (n.unread ? ' unread' : '')} key={i}>
              <div className={'notif-ic ' + n.tint}>
                <Icon name={n.icon} />
              </div>
              <div className="notif-body" style={{ flex: 1 }}>
                <b>{n.title}</b>
                <p>{n.body}</p>
                <span className="t">{n.time} ago</span>
              </div>
              {n.unread && (
                <span style={{
                  width: 8, height: 8, borderRadius: 99,
                  background: 'var(--accent)', flexShrink: 0, marginTop: 4,
                }} />
              )}
            </div>
          ))}
        </div>
        <div className="modal-foot" style={{ justifyContent: 'space-between' }}>
          <button className="btn btn-subtle btn-sm">
            <Icon name="check" size={13} /> Mark all read
          </button>
          <button className="btn btn-ghost btn-sm">Notification settings</button>
        </div>
      </div>
    </div>
  );
}

/* ─── Profile Drawer ─── */
export function ProfileDrawer({ onClose }: { onClose: () => void }) {
  const { user, logout } = useAuth();
  const displayName = user?.name ?? user?.username ?? 'Signed-in user';
  const email = user?.email ?? '';
  // Show only realm roles (e.g. "insightx-analyst").
  // Client feature roles (feat:*:*) are operational guards, not display labels.
  const realmRoles = (user?.roles ?? []).filter(r => !r.includes(':'));

  return (
    <div className="drawer-wrap drawer-left">
      <div className="drawer-scrim" onClick={onClose} />
      <div className="drawer">
        <div className="modal-head">
          <h2>Profile</h2>
          <button className="icon-btn x" onClick={onClose} style={{ marginLeft: 'auto' }}>
            <Icon name="x" />
          </button>
        </div>
        <div className="modal-body" style={{ flex: 1 }}>
          <div style={{
            display: 'flex', flexDirection: 'column', alignItems: 'center',
            gap: 10, padding: '8px 0 20px', borderBottom: '1px solid var(--border-soft)',
          }}>
            <div className="avatar" style={{
              width: 68, height: 68, fontSize: 24,
              background: 'linear-gradient(140deg, oklch(0.62 0.15 28), oklch(0.58 0.16 350))',
            }}>
              {initials(user?.name, user?.email)}
            </div>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: 17, fontWeight: 800 }}>{displayName}</div>
              {email && <div className="faint" style={{ fontSize: 13 }}>{email}</div>}
            </div>
            {realmRoles.length > 0 && (
              <div className="row" style={{ gap: 7, flexWrap: 'wrap', justifyContent: 'center' }}>
                {realmRoles.map((r) => (
                  <span key={r} className="pill pill-purple">{r}</span>
                ))}
              </div>
            )}
          </div>

          <div className="grid" style={{ gap: 14, marginTop: 20 }}>
            <div className="field">
              <label>Display name</label>
              <input className="input" defaultValue={displayName} readOnly />
            </div>
            <div className="field">
              <label>Email</label>
              <input className="input mono" defaultValue={email} readOnly />
            </div>
            <div className="field">
              <label>Username</label>
              <input className="input mono" defaultValue={user?.username ?? ''} readOnly />
            </div>
            <p className="faint" style={{ fontSize: 11.5, margin: 0, lineHeight: 1.5 }}>
              Profile details are managed in Keycloak (SSO). Update them in your
              identity provider to change them here.
            </p>
          </div>
        </div>
        <div className="modal-foot" style={{ justifyContent: 'flex-end' }}>
          <button className="btn btn-danger btn-sm" onClick={logout}>
            <Icon name="logout" size={14} /> Sign out
          </button>
        </div>
      </div>
    </div>
  );
}
