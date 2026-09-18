import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
 * How many events the app holds, and how many it asks for.
 *
 * One number rather than two. It used to fetch a hundred and keep two hundred,
 * so every reconnect quietly halved the history: the list grew past a hundred
 * as the night ran, and the next backfill replaced it with the last hundred.
 */
const EVENT_LIMIT = 200

/**
 * Fetched history and whatever arrived live, as one list. Newest first.
 *
 * Neither side may simply win. Replacing the live list with the fetched one
 * loses anything that arrived while the fetch was in the air, and a reconnect is
 * exactly when that happens: a deploy restarts the service, the socket comes
 * back, the backfill goes out, and the service's own startup events are pushed
 * during the round trip. Those are the lines that say it came back up, and they
 * were the ones being erased.
 *
 * Replacing the other way round is no better, because the fetched list is the
 * only thing that can fill a gap the phone slept through.
 *
 * So both, keyed by id. Ids come from the service and survive its restarts,
 * because `seed` reads the highest one back out of the database on startup.
 */
function mergeEvents(fetched: DeviceEvent[], live: DeviceEvent[]): DeviceEvent[] {
  const byId = new Map<number, DeviceEvent>()
  for (const event of live) byId.set(event.id, event)
  for (const event of fetched) byId.set(event.id, event)
  return [...byId.values()].sort((a, b) => b.id - a.id).slice(0, EVENT_LIMIT)
}

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
  // Mirrors `connected` for the socket callback, which closes over a render and
  // cannot read the state it set. Starts true to match the optimism above, so
  // the socket's first open is not mistaken for a reconnection.
  const wasConnected = useRef(true)

  useEffect(() => {
    let live = true
    void Promise.all([
      client.info(),
      client.getState(),
      client.getSchedule(),
      client.getEvents(EVENT_LIMIT),
      client.getPowerHistory(),
      client.getHealth(),
    ]).then(([i, s, sch, ev, pw, hp]) => {
      if (!live) return
      setInfo(i)
      // The build this page is running, for the reconnect check above.
      if (loadedBuild === null) loadedBuild = i.build
      setState(s)
      setSchedule(sch)
      // Merged, not assigned. The socket subscribes in the effect below, on the
      // same mount, so it can already have delivered an event by the time this
      // first fetch lands.
      setEvents((prev) => mergeEvents(ev, prev))
      setPower(pw)
      setHealth(hp)
    })
    return () => {
      live = false
    }
  }, [client])

  /**
   * Fetch the two things the socket cannot backfill on its own.
   *
   * On connect the service resends state, schedule and health, so those look
   * after themselves. Events and power do not. Events only ever arrive as they
   * happen and are appended to whatever was fetched when the page loaded, and
   * power is only ever fetched here.
   *
   * So a phone that loaded the app in the evening, slept through the night
   * routine and woke at midnight reconnected holding an event list that stopped
   * at the moment it went to sleep. Nothing about it said so. The next event to
   * arrive live was appended to the top, leaving a list that ran straight from
   * early evening to now with the whole night missing from the middle and no gap
   * to see.
   *
   * That happened on 16 September and cost an evening: the pre-heat and Drift had
   * both run perfectly, and the app showed no trace of either, so the conclusion
   * from the screen was that the night had been missed entirely. A log that
   * quietly omits what it did not witness is worse than no log, because it is
   * believed.
   */
  const backfill = useCallback(
    () =>
      Promise.all([client.getEvents(EVENT_LIMIT), client.getPowerHistory()])
        .then(([ev, pw]) => {
          setEvents((prev) => mergeEvents(ev, prev))
          setPower(pw)
        })
        .catch(() => {
          // The socket has only just come back. There will be another reconnect
          // along, and a failed backfill must never take the app down with it.
        }),
    [client],
  )

  useEffect(
    () =>
      client.subscribe((update) => {
        if (update.state) setState(update.state)
        if (update.schedule) setSchedule(update.schedule)
        if (update.event) setEvents((prev) => mergeEvents([update.event!], prev))
        if (update.health) setHealth(update.health)
        if (update.connected !== undefined) {
          setConnected(update.connected)
          if (update.connected) {
            void reloadIfTheServiceMovedOn(client)
            // Only after an actual gap. The socket reports every open, including
            // the first, and the first is the page load that has just fetched all
            // of this already.
            if (!wasConnected.current) void backfill()
          }
          wasConnected.current = update.connected
        }
      }),
    [client, backfill],
  )

  const refreshPower = useCallback(() => {
    void client.getPowerHistory().then(setPower)
  }, [client])

  return useMemo(
    () => ({ info, state, schedule, events, power, health, connected, refreshPower }),
    [info, state, schedule, events, power, health, connected, refreshPower],
  )
}
