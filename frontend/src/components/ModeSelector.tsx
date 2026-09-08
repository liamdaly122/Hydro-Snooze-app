import { useState } from 'react'
import { Card } from './Card'
import { COOLING_MODES, MODE_LABEL, type DeviceState, type Mode } from '../types'

type Scope = 'now' | 'schedule'

interface Props {
  state: DeviceState
  /** Tonight's setting, used by every cooling stage. */
  scheduleMode: Mode
  onScheduleChange: (mode: Mode) => void
  /** Change the unit's mode right now. */
  onLiveChange: (mode: Mode) => void
}

/**
 * Cooling speeds, for the unit right now or for tonight's stages.
 *
 * These are two genuinely different things and the card used to conflate them:
 * one control that saved a schedule setting, with a change to the running unit
 * bolted on when it happened to be safe. That made the common case invisible
 * (nothing appeared to happen) and the useful case impossible (there was no way
 * to change the live speed without also changing tonight's).
 *
 * The same Now / rest-of-the-night split the Temperature card already uses, for
 * the same reason: what you are editing should be a thing you pick, not a thing
 * the app infers.
 *
 * Warming is never an option here. Whether a stage cools or warms is worked out
 * from its temperature, so it is not a speed to choose.
 */
export function ModeSelector({ state, scheduleMode, onScheduleChange, onLiveChange }: Props) {
  const [scope, setScope] = useState<Scope>('now')

  const on = state.power === 'on'
  const warming = state.assumed_mode === 'warming'
  // Only a cooling mode can highlight a cooling speed. Warming is a different
  // thing and unknown is not a value, so in both cases nothing is pressed.
  const liveMode = state.assumed_mode !== null && !warming ? state.assumed_mode : null

  const selected = scope === 'now' ? liveMode : scheduleMode
  const disabled = scope === 'now' && !on

  const nowValue =
    state.power === 'off'
      ? 'Off'
      : state.assumed_mode === null
        ? 'unknown'
        : MODE_LABEL[state.assumed_mode]

  return (
    <Card label="Cooling speed">
      <div className="tabs" role="tablist" aria-label="What the speed applies to">
        <Tab
          selected={scope === 'now'}
          onClick={() => setScope('now')}
          value={nowValue}
          // Dimmed whenever none of the three speeds below can be pressed, which
          // covers both "we do not know" and "the unit is warming, which is not
          // a speed". Different reasons, same thing to say: nothing here is
          // selected, and the note underneath explains which it is.
          dimmed={liveMode === null}
          label="Now"
        />
        <Tab
          selected={scope === 'schedule'}
          onClick={() => setScope('schedule')}
          value={MODE_LABEL[scheduleMode]}
          dimmed={false}
          label="Tonight"
        />
      </div>

      <div className="segmented" role="group" aria-label="Cooling speed">
        {COOLING_MODES.map((m) => (
          <button
            key={m}
            type="button"
            className="segment"
            aria-pressed={selected === m}
            disabled={disabled}
            onClick={() => (scope === 'now' ? onLiveChange(m) : onScheduleChange(m))}
          >
            {MODE_LABEL[m]}
          </button>
        ))}
      </div>

      <p className="footnote" style={{ marginTop: 12 }}>
        {note(scope, { on, warming, unknown: liveMode === null })}
      </p>
    </Card>
  )
}

/** Says what will happen before it happens, including when nothing will. */
function note(scope: Scope, s: { on: boolean; warming: boolean; unknown: boolean }): string {
  if (scope === 'schedule') {
    return 'Used by every cooling stage tonight. The unit is not changed now. Quiet is the slowest and the least noisy, which matters next to a bed.'
  }
  if (!s.on) return 'The unit is off. Only the power button responds.'
  if (s.warming) {
    // Not refused, because on this tab it was asked for explicitly. Just said
    // out loud first: it is a bigger change than picking a speed sounds.
    return 'The unit is warming. Picking a speed switches it to cooling, which will start cooling the bed.'
  }
  if (s.unknown) return "The unit's mode is unknown. Picking one sets it and settles that."
  return 'Changes the unit now. Tonight’s stages keep their own setting.'
}

function Tab({
  selected,
  onClick,
  value,
  dimmed,
  label,
}: {
  selected: boolean
  onClick: () => void
  value: string
  dimmed: boolean
  label: string
}) {
  return (
    <button type="button" role="tab" className="tab" aria-selected={selected} onClick={onClick}>
      <span className={`tab__value tab__value--mode${dimmed ? ' tab__value--unset' : ''}`}>
        {value}
      </span>
      <span className="tab__label">{label}</span>
    </button>
  )
}
