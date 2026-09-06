import { Card } from './Card'
import { Toggle } from './Toggle'
import { Clock, Snowflake, Waves } from './Icons'
import { DAY_INITIALS, formatDays, formatDayTime, formatTime, nextPlan, tint } from '../domain'
import { MODE_LABEL, type Schedule } from '../types'

interface Props {
  draft: Schedule
  onDraftChange: (patch: Partial<Schedule>) => void
}

export function WakeCard({ draft, onDraftChange }: Props) {
  const plan = nextPlan(draft)

  function toggleDay(day: number) {
    const next = draft.days_of_week.includes(day)
      ? draft.days_of_week.filter((d) => d !== day)
      : [...draft.days_of_week, day].sort((a, b) => a - b)
    onDraftChange({ days_of_week: next })
  }

  return (
    <Card label="Wake">
      <p className="wake__days">
        {draft.days_of_week.length === 0 ? 'No days selected' : `Every ${formatDays(draft.days_of_week)}`}
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
        Never a bare time. Waking at 06:30 on Tuesday means arming at 22:00 on
        Monday, and that off-by-one-day is exactly the sort of thing that ruins a
        night, so the day is always spelled out.
      */}
      <div className="chips">
        <span className="chip">
          <Clock />
          {plan ? `Arms ${formatDayTime(plan.armAt)}` : 'Not scheduled'}
        </span>
        <span className="chip">
          <Snowflake />
          {plan?.precoolAt ? `Pre-cool ${formatTime(plan.precoolAt)}` : 'No pre-cool'}
        </span>
        <span className="chip">
          <Waves />
          <span className="chip__accent" style={{ color: tint(draft.phase1_temp_c) }}>
            {MODE_LABEL[draft.mode]}
          </span>
        </span>
      </div>

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
