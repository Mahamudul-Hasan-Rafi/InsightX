import type { ChartPoint } from '@/lib/types';

const COLORS = ['var(--c1)', 'var(--c2)', 'var(--c3)', 'var(--c4)', 'var(--c5)'];

interface BarChartProps {
  data: ChartPoint[];
  unit?: string;
}

export default function BarChart({ data, unit = '' }: BarChartProps) {
  const max = Math.max(...data.map(d => d.value));
  return (
    <div className="barchart">
      {data.map((d, i) => (
        <div className="bar-row" key={i}>
          <div className="bar-label">{d.label}</div>
          <div className="bar-track">
            <div
              className="bar-fill"
              style={{
                width: `${(d.value / max) * 100}%`,
                background: COLORS[i % COLORS.length],
              }}
            />
          </div>
          <div className="bar-val">{d.value}{unit}</div>
        </div>
      ))}
    </div>
  );
}
