import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { MockApiClient } from './api/mock'
import './styles/global.css'

/**
 * The one line that swaps the whole app between seed data and the real service.
 *
 *   const client = new HttpApiClient()
 *
 * Everything else talks to the ApiClient interface, not to either implementation,
 * so nothing built against the mock has to be rewritten.
 */
const client = new MockApiClient()

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
