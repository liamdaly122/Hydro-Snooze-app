import { useEffect, useRef, useState } from 'react'
import { Card } from './Card'
import { Minus, Plus } from './Icons'
import { canSetTemperature, clampToMode, formatTemp, tint, tintAlpha } from '../domain'
import { MODE_RANGE, type DeviceState, type Mode, type Schedule } from '../types'

export type TabKey = 'now' | 'phase1' | 'phase2' | 'phase3'

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'now', label: 'Now' },
  { key: 'phase1', label: 'Phase 1' },
  { key: 'phase2', label: 'Phase 2' },
  { key: 'phase3', label: 'Wake' },
]

/** How long to wait after the last tap before firing a real infrared run. */
const COMMIT_DELAY_MS = 900

interface Props {
  state: DeviceState
  /** The locally edited schedule, which may differ from what the unit holds. */
  draft: Schedule
  maxC: number
  onDraftChange: (patch: Partial<Schedule>) => void
  onSetNow: (targetC: number) => void
}

export function TemperatureCard({ state, draft, maxC, onDraftChange, onSetNow }: Props) {
  const [tab, setTab] = useState<TabKey>('now')

  // "Now" is edited optimistically and committed once the tapping stops, because
  // every commit is a 25-plus press infrared run and firing one per tap would be
  // both slow and pointless.
  const [pendingNow, setPendingNow] = useState<number | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout>>()

  useEffect(() => setPendingNow(null), [state.assumed_target_c])
  useEffect(() => () => clearTimeout(timer.current), [])

  const nowMode: Mode = state.assumed_mode ?? draft.mode
  const nowValue = pendingNow ?? state.assumed_target_c
  const editable = canSetTemperature(state)

  const values: Record<TabKey, number | null> = {
    now: nowValue,
    phase1: draft.phase1_temp_c,
    phase2: draft.phase2_temp_c,
    phase3: draft.phase3_temp_c,
  }

  const selected = values[tab]
  const mode = tab === 'now' ? nowMode : draft.mode
  const [low] = MODE_RANGE[mode]
  const ceiling = Math.min(MODE_RANGE[mode][1], maxC)

  // The glow takes its colour from whichever tab is selected, so changing tabs
  // washes the card through blue, purple and pink as the temperatures do.
  const glowTemp = selected ?? draft.phase1_temp_c

  function step(delta: number) {
    if (tab === 'now') {
      if (!editable) return
      // With no confirmed target there is nothing to step from, so seed at the
      // phase 1 temperature. Railing makes the command absolute either way.
      const base = nowValue ?? draft.phase1_temp_c
      const next = clampToMode(base + delta, nowMode, maxC)
      setPendingNow(next)
      clearTimeout(timer.current)
      timer.current = setTimeout(() => onSetNow(next), COMMIT_DELAY_MS)
      return
    }
    const current = values[tab] ?? draft.phase1_temp_c
    const next = clampToMode(current + delta, draft.mode, maxC)
    const key = ({ phase1: 'phase1_temp_c', phase2: 'phase2_temp_c', phase3: 'phase3_temp_c' } as const)[
      tab
    ]
    onDraftChange({ [key]: next })
  }

  const canDown = tab !== 'now' || editable
  const canUp = canDown
  const atFloor = selected !== null && selected <= low
  const atCeiling = selected !== null && selected >= ceiling

  return (
    <Card label="Temperature">
      <div className="tabs" role="tablist" aria-label="Temperature to edit">
        {TABS.map(({ key, label }) => {
          const value = values[key]
          return (
            <button
              key={key}
              type="button"
              role="tab"
              className="tab"
              aria-selected={tab === key}
              onClick={() => setTab(key)}
            >
              <span
                className={`tab__value${value === null ? ' tab__value--unknown' : ''}`}
                style={value === null ? undefined : { color: tint(value) }}
              >
                {formatTemp(value)}
                {value === null ? '' : '°'}
              </span>
              <span className="tab__label">{label}</span>
            </button>
          )
        })}
      </div>

      <div
        className="stage"
        style={
          {
            '--glow-core': tintAlpha(glowTemp, 0.44),
            '--glow-strong': tintAlpha(glowTemp, 0.5),
            '--glow-soft': tintAlpha(glowTemp, 0.27),
          } as React.CSSProperties
        }
      >
        <button
          type="button"
          className="step"
          onClick={() => step(-1)}
          disabled={!canDown || atFloor}
          aria-label="Colder"
        >
          <Minus />
        </button>

        {selected === null ? (
          <span className="stage__value stage__value--unknown">unknown</span>
        ) : (
          <span className="stage__value">
            {selected}
            <span className="stage__unit">°C</span>
          </span>
        )}

        <button
          type="button"
          className="step"
          onClick={() => step(1)}
          disabled={!canUp || atCeiling}
          aria-label="Warmer"
        >
          <Plus />
        </button>

        {tab === 'now' && <StageNote state={state} unknown={nowValue === null} ceiling={ceiling} atCeiling={atCeiling} maxC={maxC} />}
      </div>
    </Card>
  )
}

/**
 * The card never guesses. When a control is dead, it says which of the unit's
 * real constraints made it dead.
 */
function StageNote({
  state,
  unknown,
  atCeiling,
  ceiling,
  maxC,
}: {
  state: DeviceState
  unknown: boolean
  atCeiling: boolean
  ceiling: number
  maxC: number
}) {
  let note: string | null = null
  if (state.power === 'off') {
    note = 'Unit is off. Only the power button responds.'
  } else if (state.power === 'unknown') {
    note = 'Unit state unknown. Check the plug reading below.'
  } else if (state.in_schedule === 'true') {
    note = 'Schedule running. The unit ignores temperature presses until it ends.'
  } else if (state.in_schedule === 'unknown') {
    note = 'Not sure whether a schedule is running, so this may be ignored.'
  } else if (unknown) {
    note = 'No confirmed target. Press + or − to set one.'
  } else if (atCeiling && ceiling === maxC) {
    note = `${maxC}°C safety cap.`
  }
  return note ? <p className="stage__note">{note}</p> : null
}
