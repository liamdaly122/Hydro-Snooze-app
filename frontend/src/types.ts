/**
 * Mirrors backend/hydrosnooze/models.py. When one changes, change the other.
 *
 * These are the shapes the service really sends, which is why the design is built
 * against them from the first commit rather than against invented mock data. The
 * mock client below implements the same contract the live client will.
 */

export type Mode = 'quiet' | 'standard' | 'turbo' | 'warming'
export type Power = 'on' | 'off' | 'unknown'
export type Tristate = 'true' | 'false' | 'unknown'
export type Activity = 'off' | 'idle' | 'cooling' | 'heating' | 'unknown'

export const COOLING_MODES: Mode[] = ['quiet', 'standard', 'turbo']
export const ALL_MODES: Mode[] = ['quiet', 'standard', 'turbo', 'warming']

export const MODE_LABEL: Record<Mode, string> = {
  quiet: 'Quiet',
  standard: 'Standard',
  turbo: 'Turbo',
  warming: 'Warming',
}

export const MODE_RANGE: Record<Mode, [number, number]> = {
  quiet: [15, 35],
  standard: [15, 35],
  turbo: [15, 35],
  warming: [25, 55],
}

/**
 * The highest temperature the app will accept. The service is the authority and
 * sends its own value; this is only the fallback before that arrives.
 */
export const MAX_TEMPERATURE_C = 55

/**
 * How the bed gets ready before the first stage starts. Worked out by the
 * service, never chosen: the bed starts at room temperature, the first stage
 * says where it has to be, and the gap decides both the mode and how long
 * before bedtime to switch on.
 *
 * A null mode means there is nothing to do, and `reason` says why in words.
 */
export interface Preconditioning {
  mode: Mode | null
  lead_minutes: number
  reason: string
}

/**
 * The lowest temperature warming mode can express. Below this the unit has no way
 * to heat the bed at all, whatever the app does.
 */
export const WARMING_FLOOR_C = 25

/**
 * The parts of a night, in the order they happen.
 *
 * Deep sleep is concentrated in the first third and REM lengthens through the
 * second half, so the order is chronological rather than alphabetical.
 */
export type Stage = 'deep' | 'rem' | 'wake'

export const STAGE_ORDER: Stage[] = ['deep', 'rem', 'wake']

/**
 * No stage is allowed to disappear, and this is also the step a boundary moves
 * by, so a stage can always be nudged back off its own floor. Mirrors
 * MIN_STAGE_MINUTES in backend/hydrosnooze/models.py.
 */
export const MIN_STAGE_MINUTES = 15

export const STAGE_LABEL: Record<Stage, string> = {
  deep: 'Deep',
  rem: 'REM',
  wake: 'Wake',
}

export interface SleepStage {
  stage: Stage
  duration_minutes: number
  temp_c: number
  /**
   * Derived by the service, not chosen, and derived from the whole night rather
   * than this stage. Between 25 and 35 both modes reach the number, so which one
   * a stage lands in depends on the temperature before it.
   */
  mode: Mode
}

export interface Schedule {
  id: number
  name: string
  enabled: boolean
  /** Keyed to the wake morning. Monday is 0. */
  days_of_week: number[]
  /** "HH:MM", 24 hour. */
  wake_time: string
  /** "HH:MM", 24 hour. Set, not derived: with the wake time it fixes the night. */
  bed_time: string
  /** Lights out to alarm, wrapping midnight. Derived by the service. */
  night_minutes: number
  /** The night, in order. Their durations always add up to `night_minutes`. */
  stages: SleepStage[]
  /** Which speed a cooling stage runs at. Warming stages ignore it. */
  cooling_speed: Mode
  /** Decided by the service from the first stage, not a setting. */
  preconditioning: Preconditioning
  updated_at: string | null
}

export type Health = 'ok' | 'degraded' | 'down' | 'simulated' | 'unknown'

/** One thing that can independently stop working, and how it is doing. */
export interface DeviceHealth {
  name: string
  health: Health
  detail: string
  last_ok_at: string | null
}

export interface DeviceState {
  power: Power
  /** Which part of the night is running. Known, not assumed: the app drives it. */
  current_stage: Stage | null
  /** When a compressed rehearsal night ends, or null if none is running. */
  rehearsal_ends_at: string | null
  /** Set by us, never read back off the unit. */
  assumed_mode: Mode | null
  /** The last target we commanded. null means we genuinely do not know. */
  assumed_target_c: number | null
  /** The one honestly observed value on this screen: it comes from the plug. */
  observed_power_w: number | null
  /** Measured on the hoses, not believed. Null means no probe board, or a
   *  reading too old to call current: never a guess and never a stale one. */
  observed_flow_c: number | null
  observed_return_c: number | null
  observed_room_c: number | null
  inferred_activity: Activity
  last_command_at: string | null
  last_error: string | null
}

export interface PowerSample {
  at: string
  watts: number
}

export type EventLevel = 'info' | 'warning' | 'error'

export interface DeviceEvent {
  id: number
  at: string
  level: EventLevel
  kind: string
  message: string
}

/** Reported by the service so the app knows it is talking to a simulated unit. */
export interface ServiceInfo {
  fake_transmitter: boolean
  fake_power_monitor: boolean
  max_temperature_c: number
}
