import type { AutopilotBand, AutopilotMark, AutopilotPoint, AdjustmentKind } from '../types'

/**
 * Every adjustment Autopilot made last night, on the line it made it to.
 *
 * Two lines, both in degrees: what was asked for, and what the bed did. The
 * gap between them is the story, and it is a gap you can read off the axis
 * rather than a number you have to take on trust.
 *
 * It used to draw one line of *offsets* from the setpoint, on the argument that
 * a night stepping 19° to 26° has no single line to be near. True, and the cost
 * was worse: a step in that line could be the bed moving or the target moving,
 * which are opposite kinds of news, and nothing on the chart said which. The
 * target is drawn as a staircase, holding its value until it actually changes,
 * because a sloped line between two setpoints would be a temperature nobody
 * ever asked for.
 *
 * The dots are where the service did something, coloured by why. They sit on the
 * bed line rather than beside it, so the shape of the night and the reasons for
 * it are the same picture.
 */

export const KIND_COLOUR: Record<AdjustmentKind, string> = {
  phase: '#9bea4a',
  precool: '#e35ce0',
  quiet: '#ffffff',
  manual: '#ffb340',
}

/** The smallest span of degrees the axis will draw. See `bounds`. */
const MIN_SPAN_C = 4

/**
 * The degrees to draw, covering both lines with a little air.
 *
 * Floored at four degrees because a night that held one setpoint within a tenth
 * of a degree is the best possible result and, scaled to fill the box, looks
 * like a seismograph.
 *
 * Rounded outwards to whole degrees so the gridlines land on numbers somebody
 * would say out loud, which is most of the point of drawing this in degrees at
 * all.
 */
function bounds(values: number[]): [number, number] {
  if (!values.length) return [18, 18 + MIN_SPAN_C]
  let low = Math.floor(Math.min(...values))
  let high = Math.ceil(Math.max(...values))
  const short = MIN_SPAN_C - (high - low)
  if (short > 0) {
    low -= Math.floor(short / 2)
    high += Math.ceil(short / 2)
  }
  return [low, high]
}

/**
 * The target as a staircase: each reading holds the previous value until the
 * moment it changes, so a stage boundary is a vertical edge rather than a ramp.
 *
 * A straight line between 19° and 26° would draw every temperature in between as
 * though it had been asked for, and none of them were.
 */
function staircase(
  track: AutopilotPoint[],
  x: (at: string) => number,
  y: (c: number) => number,
): string {
  const parts: string[] = []
  let held: number | null = null
  for (const p of track) {
    const at = x(p.at).toFixed(1)
    if (held === null) parts.push(`M${at} ${y(p.target_c).toFixed(1)}`)
    else if (p.target_c !== held) parts.push(`L${at} ${y(held).toFixed(1)}`, `L${at} ${y(p.target_c).toFixed(1)}`)
    held = p.target_c
  }
  const last = track[track.length - 1]
  if (last) parts.push(`L${x(last.at).toFixed(1)} ${y(last.target_c).toFixed(1)}`)
  return parts.join(' ')
}

function clock(at: number): string {
  return new Date(at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

export function AdjustmentsChart({
  track,
  marks,
  bands,
}: {
  track: AutopilotPoint[]
  marks: AutopilotMark[]
  bands: AutopilotBand[]
}) {
  if (track.length < 2) {
    return (
      <p className="empty">
        No temperatures for this night. The chart comes from the hose probes.
      </p>
    )
  }

  const width = 1000
  const height = 190
  const firstAt = new Date(track[0]!.at).getTime()
  const lastAt = new Date(track[track.length - 1]!.at).getTime()
  const span = Math.max(1, lastAt - firstAt)
  const [low, high] = bounds([
    ...track.map((p) => p.bed_c),
    ...track.map((p) => p.target_c),
  ])
  const degrees = Math.max(1, high - low)

  // Inset by a hair at both ends, so a dot on the very first or very last reading
  // sits inside the box instead of hanging half of itself over the edge. The
  // first one always is: it is the moment the bed started getting ready.
  const EDGE = 6
  const x = (at: string | number) =>
    EDGE +
    (((typeof at === 'number' ? at : new Date(at).getTime()) - firstAt) / span) * (width - EDGE * 2)
  // Clamped, so a reading past the top of the axis runs along the edge instead
  // of being drawn somewhere off the chart with a line shooting up to meet it.
  const y = (c: number) =>
    height - 10 - ((Math.max(low, Math.min(high, c)) - low) / degrees) * (height - 20)

  const bedLine = track
    .map((p, i) => `${i ? 'L' : 'M'}${x(p.at).toFixed(1)} ${y(p.bed_c).toFixed(1)}`)
    .join(' ')
  const wantedLine = staircase(track, x, y)

  // Only the marks that landed inside the window and have somewhere to sit. A
  // dot at an invented height would be the chart making something up, which is
  // the one thing this project does not do.
  const dots = marks.filter((m) => {
    const at = new Date(m.at).getTime()
    return m.bed_c !== null && at >= firstAt && at <= lastAt
  })

  // Whole degrees, at most five of them, so the labels stay readable on a phone.
  const stride = Math.max(1, Math.ceil(degrees / 4))
  const rules: number[] = []
  for (let c = low; c <= high; c += stride) rules.push(c)
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => firstAt + span * f)

  return (
    <div className="apchart">
      <div className="apchart__plot">
        <svg
          className="apchart__svg"
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="none"
          role="img"
          aria-label={`${dots.length} adjustments through the night, with the bed temperature against the temperature asked for`}
        >
          {/*
            The stages behind everything, alternating so the boundaries are
            visible without a label for each one. Faint enough to be a texture
            rather than a thing to read.
          */}
          {bands.map((band, i) =>
            i % 2 ? null : (
              <rect
                key={band.starts_at}
                x={Math.max(0, x(band.starts_at))}
                y={0}
                width={Math.max(0, Math.min(width, x(band.ends_at)) - Math.max(0, x(band.starts_at)))}
                height={height}
                fill="rgb(255 255 255 / 0.025)"
              />
            ),
          )}

          {rules.map((c) => (
            <line
              key={c}
              x1={0}
              x2={width}
              y1={y(c)}
              y2={y(c)}
              stroke="rgb(255 255 255 / 0.07)"
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
            />
          ))}

          {/* Asked for, underneath. Dashed, because it is an instruction rather
              than a measurement, and every other dashed thing in this app means
              the same. */}
          <path
            d={wantedLine}
            fill="none"
            stroke="rgb(120 160 255 / 0.75)"
            strokeWidth={1.5}
            strokeDasharray="5 4"
            strokeLinejoin="miter"
            vectorEffect="non-scaling-stroke"
          />

          {/* Measured, on top, solid and brighter. */}
          <path
            d={bedLine}
            fill="none"
            stroke="rgb(255 255 255 / 0.75)"
            strokeWidth={1.5}
            strokeLinejoin="round"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
          />
        </svg>

        {/*
          The dots in their own layer, positioned in percentages rather than in
          the stretched viewBox above. The chart is drawn 1000 units wide and
          squeezed to the width of a phone, and a circle drawn inside that comes
          out as an egg.
        */}
        <div className="apchart__dots">
          {dots.map((m, i) => (
            <span
              key={`${m.at}-${i}`}
              className={`apchart__dot apchart__dot--${m.kind}`}
              style={{
                left: `${(x(m.at) / width) * 100}%`,
                top: `${(y(m.bed_c!) / height) * 100}%`,
                borderColor: KIND_COLOUR[m.kind],
              }}
              title={`${clock(new Date(m.at).getTime())} · ${m.detail}`}
            />
          ))}
        </div>

        <div className="apchart__scale" aria-hidden="true">
          {rules.map((c) => (
            <span key={c} style={{ top: `${(y(c) / height) * 100}%` }}>
              {c}&deg;
            </span>
          ))}
        </div>
      </div>

      <div className="apchart__axis">
        {ticks.map((at) => (
          <span key={at}>{clock(at)}</span>
        ))}
      </div>
    </div>
  )
}
