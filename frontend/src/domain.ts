/**
 * Pure helpers shared across the screens. Mirrors the derivations in
 * backend/hydrosnooze/models.py so the UI and the scheduler always agree about
 * what time anything happens.
 */

import {
  MODE_RANGE,
  WARMING_FLOOR_C,
  type DeviceState,
  type Holiday,
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

  // Anchored on the night, not on the stages. They add up to the same thing once
  // the service has answered, but a draft mid-edit can be a few minutes out and
  // bedtime should not flicker while it is.
  const bedtimeAt = new Date(wakeAt.getTime() - schedule.night_minutes * MINUTE)

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

  // Not a setting to read, a decision to reflect. A null mode means the bed is
  // already where it needs to be, or that nothing the unit has could get it there.
  const pre = schedule.preconditioning
  const precoolAt = pre.mode
    ? new Date(bedtimeAt.getTime() - pre.lead_minutes * MINUTE)
    : null
  return { precoolAt, bedtimeAt, wakeAt, steps }
}

/**
 * The next night that has not started yet, or null if the schedule is off.
 *
 * A holiday's nights are stepped over, and the search runs a week past the day
 * you get back rather than a week from now. Without that, a fortnight away left
 * the Alarm card promising a bedtime nothing was going to keep, and then "Not
 * scheduled" for a routine that was only resting.
 */
export function nextPlan(
  schedule: Schedule,
  now: Date = new Date(),
  holiday: Holiday | null = null,
): NightPlan | null {
  if (!schedule.enabled || schedule.days_of_week.length === 0) return null
  const away = holiday ? Math.max(0, daysBetween(isoDay(now), holiday.back_on)) : 0
  for (let offset = 0; offset < 8 + away; offset += 1) {
    const day = new Date(now)
    day.setDate(day.getDate() + offset)
    if (!schedule.days_of_week.includes(mondayFirstDay(day))) continue
    if (awayOn(holiday, day)) continue
    const plan = planForWake(day, schedule)
    const startsAt = plan.precoolAt ?? plan.bedtimeAt
    if (startsAt.getTime() > now.getTime()) return plan
  }
  return null
}

// --- Calendar days ------------------------------------------------------------
//
// A holiday is two calendar days, not two moments, and the service writes them
// as "2026-10-02". `new Date("2026-10-02")` reads that as midnight UTC, which in
// a British summer is one in the morning and anywhere west of Greenwich is the
// day before. So they are taken apart and put back together by hand, in local
// time, and compared as strings, which sort correctly as they are.

export const MONTH_SHORT = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
]
export const MONTH_LONG = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

/** "2026-10-02", in local time. */
export function isoDay(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** Local midnight on that day. */
export function parseDay(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y!, m! - 1, d!)
}

/** Whole days from one to the other. Rounded, so a clock change is not a day. */
export function daysBetween(from: string, to: string): number {
  return Math.round((parseDay(to).getTime() - parseDay(from).getTime()) / 86_400_000)
}

/** The same day, `days` later. */
export function addDays(iso: string, days: number): string {
  const d = parseDay(iso)
  d.setDate(d.getDate() + days)
  return isoDay(d)
}

/** "Fri 2 Oct". */
export function formatDay(d: Date): string {
  return `${DAY_SHORT[mondayFirstDay(d)]} ${d.getDate()} ${MONTH_SHORT[d.getMonth()]}`
}

/**
 * "Mon 22:30" when it is this week, "Mon 12 Oct 22:30" when it is not.
 *
 * A weekday on its own is only an answer while there is one of each ahead. Two
 * weeks away and "Mon" could be either of two Mondays.
 */
export function formatWhen(d: Date, now: Date = new Date()): string {
  const far = d.getTime() - now.getTime() > 6 * 86_400_000
  return far ? `${formatDay(d)} ${formatTime(d)}` : formatDayTime(d)
}

/**
 * Whether the night ending on this morning is one of the nights away.
 *
 * Mirrors Holiday.away_on. Every morning after the day you leave, up to and
 * including the day you get back: leave on Friday, back on Sunday, and the
 * Saturday and Sunday mornings are the Friday and Saturday nights.
 */
export function awayOn(holiday: Holiday | null, wakeOn: Date): boolean {
  if (!holiday) return false
  const day = isoDay(wakeOn)
  return day > holiday.leaves_on && day <= holiday.back_on
}

/**
 * The first night the bed runs after a holiday, as a plan.
 *
 * Not always the night you get back. A schedule that has Sunday nights off
 * starts again on Monday, and saying "tonight" to somebody walking in on a
 * Sunday would be the kind of promise this app does not make.
 */
export function firstNightBack(schedule: Schedule, holiday: Holiday): NightPlan | null {
  if (!schedule.enabled || schedule.days_of_week.length === 0) return null
  for (let offset = 1; offset <= 7; offset += 1) {
    const wakeOn = parseDay(addDays(holiday.back_on, offset))
    if (schedule.days_of_week.includes(mondayFirstDay(wakeOn))) {
      return planForWake(wakeOn, schedule)
    }
  }
  return null
}

/** How many of the nights away the bed would otherwise have run. */
export function scheduledNightsAway(schedule: Schedule, holiday: Holiday): number {
  let count = 0
  for (let n = 1; n <= holiday.nights; n += 1) {
    const wakeOn = parseDay(addDays(holiday.leaves_on, n))
    if (schedule.days_of_week.includes(mondayFirstDay(wakeOn))) count += 1
  }
  return count
}

export function formatTime(d: Date): string {
  return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
}

/** "Mon 22:00". Never a bare time: the day matters and is easy to get wrong. */
export function formatDayTime(d: Date): string {
  return `${DAY_SHORT[mondayFirstDay(d)]} ${formatTime(d)}`
}

/**
 * The days, without the word in front of them.
 *
 * "day" rather than "Every day" for a full week, because both callers say
 * "Every" themselves and the card read "EVERY EVERY DAY" for anyone who had the
 * schedule running seven nights.
 */
export function formatDays(days: number[]): string {
  if (days.length === 0) return 'Never'
  const sorted = [...days].sort((a, b) => a - b)
  if (sorted.length === 7) return 'day'
  // Collapse a single run into "Mon-Thu", the way the reference app does.
  const isRun = sorted.every((d, i) => i === 0 || d === sorted[i - 1]! + 1)
  if (isRun && sorted.length > 2) {
    return `${DAY_SHORT[sorted[0]!]}-${DAY_SHORT[sorted[sorted.length - 1]!]}`
  }
  return sorted.map((d) => DAY_SHORT[d]).join(', ')
}

// --- Temperature tinting ------------------------------------------------------
//
// Deep blue at 15, deep red at 55, sweeping through violet, magenta and rose in
// between. The middle of the ramp is deliberately busy across 15 to 31, because
// that is the band you actually sleep in and a flat run of near-identical blues
// there would tell you nothing.
//
// These are the colours the glow uses directly. Because it composites them over
// black at well under full opacity, the ends read deeper still: 55 lands somewhere
// around a dried-blood red rather than the pillar-box the swatch suggests.

type Rgb = [number, number, number]

const STOPS: Array<[number, Rgb]> = [
  [15, [0x2a, 0x5f, 0xea]],
  [19, [0x58, 0x52, 0xe4]],
  [23, [0x83, 0x48, 0xd2]],
  [27, [0xa9, 0x3f, 0xb0]],
  [31, [0xc4, 0x3c, 0x86]],
  [36, [0xd4, 0x40, 0x5e]],
  [42, [0xd6, 0x3a, 0x44]],
  [48, [0xd3, 0x32, 0x30]],
  [55, [0xc4, 0x25, 0x25]],
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
