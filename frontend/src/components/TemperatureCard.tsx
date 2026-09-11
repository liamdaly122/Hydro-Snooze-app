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
  /** Opens the saved nights. The card is where a whole night's temperatures live,
   *  so it is where saving and loading a set of them belongs. */
  onOpenProfiles?: () => void
}

export function TemperatureCard({
  state,
  draft,
  maxC,
  onStageChange,
  onSetNow,
  onOpenProfiles,
}: Props) {
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

  // The full span a temperature can occupy, not the current mode's range.
  //
  // Cooling covers 15 to 35 and warming covers 25 to 55, so the union runs 15 to
  // 55 with no gap: below 25 it cools, at 25 and above it warms. Bounding the
  // buttons by whichever mode the current value happens to fall in trapped it at
  // exactly 25, because warming's floor IS 25, so the minus button disabled
  // itself and there was no way back down.
  const floor = MODE_RANGE.quiet[0]
  const ceiling = Math.min(MODE_RANGE.warming[1], maxC)

  function step(delta: number) {
    const current = selected ?? fallback
    const clamped = Math.max(floor, Math.min(ceiling, current + delta))

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
  const canEdit = tab !== 'now' || editable
  const atFloor = selected !== null && selected <= floor
  const atCeiling = selected !== null && selected >= ceiling

  return (
    <Card label="Temperature" onOpen={onOpenProfiles} openLabel="Open saved nights">
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
          // "unknown" was accurate and useless: it named the problem without
          // saying what to do about it. The probes mean the note underneath can
          // now carry the measured temperature, so this can be the instruction.
          <span className="stage__value stage__value--unknown stage__value--prompt">
            Set bed temp
          </span>
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

/** Both halves when there are two, whichever there is when there is one. */
const say = (...parts: (string | null)[]) => parts.filter(Boolean).join(' ') || null

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
    // The water coming back from the bed, which is the closest thing to a bed
    // temperature this system can measure. Flow is what the unit is producing;
    // return is what the bed made of it, so return is the one to show. Falls
    // back to flow if that probe is the one that has gone quiet.
    //
    // This is the first number on this card that was measured rather than
    // decided. Everything else here is what the app last commanded.
    const bed = state.observed_return_c ?? state.observed_flow_c
    const measured = bed === null ? null : `Bed is around ${bed.toFixed(1)}° now.`

    if (state.power === 'off') note = say(measured, 'Unit is off. Only the power button responds.')
    else if (state.power === 'unknown')
      note = say(measured, 'Unit state unknown. Check the plug reading below.')
    else if (value === null) note = say(measured, 'Press + or − to set a target.')
    else if (state.current_stage !== null) {
      // Said before it happens rather than after. Reaching for the temperature
      // mid-stage is a correction, not a one-off, so it sticks; better to know
      // that while deciding than to find the schedule changed in the morning.
      note = say(
        measured,
        `${STAGE_LABEL[state.current_stage]} is running. Changing this sets ${STAGE_LABEL[state.current_stage]} to it from tomorrow too.`,
      )
    } else note = measured
  } else if (value !== null && value >= WARMING_FLOOR_C) {
    note = `Heats the bed to ${value}°C.`
  } else if (value !== null) {
    note = `Cools the bed to ${value}°C.`
  }

  if (atCeiling && ceiling === maxC) note = `${maxC}°C safety cap.`
  return note ? <p className="stage__note">{note}</p> : null
}
