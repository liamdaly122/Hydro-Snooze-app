import { Flame, Snowflake } from './Icons'
import type { TonightState } from '../types'

/**
 * The controls that only make sense from inside a running night.
 *
 * One degree, one period, no stacking. Liam was firm about that and he is right:
 * a nudge that can be tapped up to four degrees is a temperature control with a
 * timer on it, and there is already a temperature control three inches up. This
 * is a fidget, for the moment you are too warm but not warm enough to think
 * about it.
 *
 * There is no second tap to make. Once one is running the two pills are replaced
 * by the live row, so stacking is not something the app declines to do, it is
 * something it never offers.
 */
export function NudgeControls({
  tonight,
  now,
  onNudge,
  onCancel,
  disabled = false,
}: {
  tonight: TonightState
  now: Date
  onNudge: (deltaC: number) => void
  onCancel: () => void
  disabled?: boolean
}) {
  const until = tonight.nudge_until ? new Date(tonight.nudge_until) : null
  const live = tonight.nudge_c !== 0 && until !== null && until > now

  if (live) {
    const left = Math.max(1, Math.round((until.getTime() - now.getTime()) / 60000))
    const way = tonight.nudge_c < 0 ? 'cooler' : 'warmer'
    return (
      <div className="nudge">
        <span className="nudge__text">
          <b>
            {Math.abs(tonight.nudge_c)}&deg; {way}
          </b>{' '}
          for another {left} minute{left === 1 ? '' : 's'}, then back to the plan
        </span>
        <button type="button" className="nudge__x" onClick={onCancel} aria-label="Stop the nudge">
          &#10005;
        </button>
      </div>
    )
  }

  return (
    <div className="pills">
      <button type="button" className="pill" disabled={disabled} onClick={() => onNudge(-1)}>
        <Snowflake />1&deg; cooler, 30 min
      </button>
      <button type="button" className="pill" disabled={disabled} onClick={() => onNudge(1)}>
        <Flame />1&deg; warmer, 30 min
      </button>
    </div>
  )
}

/**
 * Making tonight's temperatures the usual ones.
 *
 * The old automatic behaviour, now only when it is asked for and only when there
 * is something to ask about. It names the number and the stage rather than
 * saying "save", because this is the one control here that outlives tonight.
 */
export function KeepTonight({
  tonight,
  usualStages,
  onKeep,
}: {
  tonight: TonightState
  usualStages: { stage: string; temp_c: number }[]
  onKeep: () => void
}) {
  if (!tonight.stages_changed) return null

  const moved = tonight.running.stages.filter((s) => {
    const before = usualStages.find((u) => u.stage === s.stage)
    return before && before.temp_c !== s.temp_c
  })
  if (moved.length === 0) return null

  const what =
    moved.length === 1
      ? `Save ${moved[0]!.temp_c}° as my usual ${labelOf(moved[0]!.stage)}`
      : `Save tonight's ${moved.length} temperatures as my usual`

  return (
    <div className="pills">
      <button type="button" className="pill pill--keep pill--wide" onClick={onKeep}>
        &#10003; {what}
      </button>
    </div>
  )
}

function labelOf(stage: string): string {
  return { deep: 'Deep', rem: 'REM', wake: 'Wake' }[stage] ?? stage
}

/**
 * Shaping the night, which is something you do before you are in it.
 *
 * Fifteen minutes a tap, and the resulting time is shown rather than the step,
 * so two taps for half an hour is obvious and nobody has to hold a running total.
 * "Skip tonight" is not here: it is the one with a real consequence, so it lives
 * behind the chevron where a sleeve cannot reach it.
 */
export function ShiftControls({
  bedTime,
  wakeTime,
  onShift,
  showBedEarly,
  disabled = false,
}: {
  bedTime: string
  wakeTime: string
  onShift: (patch: { bed_minutes?: number; wake_minutes?: number }) => void
  showBedEarly: boolean
  disabled?: boolean
}) {
  return (
    <div className="pills">
      {showBedEarly && (
        <button
          type="button"
          className="pill"
          disabled={disabled}
          onClick={() => onShift({ bed_minutes: -15 })}
        >
          &darr; Bed early
          <span className="pill__now">{bedTime.slice(0, 5)}</span>
        </button>
      )}
      <button
        type="button"
        className="pill"
        disabled={disabled}
        onClick={() => onShift({ wake_minutes: 15 })}
      >
        &uarr; Sleep in
        <span className="pill__now">{wakeTime.slice(0, 5)}</span>
      </button>
    </div>
  )
}
