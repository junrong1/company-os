/**
 * Which run to open.
 *
 * Read from the URL rather than defaulted, because there is nothing to default to: the gateway
 * exposes commands, reads and a stream **for a run that already exists**, and creating one is
 * deliberately not a command, so that a typo'd id cannot conjure a simulation. Guessing an id
 * here would render an empty shell that looked broken rather than one that says what is
 * missing — and now that the page can start a run itself, the absence of an id is an offer to
 * start one rather than a dead end.
 */
export function runIdFromLocation(search: string): string | null {
  const params = new URLSearchParams(search)
  const runId = params.get('run')
  return runId === null || runId === '' ? null : runId
}

/**
 * Put the run in the address bar without reloading the page.
 *
 * `replaceState` rather than `pushState`: starting a run is not a navigation the back button
 * should undo, and the reason this happens at all is that a *reload* must re-attach to the run
 * the player is in rather than silently starting a second one.
 */
export function rememberRunInLocation(runId: string): void {
  if (typeof window === 'undefined' || typeof window.history?.replaceState !== 'function') return

  const url = new URL(window.location.href)
  if (url.searchParams.get('run') === runId) return

  url.searchParams.set('run', runId)
  window.history.replaceState(null, '', url.toString())
}
