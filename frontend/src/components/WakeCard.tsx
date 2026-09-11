import { Card } from './Card'
import { Toggle } from './Toggle'
import { Clock, Flame, Snowflake, Waves } from './Icons'
import { formatDayTime, formatDays, formatDuration, formatTime, nextPlan, tint } from '../domain'
import type { Schedule } from '../types'

interface Props {
  draft: Schedule
  onDraftChange: (patch: Partial<Schedule>) => void
  onOpen: () => void
}

/**
 * The three things worth a glance, and a chevron for the rest.
 *
 * This card used to hold the whole schedule: stage durations, days, and a
 * pre-conditioning control. Two of those moved to their own screen and the third
 * stopped being a choice, which leaves the question the card is actually for:
 * when does it wake me, is it on, and when does the unit switch itself on.
 */
export function WakeCard({ draft, onDraftChange, onOpen }: Props) {
  const plan = nextPlan(draft)
  const firstTemp = draft.stages[0]?.temp_c ?? 20
  const warming = draft.preconditioning.mode === 'warming'

  // "Alarm" rather than "Wake". Wake is also the name of the last sleep stage,
  // three inches up this same screen with its own temperature, and one word
  // meaning two things on one screen is one too many.
  return (
    <Card label="Alarm" onOpen={onOpen} openLabel="Edit the schedule">
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
            {formatDuration(draft.night_minutes)}
          </span>
        </span>
      </div>
    </Card>
  )
}
