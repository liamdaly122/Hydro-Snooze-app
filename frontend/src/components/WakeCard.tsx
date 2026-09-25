import type { ReactNode } from 'react'
import { Card } from './Card'
import { Toggle } from './Toggle'
import { Clock, Flame, Snowflake, Waves } from './Icons'
import {
  formatDays,
  formatDuration,
  formatTime,
  formatWhen,
  hasOtherTimes,
  homeNow,
  nextPlan,
  tint,
} from '../domain'
import type { Holiday, Schedule } from '../types'

interface Props {
  /** The night as it is actually being run, which is not always the routine. */
  draft: Schedule
  /** The routine, so a changed time can say what it usually is. */
  usual?: Schedule
  /**
   * What this night's alarm usually is: the weekend's on a weekend. Without it
   * a Saturday's 08:30 would read as a one-off change to the weekday 06:30.
   */
  usualWake?: string
  /** Stepped over, so the next bedtime shown is one that will happen. */
  holiday?: Holiday | null
  onDraftChange: (patch: Partial<Schedule>) => void
  onOpen: () => void
  children?: ReactNode
}

/**
 * The three things worth a glance, and a chevron for the rest.
 *
 * This card used to hold the whole schedule: stage durations, days, and a
 * pre-conditioning control. Two of those moved to their own screen and the third
 * stopped being a choice, which leaves the question the card is actually for:
 * when does it wake me, is it on, and when does the unit switch itself on.
 */
export function WakeCard({
  draft,
  usual,
  usualWake,
  holiday = null,
  onDraftChange,
  onOpen,
  children,
}: Props) {
  const thisNight = usualWake ?? usual?.wake_time
  const routine = usual ?? draft
  const plan = nextPlan(draft, homeNow(), holiday)
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
        {hasOtherTimes(routine) &&
          ` · ${formatDays(routine.other_days)} ${routine.other_bed_time} to ${routine.other_wake_time}`}
      </p>

      <div className="wake__row">
        <div className={`wake__time${draft.enabled ? '' : ' wake__time--off'}`}>
          {draft.wake_time}
          {/*
            A changed time has to say it is changed, or it reads as the routine
            and the next question is why the alarm moved on its own.
          */}
          {thisNight && thisNight !== draft.wake_time && (
            <span className="wake__only">
              Tonight only &middot; usually {thisNight.slice(0, 5)}
            </span>
          )}
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
          {plan ? `Bed ${formatWhen(plan.bedtimeAt)}` : 'Not scheduled'}
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

      {children}
    </Card>
  )
}
