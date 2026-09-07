import { Card } from './Card'
import { Toggle } from './Toggle'
import { Clock, Flame, Snowflake, Waves } from './Icons'
import {
  DAY_INITIALS,
  formatDayTime,
  formatDays,
  formatDuration,
  formatTime,
  nextPlan,
  tint,
} from '../domain'
import { STAGE_LABEL, type Schedule, type Stage } from '../types'

interface Props {
  draft: Schedule
  onDraftChange: (patch: Partial<Schedule>) => void
  onStageDuration: (stage: Stage, minutes: number) => void
}

const DURATION_STEP = 15

export function WakeCard({ draft, onDraftChange, onStageDuration }: Props) {
  const plan = nextPlan(draft)
  const firstTemp = draft.stages[0]?.temp_c ?? 20
  const pre = draft.preconditioning
  const warming = pre.mode === 'warming'

  function toggleDay(day: number) {
    const next = draft.days_of_week.includes(day)
      ? draft.days_of_week.filter((d) => d !== day)
      : [...draft.days_of_week, day].sort((a, b) => a - b)
    onDraftChange({ days_of_week: next })
  }

  return (
    <Card label="Wake">
      <p className="wake__days">
        {draft.days_of_week.length === 0
          ? 'No days selected'
          : `Every ${formatDays(draft.days_of_week)}`}
      </p>

      <div className="wake__row">
        <div className={`wake__time${draft.enabled ? '' : ' wake__time--off'}`}>
          {draft.wake_time}
        </div>
        <Toggle
          on={draft.enabled}
          onChange={(enabled) => onDraftChange({ enabled })}
          label="Run this schedule automatically"
        />
      </div>

      {/*
        Never a bare time. Waking at 06:30 on Tuesday means going to bed on
        Monday, and that off-by-one-day is exactly the sort of thing that ruins a
        night, so the day is always spelled out.
      */}
      <div className="chips">
        <span className="chip">
          <Clock />
          {plan ? `Bed ${formatDayTime(plan.bedtimeAt)}` : 'Not scheduled'}
        </span>
        <span className="chip">
          {warming ? <Flame /> : <Snowflake />}
          {plan?.precoolAt
            ? `${warming ? 'Pre-heat' : 'Pre-cool'} ${formatTime(plan.precoolAt)}`
            : 'No pre-conditioning'}
        </span>
        <span className="chip">
          <Waves />
          <span className="chip__accent" style={{ color: tint(firstTemp) }}>
            {formatDuration(draft.stages.reduce((n, s) => n + s.duration_minutes, 0))}
          </span>
        </span>
      </div>

      {/*
        Bedtime is not set directly. The stages run in order and finish at the
        wake time, so how long they add up to is what decides it.
      */}
      <p className="wake__sublabel">How long each part lasts</p>
      <div className="stages">
        {draft.stages.map((stage) => {
          const step = plan?.steps.find((s) => s.stage === stage.stage)
          return (
            <div className="stagerow" key={stage.stage}>
              <div className="stagerow__name">
                {STAGE_LABEL[stage.stage]}
                <span className="stagerow__at">{step ? formatTime(step.startsAt) : ''}</span>
              </div>
              <div className="stagerow__controls">
                <button
                  type="button"
                  className="stagerow__btn"
                  aria-label={`${STAGE_LABEL[stage.stage]} shorter`}
                  disabled={stage.duration_minutes <= DURATION_STEP}
                  onClick={() => onStageDuration(stage.stage, stage.duration_minutes - DURATION_STEP)}
                >
                  −
                </button>
                <span className="stagerow__value">{formatDuration(stage.duration_minutes)}</span>
                <button
                  type="button"
                  className="stagerow__btn"
                  aria-label={`${STAGE_LABEL[stage.stage]} longer`}
                  onClick={() => onStageDuration(stage.stage, stage.duration_minutes + DURATION_STEP)}
                >
                  +
                </button>
              </div>
            </div>
          )
        })}
      </div>

      {/*
        Not a control any more. A cooler cannot warm a bed and a heater cannot
        cool one, so the direction from room temperature to the first stage picks
        the mode, and the size of that gap picks how early to switch on. Choosing
        it by hand only ever offered a way to get it wrong.
      */}
      <p className="wake__sublabel">Getting the bed ready</p>
      <p className="footnote" style={{ marginTop: 0 }}>
        {pre.reason}
        {pre.mode && plan?.precoolAt
          ? ` Starting ${formatTime(plan.precoolAt)}, ${pre.lead_minutes} minutes before bed.`
          : ''}
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
    </Card>
  )
}
