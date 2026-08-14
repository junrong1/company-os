/**
 * Which run to open.
 *
 * Read from the URL rather than defaulted, because there is nothing to default to: the gateway
 * exposes commands, reads and a stream **for a run that already exists**, and creating one is
 * not a command — `START_RUN` and `FORK_RUN` are in the proto and are not in the kernel's
 * dispatch table yet. Guessing an id here would render an empty shell that looked broken rather
 * than one that says what is missing.
 */
export function runIdFromLocation(search: string): string | null {
  const params = new URLSearchParams(search)
  const runId = params.get('run')
  return runId === null || runId === '' ? null : runId
}
