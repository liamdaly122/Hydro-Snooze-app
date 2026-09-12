import { useEffect, useState } from 'react'
import { AdjustmentsChart, KIND_COLOUR } from '../components/AdjustmentsChart'
import { Moon, Sparkle } from '../components/Icons'
import { LearningCard } from '../components/LearningCard'
import type { ApiClient } from '../api/client'
import type { AutopilotNight, Learning, Mode } from '../types'

/**
 * What the bed did last night.
 *
 * Every night this system produces a few thousand measurements and, before this
 * screen, nobody read any of them. The event log holds everything and is far too
 * long. The morning message holds four lines and is gone by the time anyone is
 * properly awake. So this is the middle: one screen, opened over breakfast, that
 * says how many times the service changed something, when, and whether the bed
 * ended up where it was asked to be.
 *
 * One thing on it is invented and it is marked twice: the boosts carry `for_fun`
 * from the service, and the card they sit on says out loud that nothing here
 * measures sleep. Everything else was written down while it happened.
 */

/**
 * Which night this is, said so it cannot be read as tonight.
 *
 * It used to be the wake date alone, and at five in the afternoon on Friday that
 * said "Friday, 11 September" about a night that had already finished nine hours
 * earlier. Every word of it was true and the whole thing read as a report on a
 * night nobody had slept yet.
 *
 * A night has two dates and naming one of them is the problem, so this names
 * both. "Last night" goes in front while the morning it ended is still today,
 * which is when anyone is actually reading this.
 */
function nightLabel(startsAt: string, wakeAt: string): string {
  // Assembled from parts rather than from one toLocaleDateString call, which
  // punctuates it as "Fri, 11 Sept" and puts a comma in the middle of a span.
  const part = (iso: string, month = false) => {
    const d = new Date(iso)
    const weekday = d.toLocaleDateString('en-GB', { weekday: 'short' })
    const mon = d.toLocaleDateString('en-GB', { month: 'short' })
    return month ? `${weekday} ${d.getDate()} ${mon}` : `${weekday} ${d.getDate()}`
  }
  const span = `${part(startsAt)} to ${part(wakeAt, true)}`

  const midnight = new Date()
  midnight.setHours(0, 0, 0, 0)
  return new Date(wakeAt) >= midnight ? `Last night · ${span}` : span
}

/** "Perfect", or how far off it typically sat. Never a bare number with no verdict. */
function howItHeld(off: number | null): string {
  if (off === null) return 'No probe readings for this night'
  if (off <= 0.3) return 'Held its setpoint all night'
  if (off <= 0.8) return `Typically ${off.toFixed(1)}° off its setpoint`
  if (off <= 2.0) return `Ran ${off.toFixed(1)}° off its setpoint on average`
  return `Drifted ${off.toFixed(1)}° off its setpoint on average`
}

export function Autopilot({ client }: { client: ApiClient }) {
  const [night, setNight] = useState<AutopilotNight | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [learning, setLearning] = useState<Learning | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let live = true
    void client
      .getAutopilot()
      .then((n) => live && setNight(n))
      .catch((e: Error) => live && setError(e.message))
    // Fetched apart from the report, and drawn even when there is no report to
    // draw. The first evening is exactly when "nothing measured yet, three
    // nights to go" is the most useful thing this screen can say, and that is
    // the morning getAutopilot has nothing for.
    void client.getLearning().then((l) => live && setLearning(l))
    return () => {
      live = false
    }
  }, [client])

  const change = (next: Promise<Learning>) => {
    setBusy(true)
    void next
      .then(setLearning)
      .catch(() => undefined)
      .finally(() => setBusy(false))
  }

  const learnCard = learning && (
    <LearningCard
      learning={learning}
      busy={busy}
      onSwitch={(on) => change(client.setLearning(on))}
      onForget={(mode: Mode) => change(client.forgetLearning(mode))}
    />
  )

  if (error) {
    return (
      <>
        <section className="card">
          <p className="empty">{error}</p>
          <p className="footnote">
            A report appears the morning after a night has finished. There is nothing to draw
            until then.
          </p>
        </section>
        {learnCard}
      </>
    )
  }

  if (!night) return <p className="empty">Reading last night…</p>

  return (
    <>
      {/* --- The hero ------------------------------------------------------- */}
      <section className="ap-hero">
        <Sparkle size={44} className="ap-hero__mark" glow />
        <p className="ap-hero__count">{night.adjustments}</p>
        <h2 className="ap-hero__title">Autopilot adjustments</h2>
        <p className="ap-hero__date">{nightLabel(night.starts_at, night.wake_at)}</p>

        {night.boosts.length > 0 && (
          <div className="ap-boosts">
            {night.boosts.map((boost) => (
              <div key={boost.key} className="ap-boost">
                <span className="ap-boost__label">
                  <Moon />
                  {boost.label}
                </span>
                <span className="ap-boost__value">&uarr; {boost.percent}%</span>
              </div>
            ))}
          </div>
        )}
      </section>

      {night.boosts.length > 0 && (
        <p className="footnote">
          The sleep figures above are for fun. Nothing in this bed measures sleep, so they are worked out from
          how tightly the water held its setpoints rather than from you. Not medical advice, and not
          a measurement.
        </p>
      )}

      {/* --- What it actually did ------------------------------------------- */}
      <section className="card">
        <header className="card__head">
          <h2 className="card__label">Temperature</h2>
        </header>

        <div className="ap-split">
          {/*
            On target rather than the adjustment count, which the hero three
            inches above already says in letters an inch high. Repeating it here
            reads as a mistake, and this is the more interesting number anyway:
            the count says how often Autopilot acted, this says whether it
            worked, and the chart underneath is a picture of exactly this.
          */}
          <div className="ap-split__total">
            <p className="ap-sublabel">On target</p>
            <p className="ap-total">{night.on_target === null ? '—' : `${night.on_target}%`}</p>
          </div>
          <div className="ap-split__breakdown">
            <p className="ap-sublabel">Breakdown</p>
            {/*
              Zero rows hidden. A night nobody touched shows "Set by hand 0"
              forever otherwise, and a row that is always zero is a row you stop
              reading, which costs the three next to it.
            */}
            {night.breakdown.filter((row) => row.count > 0).map((row) => (
              <p key={row.kind} className="ap-row">
                <span className="ap-row__dot" style={{ background: KIND_COLOUR[row.kind] }} />
                <span className="ap-row__label">{row.label}</span>
                <span className="ap-row__count">{row.count}</span>
              </p>
            ))}
          </div>
        </div>

        <AdjustmentsChart track={night.track} marks={night.marks} bands={night.bands} />

        <div className="ap-key">
          {night.breakdown.filter((row) => row.count > 0).map((row) => (
            <span key={row.kind} className="ap-key__item">
              <span className="ap-key__ring" style={{ borderColor: KIND_COLOUR[row.kind] }} />
              {row.label}
            </span>
          ))}
          <span className="ap-key__item ap-key__item--rule">
            <span className="ap-key__dash" />
            On setpoint
          </span>
        </div>
      </section>

      {/* --- The measured half ---------------------------------------------- */}
      <section className="card">
        <header className="card__head">
          <h2 className="card__label">The night</h2>
        </header>

        <div className="ap-facts">
          <div className="ap-fact">
            <span className="ap-fact__value">
              {night.stages.landed}/{night.stages.total}
            </span>
            <span className="ap-fact__label">Stages landed</span>
          </div>
          <div className="ap-fact">
            <span className="ap-fact__value">
              {/*
                Whole degrees here, and the tenths a line below. "18.6-26.4°" is
                ten characters in a column a third of a phone wide, and it
                truncated. The span is the point of this one; the precision
                belongs to the chart and to the sentence underneath it.
              */}
              {night.bed.low_c === null
                ? '—'
                : `${Math.round(night.bed.low_c)}–${Math.round(night.bed.high_c!)}°`}
            </span>
            <span className="ap-fact__label">Bed ran</span>
          </div>
          <div className="ap-fact">
            <span className="ap-fact__value">{night.energy_kwh || '—'}</span>
            <span className="ap-fact__label">kWh used</span>
          </div>
        </div>

        <p className="ap-verdict">{howItHeld(night.bed.typical_off_c)}</p>

        {night.ready && (
          <p className="ap-verdict ap-verdict--dim">
            {night.ready.reached
              ? `Ready in ${night.ready.minutes}m${
                  night.ready.start_c !== null && night.ready.end_c !== null
                    ? `, ${night.ready.start_c.toFixed(1)} to ${night.ready.end_c.toFixed(1)}°`
                    : ''
                }, measured on the hoses.`
              : `Getting ready ran ${night.ready.minutes}m without settling at ${night.ready.target_c}°.`}
          </p>
        )}

        {night.stages.missed.length > 0 && (
          <p className="ap-verdict ap-verdict--warn">Missed: {night.stages.missed.join(', ')}.</p>
        )}
      </section>

      {night.notes.length > 0 && (
        <section className="card">
          <header className="card__head">
            <h2 className="card__label">Worth a look</h2>
          </header>
          <ul className="ap-notes">
            {night.notes.slice(0, 5).map((note, i) => (
              <li key={i}>{note}</li>
            ))}
          </ul>
        </section>
      )}

      <p className="footnote">
        Rebuilt from what was recorded while the night happened, so it says what the service did
        rather than what it meant to do.
      </p>

      {learnCard}
    </>
  )
}
