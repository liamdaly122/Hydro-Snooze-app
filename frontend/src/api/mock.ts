/**
 * Seed data behind the real ApiClient contract, so the design can be judged on a
 * phone before the service exists.
 *
 * This file is the only part of the frontend that is temporary. It goes when
 * HttpApiClient lands. Everything else in src/ talks to the interface, not to
 * this, which is why none of the design work gets done twice.
 *
 * It is deliberately a little bit alive: watts drift, events accrue, and commands
 * take a plausible amount of time, because a design that has only ever been seen
 * holding still hides all its worst moments.
 */

import { ApiError, type ApiClient, type LiveUpdate } from './client'
import type {
  DeviceEvent,
  DeviceHealth,
  DeviceState,
  Mode,
  PowerSample,
  Preconditioning,
  Schedule,
  ServiceInfo,
  SleepStage,
} from '../types'
import { MAX_TEMPERATURE_C, MIN_STAGE_MINUTES, MODE_RANGE, WARMING_FLOOR_C } from '../types'

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

function nowIso(): string {
  return new Date().toISOString()
}

export class MockApiClient implements ApiClient {
  private state: DeviceState = {
    power: 'on',
    rehearsal_ends_at: null,
    current_stage: 'deep',
    assumed_mode: 'quiet',
    assumed_target_c: 19,
    observed_power_w: 168,
    // Cooling, so the water comes back warmer than it went out: the bed is
    // shedding heat into it. The sign is what the Status card reads out.
    observed_flow_c: 19.4,
    observed_return_c: 22.1,
    observed_room_c: 18.2,
    inferred_activity: 'cooling',
    last_command_at: nowIso(),
    last_error: null,
  }

  private schedule: Schedule = {
    id: 1,
    name: 'Tonight',
    enabled: true,
    days_of_week: [0, 1, 2, 3, 4],
    wake_time: '06:30',
    bed_time: '22:30',
    night_minutes: 480,
    stages: [
      { stage: 'deep', duration_minutes: 240, temp_c: 17, mode: 'quiet' },
      { stage: 'rem', duration_minutes: 210, temp_c: 20, mode: 'quiet' },
      // The one the unit's own scheduler made impossible.
      { stage: 'wake', duration_minutes: 30, temp_c: 26, mode: 'warming' },
    ],
    cooling_speed: 'quiet',
    preconditioning: { mode: 'turbo', lead_minutes: 20, reason: 'Cooling the bed from about 20C down to 17C.' },
    updated_at: new Date(Date.now() - 3 * 86_400_000).toISOString(),
  }

  private events: DeviceEvent[] = []
  private listeners = new Set<(u: LiveUpdate) => void>()
  private nextEventId = 1
  private drift?: ReturnType<typeof setInterval>

  constructor() {
    this.seedEvents()
    // Nudge the watt reading every few seconds so the status strip and the chart
    // are never suspiciously still.
    this.drift = setInterval(() => {
      if (this.state.observed_power_w === null) return
      const base = this.state.inferred_activity === 'cooling' ? 170 : 32
      this.patchState({ observed_power_w: round1(base + (Math.random() - 0.5) * 14) })
    }, 4000)
  }

  dispose(): void {
    if (this.drift) clearInterval(this.drift)
    this.listeners.clear()
  }

  async info(): Promise<ServiceInfo> {
    return {
      fake_transmitter: true,
      fake_power_monitor: true,
      max_temperature_c: MAX_TEMPERATURE_C,
      // Fixed, so the seed build never decides it is out of date and reloads
      // itself in a loop.
      build: 'seed',
    }
  }

  async getState(): Promise<DeviceState> {
    return { ...this.state }
  }

  async getSchedule(): Promise<Schedule> {
    return { ...this.schedule }
  }

  async putSchedule(patch: Partial<Schedule>): Promise<Schedule> {
    await sleep(120)
    const next = { ...this.schedule, ...patch, updated_at: nowIso() }
    // Mirrors Schedule.__post_init__: the stages always fill the night exactly,
    // whichever of the three things the patch changed.
    next.night_minutes = minutesBetween(next.bed_time, next.wake_time)
    next.stages = withModes(fitStages(next.stages, next.night_minutes), next.cooling_speed)
    next.preconditioning = preconditioningFor(next.stages[0]?.temp_c ?? 20)
    this.schedule = next
    this.emit({ schedule: { ...this.schedule } })
    return { ...this.schedule }
  }

  async pressPower(): Promise<void> {
    await sleep(400)
    // Unknown, because a single press with nothing verifying it means exactly
    // that. The pretend plug settles it a moment later, the same way the real
    // one does.
    this.patchState({ power: 'unknown', last_command_at: nowIso() })
    this.log('info', 'power', 'Sent one press of power')
  }

  async powerOn(): Promise<void> {
    await sleep(400)
    this.patchState({
      power: 'on',
      observed_power_w: 168,
      inferred_activity: 'cooling',
      last_command_at: nowIso(),
    })
    this.log('info', 'power', 'Unit powered on, plug confirms 168 W')
  }

  async powerOff(): Promise<void> {
    await sleep(400)
    this.patchState({
      power: 'off',
      current_stage: null,
      observed_power_w: 0.4,
      inferred_activity: 'off',
      last_command_at: nowIso(),
    })
    this.log('info', 'power', 'Unit powered off, plug confirms 0.4 W')
  }

  async getHealth(): Promise<DeviceHealth[]> {
    // Seed data has no hardware behind it, so it says so rather than showing
    // green for devices that are not there.
    return [
      { name: 'plug', health: 'simulated', detail: 'No plug. Watts are invented', last_ok_at: null },
      {
        name: 'blaster',
        health: 'simulated',
        detail: 'No blaster. Presses are printed',
        last_ok_at: null,
      },
    ]
  }

  async startRehearsal(seconds: number): Promise<void> {
    await sleep(300)
    const ends = new Date(Date.now() + (seconds + 30) * 1000).toISOString()
    this.patchState({ rehearsal_ends_at: ends })
    this.log('info', 'rehearsal', `Rehearsing the whole night in ${seconds}s, then off.`)
  }

  async stopRehearsal(): Promise<void> {
    await sleep(300)
    this.patchState({ rehearsal_ends_at: null, current_stage: null })
    this.log('info', 'rehearsal', 'Rehearsal stopped.')
  }

  async setTemperature(targetC: number): Promise<void> {
    if (targetC > MAX_TEMPERATURE_C) {
      throw new ApiError(`Refused: ${targetC}C is above the ${MAX_TEMPERATURE_C}C safety cap.`)
    }
    await sleep(500)
    this.patchState({ assumed_target_c: targetC, last_command_at: nowIso() })
    this.log('info', 'temperature', `Railed to minimum and counted up to ${targetC}C`)
  }

  private muted = false

  async mute(): Promise<void> {
    await sleep(400)
    // A toggle the unit remembers, which is exactly why it is never automatic.
    this.muted = !this.muted
    this.log('info', 'mute', this.muted ? "Muted the unit's beep" : "UNMUTED the unit")
  }

  async setMode(mode: Mode): Promise<void> {
    await sleep(400)
    const [low, high] = MODE_RANGE[mode]
    const target = this.state.assumed_target_c
    this.patchState({
      assumed_mode: mode,
      // Each mode remembers its own temperature, so a target outside the new
      // mode's range is no longer meaningful and we say so rather than guess.
      assumed_target_c: target === null || target < low || target > high ? null : target,
      last_command_at: nowIso(),
    })
    this.log('info', 'mode', `Set mode to ${mode} via warm then cool`)
  }

  async getEvents(limit = 100): Promise<DeviceEvent[]> {
    return this.events.slice(0, limit)
  }

  async getPowerHistory(hours = 24): Promise<PowerSample[]> {
    // A plausible night: off through the day, turbo pre-cool at 21:30, the
    // schedule's quiet cooling overnight, off at 06:30.
    const out: PowerSample[] = []
    const end = Date.now()
    const stepMs = 5 * 60_000
    for (let t = end - hours * 3_600_000; t <= end; t += stepMs) {
      const d = new Date(t)
      const mins = d.getHours() * 60 + d.getMinutes()
      let watts: number
      if (mins >= 21 * 60 + 30 && mins < 22 * 60) watts = 178
      else if (mins >= 22 * 60 || mins < 6 * 60 + 30) watts = 150 + Math.sin(t / 5_400_000) * 42
      else watts = 0.4
      // The probes report all day whatever the unit is doing: they are taped to
      // the hoses, and a hose sitting still still has a temperature. With the
      // unit off the water drifts to the room, which is what makes the shape of
      // this chart worth looking at in the first place.
      const room = round1(19.7 + Math.sin(t / 43_200_000) * 0.8)
      const running = watts > 5
      const flow = running ? 26.5 + Math.sin(t / 7_200_000) * 1.4 : room + 0.4
      // Return above flow while a body is putting heat in, and level with it
      // once nothing is moving.
      const back = running ? flow + 0.6 : flow
      // A gap where the probe board dropped off the Wi-Fi, because the chart has
      // to be honest about those and the only way to see that it is, is to have
      // one in the seed data.
      const quiet = mins >= 3 * 60 + 10 && mins < 3 * 60 + 50
      out.push({
        at: d.toISOString(),
        watts: round1(Math.max(0.3, watts + (Math.random() - 0.5) * 8)),
        flow_c: quiet ? null : round1(flow),
        return_c: quiet ? null : round1(back),
        room_c: quiet ? null : room,
      })
    }
    return out
  }

  subscribe(listener: (u: LiveUpdate) => void): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  // --- internals --------------------------------------------------------------

  private patchState(patch: Partial<DeviceState>): void {
    this.state = { ...this.state, ...patch }
    this.emit({ state: { ...this.state } })
  }

  private emit(update: LiveUpdate): void {
    for (const listener of this.listeners) listener(update)
  }

  private log(level: DeviceEvent['level'], kind: string, message: string): void {
    const event: DeviceEvent = { id: this.nextEventId++, at: nowIso(), level, kind, message }
    this.events = [event, ...this.events].slice(0, 200)
    this.emit({ event })
  }

  private seedEvents(): void {
    const ago = (mins: number) => new Date(Date.now() - mins * 60_000).toISOString()
    const seed: Array<[number, DeviceEvent['level'], string, string]> = [
      [8, 'info', 'power_check', 'Plug reads 168 W, consistent with cooling'],
      [38, 'info', 'stage', 'Deep: 17C in quiet until 02:30'],
      [70, 'info', 'temperature', 'Railed to 15C then counted up to 17C (25 + 2)'],
      [71, 'info', 'mute', "Muted the unit's button beep"],
      [72, 'info', 'power', 'Powered on for pre-cool, plug confirms 174 W'],
      [640, 'info', 'power_off', 'Night finished at 06:30, switching off'],
      [641, 'info', 'stage', 'Wake: 26C in warming until 06:30'],
    ]
    this.events = seed.map(([mins, level, kind, message]) => ({
      id: this.nextEventId++,
      at: ago(mins),
      level,
      kind,
      message,
    }))
  }
}

function round1(n: number): number {
  return Math.round(n * 10) / 10
}

/** Mirrors `minutes_between`: the gap, wrapping midnight. */
function minutesBetween(bed: string, wake: string): number {
  const mins = (hhmm: string) => {
    const [h, m] = hhmm.split(':').map(Number)
    return h * 60 + m
  }
  return (((mins(wake) - mins(bed)) % 1440) + 1440) % 1440 || 1440
}

/** Mirrors `fit_stages`: scale to fill the night, keep the shape, add up exactly. */
function fitStages(stages: SleepStage[], total: number): SleepStage[] {
  if (stages.length === 0) return []
  const target = Math.max(total, MIN_STAGE_MINUTES * stages.length)
  const current = stages.reduce((n, s) => n + s.duration_minutes, 0)
  if (current === target) return stages
  const raw = stages.map((s) =>
    current <= 0 ? target / stages.length : (s.duration_minutes * target) / current,
  )
  const minutes = raw.map((r) => Math.floor(r))
  const order = raw
    .map((r, i) => [r - minutes[i], i] as const)
    .sort((a, b) => b[0] - a[0])
    .map(([, i]) => i)
  const short = target - minutes.reduce((n, m) => n + m, 0)
  for (let n = 0; n < short; n += 1) minutes[order[n % stages.length]] += 1
  for (let i = 0; i < minutes.length; i += 1) {
    while (minutes[i] < MIN_STAGE_MINUTES) {
      let donor = 0
      for (let j = 1; j < minutes.length; j += 1) if (minutes[j] > minutes[donor]) donor = j
      if (donor === i || minutes[donor] <= MIN_STAGE_MINUTES) break
      minutes[donor] -= 1
      minutes[i] += 1
    }
  }
  return stages.map((s, i) => ({ ...s, duration_minutes: minutes[i] }))
}

/**
 * Mirrors `preconditioning_for` in backend/hydrosnooze/models.py.
 *
 * The bed starts wherever the probes say it is and the first stage says where it has to
 * be. The direction picks the mode, the distance picks the head start, and a
 * gap nothing can close means nothing runs.
 */
function preconditioningFor(firstTempC: number): Preconditioning {
  const room = 20
  const gap = firstTempC - room
  if (Math.abs(gap) <= 1) {
    return { mode: null, lead_minutes: 0, reason: `The bed already sits at about ${firstTempC}C, so there is nothing to do.` }
  }
  let mode: Mode
  let reason: string
  if (gap < 0) {
    mode = 'turbo'
    reason = `Cooling the bed from about ${room}C down to ${firstTempC}C.`
  } else if (firstTempC >= WARMING_FLOOR_C) {
    mode = 'warming'
    reason = `Warming the bed from about ${room}C up to ${firstTempC}C.`
  } else {
    return {
      mode: null,
      lead_minutes: 0,
      reason:
        `The bed has to warm from about ${room}C to ${firstTempC}C, and warming mode only goes ` +
        `down to ${WARMING_FLOOR_C}C, so the unit has no way to get it there. Body heat does ` +
        `that job once you are in it.`,
    }
  }
  // Both turbo and warming are assumed to cross their whole range in 30 minutes,
  // so the rate is that over the span, on top of a fixed 15 to get going.
  const [low, high] = MODE_RANGE[mode]
  const lead = 15 + (Math.abs(gap) * 30) / (high - low)
  return { mode, lead_minutes: Math.min(Math.round(lead), 90), reason }
}

/**
 * Mirrors `modes_for` in backend/hydrosnooze/models.py.
 *
 * Cooling reaches 15 to 35 and warming reaches 25 to 55, so between 25 and 35
 * both modes hold the number and only the direction of travel says which one can
 * actually move the bed there. That makes a stage's mode depend on the stage
 * before it, so the night is resolved in order.
 */
function withModes(stages: SleepStage[], coolingSpeed: Mode): SleepStage[] {
  const ceiling = MODE_RANGE.quiet[1]
  const out: SleepStage[] = []
  for (const [index, stage] of stages.entries()) {
    const previous = index > 0 ? stages[index - 1] : undefined
    const wasCooling = index > 0 ? out[index - 1].mode !== 'warming' : false
    let mode: Mode
    if (stage.temp_c < WARMING_FLOOR_C) mode = coolingSpeed
    else if (stage.temp_c > ceiling) mode = 'warming'
    else if (previous === undefined) mode = 'warming'
    else if (previous.temp_c > stage.temp_c) mode = coolingSpeed
    else if (previous.temp_c < stage.temp_c) mode = 'warming'
    else mode = wasCooling ? coolingSpeed : 'warming'
    out.push({ ...stage, mode })
  }
  return out
}
