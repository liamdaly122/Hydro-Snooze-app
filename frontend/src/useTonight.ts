import { useEffect, useState } from 'react'
import type { ApiClient } from './api/client'
import { homeNow } from './domain'
import type { Schedule, TonightState } from './types'

/**
 * Tonight, held once for every screen that draws it.
 *
 * It used to be fetched twice, by the app and by the home screen, and the two
 * had to agree about whether tonight was being skipped. With the Alarm and
 * Cooling speed cards on screens of their own that would have been four copies
 * of one answer, so there is one.
 *
 * Re-read whenever the schedule is pushed, which the service does after every
 * tonight-only change, and on the minute: a phase changes at bedtime and a
 * nudge lapses on its own, and neither sends anything to say so. `now` ticks
 * with it so the nudge can count itself down.
 */
export function useTonight(client: ApiClient, schedule: Schedule | null) {
  const [tonight, setTonight] = useState<TonightState | null>(null)
  const [now, setNow] = useState(() => homeNow())

  useEffect(() => {
    let live = true
    const load = () =>
      void client
        .getTonight()
        .then((t) => live && setTonight(t))
        .catch(() => undefined)
    load()
    const tick = setInterval(() => {
      if (!live) return
      setNow(homeNow())
      load()
    }, 60_000)
    return () => {
      live = false
      clearInterval(tick)
    }
  }, [client, schedule])

  return { tonight, setTonight, now }
}
