import { useEffect, useRef, useState } from 'react'
import { Card } from './Card'
import { Minus, Plus } from './Icons'
import { canSetTemperature, formatTemp, tint, tintAlpha } from '../domain'
import {
  MODE_RANGE,
  STAGE_LABEL,
  STAGE_ORDER,
  WARMING_FLOOR_C,
  type DeviceState,
  type Schedule,
  type Stage,
} from '../types'

/** "Now" is the live temperature. The rest are the parts of the night. */
export type TabKey = 'now' | Stage

const TABS: TabKey[] = ['now', ...STAGE_ORDER]

/** How long to wait after the last tap before firing a real infrared run. */
const COMMIT_DELAY_MS = 900

interface Props {
  state: DeviceState
  /** The locally edited night. */
  draft: Schedule
  maxC: number
  onStageChange: (stage: Stage, tempC: number) => void
  onSetNow: (targetC: number) => void
}

export function TemperatureCard({ state, draft, maxC, onStageChange, onSetNow }: Props) {
  const [tab, setTab] = useState<TabKey>('now')

  // "Now" is edited optimistically and committed once the tapping stops, because
  // every commit is a 25-plus press infrared run and firing one per tap would be
  // both slow and pointless.
  const [pendingNow, setPendingNow] = useState<number | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout>>()

  useEffect(() => setPendingNow(null), [state.assumed_target_c])
  useEffect(() => () => clearTimeout(timer.current), [])

  const nowValue = pendingNow ?? state.assumed_target_c
  const editable = canSetTemperature(state)

  const stageOf = (stage: Stage) => draft.stages.find((s) => s.stage === stage)
  const valueFor = (key: TabKey): number | null =>
    key === 'now' ? nowValue : (stageOf(key)?.temp_c ?? null)

  const selected = valueFor(tab)
  const fallback = draft.stages[0]?.temp_c ?? 20

  // Each stage works out for itself whether it cools or warms, so the range it
  // can be set to follows from the temperature rather than from a mode setting.
  const rangeFor = (value: number) =>
    MODE_RANGE[value >= WARMING_FLOOR_C ? 'warming' : draft.cooling_speed]

  function step(delta: number) {
    const current = selected ?? fallback
    const next = current + delta
    // 24 to 25 crosses from cooling into warming, and both can express those, so
    // stepping across the boundary needs no special handling beyond the ranges.
    const [low, high] = rangeFor(next)
    const clamped = Math.max(low, Math.min(Math.min(high, maxC), next))

    if (tab === 'now') {
      if (!editable) return
      setPendingNow(clamped)
      clearTimeout(timer.current)
      timer.current = setTimeout(() => onSetNow(clamped), COMMIT_DELAY_MS)
      return
    }
    onStageChange(tab, clamped)
  }

  const glowTemp = selected ?? fallback
  const [low, high] = rangeFor(selected ?? fallback)
  const ceiling = Math.min(high, maxC)
  const canEdit = tab !== 'now' || editable
  const atFloor = selected !== null && selected <= low
  const atCeiling = selected !== null && selected >= ceiling

  return (
    <Card label="Temperature">
      <div className="tabs" role="tablist" aria-label="Part of the night to edit">
        {TABS.map((key) => {
          const value = valueFor(key)
          const label = key === 'now' ? 'Now' : STAGE_LABEL[key]
          const running = key !== 'now' && state.current_stage === key
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
              <span className="tab__label">
                {label}
                {running && <span className="tab__live" aria-label="running now" />}
              </span>
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
          disabled={!canEdit || atFloor}
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
          disabled={!canEdit || atCeiling}
          aria-label="Warmer"
        >
          <Plus />
        </button>

        <StageNote
          state={state}
          tab={tab}
          value={selected}
          atCeiling={atCeiling}
          ceiling={ceiling}
          maxC={maxC}
        />
      </div>
    </Card>
  )
}

/**
 * The card never guesses. When a control is dead, or a temperature means
 * something other than it looks like, it says so.
 */
function StageNote({
  state,
  tab,
  value,
  atCeiling,
  ceiling,
  maxC,
}: {
  state: DeviceState
  tab: TabKey
  value: number | null
  atCeiling: boolean
  ceiling: number
  maxC: number
}) {
  let note: string | null = null

  if (tab === 'now') {
    if (state.power === 'off') note = 'Unit is off. Only the power button responds.'
    else if (state.power === 'unknown') note = 'Unit state unknown. Check the plug reading below.'
    else if (value === null) note = 'No confirmed target. Press + or − to set one.'
  } else if (value !== null && value >= WARMING_FLOOR_C) {
    note = `Heats the bed to ${value}°C.`
  } else if (value !== null) {
    note = `Cools the bed to ${value}°C.`
  }

  if (atCeiling && ceiling === maxC) note = `${maxC}°C safety cap.`
  return note ? <p className="stage__note">{note}</p> : null
}
