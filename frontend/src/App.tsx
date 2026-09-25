import { useCallback, useEffect, useState } from 'react'
import { BottomNav, type Screen } from './components/BottomNav'
import { ChevronRight, MenuIcon } from './components/Icons'
import { PowerButton } from './components/PowerButton'
import { overallHealth } from './components/DeviceBar'
import { SideMenu, type MenuView } from './components/SideMenu'
import { Home } from './screens/Home'
import { History } from './screens/History'
import { HealthReport } from './screens/HealthReport'
import { Profiles } from './screens/Profiles'
import { Autopilot } from './screens/Autopilot'
import { Schedule } from './screens/Schedule'
import { Holiday } from './screens/Holiday'
import { Alarm } from './screens/Alarm'
import { CoolingSpeed } from './screens/CoolingSpeed'
import { Status } from './screens/Status'
import { Dev } from './screens/Dev'
import { useService } from './useService'
import { useTonight } from './useTonight'
import type { ApiClient } from './api/client'
import {
  MAX_TEMPERATURE_C,
  type Holiday as HolidayType,
  type Schedule as ScheduleType,
} from './types'

/**
 * A screen pushed over home: one of the menu's, or one a screen opens itself.
 * The schedule is opened from the Alarm screen and the saved nights from the
 * Temperature card.
 */
type View = MenuView | 'schedule' | 'profiles'

/** What Back says when this is the screen it goes back to. */
const TITLE: Record<View, string> = {
  autopilot: 'Autopilot',
  alarm: 'Alarm',
  speed: 'Cooling speed',
  status: 'Status',
  holiday: 'Holiday mode',
  schedule: 'Schedule',
  profiles: 'Saved nights',
}

/**
 * Back from signing in to Withings, the service sends the browser to
 * `/?withings=connected` or `/?withings=failed`. Either way the Health Report is
 * where the answer is, so that is where the app opens, and the word comes off
 * the address so a reload does not do it again.
 */
function firstScreen(): Screen {
  const params = new URLSearchParams(window.location.search)
  if (!params.has('withings')) return 'home'
  params.delete('withings')
  const rest = params.toString()
  window.history.replaceState(null, '', `${window.location.pathname}${rest ? `?${rest}` : ''}`)
  return 'report'
}

export function App({ client }: { client: ApiClient }) {
  const [screen, setScreen] = useState<Screen>(firstScreen)
  // What is pushed over the home screen, innermost last. A stack rather than a
  // flag per screen, because the schedule is two deep now, under Alarm, and
  // Back from it should land on Alarm rather than all the way home.
  const [stack, setStack] = useState<View[]>([])
  const [scheduleError, setScheduleError] = useState<string | null>(null)
  const [menuOpen, setMenuOpen] = useState(false)
  // Here rather than in the screens, because three of them draw it: the menu
  // says whether it is on, the home screen says so in a line, and the Alarm
  // card steps over its nights.
  const [holiday, setHoliday] = useState<HolidayType | null>(null)

  const [powerError, setPowerError] = useState<string | null>(null)
  const { info, state, schedule, events, power, health, connected } = useService(client)
  const { tonight, setTonight, now } = useTonight(client, schedule)

  const ready = state !== null && schedule !== null
  const view: View | null = ready && screen === 'home' ? (stack[stack.length - 1] ?? null) : null
  const overall = overallHealth(health, connected)

  // Re-read whenever the schedule is pushed. The service pushes it when a
  // holiday is set or cleared, from this phone or any other, which is the only
  // live signal there is; and a holiday that has ended simply stops coming back.
  useEffect(() => {
    let live = true
    void client
      .getHoliday()
      .then((h) => live && setHoliday(h))
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [client, schedule])

  function saveSchedule(patch: Partial<ScheduleType>) {
    setScheduleError(null)
    void client.putSchedule(patch).catch((e: Error) => setScheduleError(e.message))
  }

  function push(next: View) {
    setScheduleError(null)
    setStack((s) => [...s, next])
  }

  function back() {
    setScheduleError(null)
    setStack((s) => s.slice(0, -1))
  }

  const closeMenu = useCallback(() => setMenuOpen(false), [])

  function openFromMenu(next: MenuView) {
    // From whichever tab the menu was opened on, and in place of whatever was
    // already pushed, so Back from a menu screen always goes home.
    setScheduleError(null)
    setScreen('home')
    setStack([next])
    setMenuOpen(false)
  }

  return (
    <div className="app">
      <header className="app__header">
        {view ? (
          <button type="button" className="app__back" onClick={back}>
            <ChevronRight className="app__back-icon" />
            {stack.length > 1 ? TITLE[stack[stack.length - 2]!] : 'Home'}
          </button>
        ) : (
          <div className="app__lead">
            <button
              type="button"
              className="app__menu"
              aria-label={
                overall === 'down'
                  ? 'Menu. Something is not answering'
                  : overall === 'degraded'
                    ? 'Menu. Something has gone quiet'
                    : overall === 'ok'
                      ? 'Menu. Everything is answering'
                      : 'Menu'
              }
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen(true)}
            >
              <MenuIcon />
              {/*
                The device chips used to sit under this header on every screen.
                They are in the menu now, and this dot is what still reaches you
                wherever you are: amber or red when one of them does, green when
                every one of them is.
              */}
              {overall && <span className={`app__menu-dot app__menu-dot--${overall}`} />}
            </button>
            <h1 className="app__title">HydroSnooze</h1>
          </div>
        )}

        {/*
          In the header rather than on the home screen, because it is the one
          thing worth reaching in a single tap from wherever you happen to be.
        */}
        {state && (
          <PowerButton
            power={state.power}
            onPress={() => client.pressPower()}
            onError={setPowerError}
          />
        )}
      </header>

      {powerError && (
        <p className="app__error" onClick={() => setPowerError(null)}>
          {powerError}
        </p>
      )}

      <main className="app__scroll">
        {!ready ? (
          <p className="empty">Connecting…</p>
        ) : screen === 'dev' ? (
          <Dev state={state} realPlug={!(info?.fake_power_monitor ?? true)} />
        ) : screen === 'history' ? (
          <History power={power} events={events} />
        ) : screen === 'report' ? (
          <HealthReport client={client} onOpenAutopilot={() => openFromMenu('autopilot')} />
        ) : view === 'holiday' ? (
          <Holiday client={client} schedule={schedule} holiday={holiday} onChanged={setHoliday} />
        ) : view === 'autopilot' ? (
          <Autopilot client={client} schedule={schedule} />
        ) : view === 'alarm' ? (
          <Alarm
            client={client}
            schedule={schedule}
            tonight={tonight}
            holiday={holiday}
            onTonight={setTonight}
            onOpenSchedule={() => push('schedule')}
          />
        ) : view === 'speed' ? (
          <CoolingSpeed
            client={client}
            state={state}
            schedule={schedule}
            tonight={tonight}
            onTonight={setTonight}
          />
        ) : view === 'status' ? (
          <Status client={client} state={state} />
        ) : view === 'profiles' ? (
          <Profiles client={client} onSaved={back} />
        ) : view === 'schedule' ? (
          <Schedule
            draft={schedule}
            onChange={saveSchedule}
            error={scheduleError}
            onDismissError={() => setScheduleError(null)}
            state={state}
            onStartRehearsal={(seconds) =>
              client.startRehearsal(seconds).catch((e: Error) => setScheduleError(e.message))
            }
            onStopRehearsal={() =>
              client.stopRehearsal().catch((e: Error) => setScheduleError(e.message))
            }
            holiday={holiday}
            skippingTonight={tonight && tonight.phase !== 'none' ? tonight.skip : null}
            onSkipTonight={(skip) => {
              setScheduleError(null)
              void client
                .skipTonight(skip)
                .then(setTonight)
                .catch((e: Error) => setScheduleError(e.message))
            }}
          />
        ) : (
          <Home
            client={client}
            state={state}
            schedule={schedule}
            holiday={holiday}
            tonight={tonight}
            onTonight={setTonight}
            now={now}
            maxC={info?.max_temperature_c ?? MAX_TEMPERATURE_C}
            onOpenProfiles={() => push('profiles')}
          />
        )}
      </main>

      <BottomNav
        screen={screen}
        onChange={(next) => {
          setScheduleError(null)
          setStack([])
          setScreen(next)
        }}
        showDev={info?.fake_transmitter ?? false}
      />

      {/* Even before the service answers: see the note in SideMenu. */}
      <SideMenu
        open={menuOpen}
        onClose={closeMenu}
        onOpen={openFromMenu}
        client={client}
        health={health}
        connected={connected}
        state={state}
        schedule={schedule}
        tonight={tonight}
        holiday={holiday}
      />

    </div>
  )
}
