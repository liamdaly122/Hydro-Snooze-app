/**
 * The live client. Same interface as MockApiClient, talking to the real service.
 *
 * Swapping between them is one line in main.tsx, which is the whole reason the
 * design was built against an interface rather than against invented data.
 *
 * URLs are relative, so this works unchanged whether the app is served by the
 * Pi itself or by the Vite dev server proxying to a laptop.
 */

import { ApiError, type ApiClient, type LiveUpdate } from './client'
import type {
  DeviceEvent,
  DeviceHealth,
  DeviceState,
  Mode,
  PowerSample,
  Schedule,
  ServiceInfo,
} from '../types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: init?.body ? { 'content-type': 'application/json' } : undefined,
  })
  if (!response.ok) {
    // FastAPI puts the reason in `detail`, and it is written to be read by a
    // person: "40C is outside quiet's range of 15 to 35".
    let message = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (typeof body?.detail === 'string') message = body.detail
    } catch {
      /* keep the status line */
    }
    throw new ApiError(message)
  }
  return (await response.json()) as T
}

export class HttpApiClient implements ApiClient {
  private socket: WebSocket | null = null
  private listeners = new Set<(u: LiveUpdate) => void>()
  private reconnect: ReturnType<typeof setTimeout> | undefined
  private closed = false

  info = () => request<ServiceInfo>('/api/info')
  getState = () => request<DeviceState>('/api/state')
  getSchedule = () => request<Schedule>('/api/schedule')
  getHealth = () => request<DeviceHealth[]>('/api/health')

  getEvents = (limit = 100) => request<DeviceEvent[]>(`/api/events?limit=${limit}`)
  getPowerHistory = (hours = 24) => request<PowerSample[]>(`/api/power?hours=${hours}`)

  putSchedule = (patch: Partial<Schedule>) =>
    request<Schedule>('/api/schedule', { method: 'PUT', body: JSON.stringify(patch) })

  pressPower = async () => {
    await request<DeviceState>('/api/power/press', { method: 'POST' })
  }

  powerOn = async () => {
    await request<DeviceState>('/api/power/on', { method: 'POST' })
  }

  powerOff = async () => {
    await request<DeviceState>('/api/power/off', { method: 'POST' })
  }

  startRehearsal = async (seconds: number) => {
    await request<unknown>('/api/rehearsal', {
      method: 'POST',
      body: JSON.stringify({ seconds }),
    })
  }

  stopRehearsal = async () => {
    await request<DeviceState>('/api/rehearsal', { method: 'DELETE' })
  }

  setTemperature = async (targetC: number) => {
    await request<DeviceState>('/api/temperature', {
      method: 'POST',
      body: JSON.stringify({ target_c: targetC }),
    })
  }

  restartBlaster = async () => {
    await request<DeviceState>('/api/blaster/restart', { method: 'POST' })
  }

  mute = async () => {
    await request<DeviceState>('/api/mute', { method: 'POST' })
  }

  setMode = async (mode: Mode) => {
    await request<DeviceState>('/api/mode', { method: 'POST', body: JSON.stringify({ mode }) })
  }

  subscribe = (listener: (u: LiveUpdate) => void): (() => void) => {
    this.listeners.add(listener)
    this.open()
    return () => {
      this.listeners.delete(listener)
      if (this.listeners.size === 0) this.close()
    }
  }

  private emit(update: LiveUpdate): void {
    for (const listener of this.listeners) listener(update)
  }

  private open(): void {
    if (this.socket || this.closed) return
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const socket = new WebSocket(`${protocol}//${location.host}/api/live`)
    this.socket = socket

    // Whether the app can reach the service is the one thing the service cannot
    // report, because a message saying "I am up" only ever arrives when it is.
    // Silence is the signal and only this end can hear it, so the socket's own
    // state is what the device bar shows for the service.
    socket.onopen = () => this.emit({ connected: true })

    socket.onmessage = (message) => {
      const payload = JSON.parse(message.data as string)
      if (payload.ping) return
      this.emit(payload as LiveUpdate)
    }
    socket.onclose = () => {
      this.socket = null
      this.emit({ connected: false })
      // The Pi rebooting, or the phone waking from sleep. Keep trying quietly.
      if (!this.closed && this.listeners.size > 0) {
        this.reconnect = setTimeout(() => this.open(), 2000)
      }
    }
    socket.onerror = () => socket.close()
  }

  private close(): void {
    this.closed = true
    clearTimeout(this.reconnect)
    this.socket?.close()
    this.socket = null
  }
}

// --- Dev only -----------------------------------------------------------------

export interface SimSnapshot {
  now: string
  speed: number
  unit: {
    powered: boolean
    mode: Mode
    target_c: number | null
    phase_temps: number[]
    display_awake: boolean
    adjusting: boolean
    wizard_phase: number | null
    schedule_running: boolean
    schedule_ends_at: string | null
    watts: number
  }
  press_log: string[]
}

export const sim = {
  get: () => request<SimSnapshot>('/api/dev/sim'),
  clock: (body: { jump_to?: string; advance_minutes?: number; speed?: number }) =>
    request<SimSnapshot>('/api/dev/clock', { method: 'POST', body: JSON.stringify(body) }),
  reset: () => request<SimSnapshot>('/api/dev/reset', { method: 'POST' }),
}
