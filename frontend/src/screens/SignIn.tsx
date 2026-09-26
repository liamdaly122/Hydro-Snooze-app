import { useState, type FormEvent } from 'react'
import type { ApiClient } from '../api/client'
import type { AuthState } from '../types'

interface Props {
  client: ApiClient
  /** Why signing in would not help from here, when that is the answer. */
  refused: string | null
  onSignedIn: (auth: AuthState) => void
}

/**
 * The password, once per device.
 *
 * Only here because the bed can be reached from outside the house now. See
 * backend/hydrosnooze/access.py for the rules. The thing this screen has to get
 * right is being rare: a phone that signs in stays signed in for half a year
 * from the last time it was used, so this is seen once and then forgotten.
 *
 * The username field is there for the phone's password manager, which will
 * not offer to save a password it cannot file under a name.
 */
export function SignIn({ client, refused, onSignedIn }: Props) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  function submit(e: FormEvent) {
    e.preventDefault()
    if (!password || busy) return
    setBusy(true)
    setError(null)
    void client
      .signIn(password)
      .then(onSignedIn)
      .catch((err: Error) => {
        setError(err.message)
        setPassword('')
      })
      .finally(() => setBusy(false))
  }

  return (
    <div className="app">
      <main className="app__scroll signin">
        <h1 className="signin__title">HydroSnooze</h1>

        {refused ? (
          <section className="card">
            <p className="signin__text">{refused}</p>
          </section>
        ) : (
          <form className="card signin__card" onSubmit={submit}>
            <input
              className="signin__hidden"
              type="text"
              name="username"
              autoComplete="username"
              value="hydrosnooze"
              readOnly
              tabIndex={-1}
              aria-hidden="true"
            />
            <label className="signin__label" htmlFor="signin-password">
              Password
            </label>
            <input
              id="signin-password"
              className="signin__input"
              type="password"
              name="password"
              autoComplete="current-password"
              autoFocus
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            {error && <p className="footnote footnote--error">{error}</p>}
            <button
              type="submit"
              className="pill pill--wide signin__go"
              disabled={busy || !password}
            >
              {busy ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
        )}

        <p className="footnote">
          One password for the house, set on the Pi with scripts/password.py. This phone stays
          signed in for six months from the last time it is used. The bed runs its nights whether
          or not anything is signed in.
        </p>
      </main>
    </div>
  )
}
