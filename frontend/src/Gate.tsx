import { useCallback, useEffect, useState } from 'react'
import { App } from './App'
import { SignIn } from './screens/SignIn'
import { ApiError, SIGNED_OUT_EVENT, type ApiClient } from './api/client'
import type { AuthState } from './types'

/** What an older service, from before there was a password, amounts to. */
const OPEN: AuthState = { required: false, signed_in: true, via: 'home', refused: null }

/**
 * The app, or the sign-in, asked before anything else.
 *
 * Nothing under App is mounted until this has an answer, so a phone that is not
 * signed in never opens the live socket or asks for a single reading. Signed
 * out underneath the app, by a session running out or by "Sign out every
 * device" on another phone, the next refused request says so and this swaps the
 * sign-in back in.
 */
export function Gate({ client }: { client: ApiClient }) {
  const [auth, setAuth] = useState<AuthState | null>(null)
  const [unreachable, setUnreachable] = useState(false)

  const check = useCallback(() => {
    client
      .getAuth()
      .then((a) => {
        setAuth(a)
        setUnreachable(false)
      })
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 404) {
          setAuth(OPEN)
          return
        }
        setUnreachable(true)
      })
  }, [client])

  useEffect(() => check(), [check])

  // Not answering yet: the Pi rebooting, or the phone between networks. Keep
  // asking quietly, the way the live socket does.
  useEffect(() => {
    if (auth !== null) return
    const timer = setInterval(check, 3000)
    return () => clearInterval(timer)
  }, [auth, check])

  useEffect(() => {
    window.addEventListener(SIGNED_OUT_EVENT, check)
    return () => window.removeEventListener(SIGNED_OUT_EVENT, check)
  }, [check])

  if (auth === null) {
    return (
      <div className="app">
        <main className="app__scroll">
          <p className="empty">
            {unreachable ? 'Cannot reach HydroSnooze yet. Still trying.' : 'Connecting…'}
          </p>
        </main>
      </div>
    )
  }
  if (auth.refused || (auth.required && !auth.signed_in)) {
    return <SignIn client={client} refused={auth.refused} onSignedIn={setAuth} />
  }
  return <App client={client} auth={auth} onAuth={setAuth} />
}
