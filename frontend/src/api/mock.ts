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
  AutopilotNight,
  DeviceEvent,
  DeviceHealth,
  DeviceState,
  Learning,
  LearningMode,
  Mode,
  PowerSample,
  Preconditioning,
  Profile,
  Schedule,
  ServiceInfo,
  SleepStage,
  Stage,
  TonightPhase,
  TonightState,
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

  /**
   * Seed profiles. Two, because one of everything never shows how a list reads,
   * and because the interesting state is a saved night that is not the one
   * currently running.
   */
  private profiles: Profile[] = [
    {
      id: 1,
      name: 'Summer',
      cooling_speed: 'quiet',
      stages: [
        { stage: 'deep', duration_minutes: 240, temp_c: 17, mode: 'quiet' },
        { stage: 'rem', duration_minutes: 210, temp_c: 20, mode: 'quiet' },
        { stage: 'wake', duration_minutes: 30, temp_c: 26, mode: 'warming' },
      ],
      active: true,
      created_at: nowIso(),
    },
    {
      id: 2,
      name: 'Winter',
      cooling_speed: 'quiet',
      stages: [
        { stage: 'deep', duration_minutes: 240, temp_c: 26, mode: 'warming' },
        { stage: 'rem', duration_minutes: 210, temp_c: 27, mode: 'warming' },
        { stage: 'wake', duration_minutes: 30, temp_c: 28, mode: 'warming' },
      ],
      active: false,
      created_at: nowIso(),
    },
  ]

  /**
   * Worked out rather than stored, the same way the service does it.
   *
   * This held `active` as a flag nothing recomputed, so editing a temperature on
   * the home screen left a profile badged Running against numbers the screen
   * visibly contradicted. The seed data is a demo of the feature, and it was
   * demonstrating precisely the drift the design exists to prevent.
   */
  private isRunning(profile: Profile): boolean {
    if (profile.cooling_speed !== this.schedule.cooling_speed) return false
    return profile.stages.every((stage, i) => {
      const mine = this.schedule.stages[i]
      return mine && mine.stage === stage.stage && mine.temp_c === stage.temp_c
    })
  }

  /**
   * Tonight, in the mock. Held in memory the way the service holds it in a row,
   * so the seed site behaves like the real thing rather than pretending.
   *
   * The phase can be forced with `?phase=` so a state a real evening takes hours
   * to reach can be looked at now.
   */
  private tonightPhase: TonightPhase =
    (new URLSearchParams(location.search).get('phase') as TonightPhase) || 'running'

  private tonightState = {
    skip: false,
    stages: null as SleepStage[] | null,
    wake_time: null as string | null,
    bed_time: null as string | null,
    nudge_c: 0,
    nudge_until: null as string | null,
  }

  private learningOn = true

  /**
   * Two modes at different points on the loop, so the card can be judged in both
   * states at once: warming measured, cooling still counting. `?nights=` on the
   * URL moves warming along, which is how the dots were chosen.
   */
  private nightsLearned = [
    {
      mode: 'warming' as Mode,
      target_c: 28,
      nights: Number(new URLSearchParams(location.search).get('nights') ?? 3),
      drift_c: -2.1,
    },
    {
      mode: 'turbo' as Mode,
      target_c: 24,
      nights: Number(new URLSearchParams(location.search).get('cool') ?? 1),
      drift_c: -0.4,
    },
  ]

  private tonightJson(): TonightState {
    const t = this.tonightState
    const wake_time = t.wake_time ?? this.schedule.wake_time
    const bed_time = t.bed_time ?? this.schedule.bed_time
    // The night's length follows its two edges and the stages refit to it, the
    // same way Schedule.__post_init__ does on the service. Spreading the old
    // night_minutes instead made an hour's lie-in read as a later bedtime.
    const night_minutes = minutesBetween(bed_time, wake_time)
    const running: Schedule = {
      ...this.schedule,
      wake_time,
      bed_time,
      night_minutes,
      stages: fitStages((t.stages ?? this.schedule.stages).map((s) => ({ ...s })), night_minutes),
    }
    return {
      phase: this.tonightPhase,
      running,
      // Not the nudge: it is visible where it happens and lapses on its own.
      changed: t.skip || t.stages !== null || t.wake_time !== null || t.bed_time !== null,
      skip: t.skip,
      stages_changed: t.stages !== null,
      times_changed: t.wake_time !== null || t.bed_time !== null,
      nudge_c: t.nudge_c,
      nudge_until: t.nudge_until,
    }
  }

  async getTonight(): Promise<TonightState> {
    await sleep(80)
    return this.tonightJson()
  }

  async setStageTonight(stage: Stage, temp_c: number): Promise<TonightState> {
    await sleep(120)
    const base = this.tonightState.stages ?? this.schedule.stages
    this.tonightState.stages = base.map((s) => (s.stage === stage ? { ...s, temp_c } : { ...s }))
    return this.tonightJson()
  }

  async nudgeTonight(delta_c: number): Promise<TonightState> {
    await sleep(120)
    const d = Math.max(-1, Math.min(1, delta_c))
    this.tonightState.nudge_c = d
    this.tonightState.nudge_until = d ? new Date(Date.now() + 30 * 60_000).toISOString() : null
    return this.tonightJson()
  }

  async shiftTonight(patch: { bed_minutes?: number; wake_minutes?: number }): Promise<TonightState> {
    await sleep(120)
    const shift = (hhmm: string, by: number) => {
      const [h, m] = hhmm.split(':').map(Number)
      const total = (((h! * 60 + m! + by) % 1440) + 1440) % 1440
      const pad = (n: number) => String(n).padStart(2, '0')
      return `${pad(Math.floor(total / 60))}:${pad(total % 60)}`
    }
    if (patch.bed_minutes) {
      this.tonightState.bed_time = shift(
        this.tonightState.bed_time ?? this.schedule.bed_time,
        patch.bed_minutes,
      )
    }
    if (patch.wake_minutes) {
      this.tonightState.wake_time = shift(
        this.tonightState.wake_time ?? this.schedule.wake_time,
        patch.wake_minutes,
      )
    }
    return this.tonightJson()
  }

  async skipTonight(skip: boolean): Promise<TonightState> {
    await sleep(120)
    this.tonightState.skip = skip
    return this.tonightJson()
  }

  async clearTonight(): Promise<TonightState> {
    await sleep(120)
    this.tonightState = {
      skip: false,
      stages: null,
      wake_time: null,
      bed_time: null,
      nudge_c: 0,
      nudge_until: null,
    }
    return this.tonightJson()
  }

  async keepTonight(): Promise<Schedule> {
    await sleep(160)
    const running = this.tonightJson().running
    this.schedule = { ...this.schedule, stages: running.stages.map((s) => ({ ...s })) }
    this.tonightState.stages = null
    return this.schedule
  }

  async getAutopilot(): Promise<AutopilotNight> {
    await sleep(120)
    return seedNight()
  }

  async getLearning(): Promise<Learning> {
    await sleep(120)
    return this.learningJson()
  }

  async setLearning(on: boolean): Promise<Learning> {
    await sleep(140)
    this.learningOn = on
    return this.learningJson()
  }

  async forgetLearning(mode?: Mode): Promise<Learning> {
    await sleep(160)
    for (const row of this.nightsLearned) {
      if (mode === undefined || row.mode === mode) row.nights = 0
    }
    return this.learningJson()
  }

  /**
   * The same shape learning_json builds, and deliberately the same sentences.
   *
   * Mirrored rather than shared, which is the cost of the mock existing at all.
   * Worth it: the unlock card is a thing to judge by eye at five different
   * counts, and waiting three real nights between looks is not a design process.
   */
  private learningJson(): Learning {
    const mode = (m: Mode, targetC: number, nights: number, driftC: number): LearningMode => {
      const needed = 3
      const measured = nights >= needed
      const moves = m === 'warming' ? 'warms' : 'cools'
      const sends = Math.round(targetC - driftC)
      return {
        mode: m,
        target_c: targetC,
        needed,
        unlocked: measured ? 2 : 0,
        skills: [
          {
            key: 'pace',
            title: `How fast your bed ${moves}`,
            runs: Math.min(nights, needed),
            needed,
            unlocked: measured,
            detail: measured
              ? `Timed from this bed rather than estimated: about ${
                  m === 'warming' ? 91 : 47
                } minutes of head start from 10° away.`
              : 'Until then the head start is an estimate. Nights that move the bed at least 2° count towards this.',
          },
          {
            key: 'settle',
            title: 'Where your bed settles',
            runs: Math.min(nights, needed),
            needed,
            unlocked: measured,
            detail: !measured
              ? 'Until then the number you ask for is the number that gets sent.'
              : !this.learningOn
                ? `Lands ${Math.abs(driftC).toFixed(1)}° below the setting. Not being corrected, because learning is switched off.`
                : sends === targetC
                  ? `Lands ${Math.abs(driftC).toFixed(1)}° below the setting, which is close enough to leave alone.`
                  : `Lands ${Math.abs(driftC).toFixed(1)}° below the setting, so it sends ${sends}° for a ${targetC}° bed.`,
          },
        ],
      }
    }
    return {
      on: this.learningOn,
      modes: this.nightsLearned.map((r) => mode(r.mode, r.target_c, r.nights, r.drift_c)),
    }
  }

  async getProfiles(): Promise<Profile[]> {
    await sleep(120)
    return this.profiles.map((p) => ({ ...p, active: this.isRunning(p) }))
  }

  async saveProfile(name: string): Promise<Profile[]> {
    await sleep(200)
    const existing = this.profiles.find((p) => p.name.toLowerCase() === name.toLowerCase())
    const stages = this.schedule.stages.map((s) => ({ ...s }))
    if (existing) {
      existing.stages = stages
      // The speed too. The real save_profile updates both columns, and leaving
      // it behind meant a profile saved under turbo activated as quiet.
      existing.cooling_speed = this.schedule.cooling_speed
    } else {
      this.profiles.push({
        id: Math.max(0, ...this.profiles.map((p) => p.id)) + 1,
        name,
        cooling_speed: this.schedule.cooling_speed,
        stages,
        active: false,
        created_at: nowIso(),
      })
    }
    return this.getProfiles()
  }

  async activateProfile(id: number): Promise<void> {
    await sleep(200)
    const wanted = this.profiles.find((p) => p.id === id)
    if (!wanted) return
    // Through putSchedule, which mirrors what Schedule.__post_init__ does on the
    // real service: refits the stages to the night, re-derives each stage's mode
    // and rebuilds the preconditioning. Writing stages straight in skipped all
    // three, and dropped cooling_speed entirely.
    await this.putSchedule({
      stages: wanted.stages.map((s) => ({ ...s })),
      cooling_speed: wanted.cooling_speed,
    })
  }

  async deleteProfile(id: number): Promise<Profile[]> {
    await sleep(150)
    this.profiles = this.profiles.filter((p) => p.id !== id)
    return this.getProfiles()
  }

  async restartBlaster(): Promise<void> {
    await sleep(400)
    this.log('info', 'blaster', 'Asked the blaster to restart.')
  }

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


/**
 * A plausible night for the Autopilot screen.
 *
 * Shaped off a real one. An eight and a half hour night driven through the real
 * scheduler and the real sequences produces five adjustments, not forty: this
 * system sets a stage and holds it, where the design it borrows from is a closed
 * loop nudging itself every few minutes. So the seed shows five, because a seed
 * that flatters the screen is a seed that hides what the screen will look like
 * on the morning it matters.
 *
 * The bed chases whatever the unit was last told, with a lag, which is what puts
 * the shape in the line: a long climb to each new stage and then a flat stretch
 * holding it.
 */
function seedNight(): AutopilotNight {
  const wake = new Date()
  wake.setHours(7, 30, 0, 0)
  if (wake.getTime() > Date.now()) wake.setDate(wake.getDate() - 1)

  const at = (h: number, m: number) => {
    const d = new Date(wake)
    d.setHours(h, m, 0, 0)
    if (h > 12) d.setDate(d.getDate() - 1)
    return d
  }

  const bands = [
    { label: 'Deep', starts_at: at(22, 30), ends_at: at(2, 44), temp_c: 19 },
    { label: 'REM', starts_at: at(2, 44), ends_at: at(6, 26), temp_c: 22 },
    { label: 'Wake', starts_at: at(6, 26), ends_at: at(7, 30), temp_c: 26 },
  ]

  // The bed, chasing the setpoint at the rate the real one does.
  const from = at(21, 30).getTime()
  const to = at(7, 29).getTime()
  const track: AutopilotNight['track'] = []
  let bed = 21.8
  let on = false
  for (let ms = from, i = 0; ms <= to; ms += 60_000, i++) {
    if (ms >= at(22, 12).getTime()) on = true
    const band = bands.find((b) => ms >= b.starts_at.getTime() && ms < b.ends_at.getTime())
    const target = band ? band.temp_c : bands[0]!.temp_c
    bed += on ? (target - bed) * 0.06 : (20.5 - bed) * 0.01
    const shown = bed + Math.sin(i / 37) * 0.28
    track.push({ at: new Date(ms).toISOString(), offset_c: Math.round((shown - target) * 100) / 100 })
  }

  const offsetAt = (ms: number) =>
    track.reduce((best, p) =>
      Math.abs(new Date(p.at).getTime() - ms) < Math.abs(new Date(best.at).getTime() - ms) ? p : best,
    ).offset_c

  // The nine a real night produces: a mode press and a rail-and-count at each
  // boundary, two for getting the bed ready, two for the one drift correction.
  const marks: AutopilotNight['marks'] = (
    [
      [at(22, 12), 'precool', 'Set mode to turbo via warm then cool'],
      [at(22, 13), 'precool', 'Railed to 15° then counted up to 19° (25 + 4)'],
      [at(22, 30), 'phase', 'Set mode to quiet via warm then cool'],
      [at(22, 30), 'phase', 'Railed to 15° then counted up to 19° (25 + 4)'],
      [at(2, 44), 'phase', 'Railed to 15° then counted up to 22° (25 + 7)'],
      [at(6, 26), 'phase', 'Set mode to warming via warm then cool'],
      [at(6, 26), 'phase', 'Railed to 25° then counted up to 26° (35 + 1)'],
      [at(7, 12), 'quiet', 'Set mode to quiet via warm then cool'],
      [at(7, 12), 'quiet', 'Railed to 15° then counted up to 26° (25 + 11)'],
    ] as const
  ).map(([when, kind, detail]) => ({
    at: when.toISOString(),
    kind,
    label: { phase: 'Phase & mode change', precool: 'Getting the bed ready', quiet: 'Drift response', manual: 'Set by hand' }[kind],
    detail,
    offset_c: offsetAt(when.getTime()),
  }))

  const off = track.map((p) => Math.abs(p.offset_c))
  return {
    wake_at: wake.toISOString(),
    starts_at: at(21, 30).toISOString(),
    adjustments: marks.length,
    measured: true,
    breakdown: (['phase', 'precool', 'quiet', 'manual'] as const).map((kind) => ({
      kind,
      label: {
        phase: 'Phase & mode change',
        precool: 'Getting the bed ready',
        quiet: 'Drift response',
        manual: 'Set by hand',
      }[kind],
      count: marks.filter((m) => m.kind === kind).length,
    })),
    marks,
    track,
    bands: bands.map((b) => ({
      label: b.label,
      starts_at: b.starts_at.toISOString(),
      ends_at: b.ends_at.toISOString(),
      temp_c: b.temp_c,
    })),
    boosts: [
      { key: 'deep', label: 'Increased deep sleep', percent: 30, for_fun: true },
      { key: 'rem', label: 'Increased REM sleep', percent: 27, for_fun: true },
      { key: 'ready', label: 'Fell asleep faster', percent: 6, for_fun: true },
    ],
    stages: { landed: 3, total: 3, missed: [] },
    on_target: Math.round((100 * off.filter((o) => o <= 0.5).length) / off.length),
    bed: {
      low_c: 18.7,
      high_c: 25.7,
      typical_off_c: Math.round((off.reduce((a, b) => a + b, 0) / off.length) * 10) / 10,
    },
    ready: { minutes: 29, reached: true, target_c: 19, start_c: 21.1, end_c: 19.2 },
    energy_kwh: 1.21,
    notes: [],
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
