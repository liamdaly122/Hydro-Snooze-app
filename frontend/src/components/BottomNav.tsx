import { ChartIcon, HomeIcon, WrenchIcon } from './Icons'

export type Screen = 'home' | 'history' | 'dev'

/**
 * Two icons, as designed. The third only appears when the service reports a fake
 * transmitter, so it is never there on the Pi with real hardware attached.
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
  return (
    <nav className={`nav${showDev ? ' nav--three' : ''}`} aria-label="Sections">
      <button
        type="button"
        className="nav__item"
        aria-current={screen === 'home' ? 'page' : undefined}
        aria-label="Home"
        onClick={() => onChange('home')}
      >
        <HomeIcon />
      </button>
      <button
        type="button"
        className="nav__item"
        aria-current={screen === 'history' ? 'page' : undefined}
        aria-label="History"
        onClick={() => onChange('history')}
      >
        <ChartIcon />
      </button>
      {showDev && (
        <button
          type="button"
          className="nav__item"
          aria-current={screen === 'dev' ? 'page' : undefined}
          aria-label="Simulator"
          onClick={() => onChange('dev')}
        >
          <WrenchIcon />
        </button>
      )}
    </nav>
  )
}
