import { useEffect, useRef, useState } from 'react'
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
 *
 * Submit puts it away. Open on every night, three rows of buttons made the
 * Health Report long, so once it is submitted it folds to one line saying what
 * was answered, with Edit to open it again. The answers were saved as they were
 * tapped, so Submit only says the card is finished with; it is remembered by
 * the service, so it stays folded after a reload and on another phone.
 *
 * The saves go one at a time, in the order they were tapped, and only the
 * answer to the newest one is drawn. Sent all at once, the answer to the first
 * tap could land after the third and put the card back to how it was two taps
 * ago: Submit folded it, an earlier answer unfolded it, and it folded again.
 */
export function NightNoteCard({ client, wakeOn }: { client: ApiClient; wakeOn: string }) {
  const [note, setNote] = useState<NightNote | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Opened again with Edit after it was submitted. Not remembered: the next
  // time the report is opened, a submitted note is folded again.
  const [editing, setEditing] = useState(false)
  const queue = useRef<Promise<unknown>>(Promise.resolve())
  const newest = useRef(0)

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

  const answered = note.rating !== null || note.felt !== null || note.tags.length > 0

  if (note.submitted && !editing) {
    const said = [
      note.choices.ratings.find((r) => r.value === note.rating)?.label,
      note.choices.felt.find((f) => f.value === note.felt)?.label.toLowerCase(),
      ...note.choices.tags.filter((t) => note.tags.includes(t.key)).map((t) => t.label),
    ].filter(Boolean)
    return (
      <section className="card note-done">
        <p className="note-done__text">
          <span className="note-done__label">The night</span>{' '}
          {said.length ? said.join(' · ') : 'Nothing noted'}
          {note.left_out.length > 0 && (
            <span className="note-done__aside"> · left out of the scoreboard</span>
          )}
        </p>
        <button type="button" className="note-done__edit" onClick={() => setEditing(true)}>
          Edit
        </button>
      </section>
    )
  }

  function save(patch: NightNotePatch, shown: NightNote) {
    setNote(shown)
    setError(null)
    const mine = ++newest.current
    queue.current = queue.current
      .then(() => client.saveNote(wakeOn, patch))
      .then((saved) => {
        // What the service holds after every tap so far, so the newest answer
        // is the whole truth. An older one would undo taps it has not seen.
        if (mine === newest.current) setNote(saved)
      })
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

      {/* Only once something is answered: an empty Submit would put away a card
          that says nothing, and there would be nothing to fold it down to. */}
      <button
        type="button"
        className="pill pill--wide note__submit"
        disabled={!answered}
        onClick={() => {
          save({ submitted: true }, { ...note, submitted: true })
          setEditing(false)
        }}
      >
        Submit
      </button>
    </section>
  )
}
