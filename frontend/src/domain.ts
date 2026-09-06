/**
 * Pure helpers shared across the screens. Mirrors the derivations in
 * backend/hydrosnooze/models.py so the UI and the scheduler always agree about
 * what time anything happens.
 */

import {
  MODE_RANGE,
  WARMING_FLOOR_C,
  type DeviceState,
  type Mode,
  type Schedule,
  type SleepStage,
  type Stage,
} from './types'

const MINUTE = 60_000

/** Monday is 0, matching the backend. JS getDay() puts Sunday first. */
export function mondayFirstDay(d: Date): number {
  return (d.getDay() + 6) % 7
}

export const DAY_INITIALS = ['M', 'T', 'W', 'T', 'F', 'S', 'S']
export const DAY_SHORT = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

export interface StageStep {
  stage: Stage
  startsAt: Date
  endsAt: Date
  tempC: number
  mode: Mode
}

export interface NightPlan {
  precoolAt: Date | null
  bedtimeAt: Date
  wakeAt: Date
  steps: StageStep[]
}

function parseHhMm(value: string): [number, number] {
  const [h, m] = value.split(':').map((n) => parseInt(n, 10))
  return [h ?? 0, m ?? 0]
}

/**
 * Work backwards from the morning you want to wake up.
 *
 * The stages run in order and finish at the wake time, so bedtime falls out of
 * how long they add up to. There is no fixed length any more: that was the unit's
 * own scheduler, and dropping it is what allows heating and cooling in one night.
 */
export function planForWake(wakeOn: Date, schedule: Schedule): NightPlan {
  const [h, m] = parseHhMm(schedule.wake_time)
  const wakeAt = new Date(wakeOn)
  wakeAt.setHours(h, m, 0, 0)

  const total = schedule.stages.reduce((n, s) => n + s.duration_minutes, 0)
  const bedtimeAt = new Date(wakeAt.getTime() - total * MINUTE)

  const steps: StageStep[] = []
  let cursor = bedtimeAt
  for (const stage of schedule.stages) {
    const endsAt = new Date(cursor.getTime() + stage.duration_minutes * MINUTE)
    steps.push({
      stage: stage.stage,
      startsAt: cursor,
      endsAt,
      tempC: stage.temp_c,
      mode: stage.mode,
    })
    cursor = endsAt
  }

  const precoolAt = schedule.precool_enabled
    ? new Date(bedtimeAt.getTime() - schedule.precool_lead_minutes * MINUTE)
    : null
  return { precoolAt, bedtimeAt, wakeAt, steps }
}

/** The next night that has not started yet, or null if the schedule is off. */
export function nextPlan(schedule: Schedule, now: Date = new Date()): NightPlan | null {
  if (!schedule.enabled || schedule.days_of_week.length === 0) return null
  for (let offset = 0; offset < 8; offset += 1) {
    const day = new Date(now)
    day.setDate(day.getDate() + offset)
    if (!schedule.days_of_week.includes(mondayFirstDay(day))) continue
    const plan = planForWake(day, schedule)
    const startsAt = plan.precoolAt ?? plan.bedtimeAt
    if (startsAt.getTime() > now.getTime()) return plan
  }
  return null
}

export function formatTime(d: Date): string {
  return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
}

/** "Mon 22:00". Never a bare time: the day matters and is easy to get wrong. */
export function formatDayTime(d: Date): string {
  return `${DAY_SHORT[mondayFirstDay(d)]} ${formatTime(d)}`
}

export function formatDays(days: number[]): string {
  if (days.length === 0) return 'Never'
  const sorted = [...days].sort((a, b) => a - b)
  if (sorted.length === 7) return 'Every day'
  // Collapse a single run into "Mon-Thu", the way the reference app does.
  const isRun = sorted.every((d, i) => i === 0 || d === sorted[i - 1]! + 1)
  if (isRun && sorted.length > 2) {
    return `${DAY_SHORT[sorted[0]!]}-${DAY_SHORT[sorted[sorted.length - 1]!]}`
  }
  return sorted.map((d) => DAY_SHORT[d]).join(', ')
}

// --- Temperature tinting ------------------------------------------------------
//
// The brief asks for blue when cold, purple in the middle, warm orange when
// heating. The reference screenshot runs blue to purple to pink across a narrow
// band around the temperatures you actually sleep at, which is what gives it its
// character. These stops do both: the cooling band 15-30 carries the blue to
// purple to pink sweep so ordinary use is never flat, and the warming band beyond
// carries on into orange.

type Rgb = [number, number, number]

const STOPS: Array<[number, Rgb]> = [
  [15, [0x3b, 0x6f, 0xe8]],
  [18, [0x6a, 0x5f, 0xe0]],
  [21, [0x90, 0x58, 0xce]],
  [24, [0xb4, 0x5b, 0xb8]],
  [27, [0xcc, 0x5c, 0x9e]],
  [30, [0xd8, 0x60, 0x7f]],
  [38, [0xde, 0x70, 0x57]],
  [47, [0xe4, 0x82, 0x3f]],
  [55, [0xee, 0x9a, 0x33]],
]

function lerp(a: number, b: number, t: number): number {
  return Math.round(a + (b - a) * t)
}

function tintRgb(tempC: number): Rgb {
  if (tempC <= STOPS[0]![0]) return STOPS[0]![1]
  const last = STOPS[STOPS.length - 1]!
  if (tempC >= last[0]) return last[1]
  for (let i = 0; i < STOPS.length - 1; i += 1) {
    const [lowT, lowC] = STOPS[i]!
    const [highT, highC] = STOPS[i + 1]!
    if (tempC <= highT) {
      const t = (tempC - lowT) / (highT - lowT)
      return [lerp(lowC[0], highC[0], t), lerp(lowC[1], highC[1], t), lerp(lowC[2], highC[2], t)]
    }
  }
  return last[1]
}

/** Solid tint for a temperature, used on the tab values. */
export function tint(tempC: number): string {
  const [r, g, b] = tintRgb(tempC)
  return `rgb(${r} ${g} ${b})`
}

/** Same hue at an arbitrary alpha, used for the glow behind the big numeral. */
export function tintAlpha(tempC: number, alpha: number): string {
  const [r, g, b] = tintRgb(tempC)
  return `rgb(${r} ${g} ${b} / ${alpha})`
}

// --- Formatting ---------------------------------------------------------------

/** The app is Celsius throughout. The unit is, the manual is, and Liam is. */
export function formatTemp(tempC: number | null): string {
  return tempC === null ? '--' : `${tempC}`
}

export function formatWatts(watts: number | null): string {
  if (watts === null) return 'unknown'
  return watts < 10 ? `${watts.toFixed(1)} W` : `${Math.round(watts)} W`
}

export function clampToMode(tempC: number, mode: Mode, maxC: number): number {
  const [low, high] = MODE_RANGE[mode]
  return Math.max(low, Math.min(Math.min(high, maxC), tempC))
}

/**
 * Only dead when the unit is off, where the power button is the only one that
 * responds. It used to also be dead during the unit's own sleep schedule; the app
 * never arms that now.
 */
export function canSetTemperature(state: DeviceState): boolean {
  return state.power === 'on'
}

/** "4h 30m", or "45m" when it is under an hour. */
export function formatDuration(minutes: number): string {
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  if (h === 0) return `${m}m`
  return m === 0 ? `${h}h` : `${h}h ${m}m`
}

/**
 * Whether a stage cools or warms, worked out from its temperature.
 *
 * Below 25°C it has to cool, because warming mode cannot express a number that
 * low. At 25°C and above it warms, because nobody asks for a bed at 27°C unless
 * they want it actively warmed there.
 */
export function stageIsWarming(stage: SleepStage): boolean {
  return stage.temp_c >= WARMING_FLOOR_C
}
