import { Card } from '../components/Card'
import { PowerChart } from '../components/PowerChart'
import { EventLog } from '../components/EventLog'
import type { DeviceEvent, PowerSample } from '../types'

export function History({ power, events }: { power: PowerSample[]; events: DeviceEvent[] }) {
  return (
    <>
      <Card label="Power, 24 hours">
        <PowerChart samples={power} />
      </Card>
      <Card label="Events">
        <EventLog events={events} />
      </Card>
    </>
  )
}
