import { useState } from 'react'
import { BottomNav, type Screen } from './components/BottomNav'
import { ChevronRight } from './components/Icons'
import { Home } from './screens/Home'
import { History } from './screens/History'
import { Schedule } from './screens/Schedule'
import { Dev } from './screens/Dev'
import { useService } from './useService'
import type { ApiClient } from './api/client'
import { MAX_TEMPERATURE_C, type Schedule as ScheduleType } from './types'

export function App({ client }: { client: ApiClient }) {
  const [screen, setScreen] = useState<Screen>('home')
  // Pushed over the home screen rather than a fourth tab: it is where the Wake
  // card goes, not a section of its own, and it has a way back rather than a way
  // around.
  const [editingSchedule, setEditingSchedule] = useState(false)
  const [scheduleError, setScheduleError] = useState<string | null>(null)
  const { info, state, schedule, events, power } = useService(client)

  const ready = state !== null && schedule !== null

  function saveSchedule(patch: Partial<ScheduleType>) {
    setScheduleError(null)
    void client.putSchedule(patch).catch((e: Error) => setScheduleError(e.message))
  }

  function leaveSchedule() {
    setEditingSchedule(false)
    setScheduleError(null)
  }

  const inSchedule = ready && screen === 'home' && editingSchedule

  return (
    <div className="app">
      <header className="app__header">
        {inSchedule ? (
          <button type="button" className="app__back" onClick={leaveSchedule}>
            <ChevronRight className="app__back-icon" />
            Home
          </button>
        ) : (
          <h1 className="app__title">HydroSnooze</h1>
        )}
      </header>

      <main className="app__scroll">
        {!ready ? (
          <p className="empty">Connecting…</p>
        ) : screen === 'dev' ? (
          <Dev state={state} realPlug={!(info?.fake_power_monitor ?? true)} />
        ) : screen === 'history' ? (
          <History power={power} events={events} />
        ) : editingSchedule ? (
          <Schedule
            draft={schedule}
            onChange={saveSchedule}
            error={scheduleError}
            onDismissError={() => setScheduleError(null)}
          />
        ) : (
          <Home
            client={client}
            state={state}
            schedule={schedule}
            maxC={info?.max_temperature_c ?? MAX_TEMPERATURE_C}
            onOpenSchedule={() => setEditingSchedule(true)}
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
    </div>
  )
}
