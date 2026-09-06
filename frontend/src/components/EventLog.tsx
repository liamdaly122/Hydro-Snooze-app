import type { DeviceEvent } from '../types'

/**
 * Every command sent, every state change, every failure. This is the only way to
 * work out what went wrong at 3am, so it is plain text in time order and nothing
 * is summarised away.
 */
export function EventLog({ events }: { events: DeviceEvent[] }) {
  if (events.length === 0) {
    return <p className="empty">Nothing logged yet.</p>
  }
  return (
    <div className="log">
      {events.map((event) => (
        <div className="log__row" key={event.id}>
          <span className="log__time">
            {new Date(event.at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}
          </span>
          <span className={`log__msg log__msg--${event.level}`}>{event.message}</span>
        </div>
      ))}
    </div>
  )
}
