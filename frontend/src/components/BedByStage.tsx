import type { HealthBed, HealthStage } from '../types'
import { hourLabel, STAGE_COLOUR, STAGE_LABEL, wholeHours } from './Hypnogram'

/**
 * What the bed was doing in each stage of sleep, which is what the whole Withings
 * integration is for.
 *
 * The bed's temperature through the night (the water coming back from it), with
 * what it was being asked for dashed underneath, over the stages themselves
 * as faint bands. Same minutes and same hours as the stages chart above it, so
 * reading down from one to the other lands on the same moment.
 *
 * Under it, what the bed averaged in each state: the start of an answer to
 * whether the temperature it was asked for suits the sleep that happened at it.
 *
 * A minute the probes said nothing in is a gap in the line. A flat line drawn
 * across a hole is the chart lying about the one thing it exists to show.
 */

const WIDTH = 1000
const HEIGHT = 150
const MIN_SPAN_C = 4

/** Whole degrees covering both lines, never less than four apart. See
 *  AdjustmentsChart: a night held within a tenth of a degree, scaled to fill the
 *  box, looks like a seismograph. */
function bounds(values: number[]): [number, number] {
  if (!values.length) return [20, 20 + MIN_SPAN_C]
  let low = Math.floor(Math.min(...values))
  let high = Math.ceil(Math.max(...values))
  const short = MIN_SPAN_C - (high - low)
  if (short > 0) {
    low -= Math.floor(short / 2)
    high += Math.ceil(short / 2)
  }
  return [low, high]
}

const ORDER = ['deep', 'rem', 'light', 'awake', 'out_of_bed'] as const

export function BedByStage({
  bed,
  stages,
  outOfBed,
  endsAt,
}: {
  bed: HealthBed
  stages: HealthStage[]
  outOfBed: { starts_at: string; ends_at: string }[]
  endsAt: string
}) {
  if (!bed.measured) {
    return (
      <p className="hr-source__line">
        No bed temperatures for this night. They come from the probes on the hoses, and they were
        not reporting.
      </p>
    )
  }

  const first = new Date(bed.starts_at).getTime()
  const last = new Date(endsAt).getTime()
  const span = Math.max(1, last - first)
  const x = (at: number | string) =>
    (((typeof at === 'number' ? at : new Date(at).getTime()) - first) / span) * WIDTH
  const at = (i: number) => first + i * bed.step_s * 1000

  const readings = bed.bed_c.filter((c): c is number => c !== null)
  const asked = bed.target_c.filter((c): c is number => c !== null)
  const [low, high] = bounds([...readings, ...asked])
  const y = (c: number) =>
    HEIGHT - 10 - ((Math.max(low, Math.min(high, c)) - low) / Math.max(1, high - low)) * (HEIGHT - 20)

  // The bed, broken wherever the probes were quiet.
  const line: string[] = []
  bed.bed_c.forEach((c, i) => {
    if (c === null) return
    const joined = i > 0 && bed.bed_c[i - 1] !== null
    line.push(`${joined ? 'L' : 'M'}${x(at(i)).toFixed(1)} ${y(c).toFixed(1)}`)
  })

  // What it was asked for, as a staircase: a number held until it changes.
  const wanted: string[] = []
  let held: number | null = null
  bed.target_c.forEach((c, i) => {
    if (c === null) {
      held = null
      return
    }
    const px = x(at(i)).toFixed(1)
    if (held === null) wanted.push(`M${px} ${y(c).toFixed(1)}`)
    else if (c !== held) wanted.push(`L${px} ${y(held).toFixed(1)}`, `L${px} ${y(c).toFixed(1)}`)
    else wanted.push(`L${px} ${y(c).toFixed(1)}`)
    held = c
  })

  const stride = Math.max(1, Math.ceil((high - low) / 4))
  const rules: number[] = []
  for (let c = low; c <= high; c += stride) rules.push(c)

  return (
    <>
      <div className="bedchart">
        <svg
          className="bedchart__svg"
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          preserveAspectRatio="none"
          role="img"
          aria-label="The bed's temperature through the night, over the stages of sleep"
        >
          {stages.map((s) => (
            <rect
              key={s.starts_at}
              x={x(s.starts_at)}
              y={0}
              width={Math.max(0.5, x(s.ends_at) - x(s.starts_at))}
              height={HEIGHT}
              fill={STAGE_COLOUR[s.stage]}
              opacity={s.stage === 'awake' ? 0.05 : 0.09}
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
          {rules.map((c) => (
            <line
              key={c}
              x1={0}
              x2={WIDTH}
              y1={y(c)}
              y2={y(c)}
              stroke="rgb(255 255 255 / 0.07)"
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
            />
          ))}
          <path
            d={wanted.join(' ')}
            fill="none"
            stroke="rgb(120 160 255 / 0.8)"
            strokeWidth={1.5}
            strokeDasharray="5 4"
            vectorEffect="non-scaling-stroke"
          />
          <path
            d={line.join(' ')}
            fill="none"
            stroke="rgb(255 255 255 / 0.9)"
            strokeWidth={1.8}
            strokeLinejoin="round"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
          />
        </svg>
        <div className="bedchart__scale" aria-hidden="true">
          {rules.map((c) => (
            <span key={c} style={{ top: `${(y(c) / HEIGHT) * 100}%` }}>
              {c}&deg;
            </span>
          ))}
        </div>
      </div>

      {/*
        The stages again, full strength, as a strip under the line. The bands
        behind it are a hint; this is the thing to read the stage off, directly
        under whatever the bed was doing at that moment.
      */}
      <svg
        className="bedchart__ribbon"
        viewBox={`0 0 ${WIDTH} 10`}
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        {stages.map((s) => (
          <rect
            key={s.starts_at}
            x={x(s.starts_at)}
            y={0}
            width={Math.max(0.5, x(s.ends_at) - x(s.starts_at))}
            height={10}
            fill={STAGE_COLOUR[s.stage]}
          />
        ))}
      </svg>

      <div className="hypno__axis" aria-hidden="true">
        {wholeHours(first, last).map((t) => (
          <span key={t} style={{ left: `${(x(t) / WIDTH) * 100}%` }}>
            {hourLabel(t)}
          </span>
        ))}
      </div>

      <div className="hr-legend hr-legend--lines">
        <span className="hr-legend__item">
          <span className="hr-legend__line" />
          Bed
        </span>
        <span className="hr-legend__item">
          <span className="hr-legend__line hr-legend__line--asked" />
          Asked for
        </span>
      </div>

      <h4 className="bedstages__title">In each stage, the bed averaged</h4>
      <div className="bedstages">
        {ORDER.map((key) => {
          const s = bed.by_stage[key]
          if (!s || s.of === 0) return null
          return (
            <div key={key} className="bedstages__item">
              <span
                className={`hr-legend__swatch${key === 'out_of_bed' ? ' hr-legend__swatch--out' : ''}`}
                style={key === 'out_of_bed' ? undefined : { background: STAGE_COLOUR[key] }}
              />
              <span className="bedstages__name">
                {key === 'out_of_bed' ? 'Out of bed' : STAGE_LABEL[key]}
              </span>
              <span className="bedstages__value">
                {s.mean_c === null ? '—' : `${s.mean_c.toFixed(1)}°`}
              </span>
            </div>
          )
        })}
      </div>
    </>
  )
}
