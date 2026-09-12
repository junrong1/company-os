import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import { PAL, PRODUCT_GROUND, RESERVED_BEAM, contrastRatio } from '../src/design/tokens'

/**
 * The chrome's palette, held to the token module's.
 *
 * Two copies of a palette is two chances to drift, and the drift would be invisible in
 * review: a panel one value off looks fine on its own and wrong beside the office. The
 * stylesheet cannot import TypeScript, so the guarantee has to be a test that reads both
 * files rather than a mechanism that makes divergence impossible.
 *
 * These read the source text rather than a rendered DOM on purpose. jsdom does not apply
 * stylesheets it was not given and does not resolve custom properties through a cascade, so
 * a DOM-based version of this test would assert that jsdom has a default, not that the
 * product is on palette.
 */

function read(relative: string): string {
  return readFileSync(fileURLToPath(new URL(relative, import.meta.url)), 'utf8')
}

/**
 * The stylesheet with its comments removed.
 *
 * Every assertion here is about what the chrome *renders*, and a comment renders nothing. It
 * also renders these tests unrunnable if left in: the note at the head of shell.css explains
 * which slot is deliberately absent, and naming a slot in prose would fail an assertion that
 * the slot is unused. The rule is about declarations, so the input should be declarations.
 */
function withoutComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, '')
}

const SHELL_CSS = withoutComments(read('../src/ui/shell.css'))
const INDEX_CSS = withoutComments(read('../src/index.css'))
const APP_CSS = withoutComments(read('../src/App.css'))

/** `--text-muted` is `PAL.textMuted`. Kebab in CSS, camel in TypeScript, one slot. */
function slotFor(property: string): string {
  return property.replace(/-([a-z])/g, (_, letter: string) => letter.toUpperCase())
}

/** Every `--name: #value` declared in a `:root` block. */
function customProperties(css: string): Map<string, string> {
  const root = /:root\s*\{([^}]*)\}/s.exec(css)
  if (root === null) return new Map()

  const found = new Map<string, string>()
  for (const [, name, value] of root[1].matchAll(/--([a-z-]+):\s*(#[0-9a-f]{3,8})\s*;/g)) {
    found.set(name, value)
  }
  return found
}

/** Every `#rrggbb` literal anywhere in a stylesheet. */
function literals(css: string): string[] {
  return [...css.matchAll(/#[0-9a-f]{6}\b/g)].map(([hex]) => hex)
}

describe('the chrome palette', () => {
  const properties = customProperties(SHELL_CSS)

  it('declares the slots the chrome actually needs', () => {
    // A guard on the guard: if the regex stops matching, every assertion below passes
    // vacuously and the drift this file exists to catch ships silently.
    expect(properties.size).toBeGreaterThan(15)
  })

  it('agrees with the token module on every value', () => {
    for (const [name, value] of properties) {
      const slot = slotFor(name)
      expect(PAL, `shell.css declares --${name}, which has no slot in PAL`).toHaveProperty(slot)
      expect(value, `--${name} disagrees with PAL.${slot}`).toBe(
        PAL[slot as keyof typeof PAL],
      )
    }
  })

  it('keeps the player’s signal out of the interface', () => {
    // 石绿 is the CEO's colour inside the office and measures 2.1:1 against ivory. A control
    // wearing it would be a control nobody can read, which is why the accent split by
    // surface rather than one side losing.
    expect(SHELL_CSS).not.toContain('--shilv')
    expect(SHELL_CSS).not.toContain(PAL.shilv)
    expect(contrastRatio(PAL.shilv, PRODUCT_GROUND)).toBeLessThan(3)
  })

  it('spends the reserved amber on the beam and nothing else', () => {
    const beamRules = SHELL_CSS.split('\n')
      .map((line, index) => ({ line, index }))
      .filter(({ line }) => line.includes('var(--beam)'))

    expect(beamRules.length).toBeGreaterThan(0)

    // Every use of the reserved value sits inside a rule that is about someone waiting.
    // Checked against the surrounding block rather than the line, because the declaration
    // itself carries no selector.
    for (const { index } of beamRules) {
      const preceding = SHELL_CSS.split('\n').slice(0, index).join('\n')
      const selector = preceding.slice(preceding.lastIndexOf('\n', preceding.lastIndexOf('{')))
      // The four shapes "somebody is waiting on you" takes in the chrome: the dot beside a
      // person, the conversation with one of them open, the tray that holds the queue, and — since
      // U15 — the card for a director who cannot get on until the CEO answers them (M41). Anything
      // else spending the amber is the reserved signal leaking.
      const aboutWaiting =
        selector.includes('beam') ||
        selector.includes("data-state='blocked'") ||
        selector.includes('data-waiting') ||
        selector.includes('decision--ask')
      expect(aboutWaiting, `var(--beam) used under selector: ${selector.trim()}`).toBe(true)
    }
  })

  it('never draws the beam without its edge', () => {
    // 决策琥珀 is 1.6:1 against a panel. Wherever the chrome spends it, something has to
    // carry the contrast the fill cannot, or the one signal that must not be missed becomes
    // the one signal nobody sees.
    const blocks = SHELL_CSS.split('}')
    for (const block of blocks) {
      if (!block.includes('var(--beam)')) continue
      expect(block, `a rule spends --beam without --beam-edge:\n${block}`).toContain(
        'var(--beam-edge)',
      )
    }
  })
})

describe('every stylesheet', () => {
  it('carries no colour the palette does not know about', () => {
    // App.css and index.css render before shell.css is parsed, so they spell their values
    // out. Spelling them out is fine; inventing them is not.
    const known = new Set<string>(Object.values(PAL))

    for (const [name, css] of [
      ['index.css', INDEX_CSS],
      ['App.css', APP_CSS],
      ['shell.css', SHELL_CSS],
    ] as const) {
      for (const hex of literals(css)) {
        expect(known.has(hex), `${name} uses ${hex}, which is not a palette slot`).toBe(true)
      }
    }
  })

  it('never spends the reserved amber outside the office chrome', () => {
    for (const [name, css] of [
      ['index.css', INDEX_CSS],
      ['App.css', APP_CSS],
    ] as const) {
      expect(literals(css), `${name}`).not.toContain(RESERVED_BEAM)
    }
  })

  it('opens on the product ground rather than flashing a placeholder', () => {
    // The body's background is painted before shell.css lands. A stale value here is a dark
    // flash on every cold load — the one frame of the old console the redesign would leave
    // behind.
    expect(INDEX_CSS).toContain('color-scheme: light')
    expect(INDEX_CSS).toContain(PRODUCT_GROUND)
    expect(INDEX_CSS).toContain(PAL.text)
  })
})

// =========================================================================
// People first (U15)
// =========================================================================

describe('the composition', () => {
  it('gives the office a room of its own above the wide breakpoint', () => {
    // Stacked, the shell puts metrics above the office and panels below it, and the office
    // becomes a letterbox between two dashboards — which inverts R1 before a single value is
    // read. The wide layout moves the panels to the side so the stage claims the height.
    expect(SHELL_CSS).toMatch(/@media \(min-width: \d+px\)/)

    const wide = /@media \(min-width: \d+px\) \{([\s\S]*?)\n\}/.exec(SHELL_CSS)?.[1] ?? ''
    expect(wide).toContain('grid-template-columns')
    expect(wide).toContain("'stage  panels'")
  })

  it('lets the stage take the free row in both layouts', () => {
    // `1fr` on the stage's row is what makes the office the thing that grows when there is
    // room, rather than the thing that gets squeezed when there is not.
    const rows = [...SHELL_CSS.matchAll(/grid-template-rows:\s*([^;]+);/g)].map((m) => m[1])
    expect(rows.length).toBeGreaterThanOrEqual(2)
    for (const row of rows) expect(row).toContain('1fr')
  })

  it('stops the stage scrolling now that the camera follows', () => {
    // Keeping both would let a player scroll away from a viewport that then fights them back.
    // Asserted over every `.stage` rule rather than the first, because the grid areas add a
    // one-line `.stage { grid-area: stage; }` that a first-match regex finds instead.
    const rules = [...SHELL_CSS.matchAll(/\.stage \{([^}]*)\}/g)].map((match) => match[1])
    expect(rules.length).toBeGreaterThan(0)
    expect(rules.some((rule) => rule.includes('overflow: hidden'))).toBe(true)
    expect(rules.some((rule) => rule.includes('overflow: auto'))).toBe(false)
  })

  it('makes the waiting person the only warm thing in the chrome', () => {
    // R12: one warm accent, and it belongs to the person who needs you. Every rule that
    // spends it is checked above; this checks that it is spent at all, in the two places the
    // eye actually lands — the row and the panel holding it.
    expect(SHELL_CSS).toContain("[data-panel='tray']")
    expect(SHELL_CSS).toContain(".person[data-waiting='1']")
  })

  it('gives the bench block no hue in any of its four states (M21, R36)', () => {
    // The newest marking's rule, asserted where a hue would be added. A briefing is not a warning
    // and a scripted reply is not an error: strip every colour from this block and a canned line
    // must still be distinguishable from a director's view, which the glyph, the label and the
    // sentence beside them carry. A hue here would move that distinction into something a reader
    // has to see in colour to get.
    const start = SHELL_CSS.indexOf('.conversation__bench')
    expect(start).toBeGreaterThan(-1)
    const block = SHELL_CSS.slice(start, SHELL_CSS.indexOf('.conversation__work', start))
    expect(block.length).toBeGreaterThan(200)

    expect(block).not.toMatch(/#[0-9a-fA-F]{3,6}/)
    for (const hue of ['--zhuhong', '--cuilv', '--tianlan', '--chutaofenhong', '--tacit']) {
      expect(block, `the bench block spends ${hue}`).not.toContain(hue)
    }
  })

  it('keeps the chrome compact enough to leave the office room', () => {
    // Not a pixel budget — a type-scale one. Nothing in the chrome is bigger than a metric's
    // own figure, so the band that describes the office cannot be taller than the office
    // deserves. In px because the approved direction sets the interface in px and reserves
    // relative units for the office, which scales by an integer zoom instead.
    const sizes = [...SHELL_CSS.matchAll(/font-size:\s*(\d+)px/g)].map((m) => Number(m[1]))
    expect(sizes.length).toBeGreaterThan(20)
    expect(Math.max(...sizes)).toBeLessThanOrEqual(23)
  })
})
