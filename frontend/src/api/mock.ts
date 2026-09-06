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
  DeviceState,
  Mode,
  PowerSample,
  Schedule,
  ServiceInfo,
} from '../types'
import { MAX_TEMPERATURE_C, MODE_RANGE, WARMING_FLOOR_C } from '../types'

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

function nowIso(): string {
  return new Date().toISOString()
}

export class MockApiClient implements ApiClient {
  private state: DeviceState = {
    power: 'on',
    current_stage: 'deep',
    assumed_mode: 'quiet',
    assumed_target_c: 19,
    observed_power_w: 168,
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
    stages: [
      { stage: 'deep', duration_minutes: 240, temp_c: 17, mode: 'quiet' },
      { stage: 'rem', duration_minutes: 210, temp_c: 20, mode: 'quiet' },
      // The one the unit's own scheduler made impossible.
      { stage: 'wake', duration_minutes: 30, temp_c: 26, mode: 'warming' },
    ],
    cooling_speed: 'quiet',
    precool_enabled: true,
    precondition: 'cool',
    preheat_is_possible: false,
    precool_lead_minutes: 30,
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
    return { fake_transmitter: true, fake_power_monitor: true, max_temperature_c: MAX_TEMPERATURE_C }
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
    // Mirrors the service: warming cannot express a target below its floor, so
    // the combination is refused rather than quietly downgraded.
    const first = next.stages[0]?.temp_c ?? 20
    // Derived the same way the service derives it: below 25 has to cool.
    next.stages = next.stages.map((st) => ({
      ...st,
      mode: st.temp_c >= WARMING_FLOOR_C ? 'warming' : next.cooling_speed,
    }))
    next.preheat_is_possible = first >= WARMING_FLOOR_C
    if (next.precondition === 'warm' && !next.preheat_is_possible) {
      throw new ApiError(
        `Pre-heating cannot reach ${first}C. Warming mode only goes down to ` +
          `${WARMING_FLOOR_C}C, so the unit has no way to warm the bed to it.`,
      )
    }
    this.schedule = next
    this.emit({ schedule: { ...this.schedule } })
    return { ...this.schedule }
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

  async setTemperature(targetC: number): Promise<void> {
    if (targetC > MAX_TEMPERATURE_C) {
      throw new ApiError(`Refused: ${targetC}C is above the ${MAX_TEMPERATURE_C}C safety cap.`)
    }
    await sleep(500)
    this.patchState({ assumed_target_c: targetC, last_command_at: nowIso() })
    this.log('info', 'temperature', `Railed to minimum and counted up to ${targetC}C`)
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
      out.push({ at: d.toISOString(), watts: round1(Math.max(0.3, watts + (Math.random() - 0.5) * 8)) })
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
