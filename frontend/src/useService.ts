import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ApiClient } from './api/client'
import type { DeviceEvent, DeviceState, PowerSample, Schedule, ServiceInfo } from './types'

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

  useEffect(() => {
    let live = true
    void Promise.all([
      client.info(),
      client.getState(),
      client.getSchedule(),
      client.getEvents(),
      client.getPowerHistory(),
    ]).then(([i, s, sch, ev, pw]) => {
      if (!live) return
      setInfo(i)
      setState(s)
      setSchedule(sch)
      setEvents(ev)
      setPower(pw)
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
      }),
    [client],
  )

  const refreshPower = useCallback(() => {
    void client.getPowerHistory().then(setPower)
  }, [client])

  return useMemo(
    () => ({ info, state, schedule, events, power, refreshPower }),
    [info, state, schedule, events, power, refreshPower],
  )
}
