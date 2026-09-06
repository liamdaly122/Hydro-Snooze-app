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
  DeviceState,
  Mode,
  PowerSample,
  Schedule,
  ServiceInfo,
  WriteProgress,
} from '../types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: init?.body ? { 'content-type': 'application/json' } : undefined,
  })
  if (!response.ok) {
    // FastAPI puts the reason in `detail`, and it is written to be read by a
    // person: "22C is above the 30C safety cap".
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
  getEvents = (limit = 100) => request<DeviceEvent[]>(`/api/events?limit=${limit}`)
  getPowerHistory = (hours = 24) => request<PowerSample[]>(`/api/power?hours=${hours}`)

  putSchedule = (patch: Partial<Schedule>) =>
    request<Schedule>('/api/schedule', { method: 'PUT', body: JSON.stringify(patch) })

  armSchedule = async () => {
    await request<DeviceState>('/api/schedule/arm', { method: 'POST' })
  }

  powerOn = async () => {
    await request<DeviceState>('/api/power/on', { method: 'POST' })
  }

  powerOff = async () => {
    await request<DeviceState>('/api/power/off', { method: 'POST' })
  }

  setTemperature = async (targetC: number) => {
    await request<DeviceState>('/api/temperature', {
      method: 'POST',
      body: JSON.stringify({ target_c: targetC }),
    })
  }

  setMode = async (mode: Mode) => {
    await request<DeviceState>('/api/mode', { method: 'POST', body: JSON.stringify({ mode }) })
  }

  /**
   * Around ninety presses over about forty five seconds, streamed back one JSON
   * object per line so the progress bar reflects presses actually sent rather
   * than a guess at how long it should take.
   */
  writeSchedule = async (onProgress: (p: WriteProgress) => void): Promise<void> => {
    const response = await fetch('/api/schedule/write', { method: 'POST' })
    if (!response.ok || !response.body) {
      throw new ApiError(`Could not start the write: ${response.status}`)
    }
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let failure: string | null = null

    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      for (const line of lines) {
        if (!line.trim()) continue
        const frame = JSON.parse(line) as WriteProgress
        if (frame.phase === 'failed') failure = frame.message
        onProgress(frame)
      }
    }
    if (failure) throw new ApiError(failure)
  }

  subscribe = (listener: (u: LiveUpdate) => void): (() => void) => {
    this.listeners.add(listener)
    this.open()
    return () => {
      this.listeners.delete(listener)
      if (this.listeners.size === 0) this.close()
    }
  }

  private open(): void {
    if (this.socket || this.closed) return
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const socket = new WebSocket(`${protocol}//${location.host}/api/live`)
    this.socket = socket

    socket.onmessage = (message) => {
      const payload = JSON.parse(message.data as string)
      if (payload.ping) return
      for (const listener of this.listeners) listener(payload as LiveUpdate)
    }
    socket.onclose = () => {
      this.socket = null
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
