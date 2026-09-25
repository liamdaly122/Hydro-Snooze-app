import type { SleepTiming, TimingBoundary } from '../types'
import { hourLabel, STAGE_COLOUR, wholeHours } from './Hypnogram'

/**
 * When I really sleep, against the parts of the night the bed runs.
 *
 * The chart is the recent nights laid over each other from the schedule's
 * lights out: how often I was in deep sleep, and in REM, at each point of the
 * night. Under it, the schedule's own parts, so the Deep part can be read
 * against the deep sleep directly above it.
 *
 * It suggests and never changes anything by itself. The button moves where
 * Drift and Deep end, in the Schedule screen's own steps, and the Schedule
 * screen moves them back. The temperatures are not touched.
 *
 * Everything worth deciding is decided by the service (withings/timing.py):
 * how many nights, whether they agree, where a boundary should go. This draws
 * it and says it in words.
 */

const WIDTH = 1000
const HEIGHT = 120
const TOP = 6

/** Minutes from lights out as a clock time, wrapping midnight. */
function clock(lightsOut: string, offset: number): string {
  const [h, m] = lightsOut.split(':').map(Number)
  const at = (((h! * 60 + m! + offset) % 1440) + 1440) % 1440
  return `${String(Math.floor(at / 60)).padStart(2, '0')}:${String(at % 60).padStart(2, '0')}`
}

function duration(minutes: number): string {
  const m = Math.abs(minutes)
  const h = Math.floor(m / 60)
  if (h === 0) return `${m}m`
  return m % 60 === 0 ? `${h}h` : `${h}h ${m % 60}m`
}

/** What the measured time is, for each boundary. */
const MEASURES: Record<TimingBoundary['part'], string> = {
  drift: 'Falling asleep',
  deep: 'Deep sleep mostly done',
}

/** Why moving each boundary to the sleep is the point. */
const WHY: Record<TimingBoundary['part'], string> = {
  drift: 'would start Deep closer to when you usually fall asleep',
  deep: 'would start REM once your deep sleep is usually over',
}

function verdict(b: TimingBoundary, t: SleepTiming): string | null {
  if (t.nights < t.suggests_at || b.measured === null) return null
  const now = clock(t.lights_out, b.ends_min)
  if (b.steady === false) {
    return `${b.label} ends at ${now}. Your nights vary too much here to pick a better time yet.`
  }
  if (b.suggest_min === null) return `${b.label} ends at ${now}, which already fits.`
  return `${b.label} ends at ${now}. Ending it at ${clock(t.lights_out, b.suggest_min)} ${WHY[b.part]}.`
}

export function SleepTimingCard({
  timing,
  onUse,
  busy = false,
}: {
  timing: SleepTiming
  /** Move the boundaries to where the service suggests. */
  onUse: (ends: Partial<Record<TimingBoundary['part'], number>>) => void
  busy?: boolean
}) {
  const t = timing
  const left = t.shows_at - t.nights
  // A schedule with no parts has nothing to set the sleep against.
  if (t.parts.length === 0) return null

  if (t.profile === null || left > 0) {
    return (
      <section className="card">
        <header className="card__head">
          <h2 className="card__label">Sleep timing</h2>
        </header>
        <p className="learn-lead">
          <b>
            {Math.max(1, left)} more night{Math.max(1, left) === 1 ? '' : 's'}
          </b>{' '}
          on the mat before there is a pattern to show.
        </p>
        <p className="footnote">
          This will show when your deep sleep and REM really happen, against the parts of your
          night, and suggest moving where Drift and Deep end once {t.suggests_at} nights agree.
        </p>
      </section>
    )
  }

  const x = (offset: number) => (Math.max(0, Math.min(t.night_minutes, offset)) / t.night_minutes) * WIDTH
  const y = (share: number) => HEIGHT - share * (HEIGHT - TOP)

  // Each bin plotted at its middle, closed down to the baseline at both ends.
  const area = (shares: number[]) => {
    const pts = shares.map((v, i) => `${x((i + 0.5) * t.bin_min).toFixed(1)} ${y(v).toFixed(1)}`)
    return `M0 ${HEIGHT} L0 ${y(shares[0] ?? 0).toFixed(1)} L${pts.join(' L')} L${WIDTH} ${y(
      shares[shares.length - 1] ?? 0,
    ).toFixed(1)} L${WIDTH} ${HEIGHT} Z`
  }
  const line = (shares: number[]) =>
    `M${shares.map((v, i) => `${x((i + 0.5) * t.bin_min).toFixed(1)} ${y(v).toFixed(1)}`).join(' L')}`

  // Clock hours along the bottom. Any day will do: only the hours are drawn.
  const [h, m] = t.lights_out.split(':').map(Number)
  const first = new Date(2000, 0, 1, h, m).getTime()
  const last = first + t.night_minutes * 60_000
  const hourX = (at: number) => ((at - first) / (last - first)) * 100

  const suggestions = t.boundaries.filter((b) => b.suggest_min !== null)
  const toGo = t.suggests_at - t.nights

  return (
    <section className="card">
      <header className="card__head">
        <h2 className="card__label">Sleep timing</h2>
      </header>
      <p className="timing__sub">
        When your deep sleep and REM really happen, against the parts of your night.
      </p>

      <div className="timing">
        <svg
          className="timing__svg"
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          preserveAspectRatio="none"
          role="img"
          aria-label={`How often you were in deep sleep and REM through the night, over ${t.nights} nights`}
        >
          {[0.5, 1].map((share) => (
            <line
              key={share}
              x1={0}
              x2={WIDTH}
              y1={y(share)}
              y2={y(share)}
              stroke="rgb(255 255 255 / 0.07)"
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
            />
          ))}
          <path d={area(t.profile.deep)} fill={STAGE_COLOUR.deep} opacity={0.45} />
          <path d={area(t.profile.rem)} fill={STAGE_COLOUR.rem} opacity={0.4} />
          <path
            d={line(t.profile.deep)}
            fill="none"
            stroke={STAGE_COLOUR.deep}
            strokeWidth={1.8}
            vectorEffect="non-scaling-stroke"
          />
          <path
            d={line(t.profile.rem)}
            fill="none"
            stroke={STAGE_COLOUR.rem}
            strokeWidth={1.8}
            vectorEffect="non-scaling-stroke"
          />
          {/* Where each part ends now, and where it could. */}
          {t.parts.slice(0, -1).map((p) => (
            <line
              key={p.part}
              x1={x(p.ends_min)}
              x2={x(p.ends_min)}
              y1={0}
              y2={HEIGHT}
              stroke="rgb(255 255 255 / 0.45)"
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
            />
          ))}
          {suggestions.map((b) => (
            <line
              key={b.part}
              x1={x(b.suggest_min!)}
              x2={x(b.suggest_min!)}
              y1={0}
              y2={HEIGHT}
              stroke="#7dffa0"
              strokeWidth={1.5}
              strokeDasharray="4 3"
              vectorEffect="non-scaling-stroke"
            />
          ))}
        </svg>
        <div className="bedchart__scale" aria-hidden="true">
          <span style={{ top: `${(y(1) / HEIGHT) * 100}%` }}>All</span>
          <span style={{ top: `${(y(0.5) / HEIGHT) * 100}%` }}>Half</span>
        </div>
      </div>

      {/*
        The schedule's parts, as a strip under the chart on the same minutes, so
        the Deep part sits directly under the deep sleep it is meant to cover.
      */}
      <div className="timing__parts" aria-label="The parts of your night">
        {t.parts.map((p) => (
          <span
            key={p.part}
            className={`timing__part timing__part--${p.part}`}
            style={{ width: `${((p.ends_min - p.starts_min) / t.night_minutes) * 100}%` }}
          >
            {(p.ends_min - p.starts_min) / t.night_minutes >= 0.14 ? p.label : ''}
          </span>
        ))}
      </div>

      <div className="hypno__axis" aria-hidden="true">
        {wholeHours(first, last).map((at) => (
          <span key={at} style={{ left: `${hourX(at)}%` }}>
            {hourLabel(at)}
          </span>
        ))}
      </div>

      <div className="hr-legend hr-legend--lines">
        <span className="hr-legend__item">
          <span className="hr-legend__swatch" style={{ background: STAGE_COLOUR.deep }} />
          Deep sleep
        </span>
        <span className="hr-legend__item">
          <span className="hr-legend__swatch" style={{ background: STAGE_COLOUR.rem }} />
          REM
        </span>
        {suggestions.length > 0 && (
          <span className="hr-legend__item">
            <span className="hr-legend__line timing__legend-suggest" />
            Suggested
          </span>
        )}
      </div>

      <div className="timing__facts">
        {t.boundaries.map((b) =>
          b.measured === null ? null : (
            <div key={b.part} className="timing__fact">
              <span className="timing__fact-name">{MEASURES[b.part]}</span>
              <span className="timing__fact-value">{clock(t.lights_out, b.measured.median_min)}</span>
              <span className="timing__fact-range">
                {b.part === 'drift'
                  ? `${duration(b.measured.median_min)} ${
                      b.measured.median_min < 0 ? 'before' : 'after'
                    } lights out. `
                  : ''}
                Half your nights: {clock(t.lights_out, b.measured.low_min)} to{' '}
                {clock(t.lights_out, b.measured.high_min)}.
              </span>
            </div>
          ),
        )}
      </div>

      {toGo > 0 ? (
        <p className="ap-verdict ap-verdict--dim">
          Suggestions start after {t.suggests_at} nights. {toGo} to go.
        </p>
      ) : (
        t.boundaries.map((b) => {
          const said = verdict(b, t)
          return said ? (
            <p key={b.part} className="ap-verdict">
              {said}
            </p>
          ) : null
        })
      )}

      {suggestions.length > 0 && (
        <button
          type="button"
          className="pill timing__use"
          disabled={busy}
          onClick={() =>
            onUse(Object.fromEntries(suggestions.map((b) => [b.part, b.suggest_min!])))
          }
        >
          Use suggested times
        </button>
      )}

      <p className="footnote">
        From your last {t.nights} night{t.nights === 1 ? '' : 's'} on the mornings your schedule
        runs, measured from lights out at {t.lights_out}. Mostly done means four fifths of that
        night&apos;s deep sleep. Using a suggestion only moves where each part ends. The
        temperatures stay as they are, and you can move the times back on the Schedule screen.
      </p>
    </section>
  )
}
