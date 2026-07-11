import React from 'react';

const PATHS: Record<string, string> = {
  insight:    '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5c.7.7 1 1.2 1 2V16h6v-.5c0-.8.3-1.3 1-2A6 6 0 0 0 12 3Z"/>',
  dashboard:  '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  database:   '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
  users:      '<circle cx="9" cy="8" r="3.2"/><path d="M3.5 19a5.5 5.5 0 0 1 11 0"/><path d="M16 5.2a3.2 3.2 0 0 1 0 6M17.5 19a5.5 5.5 0 0 0-2-4.3"/>',
  glossary:   '<path d="M5 4h11a2 2 0 0 1 2 2v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4Z"/><path d="M5 4a2 2 0 0 0-2 2v12a2 2 0 0 1 2-2"/><path d="M9 8h6M9 11h4"/>',
  developers: '<path d="m8 8-4 4 4 4M16 8l4 4-4 4M13.5 6l-3 12"/>',
  settings:   '<circle cx="12" cy="12" r="3"/><path d="M12 2.5l1.3 2.4 2.7-.4.4 2.7 2.4 1.3-1.1 2.5 1.1 2.5-2.4 1.3-.4 2.7-2.7-.4L12 21.5l-1.3-2.4-2.7.4-.4-2.7L5.2 15.5l1.1-2.5-1.1-2.5 2.4-1.3.4-2.7 2.7.4Z"/>',
  bell:       '<path d="M6 9a6 6 0 0 1 12 0c0 5 2 6 2 6H4s2-1 2-6Z"/><path d="M10 19a2 2 0 0 0 4 0"/>',
  plus:       '<path d="M12 5v14M5 12h14"/>',
  send:       '<path d="M5 12h14M13 6l6 6-6 6"/>',
  sparkle:    '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z"/>',
  chart:      '<path d="M4 19V5M4 19h16"/><rect x="7" y="11" width="3" height="5" rx="0.6"/><rect x="12" y="8" width="3" height="8" rx="0.6"/><rect x="17" y="13" width="3" height="3" rx="0.6"/>',
  line:       '<path d="M4 19V5M4 19h16"/><path d="M5 15l4-4 3 2 6-7"/>',
  table:      '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M9 9v11M3 14h18"/>',
  code:       '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m9 9-2 3 2 3M15 9l2 3-2 3"/>',
  chevronR:   '<path d="m9 6 6 6-6 6"/>',
  chevronD:   '<path d="m6 9 6 6 6-6"/>',
  chevronL:   '<path d="m15 6-6 6 6 6"/>',
  x:          '<path d="M6 6l12 12M18 6 6 18"/>',
  search:     '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  check:      '<path d="M5 12.5 10 17l9-10"/>',
  copy:       '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/>',
  key:        '<circle cx="8" cy="15" r="4"/><path d="m11 12 8-8M16 4l3 3M14 6l2 2"/>',
  link:       '<path d="M9 14a4 4 0 0 0 5.7 0l3-3A4 4 0 0 0 12 5.3l-1.5 1.5"/><path d="M15 10a4 4 0 0 0-5.7 0l-3 3A4 4 0 0 0 12 18.7l1.5-1.5"/>',
  trash:      '<path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M6 7l1 13h10l1-13"/>',
  edit:       '<path d="M5 19h14M15 4l4 4-9 9H6v-4l9-9Z"/>',
  arrowUp:    '<path d="M12 19V6M6 11l6-6 6 6"/>',
  arrowDown:  '<path d="M12 5v13M6 13l6 6 6-6"/>',
  clock:      '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  shield:     '<path d="M12 3l7 3v5c0 5-3.5 8-7 10-3.5-2-7-5-7-10V6l7-3Z"/>',
  coins:      '<ellipse cx="9" cy="7" rx="6" ry="3"/><path d="M3 7v5c0 1.7 2.7 3 6 3M15 9.2c0 1.5 2.7 2.8 6 2.8"/><ellipse cx="15" cy="14" rx="6" ry="3"/><path d="M9 12v0M9 17c-3.3 0-6-1.3-6-3M21 12v5c0 1.7-2.7 3-6 3s-6-1.3-6-3"/>',
  bolt:       '<path d="M13 3 4 14h7l-1 7 9-11h-7l1-7Z"/>',
  star:       '<path d="M12 3.5l2.6 5.3 5.9.9-4.2 4.1 1 5.9-5.3-2.8-5.3 2.8 1-5.9L4.5 9.7l5.9-.9L12 3.5Z"/>',
  doc:        '<path d="M7 3h7l4 4v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z"/><path d="M14 3v4h4M9 13h6M9 16h4"/>',
  plug:       '<path d="M9 3v6M15 3v6M7 9h10v3a5 5 0 0 1-10 0V9ZM12 17v4"/>',
  refresh:    '<path d="M4 12a8 8 0 0 1 13.7-5.6L20 8M20 4v4h-4M20 12a8 8 0 0 1-13.7 5.6L4 16M4 20v-4h4"/>',
  filter:     '<path d="M3 5h18l-7 8v6l-4-2v-4L3 5Z"/>',
  dots:       '<circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/>',
  logout:     '<path d="M14 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4M10 8l-4 4 4 4M6 12h10"/>',
  moon:       '<path d="M20 14a8 8 0 0 1-10-10 8 8 0 1 0 10 10Z"/>',
  cloud:      '<path d="M7 17a4 4 0 0 1 0-8 5 5 0 0 1 9.6 1.3A3.5 3.5 0 0 1 17 17H7Z"/>',
  cpu:        '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/><path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3"/>',
  history:    '<path d="M3 12a9 9 0 1 0 3-6.7L3 8M3 4v4h4"/><path d="M12 8v4l3 2"/>',
  message:    '<path d="M5 5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H9l-4 3V6a1 1 0 0 1 1-1Z"/>',
  download:   '<path d="M12 4v10M8 11l4 4 4-4M5 19h14"/>',
  pin:        '<path d="M12 21s7-6.3 7-11a7 7 0 1 0-14 0c0 4.7 7 11 7 11Z"/><circle cx="12" cy="10" r="2.5"/>',
  flow:       '<rect x="3" y="4" width="6" height="5" rx="1.5"/><rect x="15" y="15" width="6" height="5" rx="1.5"/><path d="M9 6.5h4a2 2 0 0 1 2 2v7"/>',
  pause:      '<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>',
};

interface IconProps {
  name: string;
  size?: number;
  className?: string;
  style?: React.CSSProperties;
  stroke?: number;
}

export default function Icon({ name, size = 20, className, style, stroke = 1.7 }: IconProps) {
  const path = PATHS[name] ?? '';
  return (
    <svg
      className={className}
      style={style}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
      dangerouslySetInnerHTML={{ __html: path }}
    />
  );
}
