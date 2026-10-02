import { useEffect, useRef, useState } from 'react'
import { Card } from './Card'
import { Minus, Plus, Sparkle } from './Icons'
import { canSetTemperature, formatDuration, formatTemp, tint, tintAlpha } from '../domain'
import {
  MODE_RANGE,
  STAGE_LABEL,
  STAGE_ORDER,
  WARMING_FLOOR_C,
  type DeviceState,
  type Mode,
  type Schedule,
  type Stage,
} from '../types'

/** "Now" is the live temperature. The rest are the parts of the night, which
 *  for Bedtime only is the one part. */
export type TabKey = 'now' | Stage | 'bedtime'

const NIGHT_TABS: TabKey[] = ['now', ...STAGE_ORDER]
const BEDTIME_TABS: TabKey[] = ['now', 'bedtime']

/** Bedtime only's length moves in quarter hours, from one to twelve hours. */
const BEDTIME_STEP = 15
const BEDTIME_LONGEST = 720

/** How long to wait after the last tap before firing a real infrared run. */
const COMMIT_DELAY_MS = 900

interface Props {
  state: DeviceState
  /** The locally edited night. */
  draft: Schedule
  maxC: number
  onStageChange: (stage: Stage | 'bedtime', tempC: number) => void
  /**
   * Whole night or Bedtime only. A setting rather than tonight's, so it stays
   * until it is switched back; the four parts are kept underneath either way.
   */
  onBedtimeOnly?: (on: boolean) => void
  onBedtimeMinutes?: (minutes: number) => void
  onSetNow: (targetC: number) => void
  /** Opens the saved nights. The card is where a whole night's temperatures live,
   *  so it is where saving and loading a set of them belongs. */
  onOpenProfiles?: () => void
  /**
   * The tonight-only controls. Inside the glow with the big number rather than
   * under the card, because a nudge belongs with the temperature it nudges.
   */
  children?: React.ReactNode
  /** Shown on every tab, unlike `children`. See the note by the slot. */
  keep?: React.ReactNode
}

export function TemperatureCard({
  state,
  draft,
  maxC,
  onStageChange,
  onSetNow,
  onOpenProfiles,
  onBedtimeOnly,
  onBedtimeMinutes,
  children,
  keep,
}: Props) {
  const [tab, setTab] = useState<TabKey>('now')
  const bedtimeOnly = draft.bedtime_only
  const tabs = bedtimeOnly ? BEDTIME_TABS : NIGHT_TABS

  // Switched between the two, a tab that has gone goes back to Now rather than
  // showing nothing.
  useEffect(() => {
    if (!(bedtimeOnly ? BEDTIME_TABS : NIGHT_TABS).includes(tab)) setTab('now')
  }, [bedtimeOnly, tab])

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
    key === 'now'
      ? nowValue
      : key === 'bedtime'
        ? draft.bedtime_temp_c
        : (stageOf(key)?.temp_c ?? null)

  const selected = valueFor(tab)
  const fallback = bedtimeOnly ? draft.bedtime_temp_c : (draft.stages[0]?.temp_c ?? 20)

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
      {/*
        Inside the card rather than as a card of its own, because it changes
        what this card holds, not what the screen holds. A row of its own under
        the header rather than in it: beside the label and the chevron there is
        not room for it on a small phone.
      */}
      {onBedtimeOnly && (
        <div className="night-kind" role="group" aria-label="What the night runs">
          <button
            type="button"
            className="night-kind__btn"
            aria-pressed={!bedtimeOnly}
            onClick={() => bedtimeOnly && onBedtimeOnly(false)}
          >
            Whole night
          </button>
          <button
            type="button"
            className="night-kind__btn"
            aria-pressed={bedtimeOnly}
            onClick={() => !bedtimeOnly && onBedtimeOnly(true)}
          >
            Bedtime only
          </button>
        </div>
      )}
      <div className="tabs" role="tablist" aria-label="Part of the night to edit">
        {tabs.map((key) => {
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
        <div className="stage__row">
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
          // Nothing to show is not the same as nothing happening. When the app
          // has not commanded a temperature, the night ahead is still Autopilot's
          // and saying so is more use than a blank or an instruction: the thing
          // worth knowing here is that it is handled. The note underneath carries
          // the measured bed temperature and how to override it.
          //
          // It reads the schedule rather than assuming. A night switched off is a
          // night nothing will drive, and claiming otherwise on the largest text
          // on the home screen would be the worst place in the app to be wrong.
          <span className="stage__value stage__value--unknown stage__value--prompt">
            <Sparkle size={20} className="stage__mark" glow />
            {draft.enabled ? 'Autopilot on' : 'Autopilot off'}
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
        </div>

        {/*
          The nudge is about the bed right now, so it belongs on Now and nowhere
          else. `keep` is about the tab you are looking at: you change Deep on
          the Deep tab, and offering to make that permanent on a different tab is
          offering it where nobody will look.
        */}
        {tab === 'now' && children}
        {tab === 'bedtime' && <BedtimeLength draft={draft} onMinutes={onBedtimeMinutes} />}
        {keep}

        <StageNote
          state={state}
          tab={tab}
          value={selected}
          mode={
            tab === 'now'
              ? null
              : tab === 'bedtime'
                ? draft.bedtime_mode
                : (stageOf(tab)?.mode ?? null)
          }
          atCeiling={atCeiling}
          ceiling={ceiling}
          maxC={maxC}
        />
      </div>
    </Card>
  )
}

/** "22:30" plus a number of minutes, round the clock. */
function later(hhmm: string, minutes: number): string {
  const [h, m] = hhmm.split(':').map(Number)
  const total = (h! * 60 + m! + minutes) % (24 * 60)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(Math.floor(total / 60))}:${pad(total % 60)}`
}

/**
 * How long Bedtime only holds its temperature, and what that means in clock
 * time. A setting, like the switch above, so a tap changes every night until it
 * is changed back; shown as the times it lands on rather than the step.
 */
function BedtimeLength({
  draft,
  onMinutes,
}: {
  draft: Schedule
  onMinutes?: (minutes: number) => void
}) {
  // Shown straight away and saved behind, so three quick taps read as three.
  const [pending, setPending] = useState<number | null>(null)
  useEffect(() => setPending(null), [draft.bedtime_minutes])
  const minutes = pending ?? draft.bedtime_minutes
  const longest = Math.min(BEDTIME_LONGEST, draft.night_minutes)
  const held = Math.min(minutes, draft.night_minutes)

  function change(by: number) {
    const next = Math.max(BEDTIME_STEP, Math.min(longest, minutes + by))
    if (next === minutes) return
    setPending(next)
    onMinutes?.(next)
  }

  return (
    <div className="bedtime-length">
      <div className="bedtime-length__row">
        <button
          type="button"
          className="step step--small"
          onClick={() => change(-BEDTIME_STEP)}
          disabled={!onMinutes || minutes <= BEDTIME_STEP}
          aria-label="Shorter"
        >
          <Minus />
        </button>
        <span className="bedtime-length__for">for {formatDuration(held)}</span>
        <button
          type="button"
          className="step step--small"
          onClick={() => change(BEDTIME_STEP)}
          disabled={!onMinutes || minutes >= longest}
          aria-label="Longer"
        >
          <Plus />
        </button>
      </div>
      <p className="bedtime-length__when">
        Ready by {draft.bed_time}, off at {later(draft.bed_time, held)}, then off until morning.
      </p>
    </div>
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
  mode,
  atCeiling,
  ceiling,
  maxC,
}: {
  state: DeviceState
  tab: TabKey
  value: number | null
  /** The stage's own mode, as the service worked it out for the whole night. */
  mode: Mode | null
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
    const measured = bed === null ? null : `Bed is around ${bed.toFixed(1)}° right now.`

    // The instruction goes first when there is one, the measurement second. The
    // order matters: this is read by somebody who has just seen "Autopilot on"
    // and wants to know whether they can overrule it.
    if (state.power === 'off')
      note = say(measured, 'The unit is off, so only the power button responds.')
    else if (state.power === 'unknown')
      note = say(measured, 'Unit state unknown. The plug reading is under Status in the menu.')
    else if (value === null)
      note = say('Adjust the bed temp by hand with + and −.', measured)
    else if (state.current_stage !== null) {
      // Said before it happens rather than after, and it says the opposite of
      // what it used to. Reaching for the temperature mid-stage used to rewrite
      // the saved routine, so the warning was "this sticks". It is tonight only
      // now, so the useful thing to say is that it does not.
      const label = STAGE_LABEL[state.current_stage]
      note = say(measured, `${label} is running. Changing this is for tonight only.`)
    } else note = measured
  } else if (value !== null) {
    // The stage's mode when the service has said, because between 25 and 35
    // both modes reach the number and the stage before decides. A Wake at 26
    // after a REM at 28 cools, and reading the number alone said it heats.
    const warms = mode !== null ? mode === 'warming' : value >= WARMING_FLOOR_C
    note = warms ? `Heats the bed to ${value}°C.` : `Cools the bed to ${value}°C.`
  }

  if (atCeiling && ceiling === maxC) note = `${maxC}°C safety cap.`
  return note ? <p className="stage__note">{note}</p> : null
}
