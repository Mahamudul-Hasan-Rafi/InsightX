'use client';

import { useState } from 'react';

const LOGOS: Record<string, string> = {
  oracle:     'https://cdn.jsdelivr.net/gh/devicons/devicon/icons/oracle/oracle-original.svg',
  postgres:   'https://cdn.jsdelivr.net/gh/devicons/devicon/icons/postgresql/postgresql-original.svg',
  postgresql: 'https://cdn.jsdelivr.net/gh/devicons/devicon/icons/postgresql/postgresql-original.svg',
  sqlserver:  'https://cdn.jsdelivr.net/gh/devicons/devicon/icons/microsoftsqlserver/microsoftsqlserver-plain.svg',
};

interface DBLogoProps {
  slug: string;
  size?: number;
  radius?: number;
  letter?: string;
  color?: string;
}

export default function DBLogo({ slug, size = 38, radius = 10, letter, color }: DBLogoProps) {
  const [err, setErr] = useState(false);
  const src = LOGOS[slug];

  if (err || !src) {
    return (
      <div style={{
        width: size, height: size, borderRadius: radius,
        background: color ?? 'var(--surface-3)',
        display: 'grid', placeItems: 'center',
        color: 'white', fontWeight: 800, fontFamily: 'var(--mono)',
        fontSize: size * 0.4, flexShrink: 0,
      }}>
        {letter ?? '?'}
      </div>
    );
  }

  return (
    <div className="db-tile" style={{ width: size, height: size, borderRadius: radius }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={src} alt={`${slug} logo`} onError={() => setErr(true)} />
    </div>
  );
}
