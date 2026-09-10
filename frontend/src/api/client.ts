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
  DeviceEvent,
  DeviceHealth,
  DeviceState,
  Mode,
  PowerSample,
  Schedule,
  ServiceInfo,
} from '../types'

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

export interface ApiClient {
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

  /**
   * Run tonight's whole night, compressed into a few minutes, right now.
   *
   * The same scheduler, the same sequences, the same plug checks. Only the
   * durations are short, because the one thing that cannot be tested any other
   * way is whether a stage boundary really lands on the unit.
   */
  startRehearsal(seconds: number): Promise<void>
  /** Stop early. Always leaves the unit off, which is where a night ends. */
  stopRehearsal(): Promise<void>

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
  constructor(message: string) {
    super(message)
    this.name = 'ApiError'
  }
}
