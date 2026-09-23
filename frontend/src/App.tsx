import { useCallback, useEffect, useState } from 'react'
import { BottomNav, type Screen } from './components/BottomNav'
import { ChevronRight, MenuIcon } from './components/Icons'
import { PowerButton } from './components/PowerButton'
import { DeviceBar } from './components/DeviceBar'
import { SideMenu } from './components/SideMenu'
import { Home } from './screens/Home'
import { History } from './screens/History'
import { Profiles } from './screens/Profiles'
import { Autopilot } from './screens/Autopilot'
import { Schedule } from './screens/Schedule'
import { Holiday } from './screens/Holiday'
import { Dev } from './screens/Dev'
import { useService } from './useService'
import type { ApiClient } from './api/client'
import {
  MAX_TEMPERATURE_C,
  type Holiday as HolidayType,
  type Schedule as ScheduleType,
  type TonightState,
} from './types'

export function App({ client }: { client: ApiClient }) {
  const [screen, setScreen] = useState<Screen>('home')
  // Pushed over the home screen rather than a fourth tab: it is where the Wake
  // card goes, not a section of its own, and it has a way back rather than a way
  // around.
  const [editingSchedule, setEditingSchedule] = useState(false)
  // Pushed over the home screen the same way the schedule is, and for the same
  // reason: it is where the Temperature card goes, not a section of its own.
  const [inProfiles, setInProfiles] = useState(false)
  // Pushed the same way, from the card at the top of the home screen.
  const [inAutopilot, setInAutopilot] = useState(false)
  // Only the Schedule screen needs this, but App is where it is fetched because
  // Home fetches its own copy and the two must not disagree about whether
  // tonight is being skipped.
  const [tonight, setTonight] = useState<TonightState | null>(null)
  const [scheduleError, setScheduleError] = useState<string | null>(null)
  // The side menu, and the one screen that is only reached from it.
  const [menuOpen, setMenuOpen] = useState(false)
  const [inHoliday, setInHoliday] = useState(false)
  // Here rather than in the screens, because three of them draw it: the menu
  // says whether it is on, the home screen says so in a line, and the Alarm
  // card steps over its nights.
  const [holiday, setHoliday] = useState<HolidayType | null>(null)

  const [powerError, setPowerError] = useState<string | null>(null)
  const { info, state, schedule, events, power, health, connected } = useService(client)

  const ready = state !== null && schedule !== null

  useEffect(() => {
    let live = true
    void client
      .getTonight()
      .then((t) => live && setTonight(t))
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [client, schedule])

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

  function leaveSchedule() {
    setEditingSchedule(false)
    setInProfiles(false)
    setInAutopilot(false)
    setInHoliday(false)
    setScheduleError(null)
  }

  const closeMenu = useCallback(() => setMenuOpen(false), [])

  function openHoliday() {
    // From whichever tab the menu was opened on. It is pushed over home like
    // the other screens, so Back always has the same place to go.
    leaveSchedule()
    setScreen('home')
    setInHoliday(true)
    setMenuOpen(false)
  }

  const showingHoliday = ready && screen === 'home' && inHoliday
  const inSchedule = ready && screen === 'home' && editingSchedule
  const showingProfiles = ready && screen === 'home' && inProfiles && !editingSchedule
  const showingAutopilot = ready && screen === 'home' && inAutopilot && !editingSchedule && !inProfiles

  return (
    <div className="app">
      <header className="app__header">
        {inSchedule || showingProfiles || showingAutopilot || showingHoliday ? (
          <button type="button" className="app__back" onClick={leaveSchedule}>
            <ChevronRight className="app__back-icon" />
            Home
          </button>
        ) : (
          <div className="app__lead">
            <button
              type="button"
              className="app__menu"
              aria-label="Menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen(true)}
            >
              <MenuIcon />
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

      {/*
        Under the header rather than on a screen, because a device going down
        matters wherever you happen to be looking.
      */}
      <DeviceBar health={health} connected={connected} />

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
        ) : showingHoliday ? (
          <Holiday client={client} schedule={schedule} holiday={holiday} onChanged={setHoliday} />
        ) : showingAutopilot ? (
          <Autopilot client={client} />
        ) : showingProfiles ? (
          <Profiles client={client} onSaved={() => setInProfiles(false)} />
        ) : editingSchedule ? (
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
            maxC={info?.max_temperature_c ?? MAX_TEMPERATURE_C}
            onOpenSchedule={() => setEditingSchedule(true)}
            onOpenProfiles={() => setInProfiles(true)}
            onOpenAutopilot={() => setInAutopilot(true)}
          />
        )}
      </main>

      <BottomNav
        screen={screen}
        onChange={(next) => {
          leaveSchedule()
          setScreen(next)
        }}
        showDev={info?.fake_transmitter ?? false}
      />

      <SideMenu
        open={menuOpen}
        onClose={closeMenu}
        holiday={holiday}
        onOpenHoliday={openHoliday}
      />
    </div>
  )
}
