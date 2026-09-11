import type { AutopilotBand, AutopilotMark, AutopilotPoint, AdjustmentKind } from '../types'

/**
 * Every adjustment Autopilot made last night, on the line it made it to.
 *
 * The y axis is **distance from setpoint**, not temperature. A night that steps
 * from 19° to 26° has no single line to be near, and drawing it against one
 * makes a night that went perfectly look like a climb. Zero is the bed being
 * exactly where it was asked to be, which is the thing worth seeing at a glance,
 * and it gets the dashed rule across the middle.
 *
 * The dots are where the service did something, coloured by why. They sit on the
 * line rather than beside it, so the shape of the night and the reasons for it
 * are the same picture.
 */

export const KIND_COLOUR: Record<AdjustmentKind, string> = {
  phase: '#9bea4a',
  precool: '#e35ce0',
  quiet: '#ffffff',
  manual: '#ffb340',
}

/** Half the smallest range the axis will draw. See `scale`. */
const MIN_HALF_C = 1.5

/** Above this share of readings, the rest is allowed off the top. See `scale`. */
const KEEP = 0.94

/**
 * The range to draw: symmetrical about zero, never tighter than ±1.5°, and fitted
 * to almost all of the night rather than to all of it.
 *
 * Symmetrical because the dashed rule has to sit in the middle to read as "on
 * target". Floored because a bed that held within a tenth of a degree all night
 * is the best possible result and, scaled to fill the box, looks like a
 * seismograph.
 *
 * And fitted to 94% because the first half hour is the bed arriving at
 * temperature from wherever the room left it, which is several degrees out and
 * is *supposed* to be. Scaling to that squashes the eight hours that follow into
 * a flat line through the middle, so the one stretch of the night nothing was
 * controlling gets to decide how the rest of it looks. It runs off the top
 * instead, which is the honest picture of it.
 */
function scale(values: number[]): number {
  if (!values.length) return MIN_HALF_C
  const sorted = values.map(Math.abs).sort((a, b) => a - b)
  const kept = sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * KEEP))]!
  return Math.max(MIN_HALF_C, Math.ceil(kept * 2) / 2)
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
  const half = scale(track.map((p) => p.offset_c))

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
    height / 2 - (Math.max(-half, Math.min(half, c)) / half) * (height / 2 - 10)

  const line = track.map((p, i) => `${i ? 'L' : 'M'}${x(p.at).toFixed(1)} ${y(p.offset_c).toFixed(1)}`).join(' ')

  // Only the marks that landed inside the window and have somewhere to sit. A
  // dot at an invented height would be the chart making something up, which is
  // the one thing this project does not do.
  const dots = marks.filter((m) => {
    const at = new Date(m.at).getTime()
    return m.offset_c !== null && at >= firstAt && at <= lastAt
  })

  const rules = [half, half / 2, 0, -half / 2, -half]
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => firstAt + span * f)

  return (
    <div className="apchart">
      <div className="apchart__plot">
        <svg
          className="apchart__svg"
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="none"
          role="img"
          aria-label={`${dots.length} adjustments through the night, against how far the bed sat from its setpoint`}
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
              stroke={c === 0 ? 'rgb(255 255 255 / 0.35)' : 'rgb(255 255 255 / 0.07)'}
              strokeWidth={1}
              strokeDasharray={c === 0 ? '6 6' : undefined}
              vectorEffect="non-scaling-stroke"
            />
          ))}

          <path
            d={line}
            fill="none"
            stroke="rgb(255 255 255 / 0.55)"
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
                top: `${(y(m.offset_c!) / height) * 100}%`,
                borderColor: KIND_COLOUR[m.kind],
              }}
              title={`${clock(new Date(m.at).getTime())} · ${m.detail}`}
            />
          ))}
        </div>

        <div className="apchart__scale" aria-hidden="true">
          {rules.map((c) => (
            <span key={c} style={{ top: `${(y(c) / height) * 100}%` }}>
              {c > 0 ? `+${c}` : c}
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
