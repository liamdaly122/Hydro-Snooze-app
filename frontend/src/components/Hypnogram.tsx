import type { HealthStage, SleepStateName } from '../types'

/**
 * The night as a staircase through four levels: awake at the top, deep at the
 * bottom, REM and light between, the order the reference draws them in.
 *
 * Every stage is a bar at its own level, joined to the next by a thin upright,
 * so the eye follows one line through the night rather than reading a bar
 * chart.
 *
 * Time out of bed is a gap, drawn as a faint band with nothing in it. Withings
 * sends no stage for it and the chart does not invent one, which is the same
 * rule the bed temperature chart keeps: a line drawn across a hole is the chart
 * lying about the one thing it exists to show.
 *
 * Drawn in a stretched viewBox like the Autopilot chart, with everything that
 * would distort when stretched (the hour labels) kept outside it in HTML.
 */

export const STAGE_ORDER: SleepStateName[] = ['awake', 'rem', 'light', 'deep']

export const STAGE_COLOUR: Record<SleepStateName, string> = {
  awake: '#c9c9d1',
  rem: '#8cb6ff',
  light: '#3e62d6',
  deep: '#1f55ff',
}

export const STAGE_LABEL: Record<SleepStateName, string> = {
  awake: 'Awake',
  rem: 'REM',
  light: 'Light',
  deep: 'Deep',
}

const WIDTH = 1000
const HEIGHT = 140
const ROW = HEIGHT / STAGE_ORDER.length
const BAR = 13
const HOUR = 60 * 60 * 1000

export function hourLabel(at: number): string {
  return new Date(at).toLocaleTimeString('en-GB', { hour: '2-digit' }).slice(0, 2)
}

/**
 * Every whole hour inside a night. Whole hours in UTC are whole hours in London,
 * clocks going back included, and on that night one of them is labelled twice,
 * which is what actually happened.
 */
export function wholeHours(first: number, last: number): number[] {
  const hours: number[] = []
  for (let t = Math.ceil(first / HOUR) * HOUR; t <= last; t += HOUR) hours.push(t)
  return hours
}

export function Hypnogram({
  stages,
  outOfBed,
  startsAt,
  endsAt,
}: {
  stages: HealthStage[]
  outOfBed: { starts_at: string; ends_at: string }[]
  startsAt: string
  endsAt: string
}) {
  const first = new Date(startsAt).getTime()
  const last = new Date(endsAt).getTime()
  const span = Math.max(1, last - first)
  const x = (iso: string | number) =>
    (((typeof iso === 'number' ? iso : new Date(iso).getTime()) - first) / span) * WIDTH
  const middle = (stage: SleepStateName) => ROW * STAGE_ORDER.indexOf(stage) + ROW / 2

  const hours = wholeHours(first, last)

  return (
    <div className="hypno">
      <svg
        className="hypno__svg"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`Sleep stages from ${new Date(startsAt).toLocaleTimeString('en-GB', {
          hour: '2-digit',
          minute: '2-digit',
        })} to ${new Date(endsAt).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}`}
      >
        {STAGE_ORDER.map((stage) => (
          <line
            key={stage}
            x1={0}
            x2={WIDTH}
            y1={middle(stage)}
            y2={middle(stage)}
            stroke="rgb(255 255 255 / 0.05)"
            strokeWidth={1}
            vectorEffect="non-scaling-stroke"
          />
        ))}

        {outOfBed.map((gap) => (
          <rect
            key={gap.starts_at}
            x={x(gap.starts_at)}
            y={0}
            width={Math.max(1, x(gap.ends_at) - x(gap.starts_at))}
            height={HEIGHT}
            fill="rgb(255 255 255 / 0.06)"
          />
        ))}

        {stages.map((s, i) => {
          const next = stages[i + 1]
          const joined = next && next.starts_at === s.ends_at
          return (
            <g key={s.starts_at}>
              {joined && (
                <line
                  x1={x(s.ends_at)}
                  x2={x(s.ends_at)}
                  y1={middle(s.stage)}
                  y2={middle(next.stage)}
                  stroke="rgb(255 255 255 / 0.28)"
                  strokeWidth={1}
                  vectorEffect="non-scaling-stroke"
                />
              )}
              <rect
                x={x(s.starts_at)}
                y={middle(s.stage) - BAR / 2}
                width={Math.max(1, x(s.ends_at) - x(s.starts_at))}
                height={BAR}
                fill={STAGE_COLOUR[s.stage]}
              />
            </g>
          )
        })}
      </svg>

      <div className="hypno__axis" aria-hidden="true">
        {hours.map((t) => (
          <span key={t} style={{ left: `${(x(t) / WIDTH) * 100}%` }}>
            {hourLabel(t)}
          </span>
        ))}
      </div>
    </div>
  )
}
