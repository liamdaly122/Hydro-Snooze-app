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

/** Hard safety cap. A heater capable of 55C under a bed gets a ceiling. */
export const MAX_TEMPERATURE_C = 30

/**
 * How the bed is brought to the phase 1 temperature before the schedule arms.
 *
 * A cooler cannot warm a bed, so if phase 1 is above whatever the bed is resting
 * at, only warming gets there. It only affects the hour before arming: once
 * someone is in the bed, body heat means cooling to 24°C works properly.
 */
export type Precondition = 'cool' | 'warm'

/**
 * The lowest temperature warming mode can express. Below this the unit has no way
 * to heat the bed at all, whatever the app does.
 */
export const WARMING_FLOOR_C = 25

/** Fixed by the unit: 4h + 4h + 30m. Not configurable, which is the whole trick. */
export const SCHEDULE_DURATION_MINUTES = 8 * 60 + 30

export interface Schedule {
  id: number
  name: string
  enabled: boolean
  /** Keyed to the wake morning. Monday is 0. */
  days_of_week: number[]
  /** "HH:MM", 24 hour. */
  wake_time: string
  phase1_temp_c: number
  phase2_temp_c: number
  phase3_temp_c: number
  mode: Mode
  precool_enabled: boolean
  /** How to get the bed to the phase 1 temperature before the schedule arms. */
  precondition: Precondition
  /** False when phase 1 is below warming's floor, so pre-heating cannot work. */
  preheat_is_possible: boolean
  precool_lead_minutes: number
  /** ISO timestamp of the last successful write to the unit, or null if never. */
  last_written_at: string | null
  updated_at: string | null
}

export interface DeviceState {
  power: Power
  in_schedule: Tristate
  /** Set by us, never read back off the unit. */
  assumed_mode: Mode | null
  /** The last target we commanded. null means we genuinely do not know. */
  assumed_target_c: number | null
  /** The one honestly observed value on this screen: it comes from the plug. */
  observed_power_w: number | null
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

/** Progress frames streamed while write_schedule runs. */
export interface WriteProgress {
  phase: 'mode' | 'phase1' | 'phase2' | 'phase3' | 'exit' | 'done' | 'failed'
  presses_sent: number
  presses_total: number
  message: string
}

/** Reported by the service so the app knows it is talking to a simulated unit. */
export interface ServiceInfo {
  fake_transmitter: boolean
  fake_power_monitor: boolean
  max_temperature_c: number
}
