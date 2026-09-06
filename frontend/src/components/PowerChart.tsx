import { formatWatts } from '../domain'
import type { PowerSample } from '../types'

/**
 * Power draw over the last 24 hours. This is the only genuinely observed signal in
 * the whole app, so it is worth looking at: a night that went right has a turbo
 * spike at pre-cool, a long cooling plateau, then a drop to nothing at wake time.
 */
export function PowerChart({ samples }: { samples: PowerSample[] }) {
  if (samples.length < 2) {
    return <p className="empty">No power readings yet.</p>
  }

  const width = 1000
  const height = 200
  // Two fixed scales rather than one per chart, so last night and tonight can be
  // compared by eye. The upper one leaves room for heating's 300 W.
  const observed = Math.max(...samples.map((s) => s.watts))
  const peak = observed < 200 ? 220 : 350
  const firstAt = new Date(samples[0]!.at).getTime()
  const lastAt = new Date(samples[samples.length - 1]!.at).getTime()
  const span = Math.max(1, lastAt - firstAt)

  const x = (at: string) => ((new Date(at).getTime() - firstAt) / span) * width
  const y = (watts: number) => height - (watts / peak) * height

  const line = samples.map((s, i) => `${i === 0 ? 'M' : 'L'}${x(s.at).toFixed(1)} ${y(s.watts).toFixed(1)}`).join(' ')
  const area = `${line} L${width} ${height} L0 ${height} Z`

  const labels = [0, 0.25, 0.5, 0.75, 1].map((f) =>
    new Date(firstAt + span * f).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }),
  )

  return (
    <div className="chart">
      <svg
        className="chart__svg"
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`Power draw over the last 24 hours, peaking at ${formatWatts(
          Math.max(...samples.map((s) => s.watts)),
        )}`}
      >
        <defs>
          <linearGradient id="powerFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#8459c8" stopOpacity="0.5" />
            <stop offset="100%" stopColor="#3b6fe8" stopOpacity="0.03" />
          </linearGradient>
        </defs>
        <path d={area} fill="url(#powerFill)" />
        <path
          d={line}
          fill="none"
          stroke="#a85bb4"
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
          strokeLinejoin="round"
        />
      </svg>
      <div className="chart__axis">
        {labels.map((label, i) => (
          <span key={i}>{label}</span>
        ))}
      </div>
    </div>
  )
}
