import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { HttpApiClient } from './api/http'
import { MockApiClient } from './api/mock'
import './styles/global.css'

/**
 * The one line that swaps the whole app between the real service and seed data.
 *
 * HttpApiClient is the real one. MockApiClient stays for the Vercel deployment,
 * which has no service behind it and can only ever show invented data, and for
 * looking at the design on a phone without anything else running.
 *
 * Everything else talks to the ApiClient interface rather than to either
 * implementation, so neither one is ever built twice.
 */
const useSeedData = import.meta.env.VITE_SEED_DATA === 'true'
const client = useSeedData ? new MockApiClient() : new HttpApiClient()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App client={client} />
  </StrictMode>,
)

// Cache the shell so the app opens instantly from the Home Screen, including when
// the Pi is slow to answer or briefly unreachable.
if ('serviceWorker' in navigator && import.meta.env.PROD) {
  window.addEventListener('load', () => {
    void navigator.serviceWorker.register('/sw.js')
  })
}
