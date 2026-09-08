import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ApiClient } from './api/client'
import type {
  DeviceEvent,
  DeviceHealth,
  DeviceState,
  PowerSample,
  Schedule,
  ServiceInfo,
} from './types'

/**
 * Holds everything the service knows and keeps it fresh off the live feed.
 *
 * Nothing here cares whether the client is the mock or the real one, which is the
 * point: when HttpApiClient lands, only main.tsx changes.
 */
export function useService(client: ApiClient) {
  const [info, setInfo] = useState<ServiceInfo | null>(null)
  const [state, setState] = useState<DeviceState | null>(null)
  const [schedule, setSchedule] = useState<Schedule | null>(null)
  const [events, setEvents] = useState<DeviceEvent[]>([])
  const [power, setPower] = useState<PowerSample[]>([])
  const [health, setHealth] = useState<DeviceHealth[]>([])
  // Optimistic on the first render: the socket has not failed, it has not
  // opened yet. Showing the service as down for the half second before it
  // connects would make the bar cry wolf every time the app is opened.
  const [connected, setConnected] = useState(true)

  useEffect(() => {
    let live = true
    void Promise.all([
      client.info(),
      client.getState(),
      client.getSchedule(),
      client.getEvents(),
      client.getPowerHistory(),
      client.getHealth(),
    ]).then(([i, s, sch, ev, pw, hp]) => {
      if (!live) return
      setInfo(i)
      setState(s)
      setSchedule(sch)
      setEvents(ev)
      setPower(pw)
      setHealth(hp)
    })
    return () => {
      live = false
    }
  }, [client])

  useEffect(
    () =>
      client.subscribe((update) => {
        if (update.state) setState(update.state)
        if (update.schedule) setSchedule(update.schedule)
        if (update.event) setEvents((prev) => [update.event!, ...prev].slice(0, 200))
        if (update.health) setHealth(update.health)
        if (update.connected !== undefined) setConnected(update.connected)
      }),
    [client],
  )

  const refreshPower = useCallback(() => {
    void client.getPowerHistory().then(setPower)
  }, [client])

  return useMemo(
    () => ({ info, state, schedule, events, power, health, connected, refreshPower }),
    [info, state, schedule, events, power, health, connected, refreshPower],
  )
}
