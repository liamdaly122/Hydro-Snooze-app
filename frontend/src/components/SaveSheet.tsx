import type { WriteProgress } from '../types'

export type SaveStage = 'confirm' | 'running' | 'done' | 'failed'

/**
 * Writing the schedule is the risky operation: around 90 infrared presses walking
 * the unit through its setup wizard, and nothing in software can read back what
 * landed. So it is never automated, always confirmed, and always followed by an
 * instruction to go and check it by hand.
 */
export function SaveSheet({
  stage,
  progress,
  error,
  onConfirm,
  onClose,
}: {
  stage: SaveStage
  progress: WriteProgress | null
  error: string | null
  onConfirm: () => void
  onClose: () => void
}) {
  const pct = progress ? Math.round((progress.presses_sent / progress.presses_total) * 100) : 0

  return (
    <div className="scrim" role="dialog" aria-modal="true" aria-label="Write schedule to the unit">
      <div className="sheet">
        {stage === 'confirm' && (
          <>
            <h3 className="sheet__title">Write this to the unit?</h3>
            <p className="sheet__body">
              This takes about 45 seconds and sends a lot of infrared. Watch the unit to check it
              steps through three phases.
            </p>
            <div className="sheet__actions">
              <button type="button" className="sheet__btn" onClick={onClose}>
                Cancel
              </button>
              <button type="button" className="sheet__btn sheet__btn--primary" onClick={onConfirm}>
                Write
              </button>
            </div>
          </>
        )}

        {stage === 'running' && (
          <>
            <h3 className="sheet__title">Writing</h3>
            <p className="sheet__body">Watch the unit. It should step through three phases.</p>
            <div className="progress">
              <div className="progress__track">
                <div className="progress__fill" style={{ width: `${pct}%` }} />
              </div>
              <div className="progress__caption">
                <span>{progress?.message ?? 'Starting'}</span>
                <span>
                  {progress?.presses_sent ?? 0}/{progress?.presses_total ?? 0} presses
                </span>
              </div>
            </div>
          </>
        )}

        {stage === 'done' && (
          <>
            <h3 className="sheet__title">Schedule saved</h3>
            <p className="sheet__body">
              Nothing can confirm this from here. To check it, press the sleep schedule button on
              the remote and watch the display step through the three phases.
            </p>
            <div className="sheet__actions">
              <button type="button" className="sheet__btn sheet__btn--primary" onClick={onClose}>
                Done
              </button>
            </div>
          </>
        )}

        {stage === 'failed' && (
          <>
            <h3 className="sheet__title">Write failed</h3>
            <p className="sheet__body">
              {error ?? 'The sequence did not finish.'} The unit may be holding its previous
              temperatures, which is the safe failure, but nothing here can confirm that. Check it
              on the remote before trusting it tonight.
            </p>
            <div className="sheet__actions">
              <button type="button" className="sheet__btn sheet__btn--primary" onClick={onClose}>
                Close
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
