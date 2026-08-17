#!/usr/bin/env node
/**
 * The screenshots the redesign's human judgements are made from.
 *
 * Three widths, a fixed seed, and the office with one person waiting on a decision. The
 * success criteria this serves are the ones arithmetic cannot settle — whether the room reads
 * as a workplace, whether the hierarchy is right, whether the waiting person is unmistakable.
 * `tests/visual.test.ts` takes everything that *can* be settled without a person.
 *
 * No pixel-diff baselines, deliberately. A redesign in flight spends its life regenerating
 * them, and every claim worth automating is a property rather than an image.
 *
 * Runs against a real gateway if one is up, and against a recorded genesis if not — because
 * the thing being photographed is the art, and requiring a backend to look at the art would
 * mean nobody looks at it.
 *
 *   node scripts/screenshots.mjs
 */

import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { chromium } from 'playwright'
import { createServer } from 'vite'

const OUT = new URL('../../docs/assets/company-os-visual-redesign/verification/', import.meta.url)

/** A fixed run, so two captures of the same build are the same picture. */
const RUN_ID = 'run_verification'

/**
 * The person a real day-zero run is waiting on, and what they are stopped at.
 *
 * Injected rather than played, and the injection stays: this harness runs against a recorded
 * genesis so that looking at the art needs no Python stack, and a genesis event alone puts
 * nobody in front of a decision — the first `CHECKPOINT_RAISED` arrives a tick later, from the
 * kernel. What the injection must not do is *invent* a state the product never reaches, which
 * is what it used to do: it named Marcus in Sales stopped at the AP map, while M6's authored
 * seed opens every run with Ruth in People stopped at the hiring item. These three values are
 * that event's, taken from a live day-zero log rather than chosen.
 */
const WAITING_PERSON = 'dir_hr'
const WAITING_ITEM = 'wi_hiring'
const WAITING_LABEL = 'Information'

/**
 * The genesis the office is drawn from.
 *
 * The kernel's own golden fixture, which is what the parity suite pins the client against —
 * so this photographs the office the product actually builds rather than one a harness
 * invented.
 */
const golden = JSON.parse(
  readFileSync(
    new URL('../../backend/tests/fixtures/golden/genesis.json', import.meta.url),
    'utf8',
  ),
)

const genesisFrame = {
  kind: 'GENESIS',
  seq: '1',
  tick: '0',
  runId: RUN_ID,
  payload: golden.payload ?? golden,
}

/** The widths the redesign is judged at, and why each one is on the list. */
const WIDTHS = [
  { name: 'laptop', width: 1280, height: 800, why: 'the commonest display there is' },
  { name: 'desktop', width: 1440, height: 900, why: 'the fixed size the criteria name' },
  { name: 'wide', width: 1920, height: 1080, why: 'above the breakpoint, panels on a rail' },
]

const server = await createServer({
  configFile: fileURLToPath(new URL('../vite.config.ts', import.meta.url)),
  root: fileURLToPath(new URL('..', import.meta.url)),
  server: { port: 0 },
  logLevel: 'error',
})
await server.listen()

const address = server.httpServer?.address()
const port = typeof address === 'object' && address !== null ? address.port : 5173
const base = `http://127.0.0.1:${port}/`

const browser = await chromium.launch()
mkdirSync(OUT, { recursive: true })

try {
  for (const { name, width, height, why } of WIDTHS) {
    const page = await browser.newPage({ viewport: { width, height } })
    await page.goto(`${base}?run=${RUN_ID}`, { waitUntil: 'domcontentloaded' })

    // Genesis is pushed into the store directly rather than fetched from a gateway.
    //
    // The office is what these pictures are of, and a screenshot harness that needs a Python
    // stack up to photograph the art is a harness nobody runs. The dev server serves the
    // app's own modules, and ESM instances are shared, so `import()` here reaches the very
    // store the running client is reading — no product hook, no test-only global, and no
    // second opinion about what a genesis event means.
    await page.evaluate(
      async ([genesis, waitingFor, waitingItem, waitingLabel]) => {
        const { useRunStore } = await import('/src/net/store.ts')
        useRunStore.getState().apply(genesis)
        useRunStore.getState().setConnection('open')

        // One person stopped, waiting on a decision — which is the state AE1 is written
        // about. Without it the picture shows an office where nothing needs the CEO, and
        // the criterion it exists to settle is "the waiting person is unambiguous".
        useRunStore.setState({
          tray: [
            {
              itemId: waitingItem,
              personId: waitingFor,
              cpIndex: 0,
              label: waitingLabel,
              kind: 'info',
              atTick: 1n,
              atSeq: 2n,
            },
          ],
        })
      },
      [genesisFrame, WAITING_PERSON, WAITING_ITEM, WAITING_LABEL],
    )

    // Long enough for the renderer to bake the floor, compose eleven sheets and draw.
    await page.waitForTimeout(1500)

    // Full page, not the viewport. Below the wide breakpoint the panels sit under the office
    // and the page scrolls, so a viewport capture would photograph the office and cut off
    // half of what the redesign changed.
    const shot = await page.screenshot({ fullPage: true })
    writeFileSync(new URL(`office-${name}-${width}x${height}.png`, OUT), shot)
    console.log(`office-${name}-${width}x${height}.png — ${why}`)
    await page.close()
  }
} finally {
  await browser.close()
  await server.close()
}
