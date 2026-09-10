import { Card } from '../components/Card'
import { BedChart } from '../components/BedChart'
import { PowerChart } from '../components/PowerChart'
import { EventLog } from '../components/EventLog'
import type { DeviceEvent, PowerSample } from '../types'

export function History({ power, events }: { power: PowerSample[]; events: DeviceEvent[] }) {
  return (
    <>
      {/*
        The bed first. The power chart below says what the machine did, which is
        what there was to look at for months; this says what happened to the bed,
        which is the thing the machine is for.
      */}
      <Card label="Bed, 24 hours">
        <BedChart samples={power} />
      </Card>
      <Card label="Power, 24 hours">
        <PowerChart samples={power} />
      </Card>
      <Card label="Events">
        <EventLog events={events} />
      </Card>
    </>
  )
}
