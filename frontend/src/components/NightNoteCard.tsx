import { useEffect, useState } from 'react'
import type { ApiClient } from '../api/client'
import type { NightNote, NightNotePatch } from '../types'

/**
 * How the night felt, in three taps at most, on the morning it ended.
 *
 * The mat measures everything except this. How waking up felt is the whole of
 * what the Wake part of the night is for, so this is what scores it; how the bed
 * felt sits beside each setting on the scoreboard; and three of the tags leave
 * the night out of the scoreboard altogether, because a drink or a cold or a
 * second person in the bed moves sleep by more than a degree on the pad does.
 * See backend/hydrosnooze/notes.py.
 *
 * Every tap saves. Tapping the chosen answer again clears it, because a wrong
 * tap at seven in the morning should not need a separate undo.
 */
export function NightNoteCard({ client, wakeOn }: { client: ApiClient; wakeOn: string }) {
  const [note, setNote] = useState<NightNote | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    setNote(null)
    setError(null)
    void client
      .getNote(wakeOn)
      .then((n) => live && setNote(n))
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [client, wakeOn])

  if (!note) return null

  function save(patch: NightNotePatch, shown: NightNote) {
    setNote(shown)
    setError(null)
    void client
      .saveNote(wakeOn, patch)
      .then(setNote)
      .catch((e: Error) => setError(e.message))
  }

  const toggleTag = (key: string) => {
    const tags = note.tags.includes(key) ? note.tags.filter((t) => t !== key) : [...note.tags, key]
    const leaving = note.choices.tags.filter((t) => t.leaves_out && tags.includes(t.key))
    save({ tags }, { ...note, tags, left_out: leaving.map((t) => t.label) })
  }

  return (
    <section className="card hr-card note">
      <h3 className="hr-card__title">How was the night?</h3>

      <p className="note__label">Waking up</p>
      <div className="note__row" role="group" aria-label="How waking up felt">
        {note.choices.ratings.map((r) => {
          const on = note.rating === r.value
          return (
            <button
              key={r.value}
              type="button"
              className="note__pick"
              aria-pressed={on}
              onClick={() => {
                const rating = on ? null : r.value
                save({ rating }, { ...note, rating })
              }}
            >
              {r.label}
            </button>
          )
        })}
      </div>

      <p className="note__label">The bed felt</p>
      <div className="note__row" role="group" aria-label="How the bed felt">
        {note.choices.felt.map((f) => {
          const on = note.felt === f.value
          return (
            <button
              key={f.value}
              type="button"
              className="note__pick"
              aria-pressed={on}
              onClick={() => {
                const felt = on ? null : f.value
                save({ felt }, { ...note, felt })
              }}
            >
              {f.label}
            </button>
          )
        })}
      </div>

      <p className="note__label">Anything else</p>
      <div className="note__tags" role="group" aria-label="Tags">
        {note.choices.tags.map((t) => (
          <button
            key={t.key}
            type="button"
            className="note__tag"
            aria-pressed={note.tags.includes(t.key)}
            onClick={() => toggleTag(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {note.left_out.length > 0 ? (
        <p className="footnote">
          Left out of the scoreboard for {note.left_out.join(' and ').toLowerCase()}. A night like
          that moves sleep by more than the bed does, so it would blur the comparison.
        </p>
      ) : (
        <p className="footnote">
          Waking up scores the Wake part on the scoreboard. Alcohol, Ill and Someone else in the bed
          leave a night out of it.
        </p>
      )}
      {error && <p className="footnote footnote--error">{error}</p>}
    </section>
  )
}
