import type { PowerSample } from '../types'

/**
 * What the bed actually did, from the two hose probes and the room.
 *
 * The power chart next to this says what the machine did. This says what
 * happened to the bed, which is the thing the whole system exists to control and
 * which nothing could show until the probes went on.
 *
 * The gap between the two water lines is the part worth looking at. Wide means
 * the unit is working: water going out at one temperature and coming back at
 * another is heat moving into the bed or out of it. The two lines closing
 * together means the exchange has finished and the bed is where it was asked to
 * be.
 */

/** Below this the chart pads the range out. See `scale`. */
const MIN_SPAN_C = 6

type Line = { key: string; label: string; colour: string; dash?: string; get: (s: PowerSample) => number | null }

const LINES: Line[] = [
  { key: 'return', label: 'Bed', colour: '#a85bb4', get: (s) => s.return_c },
  { key: 'flow', label: 'Out', colour: '#0a84ff', get: (s) => s.flow_c },
  { key: 'room', label: 'Room', colour: '#636366', dash: '4 5', get: (s) => s.room_c },
]

/**
 * The temperature range to draw, padded out to at least six degrees.
 *
 * Fitting the axis to the data would be the obvious thing and it would lie. A
 * bed holding 27.0 to 27.4 all night is the best possible result, and scaled to
 * fill the box it looks like a rollercoaster. The floor keeps a steady night
 * looking steady.
 */
function scale(values: number[]): [number, number] {
  const low = Math.min(...values)
  const high = Math.max(...values)
  const short = MIN_SPAN_C - (high - low)
  if (short <= 0) return [Math.floor(low) - 1, Math.ceil(high) + 1]
  return [Math.floor(low - short / 2), Math.ceil(high + short / 2)]
}

export function BedChart({ samples }: { samples: PowerSample[] }) {
  const measured = samples.filter((s) => s.return_c !== null || s.flow_c !== null || s.room_c !== null)
  if (measured.length < 2) {
    return <p className="empty">No temperatures yet. They come from the probe board.</p>
  }

  const width = 1000
  const height = 200
  const firstAt = new Date(samples[0]!.at).getTime()
  const lastAt = new Date(samples[samples.length - 1]!.at).getTime()
  const span = Math.max(1, lastAt - firstAt)

  const all = LINES.flatMap((l) => samples.map(l.get)).filter((v): v is number => v !== null)
  const [low, high] = scale(all)

  const x = (at: string) => ((new Date(at).getTime() - firstAt) / span) * width
  const y = (c: number) => height - ((c - low) / (high - low)) * height

  /**
   * A path that breaks wherever the probe board was quiet.
   *
   * `M` after every gap rather than one continuous `L`, so a stretch with no
   * readings is empty instead of a straight line drawn between the two moments
   * either side of it. Inventing a line across a gap would be the chart lying
   * about exactly the thing it is there to show.
   */
  const path = (line: Line) => {
    let d = ''
    let broken = true
    for (const s of samples) {
      const value = line.get(s)
      if (value === null) {
        broken = true
        continue
      }
      d += `${broken ? 'M' : 'L'}${x(s.at).toFixed(1)} ${y(value).toFixed(1)} `
      broken = false
    }
    return d.trim()
  }

  const bed = samples.map((s) => s.return_c).filter((v): v is number => v !== null)
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
        aria-label={
          bed.length
            ? `Bed temperature over the last 24 hours, between ${Math.min(...bed).toFixed(
                1,
              )} and ${Math.max(...bed).toFixed(1)} degrees`
            : 'Water temperatures over the last 24 hours'
        }
      >
        {/*
          Painted back to front, so the bed sits on top. The two water lines
          converge whenever nothing is moving, which is most of a good night, and
          whichever is drawn last hides the other completely. The bed is the line
          worth seeing, so it wins.
        */}
        {[...LINES].reverse().map((line) => (
          <path
            key={line.key}
            d={path(line)}
            fill="none"
            stroke={line.colour}
            strokeWidth={line.key === 'room' ? 1.5 : 2}
            strokeDasharray={line.dash}
            vectorEffect="non-scaling-stroke"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ))}
      </svg>
      <div className="chart__axis">
        {labels.map((label, i) => (
          <span key={i}>{label}</span>
        ))}
      </div>
      <div className="chart__key">
        {LINES.map((line) => (
          <span key={line.key} className="chart__key-item">
            <span className="chart__swatch" style={{ background: line.colour }} />
            {line.label}
          </span>
        ))}
        <span className="chart__key-scale">
          {low}&ndash;{high}&deg;
        </span>
      </div>
    </div>
  )
}
