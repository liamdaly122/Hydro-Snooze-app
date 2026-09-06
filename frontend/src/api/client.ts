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
  WriteProgress,
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

  /** Saves locally in the service. Does NOT touch the unit. */
  putSchedule(patch: Partial<Schedule>): Promise<Schedule>

  /**
   * Pushes the saved phase temperatures to the unit over infrared. Long running,
   * around 45 seconds, and never triggered by automation: user action only.
   */
  writeSchedule(onProgress: (p: WriteProgress) => void): Promise<void>

  /** Arm the unit's own schedule now, with whatever it already has saved. */
  armSchedule(): Promise<void>

  powerOn(): Promise<void>
  powerOff(): Promise<void>

  /** Immediate temperature change. Rejected while a schedule is running. */
  setTemperature(targetC: number): Promise<void>
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
