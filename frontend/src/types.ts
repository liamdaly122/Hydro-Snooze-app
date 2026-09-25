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
 * service, never chosen: the bed starts wherever the hose probes say it is, the
 * first stage says where it has to be, and the gap decides both the mode and
 * how long before bedtime to switch on. With no probes reporting it assumes a
 * room-temperature bed and `reason` says so.
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
 *
 * Drift is the half hour before any of that: falling asleep is helped by a
 * slightly warmer surface and deep sleep by a cooler one, and with Deep starting
 * the moment the night did, one number had to serve both.
 */
export type Stage = 'drift' | 'deep' | 'rem' | 'wake'

export const STAGE_ORDER: Stage[] = ['drift', 'deep', 'rem', 'wake']

/**
 * No stage is allowed to disappear, and this is also the step a boundary moves
 * by, so a stage can always be nudged back off its own floor. Mirrors
 * MIN_STAGE_MINUTES in backend/hydrosnooze/models.py.
 */
export const MIN_STAGE_MINUTES = 15

export const STAGE_LABEL: Record<Stage, string> = {
  drift: 'Drift',
  deep: 'Deep',
  rem: 'REM',
  wake: 'Wake',
}

/**
 * Drift holds its length rather than taking a share of the night, so a later
 * bedtime comes off the three that follow it. Mirrors HOLDS_ITS_LENGTH in
 * backend/hydrosnooze/models.py.
 */
export const HOLDS_ITS_LENGTH: Stage[] = ['drift']

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
  /**
   * What the bed was doing on the same beat. Null wherever the probe board was
   * quiet, and on every sample recorded before the probes existed. Null rather
   * than the last value carried forward, because a chart that draws a flat line
   * across a gap lies about exactly the thing it is there to show.
   */
  flow_c: number | null
  return_c: number | null
  room_c: number | null
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
  /**
   * Changes whenever the built frontend does. The app remembers the one it
   * started with and reloads itself when the service reports a different one.
   */
  build: string
}

/**
 * A saved night, by name. "Summer", "Winter", "Guest room".
 *
 * Only the shape of a night: the stage temperatures, how long each lasts, and
 * the cooling speed. Deliberately not the wake time or the days of the week,
 * because those belong to the week you are having rather than to the weather,
 * and loading "Summer" should not move an alarm.
 */
export interface Profile {
  id: number
  name: string
  cooling_speed: Mode
  stages: SleepStage[]
  /**
   * Whether the schedule is currently running exactly this. Worked out by the
   * service by comparing, never stored, so it cannot be a flag left behind by an
   * edit made afterwards. Change a temperature and this goes false, which is the
   * truth.
   */
  active: boolean
  created_at: string | null
}

/* --- Autopilot ---------------------------------------------------------------
 *
 * Last night, as the Autopilot screen draws it.
 *
 * Everything here was written down while the night happened: a count of what
 * the service did, the moments it did it, and how far the bed sat from what it
 * was being asked for at the time. `sleep` is the mat's own measurements of the
 * same night, which replaced three invented "boosts".
 */

/**
 * Why an adjustment happened: the three reasons the service ever changes
 * anything, plus the one that is not the service at all.
 */
export type AdjustmentKind = 'phase' | 'precool' | 'quiet' | 'manual'

export interface AutopilotMark {
  at: string
  kind: AdjustmentKind
  label: string
  detail: string
  /** What the bed read at that moment, so the dot sits on the line the chart
   *  draws. Null if the probes were quiet, and then it has nowhere to sit. */
  bed_c: number | null
}

export interface AutopilotPoint {
  at: string
  /** What the probes read. */
  bed_c: number
  /** What was being asked for at that moment, recorded then rather than worked
   *  out now. Includes nudges and anything set by hand. */
  target_c: number
}

export interface AutopilotBand {
  label: string
  starts_at: string
  ends_at: string
  temp_c: number
}

/**
 * One of the mat's measurements for the Autopilot night, against my usual.
 *
 * Says what changed, never why: a better night after a colder stage is two
 * facts side by side, not a result.
 */
export interface AutopilotSleep {
  key: 'deep' | 'rem' | 'asleep'
  label: string
  seconds: number
  /** The median of the nights before, or null until there are three. */
  usual_seconds: number | null
  nights: number
  change_pct: number | null
  /** More deep and REM is better; less time to fall asleep is better. */
  better: boolean | null
}

/**
 * One of the two things a mode can learn, and how close it is to knowing it.
 *
 * The sentence comes from the service rather than being assembled here. Every
 * one of them carries a measured number, and a screen that writes its own
 * sentence around a figure it was handed is a screen that can eventually
 * describe a correction that is not happening.
 */
export interface LearningSkill {
  key: 'pace' | 'settle'
  title: string
  /** Nights counted so far, never past `needed`. */
  runs: number
  needed: number
  unlocked: boolean
  detail: string
}

export interface LearningMode {
  mode: Mode
  target_c: number
  needed: number
  /** How many of this mode's skills are measured. Out of `skills.length`. */
  unlocked: number
  skills: LearningSkill[]
}

export interface Learning {
  /** The switch. Off means estimate the head start and send what was asked for. */
  on: boolean
  modes: LearningMode[]
}

export interface AutopilotNight {
  wake_at: string
  starts_at: string
  adjustments: number
  /** Whether the probes said anything at all. False means a count and no chart. */
  measured: boolean
  breakdown: { kind: AdjustmentKind; label: string; count: number }[]
  marks: AutopilotMark[]
  track: AutopilotPoint[]
  bands: AutopilotBand[]
  sleep: AutopilotSleep[]
  /** `cancelled` is stages called off by switching automation off or skipping
   * mid-night, which is not a failure and is drawn apart from `missed`. */
  stages: { landed: number; total: number; missed: string[]; cancelled: string[] }
  /** Share of the night proper, after bedtime, that the bed sat within half a
   *  degree of its setpoint. Getting ready is reported separately in `ready`. */
  on_target: number | null
  /** Whether the score came from what the app wrote down at the time. False for
   *  nights recorded before it started, which are still reconstructed. */
  from_record: boolean
  bed: { low_c: number | null; high_c: number | null; typical_off_c: number | null }
  ready: {
    minutes: number
    reached: boolean
    target_c: number
    start_c: number | null
    end_c: number | null
  } | null
  energy_kwh: number
  notes: string[]
}


/* --- Tonight only -------------------------------------------------------------
 *
 * The saved Schedule is the routine: the nights you usually have. This is the one
 * you are actually having, and it expires with the morning it belongs to.
 *
 * `phase` is the service saying which controls make sense right now, rather than
 * the app working it out again from a clock and a plan. The six do not share one
 * window: shaping a night happens before it starts, nudging one happens from
 * inside it.
 */
/* --- Holiday mode -------------------------------------------------------------
 *
 * Away from home, with nothing switching on for any of the nights in between.
 * Mirrors Holiday in backend/hydrosnooze/models.py.
 *
 * Two dates, the way anyone says them. The first night off is the evening of
 * `leaves_on` and the bed runs again the evening of `back_on`. In the wake
 * mornings every night here is named by, that is every morning after the day
 * you leave, up to and including the day you get back. See `awayOn` in
 * domain.ts.
 *
 * The routine is not touched while it is set, and it ends by itself: the
 * service stops returning it once the morning of `back_on` has gone.
 */
export interface Holiday {
  /** "YYYY-MM-DD". The day you leave: that night is the first one off. */
  leaves_on: string
  /** "YYYY-MM-DD". The day you get back: that night runs as usual. */
  back_on: string
  nights: number
}

export type TonightPhase = 'none' | 'evening' | 'running' | 'after'

export interface TonightState {
  phase: TonightPhase
  /** The schedule as tonight is actually being run. Draw this, not the routine. */
  running: Schedule
  changed: boolean
  skip: boolean
  stages_changed: boolean
  times_changed: boolean
  /** Tonight has its own cooling speed. The usual one is on the schedule. */
  speed_changed: boolean
  nudge_c: number
  nudge_until: string | null
}

/* --- Health Report -------------------------------------------------------------
 *
 * One night off the Withings Sleep Analyzer, the way the Health Report draws it.
 * Mirrors backend/hydrosnooze/withings/health.py.
 *
 * Every number is the mat's, or worked out from what the mat measured. Every
 * verdict and label comes from the service rather than being decided here, for
 * the same reason the learning sentences do: a screen that judges a number it
 * was handed can drift from the service that measured it.
 */

export type Verdict =
  | 'excellent'
  | 'good'
  | 'fair'
  | 'low'
  | 'learning'
  | 'in_range'
  | 'above'
  | 'below'

export interface Judged {
  verdict: Verdict | null
  label: string | null
  /** Only when learning: how many more nights before there is a number. */
  nights_needed?: number
}

export interface HealthDay {
  /** "YYYY-MM-DD", the morning the night ended. */
  date: string
  score: number | null
  has_night: boolean
}

export type SleepStateName = 'awake' | 'light' | 'deep' | 'rem'

export interface HealthStage {
  stage: SleepStateName
  starts_at: string
  ends_at: string
}

export interface HealthAgainst {
  seconds: number | null
  /** Share of the time asleep, not of the time in bed. */
  percent: number | null
  target_seconds: number
  met: boolean
}

export interface HealthVital extends Judged {
  value: number | null
  unit: string
  /** The middle 80% of my own recent nights, once there are enough of them. */
  range: [number, number] | null
}

export interface HealthNight {
  wake_on: string
  timezone: string | null
  completed: boolean | null
  updated_at: string | null
  in_bed: { starts_at: string; ends_at: string }
  fell_asleep_at: string | null
  woke_up_at: string | null
  score: Judged & { value: number | null }
  tiles: {
    quality: Judged & { percent: number | null; means: string }
    routine: Judged & { percent: number | null; means: string }
    time_slept: Judged & { seconds: number | null }
  }
  /** Runs of one state, in order. Gaps between them are time out of bed. */
  stages: HealthStage[]
  out_of_bed: { starts_at: string; ends_at: string }[]
  totals: Record<SleepStateName, number | null>
  rem: HealthAgainst
  deep: HealthAgainst
  vitals: { heart_rate: HealthVital; hrv: HealthVital; breath_rate: HealthVital }
  breathing: { disturbances: number | null; apnea_hypopnea_index: number | null }
  bed: HealthBed
}

/** How the bed sat through one state of sleep. */
export interface BedInState {
  mean_c: number | null
  /** Minutes in this state the probes had a reading for, out of `of`. */
  minutes: number
  of: number
}

/**
 * The bed beside the sleeper: the return hose's temperature for every minute
 * from getting into bed to getting out, on the same minutes as the stages, and
 * what it averaged in each state. Null where the probes said nothing.
 */
export interface HealthBed {
  starts_at: string
  step_s: number
  bed_c: (number | null)[]
  target_c: (number | null)[]
  by_stage: Partial<Record<SleepStateName | 'out_of_bed', BedInState>>
  measured: boolean
}

/** A part of the schedule's night, in minutes from lights out. */
export interface TimingPart {
  part: Stage
  label: string
  starts_min: number
  ends_min: number
  temp_c: number
}

/** The middle of the nights, and the middle half of them either side. */
export interface TimingSpread {
  median_min: number
  low_min: number
  high_min: number
}

/**
 * One boundary the mat can speak to. Drift's end against when I fall asleep;
 * Deep's end against when my deep sleep is mostly done.
 */
export interface TimingBoundary {
  part: 'drift' | 'deep'
  label: string
  ends_min: number
  measured: TimingSpread | null
  /** Whether the nights agree closely enough to move it. Null with nothing measured. */
  steady: boolean | null
  /** Where to move it, or null: too few nights, too unsteady, or already there. */
  suggest_min: number | null
}

/**
 * When I really sleep, against the parts of the night the bed runs. Every time
 * is minutes from the schedule's lights out.
 */
export interface SleepTiming {
  nights: number
  /** The last morning set aside by Start again, or null when every night counts. */
  since: string | null
  shows_at: number
  suggests_at: number
  lights_out: string
  wake: string
  night_minutes: number
  bin_min: number
  parts: TimingPart[]
  /** How often each state was happening in each bin, 0 to 1. Null before shows_at. */
  profile: Record<SleepStateName, number[]> | null
  boundaries: TimingBoundary[]
}

/** One temperature a part has run at, and the sleep on those nights. */
export interface ScoreSetting {
  set_c: number
  nights: number
  tests: number
  mean_s: number
  sd_s: number
  low_s: number
  high_s: number
  /** What else was going on: the bedroom, and what the bed actually averaged. */
  room_c: number | null
  bed_c: number | null
  /** What a setting must not make worse, whatever it is scored on. */
  awake_s: number | null
  asleep_after_s: number | null
}

export interface ScorePart {
  part: 'deep' | 'rem' | 'drift'
  label: string
  /** What the part is scored on: deep sleep, REM, or time to fall asleep. */
  measure: string
  more_is_better: boolean
  settings: ScoreSetting[]
  /**
   * empty: nothing yet. one_setting: only one temperature tried. not_sure: the
   * gap is inside the night-to-night swing, or too few nights to compare.
   * clear: the leader is ahead by more than the swing explains.
   */
  verdict: 'empty' | 'one_setting' | 'not_sure' | 'clear'
  leader_c: number | null
  runner_c: number | null
  gap_s: number | null
  swing_s: number | null
}

/** Each part's settings and the sleep on them, from night_runs and the mat. */
export interface Scoreboard {
  window_days: number
  /** Nights written down, mat or not. */
  recorded: number
  /** Nights written down that the mat has too. */
  nights: number
  tests: number
  setting_needs: number
  parts: ScorePart[]
}

export interface HealthReport {
  /** Seven days, Sunday first, around the night asked for. */
  week: HealthDay[]
  earliest: string | null
  latest: string | null
  /** Null for a morning the mat has no night for. */
  night: HealthNight | null
}

export interface WithingsStatus {
  /** Whether this machine has the client ID and secret at all. */
  configured: boolean
  connected: boolean
  needs_reconnect: boolean
  waiting_for_clock: boolean
  last_sync_at: string | null
  last_error: string | null
  latest_night: string | null
}
