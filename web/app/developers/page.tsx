'use client';

import { useState } from 'react';
import Icon from '@/app/component/Icon';
import { API_KEYS, API_ENDPOINTS } from '@/lib/dummy-data';

export default function DevelopersPage() {
  const [tab,      setTab]      = useState<'keys' | 'docs'>('keys');
  const [revealed, setRevealed] = useState<number | null>(null);

  return (
    <div className="page-inner fade-up">
      <div className="between" style={{ marginBottom: 8 }}>
        <div>
          <div className="eyebrow">Integrate</div>
          <h1 className="section-title" style={{ marginTop: 4 }}>Developers</h1>
        </div>
        <a className="btn btn-ghost" href="#" onClick={e => e.preventDefault()}>
          <Icon name="doc" size={15} /> Full reference
        </a>
      </div>
      <p className="muted" style={{ margin: '0 0 22px', fontSize: 14, maxWidth: 620 }}>
        Generate insights programmatically with the InsightX REST API. Authenticate with a secret key
        and the same annotated schema your team uses in chat.
      </p>

      <div className="tabs">
        <button className={'tab' + (tab === 'keys' ? ' active' : '')} onClick={() => setTab('keys')}>
          API keys
        </button>
        <button className={'tab' + (tab === 'docs' ? ' active' : '')} onClick={() => setTab('docs')}>
          API reference
        </button>
      </div>

      {tab === 'keys' ? (
        <>
          <div className="card" style={{ marginBottom: 18 }}>
            <div className="card-head">
              <Icon name="key" size={17} style={{ color: 'var(--accent-text)' }} />
              <div>
                <h3>Secret keys</h3>
                <div className="sub">Keep these private. Treat them like database passwords.</div>
              </div>
              <button className="btn btn-primary btn-sm" style={{ marginLeft: 'auto' }}>
                <Icon name="plus" size={13} /> Create key
              </button>
            </div>
            {API_KEYS.map((k, i) => (
              <div className="key-row" key={i}>
                <div className="key-ic"><Icon name="key" /></div>
                <div className="key-meta">
                  <b>{k.name}</b>
                  <div className="key-token">
                    {k.prefix}{revealed === i ? 'a1b2c3d4e5f6' : '••••••••'}{k.tail}
                  </div>
                </div>
                <div style={{ textAlign: 'right', fontSize: 11.5 }} className="faint">
                  <div>{k.scope}</div>
                  <div style={{ marginTop: 2 }}>Last used {k.last}</div>
                </div>
                <button
                  className="btn btn-subtle btn-sm"
                  onClick={() => setRevealed(revealed === i ? null : i)}
                >
                  {revealed === i ? 'Hide' : 'Reveal'}
                </button>
                <button className="icon-btn" title="Copy"><Icon name="copy" size={15} /></button>
                <button className="icon-btn" title="Revoke"><Icon name="trash" size={15} /></button>
              </div>
            ))}
          </div>

          <div className="card card-pad row" style={{ gap: 13, alignItems: 'flex-start' }}>
            <Icon name="shield" size={18} style={{ color: 'var(--warn)', marginTop: 1, flexShrink: 0 }} />
            <div>
              <b style={{ fontSize: 13.5 }}>Rotate keys regularly</b>
              <p className="muted" style={{ margin: '3px 0 0', fontSize: 13 }}>
                For compliance, production keys should be rotated every 90 days.
                Revoking a key takes effect immediately.
              </p>
            </div>
          </div>
        </>
      ) : (
        <>
          <div className="card" style={{ marginBottom: 18 }}>
            <div className="card-head">
              <Icon name="developers" size={17} style={{ color: 'var(--accent-text)' }} />
              <h3>Endpoints</h3>
              <span className="pill" style={{ marginLeft: 'auto' }}>
                Base · api.insightx.bank/v1
              </span>
            </div>
            {API_ENDPOINTS.map((e, i) => (
              <div className="endpoint-row" key={i}>
                <span className={`method ${e.method.toLowerCase()}`}>{e.method}</span>
                <span className="endpoint-path">{e.path}</span>
                <span className="endpoint-desc">{e.desc}</span>
              </div>
            ))}
          </div>

          <h3 style={{ fontSize: 14, margin: '0 0 11px', color: 'var(--text-muted)' }}>Example request</h3>
          <div className="code-sample">
            <pre>
              <span className="cc"># Generate an insight</span>{'\n'}
              {'curl https://api.insightx.bank/v1/insights \\\n'}
              {'  -H '}<span className="cs">&quot;Authorization: Bearer ix_live_…8Kd2&quot;</span>{' \\\n'}
              {'  -H '}<span className="cs">&quot;Content-Type: application/json&quot;</span>{' \\\n'}
              {'  -d '}<span className="cs">{"'{ \"source\": \"core-banking\","}</span>{'\n'}
              {'       '}<span className="cs">{"\"question\": \"Top branches by deposit growth in Q1 2026\" }'"}</span>
            </pre>
          </div>
        </>
      )}
    </div>
  );
}
