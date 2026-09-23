/**
 * The bed, in three dimensions, lit like a product shot.
 *
 * Everything here is Three.js and nothing here is React, so the component that
 * owns the canvas stays small and this can be looked at on its own. It is
 * loaded lazily, on the home screen only, so the rest of the app never waits
 * for it.
 *
 * What makes it read as a real object rather than a diagram is almost all
 * light. A black mattress on a black page is invisible until something outlines
 * it, so there are two cool rim lights behind it, a warm key light from the
 * front left that casts soft shadows, a dim fill, and a studio environment for
 * the reflections. The glowing top then lights the pillows from underneath,
 * which is the detail that ties the two together.
 *
 * The top is the one part that is not a stock material. It is the standard
 * physical material with a glow added to its emission, so it still takes the
 * studio reflections and the pillows' shadows like everything else, and the
 * glow is where the state shows: violet and flowing towards the pillows while
 * the unit is cooling, orange and flowing the other way while it is heating,
 * calm while it holds, dark when it is off.
 */

import * as THREE from 'three'
import { RoundedBoxGeometry } from 'three/examples/jsm/geometries/RoundedBoxGeometry.js'
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js'
import { RectAreaLightUniformsLib } from 'three/examples/jsm/lights/RectAreaLightUniformsLib.js'
import { mergeVertices } from 'three/examples/jsm/utils/BufferGeometryUtils.js'
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js'
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js'
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js'
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js'

/** What the top is showing. Worked out from the plug by `lookFor`. */
export type PodLook = 'cooling' | 'heating' | 'holding-cool' | 'holding-warm' | 'off' | 'unknown'

interface LookSpec {
  /** The glow's three tones: the body of it, the middle, and the highlights. */
  deep: string
  mid: string
  bright: string
  /** How strongly the top glows, 0 to 1. */
  glow: number
  /** How fast the light flows, and which way. Positive is towards the pillows. */
  flow: number
  /** How much the surface shimmers on top of the flow. */
  shimmer: number
}

const COOL = { deep: '#1a0f66', mid: '#5236e8', bright: '#b3a6ff' }
const WARM = { deep: '#5a1206', mid: '#e44d1c', bright: '#ffc08a' }

const LOOKS: Record<PodLook, LookSpec> = {
  // Cooling draws the cold water up the bed from the foot, so the light flows
  // towards the pillows. Heating runs the other way, and a little quicker.
  cooling: { ...COOL, glow: 1, flow: 1, shimmer: 1 },
  heating: { ...WARM, glow: 1, flow: -1.2, shimmer: 1.1 },
  // At temperature: the same colour, calmer, because nothing is moving.
  'holding-cool': { ...COOL, glow: 0.55, flow: 0.25, shimmer: 0.35 },
  'holding-warm': { ...WARM, glow: 0.55, flow: -0.25, shimmer: 0.35 },
  off: { deep: '#000000', mid: '#000000', bright: '#000000', glow: 0, flow: 0, shimmer: 0 },
  // Not knowing is not the same as off. A dim, colourless glow says the bed
  // is there and nothing has said what it is doing.
  unknown: { deep: '#0c0b16', mid: '#34324a', bright: '#6d6a8c', glow: 0.3, flow: 0.12, shimmer: 0.2 },
}

// --- The bed's measurements, in metres --------------------------------------
//
// A king, near enough square from the front, which is what the reference
// renders show. The cover is the thin layer on top that carries the water.

const W = 1.8
const L = 2.0
const BODY_H = 0.3
const COVER_H = 0.06
const TOP = BODY_H + COVER_H

/** How often to draw while it is moving: thirty a second. */
const FRAME_MS = 1000 / 30

const PILLOW = { w: 0.8, d: 0.44, h: 0.17 }
const PILLOW_Z = -L / 2 + 0.34
const PILLOW_X = 0.43

/**
 * Every number that sets how it looks, in one place, because they only make
 * sense against each other. Arrived at by rendering, not by arithmetic.
 *
 * In development only, `?tune={"key":30}` on the URL overrides any of them, so a
 * change can be judged side by side without editing this file. The production
 * build strips that out.
 */
const TUNE = {
  exposure: 1.05,
  environment: 0.2,
  key: 30,
  rim: 45,
  fill: 16,
  underglow: 1.3,
  bloomStrength: 0.4,
  bloomRadius: 0.2,
  bloomThreshold: 0.9,
  pillow: '#c2c2cb',
  pillowSheen: 0.35,
  glowGain: 1,
  keyX: -3.8,
  keyY: 3.2,
  rimY: 0.9,
  fov: 36,
  elevation: 26,
  fit: 1.18,
  wave: 0.6,
  caustics: 0.4,
}

if (import.meta.env.DEV) {
  const raw = new URLSearchParams(location.search).get('tune')
  if (raw) Object.assign(TUNE, JSON.parse(raw))
}

export interface Pod {
  setLook(look: PodLook): void
  /** CSS pixels. */
  setSize(width: number, height: number): void
  /** An extra turn from a finger, in radians. Zero lets it drift back. */
  setTurn(radians: number): void
  play(): void
  pause(): void
  dispose(): void
}

export function createPod(
  canvas: HTMLCanvasElement,
  options: { look: PodLook; still: boolean },
): Pod {
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: false,
    alpha: false,
    powerPreference: 'high-performance',
  })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  renderer.shadowMap.enabled = true
  renderer.shadowMap.type = THREE.PCFSoftShadowMap
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = TUNE.exposure
  renderer.outputColorSpace = THREE.SRGBColorSpace
  RectAreaLightUniformsLib.init()

  const scene = new THREE.Scene()
  scene.background = new THREE.Color('#000000')

  // A soft photographic studio for everything to reflect. Turned well down:
  // it is there for the sheen on the fabric and the edges of the cover, not to
  // light the scene, which the lights below do.
  const pmrem = new THREE.PMREMGenerator(renderer)
  const room = new RoomEnvironment()
  const environment = pmrem.fromScene(room, 0.04).texture
  room.dispose()
  scene.environment = environment
  scene.environmentIntensity = TUNE.environment

  const camera = new THREE.PerspectiveCamera(TUNE.fov, 1, 0.1, 40)
  const aim = new THREE.Vector3(0, 0.16, -0.08)

  const bed = new THREE.Group()
  scene.add(bed)

  // --- The mattress --------------------------------------------------------

  const bodyMaterial = new THREE.MeshPhysicalMaterial({
    color: '#0c0c10',
    roughness: 0.74,
    sheen: 1,
    sheenColor: new THREE.Color('#4b4b62'),
    sheenRoughness: 0.5,
  })
  const body = new THREE.Mesh(new RoundedBoxGeometry(W, BODY_H, L, 6, 0.04), bodyMaterial)
  body.position.y = BODY_H / 2
  body.castShadow = true
  body.receiveShadow = true
  bed.add(body)

  // The glow's own values, eased towards whatever the state asks for.
  const glow = {
    deep: new THREE.Color(LOOKS[options.look].deep),
    mid: new THREE.Color(LOOKS[options.look].mid),
    bright: new THREE.Color(LOOKS[options.look].bright),
    glow: LOOKS[options.look].glow,
    flow: LOOKS[options.look].flow,
    shimmer: LOOKS[options.look].shimmer,
  }
  let target = LOOKS[options.look]
  // Parsed once per change of state rather than on every frame.
  const aim3 = {
    deep: new THREE.Color(target.deep),
    mid: new THREE.Color(target.mid),
    bright: new THREE.Color(target.bright),
  }

  const uniforms = {
    uTime: { value: 0 },
    uPhase: { value: 0 },
    uGlow: { value: glow.glow },
    uGain: { value: TUNE.glowGain },
    uWave: { value: TUNE.wave },
    uCaustics: { value: TUNE.caustics },
    uShimmer: { value: glow.shimmer },
    uDeep: { value: glow.deep },
    uMid: { value: glow.mid },
    uBright: { value: glow.bright },
    uHalf: { value: new THREE.Vector2(W / 2, L / 2) },
    uCoverH: { value: COVER_H },
    uPillowA: { value: new THREE.Vector4(-PILLOW_X, PILLOW_Z, PILLOW.w / 2, PILLOW.d / 2) },
    uPillowB: { value: new THREE.Vector4(PILLOW_X, PILLOW_Z, PILLOW.w / 2, PILLOW.d / 2) },
  }

  const coverMaterial = new THREE.MeshPhysicalMaterial({
    color: '#0b0a12',
    roughness: 0.38,
    clearcoat: 0.35,
    clearcoatRoughness: 0.3,
    sheen: 0.5,
    sheenColor: new THREE.Color('#5a5680'),
    sheenRoughness: 0.4,
  })
  coverMaterial.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms)
    shader.vertexShader = shader.vertexShader
      .replace(
        '#include <common>',
        '#include <common>\nvarying vec3 vCoverPos;\nvarying vec3 vCoverNormal;',
      )
      .replace(
        '#include <begin_vertex>',
        '#include <begin_vertex>\nvCoverPos = position;\nvCoverNormal = normal;',
      )
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <common>', `#include <common>\n${GLOW_DECLARATIONS}`)
      .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>\n${GLOW_FRAGMENT}`)
  }
  const cover = new THREE.Mesh(
    new RoundedBoxGeometry(W - 0.014, COVER_H, L - 0.014, 6, 0.028),
    coverMaterial,
  )
  cover.position.y = BODY_H + COVER_H / 2 - 0.002
  cover.castShadow = true
  cover.receiveShadow = true
  bed.add(cover)

  // --- The pillows ----------------------------------------------------------

  const pillowGeometry = makePillow(PILLOW.w, PILLOW.d, PILLOW.h)
  // A soft, broad sheen rather than a bright narrow one. The rims graze the far
  // edge of each pillow, and a tighter sheen turned the sliver of seam they
  // catch into a pinpoint that the bloom spread into a smudge above the bed.
  const pillowMaterial = new THREE.MeshPhysicalMaterial({
    color: TUNE.pillow,
    roughness: 0.95,
    sheen: TUNE.pillowSheen,
    sheenColor: new THREE.Color('#ffffff'),
    sheenRoughness: 0.8,
  })
  for (const [x, turn, lean] of [
    [-PILLOW_X, 0.035, 0.05],
    [PILLOW_X, -0.03, 0.04],
  ] as const) {
    const pillow = new THREE.Mesh(pillowGeometry, pillowMaterial)
    // Resting on the cover, sunk into it a centimetre.
    pillow.position.set(x, TOP + PILLOW.h * 0.5 * 0.5 - 0.012, PILLOW_Z)
    pillow.rotation.set(lean, turn, 0)
    pillow.castShadow = true
    pillow.receiveShadow = true
    bed.add(pillow)
  }

  // --- The label ------------------------------------------------------------

  const labelTexture = new THREE.CanvasTexture(drawLabel())
  labelTexture.colorSpace = THREE.SRGBColorSpace
  labelTexture.anisotropy = 4
  const label = new THREE.Mesh(
    new THREE.PlaneGeometry(0.2, 0.075),
    new THREE.MeshStandardMaterial({
      map: labelTexture,
      transparent: true,
      roughness: 0.5,
      emissive: new THREE.Color('#ffffff'),
      emissiveMap: labelTexture,
      emissiveIntensity: 0.12,
    }),
  )
  label.position.set(-W / 2 + 0.17, BODY_H * 0.36, L / 2 + 0.002)
  bed.add(label)

  // --- Under the bed --------------------------------------------------------
  //
  // No floor. A lit one caught the rim lights and became a grey wall behind
  // the bed, and an unlit one is black on black. What is left is the glow
  // spilling out around the base, drawn straight on rather than lit.

  const spillUniforms = { uColor: { value: glow.mid }, uGlow: { value: glow.glow }, uHalf: uniforms.uHalf }
  const spill = new THREE.Mesh(
    new THREE.PlaneGeometry(W + 3, L + 3),
    new THREE.ShaderMaterial({
      uniforms: spillUniforms,
      vertexShader: SPILL_VERTEX,
      fragmentShader: SPILL_FRAGMENT,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    }),
  )
  spill.rotation.x = -Math.PI / 2
  spill.position.y = 0.003
  bed.add(spill)

  // --- The lights -----------------------------------------------------------

  // Key: warm, high and to the front left, and the only one that casts shadows.
  const key = new THREE.SpotLight('#fff3e6', TUNE.key, 0, 0.5, 0.85, 2)
  key.position.set(TUNE.keyX, TUNE.keyY, 2.8)
  key.target.position.set(0, 0.2, -0.1)
  key.castShadow = true
  key.shadow.mapSize.set(1024, 1024)
  key.shadow.bias = -0.0003
  key.shadow.normalBias = 0.02
  key.shadow.camera.near = 2
  key.shadow.camera.far = 10
  scene.add(key, key.target)

  // Rims: cool, behind and to either side. These are what outline a black bed
  // against a black page, along the top edges and down the corners.
  for (const side of [-1, 1]) {
    const rim = new THREE.SpotLight('#d6dcff', TUNE.rim, 0, 0.45, 0.8, 2)
    rim.position.set(side * 3.4, TUNE.rimY, -3.8)
    rim.target.position.set(side * 0.2, 0.25, 0.4)
    scene.add(rim, rim.target)
  }

  // Fill: dim and from the other side, so the shadow side is dark rather than
  // missing.
  const fill = new THREE.SpotLight('#e6ebff', TUNE.fill, 0, 0.7, 1, 2)
  fill.position.set(3.2, 2.0, 3.6)
  fill.target.position.set(0, 0.2, 0)
  scene.add(fill, fill.target)

  // The glowing top, as a light. It is what tints the undersides of the
  // pillows, and without it they sat on the glow like cut-outs.
  const underglow = new THREE.RectAreaLight(glow.mid, 0, W * 0.92, L * 0.92)
  underglow.position.set(0, TOP + 0.005, 0)
  underglow.lookAt(0, 10, 0)
  bed.add(underglow)

  // --- Post -----------------------------------------------------------------

  const target4x = new THREE.WebGLRenderTarget(1, 1, {
    type: THREE.HalfFloatType,
    samples: 4,
  })
  const composer = new EffectComposer(renderer, target4x)
  composer.addPass(new RenderPass(scene, camera))
  const bloom = new UnrealBloomPass(
    new THREE.Vector2(1, 1),
    TUNE.bloomStrength,
    TUNE.bloomRadius,
    TUNE.bloomThreshold,
  )
  composer.addPass(bloom)
  composer.addPass(new OutputPass())

  // --- Framing ----------------------------------------------------------------

  /** Back the camera off until the bed fits both ways, whatever the shape. */
  function frame(aspect: number) {
    camera.aspect = aspect
    const elevation = THREE.MathUtils.degToRad(TUNE.elevation)
    const halfV = THREE.MathUtils.degToRad(camera.fov / 2)
    const halfH = Math.atan(Math.tan(halfV) * aspect)
    const across = (W * 0.5 * TUNE.fit) / Math.tan(halfH)
    const up = 1.02 / Math.tan(halfV)
    const distance = Math.max(across, up)
    camera.position.set(0, aim.y + Math.sin(elevation) * distance, aim.z + Math.cos(elevation) * distance)
    camera.lookAt(aim)
    camera.updateProjectionMatrix()
  }

  // --- Running it ------------------------------------------------------------

  let raf = 0
  let playing = false
  let last = 0
  let time = 0
  let phase = 0
  let turn = 0
  let turnTarget = 0

  function step(dt: number) {
    if (!options.still) {
      time += dt
    }
    // Eased, so a change of state is a change of light rather than a cut. A
    // time constant of about half a second.
    const k = options.still ? 1 : 1 - Math.exp(-dt * 2.2)
    glow.deep.lerp(aim3.deep, k)
    glow.mid.lerp(aim3.mid, k)
    glow.bright.lerp(aim3.bright, k)
    glow.glow += (target.glow - glow.glow) * k
    glow.flow += (target.flow - glow.flow) * k
    glow.shimmer += (target.shimmer - glow.shimmer) * k
    if (!options.still) phase += dt * glow.flow

    // A slow breath in the glow, so a bed that is holding still looks alive.
    const breath = options.still ? 1 : 0.94 + 0.06 * Math.sin(time * 0.9)
    uniforms.uTime.value = time
    uniforms.uPhase.value = phase
    uniforms.uGlow.value = glow.glow * breath
    uniforms.uShimmer.value = glow.shimmer
    spillUniforms.uGlow.value = glow.glow * breath
    underglow.color.copy(glow.mid).lerp(glow.bright, 0.35)
    underglow.intensity = TUNE.underglow * glow.glow * breath

    // Square on to the screen, so its front edge runs parallel to the card
    // below. It used to sway a few degrees either way on its own, and caught
    // mid-swing it just looked crooked. A finger can still turn it, and it
    // springs back to square once it lets go.
    const kt = options.still ? 1 : 1 - Math.exp(-dt * 6)
    turn += (turnTarget - turn) * kt
    bed.rotation.y = turn
  }

  function render(now: number) {
    if (playing) raf = requestAnimationFrame(render)
    // Thirty frames a second, not sixty. Everything here moves slowly, and this
    // is a phone that may be left open on a bedside table all evening.
    if (last && now - last < FRAME_MS - 2) return
    const dt = last ? Math.min(0.1, (now - last) / 1000) : 0
    last = now
    step(dt)
    composer.render()
  }

  function renderOnce() {
    step(0)
    composer.render()
  }

  frame(1)

  // Development only: stop the clock and step it by hand, so a recording can
  // show the motion at its real speed on a machine that renders slowly. The
  // production build strips this out.
  if (import.meta.env.DEV) {
    Object.assign(window, {
      __pod: {
        advance(seconds: number) {
          playing = false
          cancelAnimationFrame(raf)
          step(seconds)
          composer.render()
        },
        look(look: PodLook) {
          target = LOOKS[look]
          aim3.deep.set(target.deep)
          aim3.mid.set(target.mid)
          aim3.bright.set(target.bright)
        },
      },
    })
  }

  return {
    setLook(look) {
      target = LOOKS[look]
      aim3.deep.set(target.deep)
      aim3.mid.set(target.mid)
      aim3.bright.set(target.bright)
      if (!playing) renderOnce()
    },
    setSize(width, height) {
      if (width < 1 || height < 1) return
      const ratio = Math.min(window.devicePixelRatio, 2)
      renderer.setPixelRatio(ratio)
      renderer.setSize(width, height, false)
      composer.setPixelRatio(ratio)
      composer.setSize(width, height)
      bloom.resolution.set(width * ratio, height * ratio)
      frame(width / height)
      if (!playing) renderOnce()
    },
    setTurn(radians) {
      turnTarget = radians
      if (!playing) renderOnce()
    },
    play() {
      // Nothing moves when motion is reduced, so there is nothing to run: one
      // frame, and another whenever something changes.
      if (options.still) {
        renderOnce()
        return
      }
      if (playing) return
      playing = true
      last = 0
      raf = requestAnimationFrame(render)
    },
    pause() {
      playing = false
      cancelAnimationFrame(raf)
    },
    dispose() {
      playing = false
      cancelAnimationFrame(raf)
      scene.traverse((object) => {
        if (object instanceof THREE.Mesh) {
          object.geometry.dispose()
          const materials = Array.isArray(object.material) ? object.material : [object.material]
          for (const material of materials) material.dispose()
        }
      })
      labelTexture.dispose()
      environment.dispose()
      pmrem.dispose()
      composer.dispose()
      target4x.dispose()
      renderer.dispose()
      // Phones allow only a handful of live contexts. Hand this one back now
      // rather than whenever the garbage collector gets round to it.
      renderer.forceContextLoss()
    },
  }
}

/**
 * A pillow: a rounded rectangle from above, a lens from the side.
 *
 * The thickness depends only on how far a point is from the middle, so the top
 * is one smooth dome. It falls away steeply at the edge, which is the pinched
 * seam a stuffed pillow has, and it is flatter underneath, where it rests on
 * something. The corners thin out because the stuffing does not reach them.
 *
 * It was a superellipsoid first, and that shape comes to a point at the top: the
 * pillows had a ridge along them like a tent.
 */
function makePillow(w: number, d: number, h: number): THREE.BufferGeometry {
  let geometry: THREE.BufferGeometry = new THREE.SphereGeometry(1, 128, 80)
  const position = geometry.attributes.position as THREE.BufferAttribute
  const v = new THREE.Vector3()
  // The outline is a superellipse: a rectangle with soft corners. Reached by
  // pushing the circle out along each direction to meet it, which stays smooth
  // everywhere. Raising cos and sin to a power instead, the usual way, has an
  // infinite slope along both axes, and it showed as creases running out from
  // the middle of each pillow.
  const n = 4.5

  for (let i = 0; i < position.count; i++) {
    v.fromBufferAttribute(position, i)
    const lat = Math.asin(THREE.MathUtils.clamp(v.y, -1, 1))
    const lon = Math.atan2(v.z, v.x)
    // 0 in the middle of the top or bottom, 1 at the seam.
    const s = Math.cos(lat)
    const c = Math.cos(lon)
    const sn = Math.sin(lon)
    const reach = Math.pow(Math.pow(Math.abs(c), n) + Math.pow(Math.abs(sn), n), -1 / n)
    // Where this point is across the pillow, -1 to 1 each way.
    const px = s * reach * c
    const pz = s * reach * sn
    let x = (w / 2) * px
    let z = (d / 2) * pz

    // A dome that is smooth across the middle and steep into the seam.
    let t = (h / 2) * Math.pow(Math.max(0, 1 - s * s), 0.55)
    const corner = Math.pow(Math.abs(px * pz), 1.4)
    t *= 1 - 0.5 * corner
    // A little lumpiness, so it is stuffed rather than moulded. From where the
    // point is, never which way it faces: at the very top every direction meets
    // at one point, and asking each of them for its own height made a spike.
    t *= 1 + 0.04 * Math.sin(px * 4.7 + 1.1) * Math.sin(pz * 3.9 + 0.4)
    let y = lat >= 0 ? t : -t * 0.5
    // The seam droops very slightly at the corners.
    y -= corner * h * 0.05
    x *= 1 - 0.02 * corner
    z *= 1 - 0.02 * corner

    position.setXYZ(i, x, y, z)
  }

  // The sphere's own seam and poles are duplicated vertices. Merged before the
  // normals are worked out, or the pinched edge shows a crease where the
  // sphere was stitched.
  geometry.deleteAttribute('normal')
  geometry.deleteAttribute('uv')
  geometry = mergeVertices(geometry)
  geometry.computeVertexNormals()
  return geometry
}

/** "HYDRO / SNOOZE", white on nothing, the way the label on the reference sits. */
function drawLabel(): HTMLCanvasElement {
  const canvas = document.createElement('canvas')
  canvas.width = 512
  canvas.height = 192
  const ctx = canvas.getContext('2d')!
  ctx.fillStyle = '#ffffff'
  ctx.font = '700 64px -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif'
  ctx.textBaseline = 'alphabetic'
  // Spaced by hand. Canvas letterSpacing is not everywhere yet.
  const spaced = (text: string, x: number, y: number) => {
    let at = x
    for (const ch of text) {
      ctx.fillText(ch, at, y)
      at += ctx.measureText(ch).width + 10
    }
  }
  spaced('HYDRO', 8, 80)
  spaced('SNOOZE', 8, 164)
  return canvas
}

// --- Shaders --------------------------------------------------------------------

const GLOW_DECLARATIONS = /* glsl */ `
uniform float uTime;
uniform float uPhase;
uniform float uGlow;
uniform float uGain;
uniform float uWave;
uniform float uCaustics;
uniform float uShimmer;
uniform vec3 uDeep;
uniform vec3 uMid;
uniform vec3 uBright;
uniform vec2 uHalf;
uniform float uCoverH;
uniform vec4 uPillowA;
uniform vec4 uPillowB;
varying vec3 vCoverPos;
varying vec3 vCoverNormal;

float podHash(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

float podNoise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(podHash(i), podHash(i + vec2(1.0, 0.0)), u.x),
    mix(podHash(i + vec2(0.0, 1.0)), podHash(i + vec2(1.0, 1.0)), u.x),
    u.y
  );
}

float podFbm(vec2 p) {
  float sum = 0.0;
  float amp = 0.5;
  for (int i = 0; i < 4; i++) {
    sum += amp * podNoise(p);
    p = p * 2.03 + vec2(1.7, 9.2);
    amp *= 0.5;
  }
  return sum;
}

// Signed distance to a rounded rectangle, for the pillows' shadow on the glow.
float podRoundRect(vec2 p, vec2 halfSize, float r) {
  vec2 q = abs(p) - halfSize + r;
  return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}

// How much of the glow a pillow stops: all of it underneath, a contact shadow
// just past its edge where it presses into the cover, and a wider, fainter one
// beyond that. Wider than it would be in a room, because from this angle depth
// is foreshortened, and a 7cm shadow in front of a pillow is three pixels high.
float podUnderPillow(vec2 p, vec4 pillow) {
  float d = podRoundRect(p - pillow.xy, pillow.zw - 0.03, 0.12);
  float contact = 1.0 - smoothstep(-0.02, 0.13, d);
  float soft = 1.0 - smoothstep(0.0, 0.38, d);
  return max(contact, soft * 0.5);
}
`

const GLOW_FRAGMENT = /* glsl */ `
{
  vec2 plan = vCoverPos.xz;
  // How far in from the nearest edge, in metres, and how much this fragment
  // faces up. The rounded edges blend between top and side on their own.
  vec2 inside = uHalf - abs(plan);
  float edge = max(min(inside.x, inside.y), 0.0);
  float up = smoothstep(0.25, 0.92, vCoverNormal.y);

  // The flow: noise warped by noise, carried along the length of the bed. The
  // phase is integrated on the CPU, so changing speed never jumps the pattern.
  vec2 q = vec2(plan.x * 2.4, plan.y * 1.7 + uPhase * 0.32);
  vec2 warp = vec2(podFbm(q + vec2(0.0, uTime * 0.05)), podFbm(q + vec2(5.2, 1.3)));
  float body = podFbm(q * 1.2 + warp * 1.6);

  // Caustics: thin bright filaments where the warped field crosses a contour,
  // like light through moving water. The Pod is water, so this is the right
  // sort of shimmer.
  float field = podFbm(q * 1.8 + warp * 2.4 - vec2(0.0, uPhase * 0.18));
  float filaments = pow(1.0 - abs(sin(field * 17.0 + uTime * 0.35)), 30.0);

  // The water channels, running the length of the bed, faint.
  float channels = 0.5 + 0.5 * cos(plan.x * 6.2831 / 0.075);
  channels = mix(1.0, 0.9 + 0.1 * channels, 0.8);

  // Brighter towards the edges, and a hard bright line right along them.
  float rim = exp(-edge * 9.0);
  float line = exp(-edge * 55.0);

  vec3 topGlow = mix(uDeep, uMid, 0.5 + 0.5 * body) * channels;
  topGlow += uBright * (rim * 0.5 + line * 2.4);
  topGlow += uBright * filaments * body * uCaustics * uShimmer;

  // The flow made visible: a soft band of light travelling the length of the
  // bed, towards the pillows while it cools and away from them while it heats.
  // It is what says "moving" at the size of a phone, where the caustics are
  // only texture.
  float along = plan.y / (2.0 * uHalf.y) + 0.5;
  float travel = fract(-uPhase * 0.09);
  float band = exp(-pow((fract(along - travel + 0.5) - 0.5) * 3.2, 2.0) * 6.0);
  topGlow += (uMid * 0.6 + uBright * 0.4) * band * uWave * uShimmer;

  // The pillows sit on it, and the glow cannot get through them.
  float shade = 1.0 - 0.8 * max(podUnderPillow(plan, uPillowA), podUnderPillow(plan, uPillowB));
  topGlow *= shade;

  // Down the side of the cover: bright at the top edge, gone by the seam, with
  // a thin line where the cover meets the mattress.
  float h = clamp(vCoverPos.y / uCoverH + 0.5, 0.0, 1.0);
  vec3 sideGlow = mix(uDeep * 0.2, uMid * 1.1 + uBright * 0.25, pow(h, 1.5));
  sideGlow += uBright * (exp(-(1.0 - h) * 18.0) * 0.8 + exp(-h * 30.0) * 0.35);
  sideGlow *= 0.9 + 0.1 * body;

  vec3 podGlow = mix(sideGlow, topGlow, up) * uGlow * uGain;
  totalEmissiveRadiance += podGlow;
  diffuseColor.rgb *= mix(1.0, shade, up);
}
`

const SPILL_VERTEX = /* glsl */ `
varying vec2 vPlan;
void main() {
  vPlan = position.xy;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`

const SPILL_FRAGMENT = /* glsl */ `
uniform vec3 uColor;
uniform float uGlow;
uniform vec2 uHalf;
varying vec2 vPlan;
void main() {
  vec2 q = abs(vPlan) - uHalf + 0.1;
  float d = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - 0.1;
  float a = exp(-max(d, 0.0) * 3.2);
  gl_FragColor = vec4(uColor * a * uGlow * 0.3, 1.0);
}
`
