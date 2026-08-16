import { describe, expect, it } from 'vitest'

import {
  ACCENT,
  ERR,
  LOAD_RAMP,
  OK,
  PAL,
  PRODUCT_GROUND,
  RESERVED_BEAM,
  RESERVED_BEAM_EDGE,
  ROOM_FLOOR,
  TACIT,
  contrastRatio,
  deptColour,
  directionColour,
  loadColour,
  loadFillPermille,
  overCeiling,
  relativeLuminance,
} from '../src/design/tokens'
import { pressureColour } from '../src/ui/hud-model'

/**
 * The palette's rules, as assertions.
 *
 * Every claim the token module's comment block makes about readability is checked here
 * rather than trusted. That matters more after the daylight inversion than it did before:
 * on ink, a value that was too dark to read was obviously wrong on sight, and on ivory a
 * value that is too light is *almost* right, which is the failure mode that ships.
 *
 * The thresholds are WCAG's. Body and label text carries 4.5:1, primary text 7:1, and
 * anything that is a shape rather than a glyph carries 3:1 — the non-text rule, which is
 * what a department stripe and a load bar actually are.
 */

/** Hue in degrees, for the one assertion that is about hue rather than about luminance. */
function hue(hex: string): number {
  const value = Number.parseInt(hex.slice(1), 16)
  const r = ((value >> 16) & 0xff) / 255
  const g = ((value >> 8) & 0xff) / 255
  const b = (value & 0xff) / 255
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const delta = max - min
  if (delta === 0) return 0

  let degrees: number
  if (max === r) degrees = 60 * (((g - b) / delta) % 6)
  else if (max === g) degrees = 60 * ((b - r) / delta + 2)
  else degrees = 60 * ((r - g) / delta + 4)

  return (degrees + 360) % 360
}

/** The shortest angular distance between two hues, in degrees. */
function hueDistance(a: string, b: string): number {
  const raw = Math.abs(hue(a) - hue(b))
  return Math.min(raw, 360 - raw)
}

describe('the palette', () => {
  it('holds every slot as a six-digit hex', () => {
    for (const [name, value] of Object.entries(PAL)) {
      expect(value, `PAL.${name}`).toMatch(/^#[0-9a-f]{6}$/)
    }
  })

  it('spends the reserved amber on nothing else', () => {
    expect(RESERVED_BEAM).toBe('#f2c46b')

    const others = Object.entries(PAL).filter(([name]) => name !== 'beam')
    for (const [name, value] of others) {
      expect(value, `PAL.${name} must not be the beam`).not.toBe(RESERVED_BEAM)
    }
  })

  it('keeps the accent off the player and off the beam', () => {
    // The split settled during the redesign: 天蓝 in the chrome, 石绿 in the office. Stated
    // as a property so a later merge of the two is a failing test rather than a quiet
    // regression that puts a 2.1:1 label on screen.
    expect(ACCENT).toBe(PAL.tianlan)
    expect(ACCENT).not.toBe(PAL.shilv)
    expect(ACCENT).not.toBe(RESERVED_BEAM)
  })
})

describe('contrast', () => {
  it('measures the extremes correctly', () => {
    expect(contrastRatio('#000000', '#ffffff')).toBeCloseTo(21, 1)
    expect(contrastRatio('#1c2938', '#1c2938')).toBeCloseTo(1, 5)
  })

  it('refuses a colour it cannot measure', () => {
    // A three-digit hex or a named colour returning a plausible number would make every
    // assertion below pass for the wrong reason.
    expect(() => relativeLuminance('#fff')).toThrow()
    expect(() => relativeLuminance('rebeccapurple')).toThrow()
  })

  it('carries text at its readable weight', () => {
    expect(contrastRatio(PAL.text, PRODUCT_GROUND)).toBeGreaterThanOrEqual(7)
    expect(contrastRatio(PAL.textMuted, PRODUCT_GROUND)).toBeGreaterThanOrEqual(4.5)
    expect(contrastRatio(PAL.textFaint, PRODUCT_GROUND)).toBeGreaterThanOrEqual(4.5)
  })

  it('carries every semantic value that reaches a label', () => {
    for (const [name, value] of Object.entries({ ACCENT, OK, ERR, TACIT })) {
      expect(contrastRatio(value, PRODUCT_GROUND), name).toBeGreaterThanOrEqual(4.5)
    }
  })

  it('carries the beam on its edge rather than on its fill', () => {
    // The amber itself cannot clear 3:1 against ivory and is not asked to — it carries the
    // meaning. The edge carries the contrast, which is why the two are exported as a pair.
    expect(contrastRatio(RESERVED_BEAM, PRODUCT_GROUND)).toBeLessThan(3)
    expect(contrastRatio(RESERVED_BEAM_EDGE, PRODUCT_GROUND)).toBeGreaterThanOrEqual(3)
    expect(contrastRatio(RESERVED_BEAM_EDGE, RESERVED_BEAM)).toBeGreaterThanOrEqual(3)
  })

  it('keeps a hairline visible without making it a border', () => {
    const ratio = contrastRatio(PAL.rule, PRODUCT_GROUND)
    expect(ratio).toBeGreaterThan(1.2)
    expect(ratio).toBeLessThan(3)
  })
})

describe('the load ramp', () => {
  it('never lands on the beam', () => {
    expect(LOAD_RAMP).not.toContain(RESERVED_BEAM)
  })

  it('never puts a stop in the beam’s confusable band', () => {
    // The constraint that survived the inversion, stated as what it actually is. A stop is
    // mistakable for 决策琥珀 when it is close in hue *and* close in lightness — that is the
    // yellow midpoint a naive green-to-red interpolation produces, and it is what would
    // make ambient capacity look like a person waiting.
    //
    // Hue alone is the wrong test: the ramp's last two stops are warm by necessity, because
    // strain has to end in red, and 赭石 sits about 25° from the beam. What keeps them
    // unmistakable is that they are dark — a brick red at a tenth of the beam's luminance
    // reads as alarm, never as amber. So a warm stop is permitted exactly when it is dark
    // enough to be unmistakable, and the pale band the interpolation would wander into is
    // closed.
    const BEAM_LUMINANCE_BAND = 0.25

    for (const stop of LOAD_RAMP) {
      const farInHue = hueDistance(stop, RESERVED_BEAM) > 60
      const farInLightness = relativeLuminance(stop) < BEAM_LUMINANCE_BAND
      expect(farInHue || farInLightness, `${stop} sits in the beam's confusable band`).toBe(
        true,
      )
    }

    // And the band is real rather than vacuous: the beam itself fails both legs.
    expect(hueDistance(RESERVED_BEAM, RESERVED_BEAM)).toBe(0)
    expect(relativeLuminance(RESERVED_BEAM)).toBeGreaterThan(BEAM_LUMINANCE_BAND)
  })

  it('keeps every stop visible against the ground', () => {
    for (const stop of LOAD_RAMP) {
      expect(contrastRatio(stop, PRODUCT_GROUND), stop).toBeGreaterThanOrEqual(3)
    }
  })

  it('reads as one continuous scale', () => {
    expect(loadColour(0, 1000)).toBe(LOAD_RAMP[0])
    expect(loadColour(1400, 1000)).toBe(LOAD_RAMP[LOAD_RAMP.length - 1])

    const atCeiling = loadColour(1000, 1000)
    expect(atCeiling).not.toBe(LOAD_RAMP[0])
    expect(atCeiling).not.toBe(LOAD_RAMP[LOAD_RAMP.length - 1])
  })

  it('never returns the beam at any load', () => {
    for (let permille = 0; permille <= 2000; permille += 10) {
      expect(loadColour(permille, 1000)).not.toBe(RESERVED_BEAM)
    }
  })

  it('keeps its fill and its ceiling arithmetic', () => {
    expect(loadFillPermille(0, 1000)).toBe(0)
    expect(loadFillPermille(1400, 1000)).toBe(1000)
    expect(loadFillPermille(2800, 1000)).toBe(1000)
    expect(loadFillPermille(700, 1000)).toBe(500)

    expect(overCeiling(1001, 1000)).toBe(true)
    expect(overCeiling(1000, 1000)).toBe(false)
  })

  it('falls back rather than dividing by a ceiling of zero', () => {
    expect(loadColour(500, 0)).toBe(LOAD_RAMP[0])
    expect(loadFillPermille(500, 0)).toBe(0)
  })
})

describe('department colour', () => {
  it('maps every room the office can build', () => {
    const rooms = [
      'executive',
      'lounge',
      'sales',
      'accounting',
      'meeting',
      'admin',
      'people',
      'hr',
      'support',
      'cs',
    ]
    for (const room of rooms) {
      expect(deptColour(room), room).toBe(ROOM_FLOOR[room])
    }
  })

  it('falls back to structure rather than to nothing', () => {
    expect(deptColour('a-room-that-does-not-exist')).toBe(PAL.jingyuhui)
  })

  it('keeps every stripe visible against the ground', () => {
    // These are 3-pixel shapes rather than glyphs, so the non-text threshold is the right
    // one — but they are the only thing distinguishing one department from another in the
    // panels, so "visible" is not optional.
    for (const [room, colour] of Object.entries(ROOM_FLOOR)) {
      expect(contrastRatio(colour, PRODUCT_GROUND), room).toBeGreaterThanOrEqual(3)
    }
  })

  it('spends the beam on no department', () => {
    for (const [room, colour] of Object.entries(ROOM_FLOOR)) {
      expect(colour, room).not.toBe(RESERVED_BEAM)
    }
  })
})

describe('semantic direction', () => {
  it('never renders a metric movement as the beam', () => {
    for (const dir of ['favourable', 'unfavourable', 'flat'] as const) {
      expect(directionColour(dir)).not.toBe(RESERVED_BEAM)
    }
  })

  it('never renders decision pressure as the beam', () => {
    for (const waiting of [0, 1, 2, 5, 40]) {
      const pressure = { waiting, supply: 40, oldestWaitTicks: 0n }
      expect(pressureColour(pressure)).not.toBe(RESERVED_BEAM)
    }
  })
})
