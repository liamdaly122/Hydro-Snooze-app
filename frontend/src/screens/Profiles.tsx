import { useEffect, useState } from 'react'
import { Card } from '../components/Card'
import { Plus } from '../components/Icons'
import { formatTemp, tint } from '../domain'
import { STAGE_LABEL } from '../types'
import type { ApiClient } from '../api/client'
import type { Profile } from '../types'

/**
 * Saved nights, by name. "Summer", "Winter", "Guest room".
 *
 * A profile holds the shape of a night and nothing else: the stage temperatures,
 * how long each lasts, and the cooling speed. Not the wake time and not the days
 * of the week, because those belong to the week you are having rather than to the
 * weather, and loading "Summer" should never move an alarm.
 *
 * Exactly one is active, and it is not stored anywhere. The service works it out
 * by comparing each saved night against the schedule, so "active" is always the
 * truth rather than a flag left behind: change a temperature afterwards and
 * nothing is active any more, which is exactly what has happened.
 *
 * That is also why this has no edit button. A profile is a snapshot. To change
 * one, load it, adjust the night on the home screen, and save over the name.
 */
export function Profiles({ client, onSaved }: { client: ApiClient; onSaved?: () => void }) {
  const [profiles, setProfiles] = useState<Profile[] | null>(null)
  const [busy, setBusy] = useState<number | 'new' | null>(null)
  const [naming, setNaming] = useState(false)
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    void client
      .getProfiles()
      .then((list) => live && setProfiles(list))
      .catch((e: Error) => live && setError(e.message))
    return () => {
      live = false
    }
  }, [client])

  async function run<T>(key: number | 'new', work: () => Promise<T>): Promise<T | undefined> {
    setBusy(key)
    setError(null)
    try {
      return await work()
    } catch (e) {
      setError((e as Error).message)
      return undefined
    } finally {
      setBusy(null)
    }
  }

  async function save() {
    const wanted = name.trim()
    if (!wanted) return
    const list = await run('new', () => client.saveProfile(wanted))
    if (!list) return
    setProfiles(list)
    setNaming(false)
    setName('')
  }

  async function activate(profile: Profile) {
    if (profile.active) return
    await run(profile.id, () => client.activateProfile(profile.id))
    const list = await client.getProfiles().catch(() => null)
    if (list) setProfiles(list)
    onSaved?.()
  }

  async function remove(profile: Profile) {
    const list = await run(profile.id, () => client.deleteProfile(profile.id))
    if (list) setProfiles(list)
  }

  return (
    <>
      <Card
        label="Profiles"
        /*
          In the card's own header rather than the app's, because it belongs to
          this list, and because what it saves is the night currently set on the
          home screen.
        */
        action={
          <button
            type="button"
            className="profiles__add"
            onClick={() => {
              setNaming((was) => !was)
              setError(null)
            }}
            aria-label="Save the current night as a profile"
            aria-expanded={naming}
          >
            <Plus />
          </button>
        }
      >
        <p className="footnote">
          A saved night is its temperatures and how long each part lasts. Loading one leaves your
          wake time and your days alone, so a profile is about the weather rather than the week.
        </p>

        {naming && (
          <div className="profiles__new">
            <input
              className="profiles__name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && void save()}
              placeholder="Summer"
              maxLength={40}
              autoFocus
              aria-label="A name for tonight's temperatures"
            />
            <button
              type="button"
              className="profiles__save"
              disabled={!name.trim() || busy === 'new'}
              onClick={() => void save()}
            >
              {busy === 'new' ? 'Saving' : 'Save'}
            </button>
          </div>
        )}

        {error && <p className="status__error">{error}</p>}

        {profiles === null ? (
          <p className="empty">Loading…</p>
        ) : profiles.length === 0 ? (
          <p className="empty">
            No saved nights yet. Tune the temperatures on the home screen, then tap the plus.
          </p>
        ) : (
          <ul className="profiles">
            {profiles.map((profile) => (
              <li key={profile.id} className={`profiles__row${profile.active ? ' is-active' : ''}`}>
                <button
                  type="button"
                  className="profiles__pick"
                  onClick={() => void activate(profile)}
                  disabled={busy === profile.id}
                  aria-pressed={profile.active}
                >
                  <span className="profiles__title">
                    <span className="profiles__label">{profile.name}</span>
                    {profile.active && <span className="profiles__badge">Running</span>}
                  </span>

                  {/*
                    The temperatures themselves, in stage order, coloured the same
                    way they are everywhere else. A name alone would make two
                    profiles indistinguishable at exactly the moment you are
                    choosing between them.
                  */}
                  <span className="profiles__temps">
                    {profile.stages.map((stage) => (
                      <span key={stage.stage} className="profiles__temp">
                        <span className="profiles__stage">{STAGE_LABEL[stage.stage]}</span>
                        <span style={{ color: tint(stage.temp_c) }}>{formatTemp(stage.temp_c)}&deg;</span>
                      </span>
                    ))}
                  </span>
                </button>

                <button
                  type="button"
                  className="profiles__delete"
                  onClick={() => void remove(profile)}
                  disabled={busy === profile.id}
                  aria-label={`Delete ${profile.name}`}
                >
                  Delete
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <p className="footnote">
        Saving under a name that already exists overwrites it. Two profiles called Summer is never
        what anyone meant, and choosing between them in a list is worse than losing the older one.
      </p>
    </>
  )
}
