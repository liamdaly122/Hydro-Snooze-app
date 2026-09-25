import { useEffect, useState } from 'react'
import { AdjustmentsChart, KIND_COLOUR } from '../components/AdjustmentsChart'
import { Moon, Sparkle } from '../components/Icons'
import { InfoButton } from '../components/InfoButton'
import { LearningCard } from '../components/LearningCard'
import { ScoreboardCard } from '../components/ScoreboardCard'
import { SuggestionFold } from '../components/Suggestion'
import { AutopilotSwitchCard } from '../components/AutopilotSwitchCard'
import { TestResultCard } from '../components/TestResultCard'
import { SleepTimingCard } from '../components/SleepTimingCard'
import type { ApiClient } from '../api/client'
import type {
  AutopilotNight,
  AutopilotSleep,
  AutopilotSwitch,
  HoldName,
  Learning,
  Mode,
  Schedule,
  Scoreboard,
  Suggestion,
  SleepStage,
  SleepTiming,
  Stage,
} from '../types'

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
 * The sleep in the hero is the mat's, set against my usual. It replaced three
 * invented "boosts" worked out from the water, from before anything in the house
 * could see sleep. Everything else was written down while it happened.
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

/** Which sensor said the bed was ready, in the morning message's own words. */
const HOW_READY: Record<string, string> = {
  probes: 'measured on the hoses',
  plug: 'measured off the plug',
}

/** "Perfect", or how far off it typically sat. Never a bare number with no verdict. */
function howItHeld(off: number | null): string {
  if (off === null) return 'No probe readings for this night'
  if (off <= 0.3) return 'Held its setpoint all night, once you were in bed'
  if (off <= 0.8) return `Typically ${off.toFixed(1)}° off its setpoint after lights out`
  if (off <= 2.0) return `Ran ${off.toFixed(1)}° off its setpoint on average after lights out`
  return `Drifted ${off.toFixed(1)}° off its setpoint on average after lights out`
}

function minutes(seconds: number): string {
  const m = Math.round(seconds / 60)
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`
}

/**
 * The change against usual, as an arrow and a percentage. The arrow says which
 * way the number went; the colour says whether that is the good way, which for
 * time to fall asleep is down.
 */
function Change({ s }: { s: AutopilotSleep }) {
  if (s.change_pct === null) return null
  const arrow = s.change_pct > 0 ? '\u2191' : s.change_pct < 0 ? '\u2193' : ''
  const tone = s.better === null ? '' : s.better ? ' ap-sleep__change--better' : ' ap-sleep__change--worse'
  return (
    <span className={`ap-sleep__change${tone}`}>
      {arrow} {Math.abs(s.change_pct)}%
    </span>
  )
}

/**
 * The schedule's stages with some of their ends moved, each stage taking what
 * its neighbour gives up, the same way the Schedule screen's buttons do it. The
 * ends not named stay exactly where they are.
 */
function withEnds(stages: SleepStage[], ends: Partial<Record<Stage, number>>): SleepStage[] {
  let from = 0
  let running = 0
  return stages.map((s) => {
    running += s.duration_minutes
    const to = ends[s.stage] ?? running
    const out = { ...s, duration_minutes: to - from }
    from = to
    return out
  })
}

export function Autopilot({ client, schedule }: { client: ApiClient; schedule: Schedule }) {
  const [night, setNight] = useState<AutopilotNight | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [learning, setLearning] = useState<Learning | null>(null)
  const [busy, setBusy] = useState(false)
  const [timing, setTiming] = useState<SleepTiming | null>(null)
  const [board, setBoard] = useState<Scoreboard | null>(null)
  const [suggestion, setSuggestion] = useState<Suggestion | null>(null)
  const [deciding, setDeciding] = useState(false)
  const [autopilot, setAutopilot] = useState<AutopilotSwitch | null>(null)
  const autopilotOn = autopilot === null ? null : autopilot.on
  const [switching, setSwitching] = useState(false)
  const [moving, setMoving] = useState(false)

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
    void client
      .getScoreboard()
      .then((b) => live && setBoard(b))
      .catch(() => undefined)
    void client
      .getSuggestion()
      .then((s) => live && setSuggestion(s))
      .catch(() => undefined)
    void client
      .getAutopilotSwitch()
      .then((a) => live && setAutopilot(a))
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [client])

  // Again whenever the schedule changes, from here or anywhere else, because
  // every boundary it talks about is the schedule's.
  useEffect(() => {
    let live = true
    void client
      .getSleepTiming()
      .then((t) => live && setTiming(t))
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [client, schedule])

  const takeTimes = (ends: Partial<Record<Stage, number>>) => {
    setMoving(true)
    void client
      .putSchedule({ stages: withEnds(schedule.stages, ends) })
      .then(() => client.getSleepTiming())
      .then(setTiming)
      .catch(() => undefined)
      .finally(() => setMoving(false))
  }

  const startTimingAgain = () => {
    setMoving(true)
    void client
      .forgetSleepTiming()
      .then(setTiming)
      .catch(() => undefined)
      .finally(() => setMoving(false))
  }

  const boardCard = board && <ScoreboardCard board={board} />

  const decide = (work: Promise<Suggestion>) => {
    setDeciding(true)
    void work
      .then(setSuggestion)
      .catch(() => undefined)
      .finally(() => setDeciding(false))
  }

  // Everything that depends on the switch is asked again after it moves: the
  // suggestion goes to off or comes back, and Learning says whether it is used.
  const flip = (on: boolean) => {
    setSwitching(true)
    void client
      .setAutopilotSwitch(on)
      .then(setAutopilot)
      .then(() => client.getSuggestion())
      .then(setSuggestion)
      .catch(() => undefined)
      .finally(() => setSwitching(false))
  }

  const holdAt = (hold: HoldName) => {
    setSwitching(true)
    void client
      .setHold(hold)
      .then(setAutopilot)
      .catch(() => undefined)
      .finally(() => setSwitching(false))
  }

  const switchCard = autopilot !== null && (
    <AutopilotSwitchCard state={autopilot} onSwitch={flip} onHold={holdAt} busy={switching} />
  )

  const suggestCard = suggestion && (
    <SuggestionFold
      suggestion={suggestion}
      busy={deciding}
      onAccept={() => decide(client.acceptSuggestion())}
      onDecline={() => decide(client.declineSuggestion())}
      onReach={(reach) => decide(client.setSuggestionReach(reach))}
    />
  )

  const timingCard = timing && (
    <SleepTimingCard
      timing={timing}
      onUse={takeTimes}
      onStartAgain={startTimingAgain}
      busy={moving}
      canUse={autopilotOn !== false}
    />
  )

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
      autopilotOn={autopilotOn !== false}
    />
  )

  if (error) {
    return (
      <>
        {switchCard}
        <section className="card">
          <p className="empty">{error}</p>
          <p className="footnote">
            A report appears the morning after a night has finished. There is nothing to draw
            until then.
          </p>
        </section>
        {suggestCard}
        {timingCard}
        {boardCard}
        {learnCard}
      </>
    )
  }

  if (!night) return <p className="empty">Reading last night…</p>

  return (
    <>
      {switchCard}

      {/* --- The hero ------------------------------------------------------- */}
      <section className="ap-hero">
        <Sparkle size={44} className="ap-hero__mark" glow />
        <p className="ap-hero__count">{night.adjustments}</p>
        <h2 className="ap-hero__title">Autopilot adjustments</h2>
        <div className="ap-hero__dateline">
          <p className="ap-hero__date">{nightLabel(night.starts_at, night.wake_at)}</p>
          <InfoButton title="Last night">
            <p>
              Autopilot adjustments counts every time the service sent the unit a new temperature
              or a new mode during the night, from what it actually sent.
            </p>
            {night.sleep.length > 0 && (
              <p>
                {night.sleep.some((s) => s.usual_seconds === null)
                  ? 'Deep sleep, REM and time to fall asleep are measured by the Sleep Analyzer. The change against your usual appears once there are three nights before this one.'
                  : `Deep sleep, REM and time to fall asleep are measured by the Sleep Analyzer, against your usual: the middle of your last ${night.sleep[0]!.nights} nights. They say what changed, not what changed it.`}
              </p>
            )}
          </InfoButton>
        </div>

        {night.sleep.length > 0 && (
          <div className="ap-sleep">
            {night.sleep.map((s) => (
              <div key={s.key} className="ap-sleep__row">
                <span className="ap-sleep__label">
                  <Moon />
                  {s.label}
                </span>
                <span className="ap-sleep__value">
                  {minutes(s.seconds)} <Change s={s} />
                </span>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* The morning after a test night: what was tried and how it compared. */}
      {night.test && <TestResultCard test={night.test} />}

      {/* --- What it actually did ------------------------------------------- */}
      <section className="card">
        <header className="card__head">
          <h2 className="card__label">Temperature</h2>
          <InfoButton title="Temperature">
            <p>
              On target is how much of the night, after lights out, the bed was within half a
              degree of what it was asked for. The chart is a picture of it: the dashed line is
              what was asked for, the solid one the bed.
            </p>
            <p>
              Each dot is one adjustment, coloured by why it happened. Phase and mode change is the
              night moving on to its next part. Getting the bed ready is switching on before
              bedtime. Drift response is a correction partway through a part, because the bed
              drifted. Set by hand is you.
            </p>
            <p>
              Rebuilt from what was recorded while the night happened, so it says what the service
              did rather than what it meant to do.
            </p>
            {!night.from_record && (
              <p>
                This night was recorded before the app started writing down what it was asking
                for, so the dashed line and the percentage are worked out backwards from your
                schedule. Editing your routine moves them. Nights from then on carry their own
                record.
              </p>
            )}
          </InfoButton>
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
            {/*
              After lights out, not from the moment the bed started getting
              ready. That stretch is the bed on its way to the number rather than
              failing to hold it, and counting it made a slow pre-heat read as a
              bad night. How getting ready went is its own line further down.
            */}
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

        {!night.from_record && (
          <p className="ap-note">Worked out from your schedule, not recorded on the night.</p>
        )}

        <div className="ap-key">
          {night.breakdown.filter((row) => row.count > 0).map((row) => (
            <span key={row.kind} className="ap-key__item">
              <span className="ap-key__ring" style={{ borderColor: KIND_COLOUR[row.kind] }} />
              {row.label}
            </span>
          ))}
          <span className="ap-key__item ap-key__item--rule">
            <span className="ap-key__dash" />
            Asked for
          </span>
          <span className="ap-key__item ap-key__item--rule">
            <span className="ap-key__solid" />
            Bed
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
                }, ${HOW_READY[night.ready.decided_by ?? ''] ?? 'measured'}.`
              : `Getting ready ran ${night.ready.minutes}m without settling at ${night.ready.target_c}°.`}
          </p>
        )}

        {night.stages.missed.length > 0 && (
          <p className="ap-verdict ap-verdict--warn">Missed: {night.stages.missed.join(', ')}.</p>
        )}
        {/* Not a warning. These are stages somebody called off by switching
            automation off or skipping mid-night, and drawing them in the same
            colour as a failure would say the night went wrong when it went as
            asked. Older service builds do not send the field, hence the guard. */}
        {(night.stages.cancelled?.length ?? 0) > 0 && (
          <p className="ap-verdict">Cancelled, as asked: {night.stages.cancelled.join(', ')}.</p>
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

      {suggestCard}
      {timingCard}
      {boardCard}
      {learnCard}
    </>
  )
}
