#!/usr/bin/env node
/**
 * The README's hero: eight seconds of the product, captured from a run that really happened.
 *
 * M66 asks for one thing — walk, conversation, decision, then a cut to the timeline tree with
 * two futures side by side — and the whole difficulty is the last clause. A hero assembled from
 * mockups is a promise; this one is a recording. Every frame below comes out of the client
 * talking to a live kernel: the CEO is walked with the arrow keys, the conversation opens
 * because they are standing next to somebody, the decision is taken in person and appended to a
 * log, the fork is a second timeline the store really holds, and the closing frames are the diff
 * route folding both of them at one sim-day.
 *
 * **It needs a backend, and refuses without one.** `screenshots.mjs` falls back to a recorded
 * genesis because what it photographs is the art; this photographs the *loop*, and there is no
 * fallback that would still be the loop. Bring one up first:
 *
 *     docker compose up -d                       # or: cd backend && uv run python single_process.py
 *     cd frontend && npm run hero
 *
 * The client itself is served by a dev server this script starts, rather than by nginx, for the
 * reason `screenshots.mjs` gives: the dev server serves the app's own modules, so the capture
 * can read the store to find out where the CEO is instead of guessing from pixels.
 *
 * Output: `docs/assets/hero/company-os-hero.gif`, plus the last frame as a still, both checked
 * in. Regenerating them is this command; nothing in CI runs it.
 */

import { mkdirSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { chromium } from 'playwright'
import { createServer } from 'vite'

import { writeGif } from './gif.mjs'
import { readPng, writePng } from './png.mjs'

const OUT = new URL('../../docs/assets/hero/', import.meta.url)
const GATEWAY = process.env.COMPANY_OS_GATEWAY_URL ?? 'http://127.0.0.1:8800'

/**
 * `HERO_DEBUG=1` narrates the capture and writes every frame out as a PNG.
 *
 * Here because a capture that goes wrong goes wrong *silently*: the film is still eight seconds
 * long, it is just eight seconds of somebody standing still. Every beat below says what it did.
 */
const NARRATE = process.env.HERO_DEBUG === '1'

function say(...parts) {
  if (NARRATE) console.log('·', ...parts)
}

/**
 * The frame this is cut at, and the size it is published at.
 *
 * 1200 is past the 1180px breakpoint where the panels move onto a rail beside the office. Below
 * it they stack underneath and the page scrolls, so the conversation and the tray — half of what
 * the hero is about — would be off-screen in every frame.
 */
const WIDTH = 1200
const HEIGHT = 675

/** 12.5 frames a second. Fast enough that the walk reads as walking, slow enough to be a file. */
const DELAY_MS = 80

/**
 * How much film the walk gets, whether or not it has arrived by then.
 *
 * **The walk is cut, not waited out**, and that is a decision about the film as much as about
 * the capture. The hero has eight seconds for four beats, and the walk's job is to show that
 * the CEO is a body on a floor rather than a cursor over a list — which two and a half seconds
 * says completely. It also takes the precision problem out of the picture: a leg is a key held
 * for a wall-clock duration against a client whose input tagging is an estimate, so the CEO
 * arrives within a tile or so and then needs a correction or two, and those corrections are
 * neither interesting nor short. They happen off camera, between this cut and the next.
 */
const WALK_FRAMES = 30

/**
 * The shipped default, because the hero is a stranger's first sight of the product and the
 * default is what `docker compose up` gives them. `wi_hiring` is seeded to `dir_hr` at genesis
 * (U2), so somebody is already waiting when the run opens — which is the beat the walk is for.
 */
const SCENARIO = 'default'
const WAITING_ON = 'dir_hr'

const frames = []

async function main() {
  await refuseWithoutAGateway()

  const runId = `hero-${Date.now().toString(36)}`
  const created = await post(`${GATEWAY}/runs`, { run_id: runId, scenario: SCENARIO })
  console.log(`run ${created.run_id}, ${created.scenario}`)

  const server = await createServer({
    configFile: fileURLToPath(new URL('../vite.config.ts', import.meta.url)),
    root: fileURLToPath(new URL('..', import.meta.url)),
    server: { port: 0 },
    logLevel: 'error',
  })
  await server.listen()
  const address = server.httpServer?.address()
  const port = typeof address === 'object' && address !== null ? address.port : 5173

  // **Headed, not headless, and that is not a preference.** The client tags every input a
  // fixed wall-time budget ahead of the tick it is *rendering*, and the render clock is driven
  // by `requestAnimationFrame`. A headless page screenshotted a frame at a time runs that loop
  // far behind the wall, so the clock falls hundreds of ticks behind the kernel and every input
  // is tagged for a tick that has already passed — measured, "input is for tick 16, which is
  // not in the future (now 314)" across the top of the film, and a walk whose legs were the
  // wrong length because the tagging was. A real window renders at a real rate.
  const browser = await chromium.launch({ headless: false })
  const context = await browser.newContext({
    viewport: { width: WIDTH, height: HEIGHT },
    reducedMotion: 'no-preference',
  })
  // The two first-run hints sit over the floor, and the hero has eight seconds to show the
  // floor. What they say goes beside the image in the README instead, where it can be read.
  await context.addInitScript(() => {
    try {
      window.localStorage.setItem('company-os.hints.dismissed', JSON.stringify(['walk', 'talk']))
    } catch {
      // A browser with storage blocked shows the hints. Not worth failing a capture over.
    }
  })

  const page = await context.newPage()
  if (NARRATE) {
    page.on('console', (message) => {
      if (message.type() === 'error') say('console:', message.text())
    })
    page.on('pageerror', (error) => say('page error:', String(error)))
    page.on('requestfailed', (request) => say('request failed:', request.url(), request.failure()?.errorText))
    page.on('response', (response) => {
      if (response.url().includes('/diff/')) say('diff response:', response.status(), response.url())
    })
  }
  try {
    await page.goto(`http://127.0.0.1:${port}/?run=${runId}`, { waitUntil: 'domcontentloaded' })
    await page.waitForSelector('.shell', { timeout: 15_000 })
    await page.waitForFunction(
      async () => {
        const { runState } = await import('/src/net/store.ts')
        return runState().genesis !== null
      },
      null,
      { timeout: 15_000 },
    )

    // **Wait for the first position echo before touching the keyboard.** The client tags every
    // input against its render clock, and that clock has nothing to chase until the kernel
    // speaks: between genesis and the first echo it sits at the tick genesis stated, while the
    // run has moved on. Inputs tagged from it are then tagged for ticks that have already
    // passed, the kernel refuses them, and the client says so in a banner across the top of the
    // page — which is correct, and which sits over the first two seconds of the film.
    await page.waitForFunction(
      async () => {
        const { runState } = await import('/src/net/store.ts')
        return runState().ceoEcho !== null
      },
      null,
      { timeout: 30_000 },
    )

    await theWalk(page)
    await theConversation(page)
    await theDecision(page)
    await theCut(page, runId)
  } finally {
    await browser.close()
    await server.close()
  }

  publish()
}

/** Say what is missing, rather than failing somewhere deep in a selector. */
async function refuseWithoutAGateway() {
  let status
  try {
    status = await fetch(`${GATEWAY}/status`)
  } catch (cause) {
    throw new Error(
      `No kernel at ${GATEWAY} (${cause.message}). The hero is a recording of a real run, so it ` +
        'needs one. Start it with `docker compose up -d`, or `uv run python single_process.py` ' +
        'from backend/, then run this again.',
    )
  }
  if (!status.ok) throw new Error(`the gateway at ${GATEWAY} answered ${status.status}`)
}

async function post(url, body) {
  const answered = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  const payload = await answered.json()
  if (!answered.ok) throw new Error(`${url} answered ${answered.status}: ${JSON.stringify(payload)}`)
  return payload
}

/**
 * One frame, decoded to RGBA so the encoder can see it.
 *
 * Checked on both sides of the shutter. A banner can appear during the hundred milliseconds a
 * screenshot takes, which a pre-check alone cannot catch — measured, exactly one frame of
 * ninety-four.
 */
async function shoot(page) {
  await clearTheBanner(page)
  let shot = await page.screenshot({ type: 'png' })
  if ((await page.$('.banner')) !== null) {
    await clearTheBanner(page)
    shot = await page.screenshot({ type: 'png' })
  }
  const { width, height, pixels } = readPng(shot)
  if (width !== WIDTH || height !== HEIGHT) {
    throw new Error(`a frame came back ${width}x${height}, not ${WIDTH}x${HEIGHT}`)
  }
  frames.push({ rgba: pixels, delayMs: DELAY_MS })
  if (NARRATE && frames.length % 5 === 0) {
    mkdirSync(new URL('frames/', OUT), { recursive: true })
    writeFileSync(
      new URL(`frames/${String(frames.length).padStart(3, '0')}.png`, OUT),
      writePng(WIDTH, HEIGHT, pixels),
    )
  }
}

/**
 * Wall-time the kernel needs to hear an input before the tick it is tagged for.
 *
 * The client tags an input a fixed wall-time budget ahead of the tick it is rendering, so a
 * key held for exactly a leg's worth of sim-time arrives a beat late and the CEO stops a beat
 * short. One budget's worth, added back.
 */
async function kernelTick(runId) {
  const answered = await fetch(`${GATEWAY}/runs/${runId}/state`)
  return Number((await answered.json()).tick)
}

/** Hold still for a beat. The pauses are what make the cuts readable. */
async function hold(page, count) {
  for (let at = 0; at < count; at += 1) await shoot(page)
}

// =========================================================================
// 1. The walk
// =========================================================================

/** Where the client last heard the CEO is, as a tile. */
async function echoTile(page) {
  return page.evaluate(async () => {
    const { runState } = await import('/src/net/store.ts')
    const state = runState()
    const { xMilli, yMilli } = state.ceoEcho ?? state.ceo
    return [Math.round(xMilli / 1000), Math.round(yMilli / 1000)]
  })
}

/** Hold until the echo says something new. */
async function echoMoves(page) {
  const was = String(await echoTile(page))
  for (let step = 0; step < 40; step += 1) {
    await film(page)
    if (String(await echoTile(page)) !== was) return
  }
}

/** A frame of the walk while the walk is still on camera, and a plain wait once it is not. */
async function film(page) {
  if (frames.length < WALK_FRAMES) await shoot(page)
  else await page.waitForTimeout(100)
}

/** The legs from wherever the CEO is now to a tile beside the person who is waiting. */
async function route(page) {
  return page.evaluate(async (person) => {
    const { runState } = await import('/src/net/store.ts')
    const { buildGrid, walkable } = await import('/src/render/floor.ts')

    const state = runState()
    const floor = state.genesis?.floor
    const seat = state.people[person]
    if (floor === undefined || seat === undefined) return null

    const grid = buildGrid(floor)
    const tile = (milli) => Math.round(milli / 1000)
    // `ceoEcho`, never `ceo`. The store's `ceo` is where the kernel *spawned* them and is
    // replaced only by a resync; the live position arrives as a `POSITION_ECHO` control frame
    // and lands in `ceoEcho`. Reading the wrong one is a walk routed from the spawn forever,
    // which looks exactly like a walk that works until the route has a corner in it.
    const here = state.ceoEcho ?? state.ceo
    const from = [tile(here.xMilli), tile(here.yMilli)]
    const at = [here.xMilli, here.yMilli]
    const seatTile = [tile(seat.xMilli), tile(seat.yMilli)]

    // Stop *beside* them, not on them: a desk is not walkable and standing next to somebody is
    // the whole mechanic.
    const goals = new Set(
      [
        [seatTile[0] - 1, seatTile[1]],
        [seatTile[0] + 1, seatTile[1]],
        [seatTile[0], seatTile[1] - 1],
        [seatTile[0], seatTile[1] + 1],
      ]
        .filter(([x, y]) => walkable(grid, x, y))
        .map(([x, y]) => `${x},${y}`),
    )
    if (goals.size === 0) return null

    const came = new Map([[`${from[0]},${from[1]}`, null]])
    const queue = [from]
    let found = null
    while (queue.length > 0 && found === null) {
      const [x, y] = queue.shift()
      for (const [dx, dy] of [
        [0, 1],
        [0, -1],
        [1, 0],
        [-1, 0],
      ]) {
        const next = [x + dx, y + dy]
        const key = `${next[0]},${next[1]}`
        if (came.has(key) || !walkable(grid, next[0], next[1])) continue
        came.set(key, [x, y])
        if (goals.has(key)) {
          found = next
          break
        }
        queue.push(next)
      }
    }
    if (found === null) return null

    const path = []
    for (let step = found; step !== null; step = came.get(`${step[0]},${step[1]}`)) path.unshift(step)

    // Collapse the tiles into runs of one direction, which is what a key press is — and give
    // each run the distance it covers in **milli-tiles between tile centres**, not in whole
    // tiles.
    //
    // That distinction is the difference between this working and not. A tile is a thousand
    // milli and the CEO's position is continuous, so a leg aimed at a tile *edge* lands on the
    // wrong side of it whenever the timing is a hair short — and the People room's door is one
    // tile wide. Measured: four attempts in a row oscillating between tile 3 and tile 5 around
    // a door at tile 4. Aiming at the centre leaves half a tile of slack on both sides.
    const runs = []
    const cursor = [at[0], at[1]]
    for (let index = 1; index < path.length; index += 1) {
      const dx = path[index][0] - path[index - 1][0]
      const dy = path[index][1] - path[index - 1][1]
      const axis = dy !== 0 ? 1 : 0
      const key = dy !== 0 ? (dy > 0 ? 'ArrowDown' : 'ArrowUp') : dx > 0 ? 'ArrowRight' : 'ArrowLeft'
      const centre = path[index][axis] * 1000 + 500
      const milli = Math.abs(centre - cursor[axis])
      cursor[axis] = centre

      const last = runs[runs.length - 1]
      if (last !== undefined && last.key === key) last.milli += milli
      else runs.push({ key, milli, tiles: 0 })
    }
    for (const run of runs) run.tiles = run.milli / 1000
    return runs
  }, WAITING_ON)
}

/**
 * Walk the CEO to the person who is waiting.
 *
 * **Routed rather than steered, and re-routed rather than routed once.** The first version
 * pressed whichever arrow closed the larger gap, which is a greedy walk across a floor with
 * walls in it — and a greedy walk into a corner stays there: measured, the CEO reached the
 * west corridor at tile (3, 8) and stood pressing Down against a wall while the capture
 * happily filmed it. So the route is a breadth-first search over the client's *own* collision
 * grid, which is `render/floor.ts` — the same `buildGrid` and `walkable` the prediction uses,
 * so a floor that changes shape re-routes instead of producing a film of somebody stuck.
 *
 * The re-routing is the second half of the same problem. A leg is held for a wall-clock
 * duration and the CEO's position is continuous, so each one lands within about a third of a
 * tile of where it was aimed — and a third of a tile is the difference between standing in a
 * doorway and standing in the wall beside it. Measured: an aim at the People room's door at
 * tile 4 left the CEO at tile 5 and the last leg walked into the wall.
 *
 * **Nothing may be clicked before this runs.** `typingTarget` gives the arrow keys to a
 * focused BUTTON rather than to the stage — deliberately, because the decision tray is built
 * out of radios — so a capture that clicked the clock control first filmed eight seconds of a
 * CEO standing still, with no `CEO_INPUT` reaching the kernel and nothing in any log to say
 * why.
 */
async function theWalk(page) {
  await page.mouse.move(WIDTH - 8, HEIGHT - 8) // the cursor out of the shot

  // **The client's nominal clock, not the kernel's measured one**, and the difference is the
  // whole of why the walk used to overshoot. A key held for D milliseconds moves the CEO by
  // whatever tick interval the *client* tags the press and the release with, and the client
  // tags from its render clock — which runs at a fixed 36 ticks per wall second per rate step,
  // by construction. The kernel, on a machine busy taking screenshots, achieves rather less
  // than that. Timing the holds against the kernel's achieved rate therefore stretched every
  // leg by the ratio of the two: measured, 36 nominal against 20.4 achieved, and legs landing
  // 1.8× too far — four attempts oscillating either side of a one-tile doorway.
  //
  // The establishing beat is still worth its frames: a moment of the office before anybody
  // moves is what the film wants anyway.
  const ticksPerSecond = await page.evaluate(async () => {
    const { runState } = await import('/src/net/store.ts')
    const { TICKS_PER_WALL_MS_NUMERATOR, TICKS_PER_WALL_MS_DENOMINATOR } = await import(
      '/src/render/clock.ts'
    )
    const rate = Math.max(1, runState().rate)
    return (Number(TICKS_PER_WALL_MS_NUMERATOR) * 1000 * rate) / Number(TICKS_PER_WALL_MS_DENOMINATOR)
  })
  await hold(page, 8)
  say('clock:', ticksPerSecond.toFixed(1), 'ticks a second, nominal')

  for (let attempt = 0; attempt < 12; attempt += 1) {
    // The route is planned from the echo, so it can only be re-planned once the echo has
    // spoken again. Without this wait the second attempt re-plans the first one's route from
    // the first one's starting tile and walks it again, and the third does the same —
    // measured, three identical attempts and a CEO one tile from the door.
    if (attempt > 0) await echoMoves(page)
    const legs = await route(page)
    if (legs === null) throw new Error('no route from the CEO to the person who is waiting')
    say(`attempt ${attempt + 1}:`, legs.map((leg) => `${leg.key}x${leg.tiles.toFixed(1)}`).join(' ') || 'already there')
    if (legs.length === 0) break

    for (const leg of legs) {
      // How long the leg takes, in sim-ticks, from the kernel's own walking speed — which the
      // client re-exports because it predicts against it.
      const ticks = await page.evaluate(async (milli) => {
        const { CEO_STRAIGHT_MILLI_PER_TICK } = await import('/src/render/interpolate.ts')
        return Math.round(milli / Number(CEO_STRAIGHT_MILLI_PER_TICK))
      }, leg.milli)

      // **Held for a wall-clock duration, not until the client says it arrived.** Neither of
      // the two things the client could be asked has the resolution: the position arrives as a
      // `POSITION_ECHO` about once a sim-hour and the CEO crosses a dozen tiles inside one, and
      // the store's *tick* is moved by that same echo, so it advances in jumps of sixty. Both
      // were tried. The first walked the CEO from tile 4 to tile 28; the second held every leg
      // for one echo interval whatever its length.
      const until = Date.now() + (ticks / ticksPerSecond) * 1000
      await page.keyboard.down(leg.key)
      // A screenshot costs about a tenth of a second, which is a third of a tile: stop taking
      // them before the leg ends and wait out the remainder exactly, or every leg overshoots by
      // however long the last frame took.
      while (Date.now() + 120 < until) {
        await film(page)
        if (await page.$('.conversation')) break
      }
      const left = until - Date.now()
      if (left > 0) await page.waitForTimeout(left)
      await page.keyboard.up(leg.key)
      if (await page.$('.conversation')) break
    }

    if (await page.$('.conversation')) break
  }

  await page.waitForSelector('.conversation', { timeout: 10_000 })
}


// =========================================================================
// 2. The conversation
// =========================================================================

async function theConversation(page) {
  await page.waitForSelector('.conversation__decision', { timeout: 10_000 })
  await hold(page, 16)
}

/**
 * Clear the refusal banner before every frame, if one is up.
 *
 * **This is a harness artefact being swept, not product UI being hidden**, and the distinction
 * is worth writing down because the line is a real one.
 *
 * The client tags every input against its *render* clock, which extrapolates from the last tick
 * the kernel said out loud and stalls after `MAX_EXTRAPOLATION_TICKS` of hearing nothing. The
 * kernel speaks once a sim-hour — 60 ticks, comfortably inside the 90-tick budget when the
 * clock is achieving its nominal rate. This machine, running the browser, Docker and a
 * screenshot every hundred milliseconds, achieves about 0.6 of it: 60 ticks then take longer in
 * wall-time than 90 ticks of extrapolation allows, the client's clock stalls before each echo,
 * and the next key release is tagged for a tick that has already passed. The kernel refuses it
 * and the client says so, correctly, across the top of the page.
 *
 * So the banner is true, and it is about the capture rather than about the product: nobody
 * playing this in a browser at sixty frames a second ever sees it. What is swept is the
 * sentence, by pressing the rate the run is already at — `command()` clears the standing
 * rejection before it sends, and that one changes nothing. Nothing is hidden with CSS and no
 * product code knows this script exists; a banner that cannot be cleared, because the run is
 * paused and every command is refused, stays in the film.
 */
async function clearTheBanner(page) {
  if ((await page.$('.banner')) === null) return
  const rate = page.locator('.rates button[data-active="true"]')
  if ((await rate.count()) === 0) return
  await rate.first().click()
  // Focus back to the stage: `typingTarget` gives the arrow keys to a focused BUTTON, and this
  // runs in the middle of a walk.
  await page.evaluate(() => {
    const focused = document.activeElement
    if (focused instanceof HTMLElement) focused.blur()
  })
  await page.mouse.move(WIDTH - 8, HEIGHT - 8)
  await page.waitForSelector('.banner', { state: 'detached', timeout: 5_000 }).catch(() => {})
}

// =========================================================================
// 3. The decision, taken in person
// =========================================================================

async function theDecision(page) {
  const options = await page.$$('.conversation__decision .option')
  if (options.length === 0) throw new Error('the conversation is open on nothing to decide')

  await options[0].click()
  await hold(page, 6)

  const settle = page.locator('.conversation__decision button', { hasText: 'Decide here' })
  if ((await settle.count()) === 0) throw new Error('the conversation offers no way to settle')
  await settle.first().click()
  await page.waitForFunction(
    async () => {
      const { runState } = await import('/src/net/store.ts')
      return Object.keys(runState().decisions).length > 0
    },
    null,
    { timeout: 10_000 },
  )
  await hold(page, 8)
}

// =========================================================================
// 4. The cut: two futures, side by side
// =========================================================================

/**
 * Fork the decision the other way, let both futures run, and open the diff on them.
 *
 * The fork goes through the Decided panel rather than through the API, because the panel *is*
 * the mechanic (U25) and a hero that forked off-screen would be showing a tree that appeared
 * from nowhere.
 *
 * **Then both timelines are run to the next day boundary, off camera.** The option the CEO took
 * and the option the fork took move different metrics, but the diff compares at a *day*, and
 * the decision was taken inside day one — so a diff taken straight after the fork is two
 * columns of identical numbers and a difference column of zeros. That is a correct reading of a
 * Universe nothing has happened in yet, and it is a terrible last frame: the whole claim of the
 * hero is that the two futures are different. One sim-day each, at ×3, through the gateway
 * rather than through the UI, because a lineage has one clock and moving it between timelines
 * is a `switch` — a verb the client has a surface for and the film has no time to show.
 *
 * The diff itself is two canvas clicks, computed from the tree's own layout constants rather
 * than measured off a screenshot, and asserted — so a click that lands on the wrong node fails
 * the capture instead of shipping a film of the wrong two timelines.
 */
async function theCut(page, parentRunId) {
  const fork = await page.$('.decided .alternative__fork')
  if (fork === null) throw new Error('the Decided panel offers no alternative to fork')
  await fork.click()

  await page.waitForSelector('.universe__tree', { timeout: 15_000 })
  await page.waitForFunction(() => document.querySelectorAll('.universe__id').length > 0, null, {
    timeout: 15_000,
  })
  const childRunId = (await page.textContent('.universe__id'))?.trim()
  if (childRunId === undefined || childRunId === parentRunId) {
    throw new Error(`the fork did not land in a child; the tree says ${childRunId}`)
  }
  say('forked into', childRunId)
  await hold(page, 8)

  await bothReachDayTwo(page, parentRunId, childRunId)

  await pickTimeline(page, 0, parentRunId)
  await (await page.$('.universe__diff')).click()
  await hold(page, 4)

  await pickTimeline(page, 1, childRunId)
  // The rows, not the panel: the panel appears immediately and says it is folding, and a
  // capture that stopped there would end on a spinner.
  await page.waitForSelector('.diff__rows .diff__row', { timeout: 30_000 })
  say('diff:', (await page.textContent('.diff__separated'))?.trim())
  await hold(page, 22)
}

/**
 * Run each timeline past the day boundary after the decision, then hand the clock back.
 *
 * One clock per lineage (U17), so this is three `switch`es rather than two `set_rate`s: resume
 * the child, wait, move the clock to the parent, wait, and park it back on the child paused —
 * which is where the player is standing and where the last frames are shot.
 */
async function bothReachDayTwo(page, parentRunId, childRunId) {
  // Two sim-days in, so the boundary is comfortably past whatever tick the decision landed on.
  // The length of a day comes from the client's own copy of the tick arithmetic rather than
  // from a number written here, for the reason that file gives: two copies drift.
  const day = await page.evaluate(async () => {
    const { TICKS_PER_SIM_DAY } = await import('/src/render/interpolate.ts')
    return Number(TICKS_PER_SIM_DAY) * 2
  })

  // A fork arrives paused and a switch names two different timelines, so the child is started
  // with the same `set_rate` the clock control sends, and only the move between them is a
  // switch.
  await setRate(childRunId, 3)
  await runPast(page, childRunId, day)
  await switchTo(childRunId, parentRunId, 3)
  await runPast(page, parentRunId, day)
  // Handed back to the timeline the player is standing in, and handed back *running*. A paused
  // run refuses every command with a sentence across the top of the page, and the last beat of
  // the film needs one command to land: the one that clears whatever banner is already there.
  await switchTo(parentRunId, childRunId, 1)
  say('both timelines past tick', day)
}

async function switchTo(from, to, rate) {
  await post(`${GATEWAY}/runs/${encodeURIComponent(from)}/switch`, { to, rate })
}

async function setRate(runId, rate) {
  await post(`${GATEWAY}/runs/${encodeURIComponent(runId)}/commands`, {
    kind: 'set_rate',
    payload: { rate },
    idempotency_key: `hero-rate-${runId}-${rate}`,
  })
}

/** Wait, off camera, until a timeline's clock is past a tick. */
async function runPast(page, runId, tick) {
  for (let waited = 0; waited < 120; waited += 1) {
    if ((await kernelTick(runId)) >= tick) return
    await page.waitForTimeout(500)
  }
  throw new Error(`${runId} did not reach tick ${tick}; is its clock running?`)
}

/**
 * Click the node at this depth on the tree canvas, and check what was selected.
 *
 * The tree is a canvas, so there is no element to click and no element to assert on — the hit
 * test is `timelineAt` over placements in canvas pixels. The coordinates here are that layout's
 * own arithmetic rather than a measurement off a screenshot, and the check is the aside: a pick
 * that lands between two nodes selects neither and the capture would otherwise go on to film a
 * diff of the wrong two timelines, or of one timeline and nothing.
 */
async function pickTimeline(page, depth, expected) {
  const GRID = 16
  const NODE_COLS = 14
  const NODE_ROWS = 3
  const DEPTH_GAP_COLS = 4

  const canvas = await page.$('.universe__tree')
  if (canvas === null) throw new Error('the Universe is not showing a tree')
  const box = await canvas.boundingBox()
  const size = await canvas.evaluate((element) => ({
    width: element.width,
    height: element.height,
  }))

  const x = depth * (NODE_COLS + DEPTH_GAP_COLS) * GRID + (NODE_COLS * GRID) / 2
  const y = (NODE_ROWS * GRID) / 2
  await page.mouse.click(
    box.x + (x * box.width) / size.width,
    box.y + (y * box.height) / size.height,
  )
  await page.mouse.move(WIDTH - 8, HEIGHT - 8)

  const picked = (await page.textContent('.universe__id'))?.trim()
  say('picked', picked, 'at depth', depth)
  if (picked !== expected) {
    throw new Error(`the pick at depth ${depth} selected ${picked}, not ${expected}`)
  }
}

// =========================================================================
// Out
// =========================================================================

function publish() {
  mkdirSync(OUT, { recursive: true })

  const gif = writeGif({ width: WIDTH, height: HEIGHT, frames })
  writeFileSync(new URL('company-os-hero.gif', OUT), gif)

  // The last frame as a still, for anywhere a GIF does not animate — a print, a slide, an
  // aggregator that strips animation. It is the diff, which is the frame the hero ends on.
  const last = frames[frames.length - 1]
  writeFileSync(new URL('company-os-hero-last-frame.png', OUT), writePng(WIDTH, HEIGHT, last.rgba))

  const seconds = ((frames.length * DELAY_MS) / 1000).toFixed(1)
  console.log(
    `${frames.length} frames, ${seconds}s, ${(gif.length / 1024 / 1024).toFixed(2)} MB ` +
      `-> docs/assets/hero/company-os-hero.gif`,
  )
}

await main()
