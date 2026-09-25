import type { AutopilotTest } from '../types'
import { InfoButton } from './InfoButton'
import { NightsProgress } from './NightsProgress'

/**
 * Last night's test, the morning after: what was tried, what the mat measured,
 * and where that leaves the scoreboard.
 *
 * Set beside the average at the usual, never judged on its own. One night says
 * almost nothing, so the card says how many nights the test temperature has and
 * how many it needs before it is compared at all. The verdict, when there is
 * one, is the scoreboard's.
 */

function minutes(seconds: number): string {
  const m = Math.round(Math.abs(seconds) / 60)
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`
}

function verdict(t: AutopilotTest): string {
  if (t.counted === false) {
    return `${t.label} was changed by hand during the night, or did not hold its setting, so this one does not count.`
  }
  if (t.verdict === 'clear' && t.leader_c !== null) {
    return t.leader_c === t.set_c
      ? `${t.set_c}° is now clearly ahead on your scoreboard.`
      : `${t.leader_c}° is still clearly ahead on your scoreboard.`
  }
  if (t.test_nights < t.needs) {
    return `One night says very little on its own. ${t.set_c}° needs ${t.needs} nights before it is compared.`
  }
  return 'Not sure yet: the gap is still inside how much ordinary nights vary.'
}

export function TestResultCard({ test }: { test: AutopilotTest }) {
  const t = test
  const which = t.offset_c < 0 ? 'cooler' : 'warmer'
  const gap = t.together_s !== null && t.usual_mean_s !== null ? t.together_s - t.usual_mean_s : null

  return (
    <section className="card test-result">
      <header className="card__head">
        <h2 className="card__label">Last night&apos;s test</h2>
        <InfoButton title="Last night's test">
          <p>
            About one night in three, the evening suggestion moves one part a degree to find out
            what that does. This is how last night went.
          </p>
          <p>
            It is set beside your average at the usual temperature for that part, from the nights
            that count. Deep sleep and REM are added together, because that is what Autopilot
            pushes for.
          </p>
          <p>
            One night says very little: ordinary nights vary by more than a degree is likely to
            change. The scoreboard only calls a temperature ahead once it has enough nights and the
            gap is bigger than that variation.
          </p>
        </InfoButton>
      </header>

      <p className="test-result__what">
        {t.label} at <b>{t.set_c}°</b>, a degree {which} than your usual {t.usual_c}°.
      </p>

      {t.together_s === null ? (
        <p className="ap-verdict ap-verdict--dim">
          Waiting for the Sleep Analyzer&apos;s night. It usually arrives a few minutes after you get
          up.
        </p>
      ) : (
        <div className="test-result__compare">
          <div className="test-result__figure">
            <span className="test-result__value">{minutes(t.together_s)}</span>
            <span className="test-result__label">Deep sleep and REM</span>
            {t.deep_s !== null && t.rem_s !== null && (
              <span className="test-result__split">
                deep {minutes(t.deep_s)} · REM {minutes(t.rem_s)}
              </span>
            )}
          </div>
          <div className="test-result__figure test-result__figure--usual">
            <span className="test-result__value">
              {t.usual_mean_s === null ? 'None yet' : minutes(t.usual_mean_s)}
            </span>
            <span className="test-result__label">
              Usual at {t.usual_c}°
              {t.usual_nights > 0 ? `, ${t.usual_nights} nights` : ''}
            </span>
            {gap !== null && (
              <span
                className={`test-result__gap${gap > 0 ? ' test-result__gap--more' : gap < 0 ? ' test-result__gap--less' : ''}`}
              >
                {gap === 0 ? 'the same' : `${minutes(gap)} ${gap > 0 ? 'more' : 'less'} last night`}
              </span>
            )}
          </div>
        </div>
      )}

      <p className="ap-verdict">{verdict(t)}</p>

      {t.counted !== false && t.test_nights < t.needs && (
        <NightsProgress
          nights={t.test_nights}
          needs={t.needs}
          label={`Compared at ${t.needs}`}
        />
      )}
    </section>
  )
}
