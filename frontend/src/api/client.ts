/**
 * The contract between the app and the service.
 *
 * This exists so the design can be built and judged tonight without the backend,
 * and without any of it being thrown away afterwards. `MockApiClient` implements
 * this now; `HttpApiClient` will implement the same thing against the REST routes
 * and the /api/live WebSocket, and swapping them is one line in main.tsx. Same
 * principle as the transmitter adapter, applied to the frontend.
 *
 * Method names track the routes in Part 3 of the brief one for one.
 */

import type {
  TrendRange,
  Trends,
  NightNote,
  NightNotePatch,
  AuthState,
  AutopilotNight,
  AutopilotSwitch,
  HoldName,
  HealthReport,
  Learning,
  DeviceEvent,
  DeviceHealth,
  DeviceState,
  Holiday,
  Mode,
  PowerSample,
  Profile,
  Schedule,
  ServiceInfo,
  Stage,
  TonightState,
  Scoreboard,
  SleepTiming,
  Suggestion,
  WithingsStatus,
} from '../types'

/**
 * Where Connect goes. A page the browser navigates to rather than a fetch,
 * because signing in happens on Withings' own site and comes back to the
 * service, not to this script.
 */
export const WITHINGS_CONNECT_URL = '/api/withings/connect'

/** Pushed over the WebSocket whenever anything changes, so the app is never stale. */
export interface LiveUpdate {
  state?: DeviceState
  schedule?: Schedule
  event?: DeviceEvent
  health?: DeviceHealth[]
  /**
   * Whether the app can currently reach the service.
   *
   * Reported by the client rather than the service, because it is the one thing
   * the service cannot answer: a message saying "I am up" can only arrive when
   * it is. Silence is the signal, and only this end can hear it.
   */
  connected?: boolean
}

/**
 * Sent on the window whenever the service answers 401: the session ran out, or
 * every device was signed out from another one. The sign-in screen takes over.
 */
export const SIGNED_OUT_EVENT = 'hydrosnooze:signed-out'

export interface ApiClient {
  /**
   * Whether to show the sign-in, asked before anything else. The only calls that
   * answer without being signed in are these four. See access.py.
   */
  getAuth(): Promise<AuthState>
  signIn(password: string): Promise<AuthState>
  signOut(): Promise<AuthState>
  /** Every device, this one included. For a phone that has gone missing. */
  signOutEverywhere(): Promise<AuthState>

  /** The nights over weeks and months. See trends.py. */
  getTrends(days: TrendRange): Promise<Trends>
  /** Pence per kWh, or null to clear it. */
  setTariff(pencePerKwh: number | null): Promise<{ tariff_p: number | null }>

  /** How a night felt, keyed by the morning it ended. See notes.py. */
  getNote(wakeOn: string): Promise<NightNote>
  saveNote(wakeOn: string, patch: NightNotePatch): Promise<NightNote>

  /**
   * What is different about this one night, and which controls make sense now.
   *
   * Everything below it changes tonight and expires with it, so an experiment
   * costs nothing to undo and nothing to remember. Making one permanent is
   * keepTonight, which is the only one that touches the saved routine.
   */
  getTonight(): Promise<TonightState>
  setStageTonight(stage: Stage, tempC: number): Promise<TonightState>
  /** One degree, one period, no stacking. Never moves the switch-off. */
  nudgeTonight(deltaC: number): Promise<TonightState>
  /** Going to bed early, or sleeping in. Moves the switch-off with the alarm. */
  shiftTonight(patch: { bed_minutes?: number; wake_minutes?: number }): Promise<TonightState>
  skipTonight(skip: boolean): Promise<TonightState>
  /** Tonight's cooling speed. Picking the usual one takes tonight's back off. */
  speedTonight(coolingSpeed: Mode): Promise<TonightState>
  clearTonight(): Promise<TonightState>
  /** Save as my preference: tonight's temperatures become the usual ones. */
  keepTonight(): Promise<Schedule>

  /**
   * Away from home. Null when there is no holiday, or the one there was is over.
   *
   * Setting one replaces whatever was set before. Neither touches the routine,
   * and a night away that has already started is switched off straight away.
   */
  getHoliday(): Promise<Holiday | null>
  /** Both "YYYY-MM-DD". Rejects when coming back is not after leaving. */
  setHoliday(leavesOn: string, backOn: string): Promise<Holiday | null>
  clearHoliday(): Promise<void>

  /**
   * Last night, for the Autopilot screen. Rejects with a 404 message when there
   * is no finished night behind us yet, which is the first morning and any
   * morning after a day the schedule was off.
   */
  getAutopilot(): Promise<AutopilotNight>

  /**
   * What the bed has taught the app, and how many nights the rest of it needs.
   *
   * Separate from getAutopilot on purpose. That one has nothing to say until a
   * night has finished, and this one matters most on the first evening, when the
   * honest answer is "nothing measured yet, three nights to go".
   */
  getLearning(): Promise<Learning>
  /** The switch. Off returns to estimating and stops correcting temperatures. */
  setLearning(on: boolean): Promise<Learning>
  /** Start again. The nights are kept; they stop counting towards what is measured. */
  forgetLearning(mode?: Mode): Promise<Learning>

  /**
   * One night off the Sleep Analyzer, the way the Health Report draws it: the
   * night ending on `date`, "YYYY-MM-DD", or the latest one. Rejects when there
   * is no sleep at all yet, which getWithings then explains.
   */
  getHealthReport(date?: string): Promise<HealthReport>
  /**
   * The recent nights against the schedule's parts: when I fall asleep, when my
   * deep sleep is mostly done, and where the boundaries could move to. Always
   * answers, with "N more nights" before there is enough to say.
   */
  getSleepTiming(): Promise<SleepTiming>
  /** Start counting again, for a routine that has changed. The nights are kept. */
  forgetSleepTiming(): Promise<SleepTiming>
  /** Each temperature each part has run at, and the sleep on those nights. */
  getScoreboard(): Promise<Scoreboard>
  /**
   * The switch over all of Autopilot: learned timings and corrections, the
   * drift response and the evening suggestion. Off, the bed runs exactly the
   * temperatures set, and tonight goes back to usual if it was running a
   * suggestion.
   */
  getAutopilotSwitch(): Promise<AutopilotSwitch>
  setAutopilotSwitch(on: boolean): Promise<AutopilotSwitch>
  /** How closely warm parts are held: quiet, balanced or close. */
  setHold(hold: HoldName): Promise<AutopilotSwitch>
  /** Tonight's suggested Deep and REM, and where it is up to. */
  getSuggestion(): Promise<Suggestion>
  /** Use it for tonight. Changes tonight only; the routine is untouched. */
  acceptSuggestion(): Promise<Suggestion>
  /** Not tonight. */
  declineSuggestion(): Promise<Suggestion>
  /** How many degrees either side of the usual Deep and REM it may go, 1 to 3. */
  setSuggestionReach(reach: number): Promise<Suggestion>
  /** Where the Withings connection is up to. */
  getWithings(): Promise<WithingsStatus>
  /** Fetch now. `asked` is false when it was too soon after the last time. */
  syncWithings(): Promise<WithingsStatus & { asked: boolean }>
  /** Forget the tokens. The nights already fetched stay. */
  disconnectWithings(): Promise<WithingsStatus>

  /** Whether we are talking to a simulated unit, and the safety cap in force. */
  info(): Promise<ServiceInfo>

  getState(): Promise<DeviceState>
  getSchedule(): Promise<Schedule>

  /**
   * Saves the night. Takes effect from the next stage boundary onwards.
   *
   * Nothing is pushed to the unit here, because the unit holds no schedule any
   * more: the service drives every stage itself. There is no forty-five second
   * infrared ritual and nothing to stand and watch.
   */
  putSchedule(patch: Partial<Schedule>): Promise<Schedule>

  /** One press of power, the way the remote's button works. */
  pressPower(): Promise<void>
  /** Absolute and verified against the plug. Used by the schedule, not the button. */
  powerOn(): Promise<void>
  powerOff(): Promise<void>

  /** Immediate temperature change. Rejected only when the unit is off. */
  setTemperature(targetC: number): Promise<void>

  /**
   * Toggle the unit's button beep. A one-time setup action, never automatic:
   * the unit remembers the setting, so sending it on a schedule would unmute it
   * every other night.
   */
  mute(): Promise<void>
  /**
   * Restart the blaster board.
   *
   * For the failure nothing here can see: the board answering, every press
   * reporting success, and no infrared leaving the LED.
   */
  restartBlaster(): Promise<void>

  /** Saved nights, newest state of each, with whichever one is running marked. */
  getProfiles(): Promise<Profile[]>
  /** Snapshot the night the schedule is holding now, under a name. */
  saveProfile(name: string): Promise<Profile[]>
  /** Copy a saved night into the schedule. The wake time is left alone. */
  activateProfile(id: number): Promise<void>
  deleteProfile(id: number): Promise<Profile[]>
  setMode(mode: Mode): Promise<void>

  /** How each device is doing. The service is not in here: see LiveUpdate.connected. */
  getHealth(): Promise<DeviceHealth[]>

  getEvents(limit?: number): Promise<DeviceEvent[]>
  getPowerHistory(hours?: number): Promise<PowerSample[]>

  /** Returns an unsubscribe function. */
  subscribe(listener: (update: LiveUpdate) => void): () => void
}

/** Thrown when the service refuses a command, e.g. above the safety cap. */
export class ApiError extends Error {
  /** The HTTP status, when there was one. 401 means sign in. */
  status?: number

  constructor(message: string, status?: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}
