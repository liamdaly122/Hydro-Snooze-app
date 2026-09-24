import type { ReactNode } from 'react'
import { ChartIcon, HomeIcon, TrendIcon, WrenchIcon } from './Icons'

export type Screen = 'home' | 'report' | 'history' | 'dev'

/**
 * Home, the Health Report and History, in that order. The fourth only appears
 * when the service reports a fake transmitter, so it is never there on the Pi
 * with real hardware attached.
 *
 * The bar chart is the Health Report's, because it is in the report this is
 * modelled on. History, which had it until then, took a line instead.
 */
export function BottomNav({
  screen,
  onChange,
  showDev = false,
}: {
  screen: Screen
  onChange: (screen: Screen) => void
  showDev?: boolean
}) {
  const item = (to: Screen, label: string, icon: ReactNode) => (
    <button
      type="button"
      className="nav__item"
      aria-current={screen === to ? 'page' : undefined}
      aria-label={label}
      onClick={() => onChange(to)}
    >
      {icon}
    </button>
  )
  return (
    <nav className={`nav ${showDev ? 'nav--four' : 'nav--three'}`} aria-label="Sections">
      {item('home', 'Home', <HomeIcon />)}
      {item('report', 'Health Report', <ChartIcon />)}
      {item('history', 'History', <TrendIcon />)}
      {showDev && item('dev', 'Simulator', <WrenchIcon />)}
    </nav>
  )
}
