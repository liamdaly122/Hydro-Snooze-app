import { Card } from '../components/Card'
import { RehearsalCard } from '../components/RehearsalCard'
import { Toggle } from '../components/Toggle'
import { Flame, Snowflake } from '../components/Icons'
import { DAY_INITIALS, formatDayTime, formatDays, formatDuration, formatTime, nextPlan } from '../domain'
import {
  MIN_STAGE_MINUTES,
  STAGE_LABEL,
  type DeviceState,
  type Schedule as ScheduleType,
} from '../types'

interface Props {
  draft: ScheduleType
  onChange: (patch: Partial<ScheduleType>) => void
  error: string | null
  onDismissError: () => void
  state: DeviceState
  onStartRehearsal: (seconds: number) => Promise<void>
  onStopRehearsal: () => Promise<void>
}

/** Boundaries move by a quarter of an hour, which is also a stage's floor. */
const STEP = MIN_STAGE_MINUTES

function toMinutes(hhmm: string): number {
  const [h, m] = hhmm.split(':').map(Number)
  return h * 60 + m
}

function toHhMm(minutes: number): string {
  const wrapped = ((minutes % 1440) + 1440) % 1440
  return `${String(Math.floor(wrapped / 60)).padStart(2, '0')}:${String(wrapped % 60).padStart(2, '0')}`
}

/**
 * Everything about the night, off the home screen.
 *
 * Bedtime and the wake time are what get set. The night is the gap between them,
 * and the stages divide it up: each row sets when that stage ends, so the time it
 * gains comes off the stage after it and the night stays exactly as long as the
 * two times say it is. There is no way in here to build a night that does not add
 * up, which is the point of editing boundaries rather than durations.
 */
export function Schedule({
  draft,
  onChange,
  error,
  onDismissError,
  state,
  onStartRehearsal,
  onStopRehearsal,
}: Props) {
  const plan = nextPlan(draft)
  const pre = draft.preconditioning
  const bedMinutes = toMinutes(draft.bed_time)

  // Where each stage ends, as minutes after lights out.
  let running = 0
  const ends = draft.stages.map((stage) => (running += stage.duration_minutes))

  function moveBoundary(index: number, delta: number) {
    // The last stage has no boundary of its own: it ends at the wake time.
    if (index >= draft.stages.length - 1) return
    const durations = draft.stages.map((s) => s.duration_minutes)
    const next = durations[index] + delta
    const after = durations[index + 1] - delta
    if (next < STEP || after < STEP) return
    durations[index] = next
    durations[index + 1] = after
    onChange({
      stages: draft.stages.map((s, i) => ({ ...s, duration_minutes: durations[i] })),
    })
  }

  function toggleDay(day: number) {
    onChange({
      days_of_week: draft.days_of_week.includes(day)
        ? draft.days_of_week.filter((d) => d !== day)
        : [...draft.days_of_week, day].sort((a, b) => a - b),
    })
  }

  return (
    <>
      <Card label="The night">
        <div className="timerow timerow--first">
          <span className="timerow__label">Run automatically</span>
          <Toggle
            on={draft.enabled}
            onChange={(enabled) => onChange({ enabled })}
            label="Run this schedule automatically"
          />
        </div>

        <div className="timerow">
          <label className="timerow__label" htmlFor="bed-time">
            Lights out
          </label>
          <input
            id="bed-time"
            className="timerow__input"
            type="time"
            value={draft.bed_time}
            onChange={(e) => e.target.value && onChange({ bed_time: e.target.value })}
          />
        </div>
        <div className="timerow">
          <label className="timerow__label" htmlFor="wake-time">
            Wake
          </label>
          <input
            id="wake-time"
            className="timerow__input"
            type="time"
            value={draft.wake_time}
            onChange={(e) => e.target.value && onChange({ wake_time: e.target.value })}
          />
        </div>
        <p className="footnote">
          {formatDuration(draft.night_minutes)} in bed
          {plan ? `, from ${formatDayTime(plan.bedtimeAt)}` : ''}. The wake time sets temperature
          only. It is not an alarm and cannot wake you.
        </p>
      </Card>

      {/*
        Boundaries, not durations. Moving one takes the time from the stage after
        it, so the night stays exactly as long as the two times above say it is
        and no stage can be squeezed out of existence.
      */}
      <Card label="Parts of the night">
        <div className="stages">
          {draft.stages.map((stage, index) => {
            const last = index === draft.stages.length - 1
            const endsAt = toHhMm(bedMinutes + ends[index])
            return (
              <div className="stagerow" key={stage.stage}>
                <div className="stagerow__name">
                  {STAGE_LABEL[stage.stage]}
                  <span className="stagerow__at">{formatDuration(stage.duration_minutes)}</span>
                </div>
                <div className="stagerow__controls">
                  {last ? (
                    <span className="stagerow__value stagerow__value--fixed">
                      until {draft.wake_time}
                    </span>
                  ) : (
                    <>
                      <button
                        type="button"
                        className="stagerow__btn"
                        aria-label={`${STAGE_LABEL[stage.stage]} ends earlier`}
                        disabled={stage.duration_minutes <= STEP}
                        onClick={() => moveBoundary(index, -STEP)}
                      >
                        −
                      </button>
                      <span className="stagerow__value">until {endsAt}</span>
                      <button
                        type="button"
                        className="stagerow__btn"
                        aria-label={`${STAGE_LABEL[stage.stage]} ends later`}
                        disabled={(draft.stages[index + 1]?.duration_minutes ?? 0) <= STEP}
                        onClick={() => moveBoundary(index, STEP)}
                      >
                        +
                      </button>
                    </>
                  )}
                </div>
              </div>
            )
          })}
        </div>
        <p className="footnote">
          Each part ends when the next begins, so moving a boundary takes the time from the part
          after it. The last one always runs to the wake time. Set the temperatures on the home
          screen.
        </p>
      </Card>

      <Card label="Days">
        <p className="wake__days">
          {draft.days_of_week.length === 0
            ? 'No days selected'
            : `Every ${formatDays(draft.days_of_week)}`}
        </p>
        <div className="days" role="group" aria-label="Days to wake">
          {DAY_INITIALS.map((initial, day) => (
            <button
              key={day}
              type="button"
              className="day"
              aria-pressed={draft.days_of_week.includes(day)}
              aria-label={formatDays([day])}
              onClick={() => toggleDay(day)}
            >
              {initial}
            </button>
          ))}
        </div>
        <p className="footnote">
          Keyed to the morning you wake up. Waking at {draft.wake_time} on Tuesday means going to
          bed on Monday.
        </p>
      </Card>

      {/*
        Never a control. A cooler cannot warm a bed and a heater cannot cool one,
        so the direction from room temperature to the first stage picks the mode,
        and the size of that gap picks how early to switch on.
      */}
      <Card label="Getting the bed ready">
        <div className="chips">
          <span className="chip">
            {pre.mode === 'warming' ? <Flame /> : <Snowflake />}
            {plan?.precoolAt ? formatTime(plan.precoolAt) : 'Nothing to do'}
          </span>
          {pre.mode && (
            <span className="chip">
              {pre.mode} for {formatDuration(pre.lead_minutes)}
            </span>
          )}
        </div>
        <p className="footnote">{pre.reason}</p>
        <p className="footnote">
          Worked out, not chosen. It assumes the room sits at about 20°C, which is the one number
          here that a real thermometer would improve.
        </p>
      </Card>

      <RehearsalCard
        state={state}
        schedule={draft}
        onStart={onStartRehearsal}
        onStop={onStopRehearsal}
      />

      {error && (
        <p className="footnote" onClick={onDismissError}>
          {error}
        </p>
      )}
    </>
  )
}