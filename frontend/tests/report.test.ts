import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { act, createElement } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { GATEWAY_BASE, universeReportUrl } from '../src/net/gateway'
import { Shell } from '../src/ui/Shell'
import { genesisFrame } from './helpers/frames'
import { useRunStore } from './helpers/store-helpers'

/**
 * Reaching the report from the game (U22: M61).
 *
 * The export itself is the backend's, and `backend/tests/test_report.py` is where the document
 * is pulled apart. What this file owns is the half of M61 that lives in the client, and it is
 * one sentence: **from a live run, the report is one action away.**
 *
 * That sounds too small to test until you write down the ways it silently stops being true —
 * every one of which leaves a control on screen that looks exactly right:
 *
 * * **it points at the mounted route.** The export is served by the report app the launcher
 *   mounts, not by the gateway, so the path carries a prefix no other call in `gateway.ts` has.
 *   A link built the way every other URL in that file is built would 404, and it would 404 in a
 *   new tab where nobody would connect it to the button they pressed;
 * * **it is an anchor.** A `<button>` that opened a window would lose middle-click, Save Link As
 *   and "copy link" — and the whole point of the artifact is that it gets sent to somebody;
 * * **the run id is encoded.** `POST /runs` accepts a client-supplied id and refuses only
 *   control characters, so a slash in one would reach the server as a different route;
 * * **it follows the run.** The shell does not own its run id — entering a timeline replaces
 *   it from above — so a link built once would go on exporting the timeline the player left.
 *
 * The control is unconditional because the shell is: the app shows the start screen until there
 * is a run, so every shell on screen has one.
 */

const ROOTS: Array<{ host: HTMLElement; root: ReturnType<typeof createRoot> }> = []

afterEach(() => {
  vi.unstubAllGlobals()
  // Unmounted rather than removed: the shell binds window listeners, and a root whose host is
  // gone still holds them. The same note is on every file in this suite that mounts a shell.
  for (const mounted of ROOTS.splice(0)) {
    act(() => mounted.root.unmount())
    mounted.host.remove()
  }
})

async function mount(element: ReturnType<typeof createElement>): Promise<HTMLElement> {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  ROOTS.push({ host, root })
  await act(async () => {
    root.render(element)
  })
  return host
}

async function shellFor(runId: string): Promise<HTMLElement> {
  act(() => {
    useRunStore.getState().reset()
    useRunStore.getState().apply(genesisFrame())
  })
  return mount(
    createElement(Shell, {
      runId,
      makeStream: () => ({ start: () => {}, stop: () => {} }),
      storage: null,
    }),
  )
}

function reportLink(host: HTMLElement): HTMLAnchorElement | null {
  return host.querySelector<HTMLAnchorElement>('.topbar__report')
}

/**
 * A source file, read off disk.
 *
 * Through a parameter rather than inline, which is not a style choice: Vite rewrites a literal
 * `new URL('./thing.css', import.meta.url)` into the URL it *serves* that asset at, so the
 * inline form resolves to `http://localhost:3000/...` and never reaches the filesystem at all.
 * Passing the path as an argument defeats the transform. `chrome.test.ts` reads its stylesheets
 * the same way, for the same reason.
 */
function read(relative: string): string {
  return readFileSync(fileURLToPath(new URL(relative, import.meta.url)), 'utf8')
}

const SHELL_CSS = read('../src/ui/shell.css')

describe('where the report lives', () => {
  it('is the route the launcher mounts, under the one proxy prefix', () => {
    expect(universeReportUrl('run-1')).toBe(`${GATEWAY_BASE}/report/runs/run-1/universe.html`)
  })

  it('encodes a run id that would otherwise change the route', () => {
    // Not hypothetical: the kernel refuses only control characters in a client-supplied id, so
    // a slash reaches the store and then this URL.
    expect(universeReportUrl('a/b')).toBe(`${GATEWAY_BASE}/report/runs/a%2Fb/universe.html`)
    expect(universeReportUrl('a b?x=1')).toBe(
      `${GATEWAY_BASE}/report/runs/a%20b%3Fx%3D1/universe.html`,
    )
  })

  it('is same-origin, so a file served from nginx needs nothing configured', () => {
    expect(universeReportUrl('run-1').startsWith('/')).toBe(true)
  })
})

describe('reaching it from a live run', () => {
  it('is one action from the office: an anchor in the top bar', async () => {
    const host = await shellFor('run-1')
    const link = reportLink(host)

    expect(link).not.toBeNull()
    expect(link?.tagName).toBe('A')
    expect(link?.getAttribute('href')).toBe(universeReportUrl('run-1'))
    expect(link?.textContent).toBe('Report')
  })

  it('opens beside the run rather than replacing it', async () => {
    // A run is a live thing with a clock and a socket. Navigating away from it to read a report
    // about it would pause nothing and lose the session's place.
    const link = reportLink(await shellFor('run-1'))

    expect(link?.getAttribute('target')).toBe('_blank')
    expect(link?.getAttribute('rel')).toContain('noreferrer')
  })

  it('is reachable from every stage, not only from the Universe', async () => {
    // The report is about the tree, so the tempting home for it is the Universe stage — where a
    // player who has never forked has never been. It sits on the bar that is always there.
    const host = await shellFor('run-1')
    const toggle = host.querySelector<HTMLElement>('.stage-toggle')

    expect(toggle).not.toBeNull()
    expect(host.querySelector('.topbar__right')?.contains(reportLink(host))).toBe(true)
    expect(toggle?.contains(reportLink(host))).toBe(false)
  })

  it('follows the run when the player enters another timeline', async () => {
    // The shell does not own its run id — a fork replaces it from above — and a link built once
    // and cached would keep exporting from the timeline the player left.
    const host = await shellFor('run-1')
    expect(reportLink(host)?.getAttribute('href')).toBe(universeReportUrl('run-1'))

    await act(async () => {
      ROOTS[ROOTS.length - 1].root.render(
        createElement(Shell, {
          runId: 'run-1-child',
          makeStream: () => ({ start: () => {}, stop: () => {} }),
          storage: null,
        }),
      )
    })

    expect(reportLink(host)?.getAttribute('href')).toBe(universeReportUrl('run-1-child'))
  })
})

describe('the control itself', () => {
  it('is styled by the chrome rather than by an inline rule', () => {
    // The same rule the rest of the top bar follows: colour and form live in `shell.css`, so a
    // palette change reaches every control at once. A test over the source because jsdom applies
    // no stylesheet it was not handed — the same reason `chrome.test.ts` reads the file.
    expect(SHELL_CSS).toContain('.topbar__report')
    // Keyboard users get the same ring every other control on the bar gets. It is one shared
    // selector list, so the assertion is that this control is on it.
    expect(SHELL_CSS).toMatch(/\.topbar__report:focus-visible/)
  })
})
