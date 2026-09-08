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
  setMode(mode: Mode): Promise<void>

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
