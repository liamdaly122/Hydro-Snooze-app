import { Moon } from './Icons'
import type { NapState } from '../types'

function hhmm(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

/**
 * One line on the home screen while a nap runs, with the way to stop it.
 *
 * The same shape as the Holiday and Tonight lines rather than a card: a nap is
 * started from the menu, and once it is going the only things worth a glance
 * are what it is doing and when it ends. Until the bed gets there the end is
 * not known, because the length counts from then, so it says when it expects
 * to be ready instead.
 */
export function NapBanner({ nap, onStop }: { nap: NapState; onStop: () => Promise<unknown> }) {
  const way = nap.mode === 'warming' ? 'Warming' : 'Cooling'
  const sub = nap.ends_at
    ? `${nap.temp_c}° until ${hhmm(nap.ends_at)}, then off`
    : `${way} to ${nap.temp_c}°, ready about ${hhmm(nap.expect_ready_at)}. ` +
      `${nap.minutes} min from then`

  return (
    <div className="holiday-banner nap-banner" role="status">
      <span className="holiday-banner__icon">
        <Moon size={16} />
      </span>
      <span className="tonight__text">
        <span className="tonight__title">Nap</span>
        <span className="tonight__sub">{sub}</span>
      </span>
      <button type="button" className="nap-banner__stop" onClick={() => void onStop()}>
        Stop
      </button>
    </div>
  )
}
