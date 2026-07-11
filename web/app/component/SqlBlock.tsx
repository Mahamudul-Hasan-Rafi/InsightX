'use client';

import { useState } from 'react';
import Icon from './Icon';

interface SqlBlockProps {
  sql: string;
}

export default function SqlBlock({ sql }: SqlBlockProps) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const copy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard?.writeText(sql);
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  };

  return (
    <div className="sqlblock">
      <button className="sql-head" onClick={() => setOpen(o => !o)}>
        <Icon name="code" size={15} />
        <span>Generated SQL</span>
        <span className="sql-spacer" />
        {open && (
          <span className="sql-copy" onClick={copy}>
            <Icon name={copied ? 'check' : 'copy'} size={13} />
            {copied ? 'Copied' : 'Copy'}
          </span>
        )}
        <Icon name="chevronD" size={15} className={'sql-chev' + (open ? ' r' : '')} />
      </button>
      {open && <pre className="sql-code mono">{sql}</pre>}
    </div>
  );
}
