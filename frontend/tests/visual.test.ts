import { describe, expect, it } from 'vitest'

import {
  PAL,
  PRODUCT_GROUND,
  RESERVED_BEAM,
  RESERVED_BEAM_EDGE,
  relativeLuminance,
} from '../src/design/tokens'
import { ATLAS, CELL_HEIGHT, CELL_WIDTH } from '../src/render/cast/atlas-index'
import { IDENTITIES, candidateFor } from '../src/render/cast/atlas'
import { TILE, buildStatic, type FloorData } from '../src/render/floor'
import { drawActor, drawWaitingBeam, placement } from '../src/render/actors'
import { ART } from '../src/render/sprites'
import { FLOORS, GLASS, WALLC } from '../src/render/palettes'

/**
 * The success criteria, as far as they can be measured.
 *
 * The redesign's criteria split cleanly into two kinds. "A fixed desktop screenshot preserves
 * the approved hierarchy without a dark vignette or dungeon-like wall mass" is half a
 * measurement and half a judgement: whether the office is *dark* is arithmetic, whether it
 * reads as a workplace is a person's call. This file takes the arithmetic. The judgement is
 * taken from the screenshots and the contact sheet in
 * `docs/assets/company-os-visual-redesign/verification/`, produced by
 * `scripts/screenshots.mjs` and `scripts/contact-sheet.mjs`.
 *
 * Deliberately no pixel-diff baselines. A redesign in flight spends its life regenerating
 * them, and every claim worth automating is a property rather than an image.
 */

const FIXTURE: FloorData = {
  cols: 31,
  rows: 18,
  hall: [1, 8, 29, 9],
  spawn: [3, 8],
  rooms: [
    {
      id: 'sales',
      kind: 'office',
      band: 'top',
      box: [9, 1, 15, 6],
      door: [12, 7],
      seat_y: 3,
      slots: [
        [10, 3],
        [12, 3],
      ],
      visit: null,
    },
  ],
  furniture: [
    [10, 4, 'desk', 1],
    [10, 3, 'chair', 0],
  ],
  lamps: [[10, 2]],
  windows: [[2, 0], [7, 0]],
}

interface Painted {
  area: number
  fill: string
}

class Frame {
  fillStyle = ''
  globalCompositeOperation = 'source-over'
  readonly painted: Painted[] = []
  readonly images: number[] = []

  fillRect(_x: number, _y: number, width: number, height: number): void {
    if (typeof this.fillStyle === 'string' && this.fillStyle.startsWith('#')) {
      this.painted.push({ area: width * height, fill: this.fillStyle })
    }
  }

  createRadialGradient(): { addColorStop: () => void } {
    return { addColorStop: () => undefined }
  }

  drawImage(...args: unknown[]): void {
    this.images.push(args.length)
  }

  createImageData(width: number, height: number): { width: number; height: number; data: Uint8ClampedArray } {
    return { width, height, data: new Uint8ClampedArray(width * height * 4) }
  }

  putImageData(): void {}
  translate(): void {}
  scale(): void {}
  save(): void {}
  restore(): void {}
  clearRect(): void {}
}

function bakeFrame(): Frame {
  const frame = new Frame()
  buildStatic(
    FIXTURE,
    () => ({ width: 0, height: 0, getContext: () => frame }) as unknown as HTMLCanvasElement,
  )
  return frame
}

describe('the office is lit', () => {
  it('paints no dark field anywhere in the room', () => {
    // The vignette, stated as the property rather than as its absence — the failure mode is
    // it coming back as an edge shade, an occlusion pass or a "subtle" overlay.
    const floor = relativeLuminance(PAL.jingyuhui)
    for (const { area, fill } of bakeFrame().painted) {
      if (area <= 64) continue
      expect(relativeLuminance(fill), `${area}px of ${fill}`).toBeGreaterThan(floor - 0.001)
    }
  })

  it('has no wall heavier than the floor it stands on', () => {
    // "Dungeon-like wall mass", as arithmetic. A wall is a partition when its face is lighter
    // than the room and masonry when it is not.
    expect(relativeLuminance(WALLC.face)).toBeGreaterThan(0.5)
    expect(relativeLuminance(WALLC.cap)).toBeGreaterThan(relativeLuminance(WALLC.face))
    expect(relativeLuminance(WALLC.base)).toBeGreaterThan(relativeLuminance(PAL.jingyuhui))
  })

  it('keeps every surface a person stands on brighter than the ink they are outlined in', () => {
    for (const [style, palette] of Object.entries(FLOORS)) {
      expect(relativeLuminance(palette.a), style).toBeGreaterThan(
        relativeLuminance(PAL.outline) * 8,
      )
    }
  })

  it('spends the reserved amber on nothing in the room', () => {
    const office = [
      ...bakeFrame().painted.map(({ fill }) => fill),
      ...Object.values(ART),
      ...Object.values(GLASS),
      ...Object.values(WALLC),
    ]
    expect(office).not.toContain(RESERVED_BEAM)
  })
})

describe('the one signal', () => {
  const actor = {
    id: 'dir_sales',
    xMilli: 10_000,
    yMilli: 4_000,
    facing: 'down' as const,
    moving: false,
    animTicks: 0,
  }

  it('appears once when somebody is waiting, and not at all when nobody is', () => {
    const waiting = new Frame()
    drawWaitingBeam(waiting as unknown as CanvasRenderingContext2D, { ...actor, waiting: true })
    const spent = waiting.painted.filter(({ fill }) => fill === RESERVED_BEAM)
    expect(spent.length).toBeGreaterThan(0)

    // Drawing a person is the whole of what happens when nobody is waiting.
    const quiet = new Frame()
    drawActor(quiet as unknown as CanvasRenderingContext2D, actor, 1)
    expect(quiet.painted.map(({ fill }) => fill)).not.toContain(RESERVED_BEAM)
  })

  it('never appears without the edge that makes it visible', () => {
    // 决策琥珀 is 1.6:1 against a daylight floor. Without its outline the one signal that must
    // not be missed is the one signal nobody sees.
    const frame = new Frame()
    drawWaitingBeam(frame as unknown as CanvasRenderingContext2D, { ...actor, waiting: true })
    const fills = frame.painted.map(({ fill }) => fill)

    expect(fills).toContain(RESERVED_BEAM)
    expect(fills).toContain(RESERVED_BEAM_EDGE)
    expect(fills.indexOf(RESERVED_BEAM_EDGE)).toBeLessThan(fills.indexOf(RESERVED_BEAM))
  })

  it('clears the head of a two-tile figure', () => {
    const frame = new Frame()
    const beam = { ...actor, waiting: true }
    drawWaitingBeam(frame as unknown as CanvasRenderingContext2D, beam)

    // Above the cell, not inside it — a beam drawn over someone's hair is a hat.
    expect(placement(beam).top).toBeGreaterThan(0)
    expect(frame.painted.length).toBeGreaterThan(0)
  })
})

describe('the cast holds together', () => {
  it('draws everyone from the approved board, leads and coworkers alike', () => {
    // AE3, read as simply as it can be. Leads carry more identity detail than a background
    // coworker *because the board drew them that way*, and the two share proportions, outline
    // and weight because they are the same 33 pieces of art.
    for (const id of [...IDENTITIES, 'temp_001', 'temp_002', 'visitor_9']) {
      expect(ATLAS[candidateFor(id, 3)], id).toBeDefined()
    }
  })

  it('gives every candidate one row of four facings by four frames', () => {
    expect(Object.keys(ATLAS)).toHaveLength(33)
    const rows = Object.values(ATLAS).map((entry) => entry.row)
    expect(Math.max(...rows)).toBe(32)
    expect(new Set(rows).size).toBe(33)
  })

  it('never casts anyone in the reserved amber', () => {
    for (const [name, entry] of Object.entries(ATLAS)) {
      expect(Object.values(entry.skin), name).not.toContain(RESERVED_BEAM)
    }
  })
})

describe('the composition', () => {
  it('makes a person nearly two tiles tall, on a tile that can host one', () => {
    // The whole reason the resolution doubled. At 16 pixels a tile this figure is four tiles
    // tall and a tile and a half wide, which is not a character in an office.
    expect(CELL_HEIGHT / TILE).toBeCloseTo(2, 1)
    expect(CELL_WIDTH / TILE).toBeCloseTo(1.5, 1)
  })

  it('keeps the ground the brightest thing in the product', () => {
    // Everything else is measured against it, so if it were not, every contrast rule in the
    // palette would be measuring against the wrong reference.
    const brightest = Math.max(
      ...Object.values(PAL).map((hex) => relativeLuminance(hex)),
    )
    expect(relativeLuminance(PRODUCT_GROUND)).toBeCloseTo(brightest, 5)
  })
})
