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
  NightNote,
  NightNotePatch,
  AuthState,
  AutopilotNight,
  AutopilotSwitch,
  AutopilotTest,
  HoldName,
  AutopilotSleep,
  DeviceEvent,
  DeviceHealth,
  DeviceState,
  HealthDay,
  HealthReport,
  Holiday,
  Learning,
  LearningMode,
  Mode,
  PowerSample,
  Preconditioning,
  Profile,
  Schedule,
  ServiceInfo,
  Scoreboard,
  SleepStage,
  SleepTiming,
  Suggestion,
  Stage,
  TonightPhase,
  TonightState,
  WithingsStatus,
} from '../types'
import {
  MAX_TEMPERATURE_C,
  MIN_STAGE_MINUTES,
  MODE_RANGE,
  STAGE_LABEL,
  WARMING_FLOOR_C,
} from '../types'
import { daysBetween, isoDay } from '../domain'

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

/** The same list as backend/hydrosnooze/notes.py. */
const MOCK_TAGS = [
  { key: 'alcohol', label: 'Alcohol', leaves_out: true },
  { key: 'ill', label: 'Ill', leaves_out: true },
  { key: 'company', label: 'Someone else in the bed', leaves_out: true },
  { key: 'caffeine', label: 'Late caffeine', leaves_out: false },
  { key: 'late_meal', label: 'Late meal', leaves_out: false },
  { key: 'exercise', label: 'Exercise', leaves_out: false },
  { key: 'stressed', label: 'Stressed', leaves_out: false },
]

function mockNote(wakeOn: string): NightNote {
  return {
    wake_on: wakeOn,
    rating: null,
    felt: null,
    tags: [],
    left_out: [],
    choices: {
      ratings: ['Rough', 'Groggy', 'OK', 'Good', 'Great'].map((label, i) => ({ value: i + 1, label })),
      felt: [
        { value: 'too_cold', label: 'Too cold' },
        { value: 'right', label: 'Right' },
        { value: 'too_warm', label: 'Too warm' },
      ],
      tags: MOCK_TAGS,
    },
  }
}

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
      // Thirty five minutes to fall asleep on, a touch warmer than Deep.
      { stage: 'drift', duration_minutes: 35, temp_c: 18, mode: 'quiet' },
      { stage: 'deep', duration_minutes: 222, temp_c: 17, mode: 'quiet' },
      { stage: 'rem', duration_minutes: 195, temp_c: 20, mode: 'quiet' },
      // The one the unit's own scheduler made impossible.
      { stage: 'wake', duration_minutes: 28, temp_c: 26, mode: 'warming' },
    ],
    cooling_speed: 'quiet',
    preconditioning: { mode: 'turbo', lead_minutes: 20, reason: 'Cooling the bed from about 20C down to 18C.' },
    updated_at: new Date(Date.now() - 3 * 86_400_000).toISOString(),
    other_days: [],
    other_bed_time: null,
    other_wake_time: null,
    other_night_minutes: null,
  }

  private events: DeviceEvent[] = []
  private listeners = new Set<(u: LiveUpdate) => void>()
  private nextEventId = 1
  private drift?: ReturnType<typeof setInterval>

  constructor() {
    this.seedEvents()
    this.seedActivity(new URLSearchParams(location.search).get('activity'))
    // Nudge the watt reading every few seconds so the status strip and the chart
    // are never suspiciously still.
    this.drift = setInterval(() => {
      if (this.state.observed_power_w === null) return
      if (this.state.inferred_activity === 'off') return
      const base = { cooling: 170, heating: 306, idle: 40, off: 0.4, unknown: 32 }[
        this.state.inferred_activity
      ]
      this.patchState({ observed_power_w: round1(base + (Math.random() - 0.5) * 14) })
    }, 4000)
  }

  dispose(): void {
    if (this.drift) clearInterval(this.drift)
    this.listeners.clear()
  }

  /**
   * `?activity=` on the URL starts the unit doing something other than
   * cooling, so each look of the bed on the home screen can be judged without
   * waiting for the real unit to do it: heating, holding, holding-warm, off or
   * unknown.
   */
  private seedActivity(activity: string | null): void {
    const set = (patch: Partial<DeviceState>) => (this.state = { ...this.state, ...patch })
    if (activity === 'heating') {
      set({ assumed_mode: 'warming', assumed_target_c: 28, observed_power_w: 306, inferred_activity: 'heating', observed_flow_c: 31.2, observed_return_c: 27.4 })
    } else if (activity === 'holding') {
      set({ observed_power_w: 41, inferred_activity: 'idle', observed_flow_c: 19.1, observed_return_c: 19.3 })
    } else if (activity === 'holding-warm') {
      set({ assumed_mode: 'warming', assumed_target_c: 28, observed_power_w: 41, inferred_activity: 'idle', observed_flow_c: 27.9, observed_return_c: 27.7 })
    } else if (activity === 'off') {
      set({ power: 'off', current_stage: null, assumed_mode: null, assumed_target_c: null, observed_power_w: 0.4, inferred_activity: 'off', observed_flow_c: 18.4, observed_return_c: 18.5 })
    } else if (activity === 'unknown') {
      set({ power: 'unknown', assumed_mode: null, assumed_target_c: null, observed_power_w: null, inferred_activity: 'unknown', observed_flow_c: null, observed_return_c: null })
    }
  }

  private notes = new Map<string, NightNote>()

  async getNote(wakeOn: string): Promise<NightNote> {
    return this.notes.get(wakeOn) ?? mockNote(wakeOn)
  }

  async saveNote(wakeOn: string, patch: NightNotePatch): Promise<NightNote> {
    await sleep(120)
    const was = await this.getNote(wakeOn)
    const tags = patch.tags ?? was.tags
    const note: NightNote = {
      ...was,
      rating: patch.rating !== undefined ? patch.rating : was.rating,
      felt: patch.felt !== undefined ? patch.felt : was.felt,
      tags: MOCK_TAGS.map((t) => t.key).filter((k) => tags.includes(k)),
      left_out: MOCK_TAGS.filter((t) => t.leaves_out && tags.includes(t.key)).map((t) => t.label),
    }
    this.notes.set(wakeOn, note)
    return note
  }

  // No password in the seed build: there is nothing behind it to protect.
  async getAuth(): Promise<AuthState> {
    return { required: false, signed_in: true, via: 'home', refused: null }
  }

  async signIn(): Promise<AuthState> {
    return this.getAuth()
  }

  async signOut(): Promise<AuthState> {
    return this.getAuth()
  }

  async signOutEverywhere(): Promise<AuthState> {
    return this.getAuth()
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
    next.other_night_minutes =
      next.other_bed_time && next.other_wake_time
        ? minutesBetween(next.other_bed_time, next.other_wake_time)
        : null
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
      // Cleared, the way _run_power_off clears it. A unit that is off is not
      // holding a temperature, and leaving the last one on the dial here meant
      // the seed data never showed the state the real app spends its day in.
      assumed_target_c: null,
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
        { stage: 'drift', duration_minutes: 35, temp_c: 18, mode: 'quiet' },
        { stage: 'deep', duration_minutes: 222, temp_c: 17, mode: 'quiet' },
        { stage: 'rem', duration_minutes: 195, temp_c: 20, mode: 'quiet' },
        { stage: 'wake', duration_minutes: 28, temp_c: 26, mode: 'warming' },
      ],
      active: true,
      created_at: nowIso(),
    },
    {
      id: 2,
      name: 'Winter',
      cooling_speed: 'quiet',
      stages: [
        { stage: 'drift', duration_minutes: 35, temp_c: 27, mode: 'warming' },
        { stage: 'deep', duration_minutes: 222, temp_c: 26, mode: 'warming' },
        { stage: 'rem', duration_minutes: 195, temp_c: 27, mode: 'warming' },
        { stage: 'wake', duration_minutes: 28, temp_c: 28, mode: 'warming' },
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
    cooling_speed: null as Mode | null,
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
      cooling_speed: t.cooling_speed ?? this.schedule.cooling_speed,
      wake_time,
      bed_time,
      night_minutes,
      stages: withModes(
        fitStages((t.stages ?? this.schedule.stages).map((s) => ({ ...s })), night_minutes),
        t.cooling_speed ?? this.schedule.cooling_speed,
      ),
    }
    return {
      phase: this.tonightPhase,
      running,
      // Not the nudge: it is visible where it happens and lapses on its own.
      changed:
        t.skip ||
        t.stages !== null ||
        t.wake_time !== null ||
        t.bed_time !== null ||
        t.cooling_speed !== null,
      skip: t.skip,
      stages_changed: t.stages !== null,
      times_changed: t.wake_time !== null || t.bed_time !== null,
      speed_changed: t.cooling_speed !== null,
      nudge_c: t.nudge_c,
      nudge_until: t.nudge_until,
      suggested: this.suggestedTonight(),
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

  async speedTonight(cooling_speed: Mode): Promise<TonightState> {
    await sleep(120)
    this.tonightState.cooling_speed =
      cooling_speed === this.schedule.cooling_speed ? null : cooling_speed
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
      cooling_speed: null,
    }
    return this.tonightJson()
  }

  async keepTonight(): Promise<Schedule> {
    await sleep(160)
    const running = this.tonightJson().running
    this.schedule = {
      ...this.schedule,
      cooling_speed: running.cooling_speed,
      stages: running.stages.map((s) => ({ ...s })),
    }
    this.tonightState.stages = null
    this.tonightState.cooling_speed = null
    return this.schedule
  }

  /**
   * Holiday mode, in memory. `?holiday=2026-10-02,2026-10-05` on the URL starts
   * with one set, so the banner and the calendar can be looked at without
   * picking dates every time.
   */
  private holiday: Holiday | null = seedHoliday(
    new URLSearchParams(location.search).get('holiday'),
  )

  async getHoliday(): Promise<Holiday | null> {
    await sleep(80)
    return this.holiday && { ...this.holiday }
  }

  async setHoliday(leaves_on: string, back_on: string): Promise<Holiday | null> {
    await sleep(160)
    // The same two refusals the service makes, in the same words.
    if (back_on <= leaves_on) {
      throw new ApiError('The day you get back has to be after the day you leave.')
    }
    if (back_on < isoDay(new Date())) {
      throw new ApiError('Those dates are already over. Pick a day back from today on.')
    }
    this.holiday = { leaves_on, back_on, nights: daysBetween(leaves_on, back_on) }
    return { ...this.holiday }
  }

  async clearHoliday(): Promise<void> {
    await sleep(120)
    this.holiday = null
  }

  async getAutopilot(): Promise<AutopilotNight> {
    await sleep(120)
    const seed = await this.health()
    return { ...seedNight(), sleep: seed.autopilot_sleep, test: seed.autopilot_test }
  }

  /**
   * The Health Report, from a fortnight of invented nights put through the real
   * report builder by scripts/health-seed.py, so the seed site draws exactly what
   * the service would send. Loaded on first use rather than bundled, so the live
   * app never downloads it.
   */
  private healthSeed: Promise<HealthSeed> | null = null
  private withingsConnected = true

  private health(): Promise<HealthSeed> {
    this.healthSeed ??= import('./seed-health.json').then((m) => m.default as unknown as HealthSeed)
    return this.healthSeed
  }

  async getHealthReport(date?: string): Promise<HealthReport> {
    const seed = await this.health()
    await sleep(180)
    const on = date ?? seed.latest
    const held = seed.reports[on]
    if (held) return held
    // A week the seed has nothing for, so it can still be stepped through.
    return { week: emptyWeek(on), earliest: seed.earliest, latest: seed.latest, night: null }
  }

  /**
   * The seed's Sleep timing, measured by the real timing.py against the seed
   * site's starting schedule, with the parts and suggestions re-read from the
   * schedule as it stands now. Mirrors _snap and _fit there, so taking a
   * suggestion here leaves nothing to suggest, the way it does on the Pi. The
   * nights stay measured from the starting lights out: moving lights out on the
   * seed site does not move them.
   */
  async getSleepTiming(): Promise<SleepTiming> {
    const seed = (await this.health()).timing
    await sleep(120)
    // Started again: nothing new has been slept since, on the seed site.
    if (this.timingSince !== null) {
      return { ...seed, nights: 0, since: this.timingSince, profile: null, boundaries: [] }
    }
    const step = MIN_STAGE_MINUTES
    let cursor = 0
    const parts = this.schedule.stages.map((s) => {
      const part = {
        part: s.stage,
        label: STAGE_LABEL[s.stage],
        starts_min: cursor,
        ends_min: cursor + s.duration_minutes,
        temp_c: s.temp_c,
      }
      cursor += s.duration_minutes
      return part
    })
    const ends = Object.fromEntries(parts.map((p) => [p.part, p.ends_min])) as Record<Stage, number>
    const snap = (target: number, current: number) =>
      current + step * Math.floor((target - current) / step + 0.5)

    const wanted: Partial<Record<Stage, number>> = {}
    if (seed.nights >= seed.suggests_at) {
      for (const b of seed.boundaries) {
        if (b.steady && b.measured) wanted[b.part] = snap(b.measured.median_min, ends[b.part])
      }
    }
    const deep = Math.min(wanted.deep ?? ends.deep, ends.rem - step)
    const drift = Math.max(step, Math.min(wanted.drift ?? ends.drift, deep - step))
    const fitted: Partial<Record<Stage, number>> = {}
    if (deep - drift >= step) {
      if (wanted.drift !== undefined || drift !== ends.drift) fitted.drift = drift
      if (wanted.deep !== undefined) fitted.deep = deep
    }

    return {
      ...seed,
      lights_out: this.schedule.bed_time,
      wake: this.schedule.wake_time,
      night_minutes: this.schedule.night_minutes,
      parts,
      boundaries: seed.boundaries.map((b) => {
        const to = fitted[b.part]
        return {
          ...b,
          ends_min: ends[b.part],
          suggest_min: to !== undefined && to !== ends[b.part] ? to : null,
        }
      }),
    }
  }

  private timingSince: string | null = null

  /**
   * Tonight's suggestion, for the seed site. Always evening here, whatever the
   * clock says, so the card can be seen at any hour: Deep a degree cooler than
   * usual as a test, REM as usual. Taking it goes through setStageTonight, the
   * same tonight-only change the service makes, so Tonight only and Back to
   * usual behave as they do on the Pi.
   */
  private autopilotOn = true
  private holdName: HoldName = 'balanced'

  private switchJson(): AutopilotSwitch {
    return {
      on: this.autopilotOn,
      hold: this.holdName,
      holds: [
        {
          name: 'quiet',
          label: 'Quiet',
          describe:
            'Quietest. The bed goes quiet half a degree short of a warm target and warms again at 2° below, so it can sit up to 2° under.',
        },
        {
          name: 'balanced',
          label: 'Balanced',
          describe:
            'Quiet once the bed reaches the target, warming again at 1° below. Within about a degree, with more warming time.',
        },
        {
          name: 'close',
          label: 'Close',
          describe:
            'Warm parts keep warming unless your body heat pushes the bed a degree over. Closest to the target, and the noisiest.',
        },
      ],
    }
  }

  async getAutopilotSwitch(): Promise<AutopilotSwitch> {
    await sleep(60)
    return this.switchJson()
  }

  async setHold(hold: HoldName): Promise<AutopilotSwitch> {
    await sleep(100)
    this.holdName = hold
    return this.switchJson()
  }

  /** Off puts tonight back to usual if it was running the suggestion, as on the Pi. */
  async setAutopilotSwitch(on: boolean): Promise<AutopilotSwitch> {
    await sleep(120)
    this.autopilotOn = on
    if (!on && this.suggestedTonight()) {
      this.tonightState.stages = null
      // As the service does after any change to tonight, so Home re-reads it.
      this.emit({ schedule: { ...this.schedule } })
    }
    return this.switchJson()
  }

  /** Tonight's change, when it is the suggestion as taken. */
  private suggestedTonight(): TonightState['suggested'] {
    if (this.suggested.decision !== 'accepted' || this.tonightState.stages === null) return null
    const offered = this.suggestionParts()
    const running = this.tonightState.stages
    const matches = offered.every(
      (p) => (running.find((st) => st.stage === p.part)?.temp_c ?? p.usual_c) === p.tonight_c,
    )
    if (!matches) return null
    const test = offered.find((p) => p.test)
    return {
      temps: Object.fromEntries(offered.map((p) => [p.part, p.tonight_c])),
      usual: Object.fromEntries(offered.map((p) => [p.part, p.usual_c])),
      test: test ? { part: test.part, offset_c: test.tonight_c - test.usual_c } : null,
    }
  }

  private suggestionParts() {
    return this.suggestionJson(true).parts
  }

  private suggested = {
    decision: null as 'accepted' | 'declined' | null,
    reach: 2,
    centre: null as { deep: number; rem: number } | null,
  }

  private suggestionJson(ignoreSwitch = false): Suggestion {
    const usualOf = (part: 'deep' | 'rem') =>
      this.schedule.stages.find((st) => st.stage === part)?.temp_c ?? 20
    const usual = { deep: usualOf('deep'), rem: usualOf('rem') }
    this.suggested.centre ??= { ...usual }
    const { reach, centre } = this.suggested
    const low = (part: 'deep' | 'rem') => centre![part] - reach
    const high = (part: 'deep' | 'rem') => centre![part] + reach
    const deep = usual.deep - 1 >= low('deep') ? usual.deep - 1 : usual.deep + 1
    const tonight = { deep, rem: usual.rem }
    const running = (this.tonightState.stages ?? this.schedule.stages)
    const undone =
      this.suggested.decision === 'accepted' &&
      running.find((st) => st.stage === 'deep')?.temp_c !== tonight.deep
    return {
      state:
        !this.autopilotOn && !ignoreSwitch
          ? 'off'
          : undone
            ? 'undone'
            : (this.suggested.decision ?? 'ready'),
      wake_on: isoDay(new Date(Date.now() + 86_400_000)),
      parts: (['deep', 'rem'] as const).map((part) => ({
        part,
        label: part === 'deep' ? 'Deep' : 'REM',
        usual_c: usual[part],
        tonight_c: tonight[part],
        low_c: low(part),
        high_c: high(part),
        test: part === 'deep',
      })),
      test: { part: 'deep', offset_c: deep - usual.deep },
      why: `A test night: Deep a degree ${deep < usual.deep ? 'cooler' : 'warmer'} than the best so far, to see what it does to your deep sleep and REM.`,
      reach,
      reach_max: 3,
      test_every: 3,
      limits: (['deep', 'rem'] as const).map((part) => ({
        part,
        label: part === 'deep' ? 'Deep' : 'REM',
        low_c: low(part),
        high_c: high(part),
      })),
    }
  }

  async getSuggestion(): Promise<Suggestion> {
    await sleep(100)
    const s = this.suggestionJson()
    // Off, the service sends the limits and nothing about tonight.
    return s.state === 'off' ? { ...s, parts: [], test: null, why: null } : s
  }

  async acceptSuggestion(): Promise<Suggestion> {
    const offered = this.suggestionJson()
    if (offered.state !== 'ready') throw new ApiError('There is no suggestion to take for tonight.')
    for (const p of offered.parts) {
      if (p.tonight_c !== p.usual_c) await this.setStageTonight(p.part, p.tonight_c)
    }
    this.suggested.decision = 'accepted'
    return this.suggestionJson()
  }

  async declineSuggestion(): Promise<Suggestion> {
    await sleep(100)
    this.suggested.decision = 'declined'
    return this.suggestionJson()
  }

  async setSuggestionReach(reach: number): Promise<Suggestion> {
    await sleep(100)
    if (reach < 1 || reach > 3) throw new ApiError('Suggestions can go 1 to 3 degrees either side.')
    const usualOf = (part: 'deep' | 'rem') =>
      this.schedule.stages.find((st) => st.stage === part)?.temp_c ?? 20
    this.suggested.reach = reach
    this.suggested.centre = { deep: usualOf('deep'), rem: usualOf('rem') }
    return this.suggestionJson()
  }

  /** From the seed's nights and what each ran, through the real scoreboard.py. */
  async getScoreboard(): Promise<Scoreboard> {
    const seed = await this.health()
    await sleep(120)
    return seed.scoreboard
  }

  async forgetSleepTiming(): Promise<SleepTiming> {
    this.timingSince = isoDay(new Date())
    return this.getSleepTiming()
  }

  async getWithings(): Promise<WithingsStatus> {
    const seed = await this.health()
    return { ...seed.status, connected: this.withingsConnected }
  }

  async syncWithings(): Promise<WithingsStatus & { asked: boolean }> {
    await sleep(700)
    return { ...(await this.getWithings()), last_sync_at: nowIso(), asked: true }
  }

  async disconnectWithings(): Promise<WithingsStatus> {
    await sleep(200)
    this.withingsConnected = false
    return this.getWithings()
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
interface HealthSeed {
  earliest: string
  latest: string
  status: WithingsStatus
  reports: Record<string, HealthReport>
  autopilot_sleep: AutopilotSleep[]
  timing: SleepTiming
  scoreboard: Scoreboard
  autopilot_test: AutopilotTest | null
}

/** Seven empty days, Sunday first, around a morning. */
function emptyWeek(on: string): HealthDay[] {
  const day = new Date(`${on}T12:00:00`)
  const sunday = new Date(day.getTime() - day.getDay() * 86_400_000)
  return Array.from({ length: 7 }, (_, i) => ({
    date: new Date(sunday.getTime() + i * 86_400_000).toISOString().slice(0, 10),
    score: null,
    has_night: false,
  }))
}

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
    track.push({
      at: new Date(ms).toISOString(),
      bed_c: Math.round(shown * 100) / 100,
      target_c: target,
    })
  }

  const bedAt = (ms: number) =>
    track.reduce((best, p) =>
      Math.abs(new Date(p.at).getTime() - ms) < Math.abs(new Date(best.at).getTime() - ms) ? p : best,
    ).bed_c

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
    bed_c: bedAt(when.getTime()),
  }))

  // Only the night itself, the way the service scores it: before bedtime the bed
  // is on its way to the number rather than failing to hold it.
  const overnight = bands[0]!.starts_at.getTime()
  const off = track
    .filter((p) => new Date(p.at).getTime() >= overnight)
    .map((p) => Math.abs(p.bed_c - p.target_c))
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
    // Filled in by getAutopilot from seed-health.json, where the real
    // against_usual worked it out for the seed's last night.
    sleep: [],
    stages: { landed: 3, total: 3, missed: [], cancelled: [] },
    on_target: Math.round((100 * off.filter((o) => o <= 0.5).length) / off.length),
    // Seed data is written as though the app had always recorded its target.
    from_record: true,
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

/** "2026-10-02,2026-10-05" off the URL, or nothing. */
function seedHoliday(param: string | null): Holiday | null {
  const [leaves_on, back_on] = (param ?? '').split(',')
  if (!leaves_on || !back_on || back_on <= leaves_on) return null
  return { leaves_on, back_on, nights: daysBetween(leaves_on, back_on) }
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
