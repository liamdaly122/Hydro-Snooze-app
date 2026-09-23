import { useEffect, useRef, useState } from 'react'
import { createPod, type Pod, type PodLook } from '../pod/scene'
import type { DeviceState } from '../types'

/**
 * What the top of the bed should be showing, from what the plug measured.
 *
 * The plug's reading and nothing else decides heating or cooling, because it is
 * the one thing here that is observed. The mode the app last asked for only
 * breaks the tie when the unit is idling at temperature, where the draw says
 * "holding" and not which way.
 */
export function lookFor(state: DeviceState): PodLook {
  if (state.power === 'off' || state.inferred_activity === 'off') return 'off'
  if (state.inferred_activity === 'cooling') return 'cooling'
  if (state.inferred_activity === 'heating') return 'heating'
  if (state.inferred_activity === 'idle') {
    return state.assumed_mode === 'warming' ? 'holding-warm' : 'holding-cool'
  }
  return 'unknown'
}

const SAID: Record<PodLook, string> = {
  cooling: 'Cooling',
  heating: 'Heating',
  'holding-cool': 'At temperature',
  'holding-warm': 'At temperature',
  off: 'Off',
  unknown: 'Unit state unknown',
}

/** How far a finger can turn it, either way. */
const MAX_TURN = 0.7

/**
 * The bed, in three dimensions, above the temperature.
 *
 * Purely to look at, and it is still not allowed to say anything untrue: the
 * glow follows the plug, and the one line under it is the plug's word and the
 * hose probes' reading, both measured.
 *
 * Loaded on its own, after the rest of the home screen, so the controls never
 * wait for a 3D engine. If the phone cannot give it a WebGL context it steps
 * aside and leaves the line on its own.
 */
export default function PodHero({ state }: { state: DeviceState }) {
  const holder = useRef<HTMLDivElement>(null)
  const pod = useRef<Pod | null>(null)
  const drag = useRef<{ x: number; from: number } | null>(null)
  const turn = useRef(0)
  const [failed, setFailed] = useState(false)
  // Bumped when the phone takes the GL context away and gives it back, which
  // iOS does to a backgrounded page. Remounting is simpler than restoring
  // every texture by hand.
  const [generation, setGeneration] = useState(0)

  const look = lookFor(state)
  const lookRef = useRef(look)
  lookRef.current = look

  useEffect(() => {
    const box = holder.current
    if (!box) return
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches

    // A new canvas every time, made here rather than rendered by React. Letting
    // go of the scene also lets go of its GL context, on purpose, and a canvas
    // whose context has been let go of can never have another one. React's
    // development mode mounts everything twice, and reusing the element came
    // back as a blank space where the bed should be.
    const element = document.createElement('canvas')
    element.className = 'pod__canvas'
    element.setAttribute('aria-hidden', 'true')
    box.prepend(element)

    let created: Pod
    try {
      created = createPod(element, { look: lookRef.current, still })
    } catch {
      element.remove()
      setFailed(true)
      return
    }
    pod.current = created

    const fit = () => {
      const box = element.getBoundingClientRect()
      created.setSize(box.width, box.height)
    }
    fit()
    const resized = new ResizeObserver(fit)
    resized.observe(element)

    // Only drawn while it can be seen. A home screen scrolled past it, or an
    // app in the background, costs nothing.
    let seen = true
    const sync = () => {
      if (seen && document.visibilityState === 'visible') created.play()
      else created.pause()
    }
    const watched = new IntersectionObserver(([entry]) => {
      seen = entry?.isIntersecting ?? true
      sync()
    })
    watched.observe(element)
    document.addEventListener('visibilitychange', sync)
    sync()

    const lost = (event: Event) => {
      event.preventDefault()
      created.pause()
    }
    const restored = () => setGeneration((g) => g + 1)
    element.addEventListener('webglcontextlost', lost)
    element.addEventListener('webglcontextrestored', restored)

    return () => {
      resized.disconnect()
      watched.disconnect()
      document.removeEventListener('visibilitychange', sync)
      element.removeEventListener('webglcontextlost', lost)
      element.removeEventListener('webglcontextrestored', restored)
      created.dispose()
      element.remove()
      pod.current = null
    }
  }, [generation])

  useEffect(() => {
    pod.current?.setLook(look)
  }, [look])

  const bed = state.observed_return_c ?? state.observed_flow_c
  const said = SAID[look] + (bed === null ? '' : ` · bed ${bed.toFixed(1)}°`)

  function letGo() {
    // Let go, and it drifts back to face you.
    drag.current = null
    turn.current = 0
    pod.current?.setTurn(0)
  }

  return (
    <section className="pod" aria-label={`The bed. ${said}`}>
      {!failed && (
        <div
          ref={holder}
          className="pod__stage"
          // Sideways turns the bed; up and down still scrolls the page.
          onPointerDown={(e) => {
            drag.current = { x: e.clientX, from: turn.current }
            e.currentTarget.setPointerCapture(e.pointerId)
          }}
          onPointerMove={(e) => {
            if (!drag.current) return
            const next = drag.current.from + (e.clientX - drag.current.x) * 0.008
            turn.current = Math.max(-MAX_TURN, Math.min(MAX_TURN, next))
            pod.current?.setTurn(turn.current)
          }}
          onPointerUp={letGo}
          onPointerCancel={letGo}
        />
      )}
      <p className="pod__caption">
        <span className={`pod__dot pod__dot--${look}`} />
        {said}
      </p>
    </section>
  )
}
