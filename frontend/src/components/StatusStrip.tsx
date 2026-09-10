import { Card } from './Card'

/** One decimal, or a dash. Never a stale value dressed up as a current one. */
const temp = (c: number | null) => (c === null ? '--' : `${c.toFixed(1)}°`)

import { formatWatts } from '../domain'
import type { DeviceState } from '../types'

import { STAGE_LABEL } from '../types'

const POWER_LABEL: Record<DeviceState['power'], string> = {
  on: 'On',
  off: 'Off',
  unknown: 'unknown',
}

/**
 * Shows what is known and says "unknown" for the rest. The unit cannot be read
 * over infrared, so a confident-looking value the app has not confirmed would be
 * a lie, and at 3am a lie here is worse than a blank.
 */
export function StatusStrip({
  state,
  onMute,
}: {
  state: DeviceState
  onMute?: () => void
}) {
  const watts = formatWatts(state.observed_power_w)
  const activity = state.inferred_activity === 'unknown' ? null : state.inferred_activity

  const flow = state.observed_flow_c
  const back = state.observed_return_c
  const hasProbes =
    flow !== null || back !== null || state.observed_room_c !== null

  // What the water is measurably doing to the bed, from the difference between
  // the two readings above. One word, because the numbers are right there and
  // the gap between them is the size: what cannot be read off them is the
  // direction, and that is what this says.
  //
  // Worth having next to the mode, which is a belief. This is the same claim
  // arrived at by measurement, so the two disagreeing is a finding rather than
  // a display bug.
  const moving =
    flow === null || back === null
      ? null
      : Math.abs(back - flow) < 0.3
        ? 'holding'
        : back > flow
          ? 'cooling'
          : 'warming'

  return (
    <Card label="Status">
      <div className="status">
        <Cell label="Unit" value={POWER_LABEL[state.power]} unknown={state.power === 'unknown'} />
        <Cell
          label="Stage"
          value={state.current_stage ? STAGE_LABEL[state.current_stage] : 'Not running'}
          unknown={false}
        />
        <Cell
          label="Draw"
          value={watts}
          sub={activity}
          unknown={state.observed_power_w === null}
        />
      </div>

      {/*
        The water, measured. Everything above this except the draw is something
        the app decided rather than something it read, because infrared is
        one-way. These three are read off the hoses.

        Shown only when there are probes, rather than as three permanent blanks
        on a setup that has none.
      */}
      {hasProbes && (
        <div className="status">
          <Cell label="Flow" value={temp(state.observed_flow_c)} sub="out" unknown={state.observed_flow_c === null} />
          <Cell label="Return" value={temp(state.observed_return_c)} sub={moving} unknown={state.observed_return_c === null} />
          <Cell label="Room" value={temp(state.observed_room_c)} unknown={state.observed_room_c === null} />
        </div>
      )}
      {state.last_error && <p className="status__error">{state.last_error}</p>}

      {/*
        A setup action, not a nightly one. The unit remembers whether it is
        muted, so this is a toggle you press once and never think about again.
      */}
      {onMute && (
        <button
          type="button"
          className="status__link"
          disabled={state.power !== 'on'}
          onClick={onMute}
        >
          Toggle the unit's beep
        </button>
      )}
    </Card>
  )
}

function Cell({
  label,
  value,
  unknown,
  sub,
}: {
  label: string
  value: string
  unknown: boolean
  sub?: string | null
}) {
  return (
    <div className="status__cell">
      <div className="status__key">{label}</div>
      <div className={`status__val${unknown ? ' status__val--unknown' : ''}`} title={value}>
        {value}
      </div>
      {sub && <div className="status__sub">{sub}</div>}
    </div>
  )
}
