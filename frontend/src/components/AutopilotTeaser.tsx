import { useEffect, useState } from 'react'
import { ChevronRight, Sparkle } from './Icons'
import type { ApiClient } from '../api/client'
import type { AutopilotNight } from '../types'

/**
 * The way into Autopilot, at the top of the home screen.
 *
 * It renders nothing at all until there is a finished night behind it. An empty
 * shell saying "no data yet" would be worse than an absence: this sits above the
 * controls, and a permanent grey box at the top of the first screen is a thing
 * you learn to look past, which is the opposite of what it is for.
 */
export function AutopilotTeaser({ client, onOpen }: { client: ApiClient; onOpen: () => void }) {
  const [night, setNight] = useState<AutopilotNight | null>(null)

  useEffect(() => {
    let live = true
    void client
      .getAutopilot()
      .then((n) => live && setNight(n))
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [client])

  if (!night) return null

  const when = new Date(night.wake_at).toLocaleDateString('en-GB', {
    weekday: 'long',
  })

  return (
    <button type="button" className="ap-teaser" onClick={onOpen}>
      <Sparkle size={26} className="ap-teaser__mark" glow />
      <span className="ap-teaser__text">
        <span className="ap-teaser__title">Autopilot</span>
        <span className="ap-teaser__sub">
          {night.adjustments} adjustment{night.adjustments === 1 ? '' : 's'} &middot; {when} night
        </span>
      </span>
      <ChevronRight className="ap-teaser__chevron" />
    </button>
  )
}
