interface LineChartProps {
  data: Array<{ v: number }>;
  height?: number;
  color?: string;
}

export default function LineChart({ data, height = 150, color = 'var(--accent)' }: LineChartProps) {
  const W = 520, PAD = 8;
  const h = height;
  const max = Math.max(...data.map(d => d.v)) * 1.15;
  const stepX = (W - PAD * 2) / Math.max(data.length - 1, 1);

  const pts: [number, number][] = data.map((d, i) => [
    PAD + i * stepX,
    h - PAD - ((d.v / max) * (h - PAD * 2 - 18)),
  ]);

  const line = pts.map(p => p.join(',')).join(' ');
  const area = `${PAD},${h - PAD} ${line} ${W - PAD},${h - PAD}`;

  return (
    <svg
      viewBox={`0 0 ${W} ${h}`}
      preserveAspectRatio="none"
      style={{ width: '100%', height, display: 'block' }}
    >
      <defs>
        <linearGradient id="lcg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"   stopColor={color} stopOpacity="0.22" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <polygon points={area} fill="url(#lcg)" />
      <polyline
        points={line}
        fill="none"
        stroke={color}
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {pts.map((p, i) => (
        <circle key={i} cx={p[0]} cy={p[1]} r="3.5" fill="var(--surface)" stroke={color} strokeWidth="2" />
      ))}
    </svg>
  );
}
