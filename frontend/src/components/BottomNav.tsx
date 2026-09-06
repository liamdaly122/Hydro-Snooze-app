import { ChartIcon, HomeIcon } from './Icons'

export type Screen = 'home' | 'history'

export function BottomNav({
  screen,
  onChange,
}: {
  screen: Screen
  onChange: (screen: Screen) => void
}) {
  return (
    <nav className="nav" aria-label="Sections">
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
    </nav>
  )
}
