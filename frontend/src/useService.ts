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

/** The build this page was loaded with, learned the first time we ask. */
let loadedBuild: string | null = null

/**
 * Reload when the service is serving a newer frontend than this one.
 *
 * A phone with the app already open keeps running the JavaScript it loaded days
 * ago. A deploy replaces the files and restarts the service, the socket drops
 * and comes back, and the page in front of you carries on calling endpoints that
 * changed underneath it. That cost two evenings, one of them spent looking for a
 * bug in a power button that had already been fixed.
 *
 * A reconnect is exactly the right moment to check, because a deploy is the
 * usual reason for one.
 */
async function reloadIfTheServiceMovedOn(client: ApiClient): Promise<void> {
  try {
    const { build } = await client.info()
    if (!build) return
    if (loadedBuild === null) {
      loadedBuild = build
      return
    }
    if (build !== loadedBuild) window.location.reload()
  } catch {
    // A failed check is not worth reporting. The socket has only just come back
    // and there will be another reconnect along.
  }
}

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
      // The build this page is running, for the reconnect check above.
      if (loadedBuild === null) loadedBuild = i.build
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
        if (update.connected !== undefined) {
          setConnected(update.connected)
          if (update.connected) void reloadIfTheServiceMovedOn(client)
        }
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
