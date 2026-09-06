import { useState } from 'react'
import { BottomNav, type Screen } from './components/BottomNav'
import { Home } from './screens/Home'
import { History } from './screens/History'
import { useService } from './useService'
import type { ApiClient } from './api/client'
import { MAX_TEMPERATURE_C } from './types'

export function App({ client }: { client: ApiClient }) {
  const [screen, setScreen] = useState<Screen>('home')
  const { info, state, schedule, events, power } = useService(client)

  const ready = state !== null && schedule !== null

  return (
    <div className="app">
      <header className="app__header">
        <h1 className="app__title">HydroSnooze</h1>
      </header>

      <main className="app__scroll">
        {!ready ? (
          <p className="empty">Connecting…</p>
        ) : screen === 'home' ? (
          <Home
            client={client}
            state={state}
            schedule={schedule}
            maxC={info?.max_temperature_c ?? MAX_TEMPERATURE_C}
          />
        ) : (
          <History power={power} events={events} />
        )}
      </main>

      <BottomNav screen={screen} onChange={setScreen} />
    </div>
  )
}
