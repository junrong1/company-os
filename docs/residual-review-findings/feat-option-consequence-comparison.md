# Residual review findings — option consequence and branch comparison

Accepted knowingly on `feat/option-consequence-comparison`, from the Tier 2 review of
`docs/plans/2026-08-14-002-feat-option-consequence-comparison-plan.md`.

Every actionable finding from that review was fixed. The three below were not, because each
needs a change to the kernel's threading model or to run compatibility that is larger than the
feature that surfaced it. They are recorded here rather than left in a session transcript.

---

## 1. Nothing synchronises the command path against the tick loop

**Severity:** P1 · **Where:** `backend/services/kernel/loop.py` (`apply_command` vs `_advance`)

`post_command` is a synchronous FastAPI route, so Starlette runs it on a worker thread.
`_advance` runs on an anyio worker thread. Both touch `run.state`, and there is no lock
anywhere in `backend/` outside tests. Every command has always done this; the reason it never
mattered is that every other command is a single-key read or write measured in microseconds.

A comparison is the first command to walk the *whole* state — `snapshot.capture` iterates
people, items, capacity, morale, hires, pending and dynamic items, twice — so it is the first
with a window wide enough to be hit.

**Mitigated, not closed.** The branches now fork from a single capture rather than one per
option, which cuts the exposure by the branch count, and `_capture_of` retries a torn read
(`SnapshotInvalid` from the self-hash check, or `RuntimeError` from a dict that changed size)
before refusing with a reason. Measured after the fix: no uncaught exception across repeated
comparisons against a live ticker.

**What closing it properly needs:** a lock held across the command path and the tick batch, or
a design where commands are enqueued onto the tick loop rather than applied from the request
thread. Both are changes to the kernel's core threading model.

---

## 2. A comparison occupies the worker pool every run's clock depends on

**Severity:** P1 · **Where:** `backend/packages/simcore/compare.py`, `backend/services/kernel/loop.py`

Measured: 0.27s per branch at `MAX_BRANCH_DAYS`, so about 1.6s for a six-option comparison —
all of it pure Python holding the GIL, on a thread drawn from anyio's default 40-slot capacity
limiter. `_advance` and the lease heartbeat draw from the same limiter.

The code reasoned carefully about not stalling the *store writer* by keeping branches outside
the append transaction, and that reasoning is sound. It did not account for the worker pool,
where the cost simply moved. Enough simultaneous comparisons would slow every run's clock in
the process, and `healthy()` asks whether the tick task is `done()`, not whether it is
progressing — so a starved clock would report ready.

**Why it is accepted:** this is a single-user local product with no auth by design; the
realistic ceiling is one person clicking one button. The cost is bounded (it does not grow with
the caller's horizon) and is now stated in the README rather than implied.

**What closing it properly needs:** a process pool for branch execution, or a dedicated
capacity limiter so comparisons cannot starve the tick loop.

---

## 3. A run created before this change shows every option as costing nothing

**Severity:** P2 · **Where:** `backend/packages/simcore/items.py`, `frontend/src/ui/conversation-model.ts`

`catalog_to_state` gained `effect`, `draw_delta` and `note` at genesis payload version 4. The
fold never re-derives the catalog — it reads `grid`, `run_seed`, `horizon_tick`, `metrics` and
`floor` and nothing else — so a run created before this change keeps its old-shaped catalog for
its lifetime. The rules version deliberately did not move, so such a run is still resumable.

The client reads defensively (`option.effect ?? {}`), so nothing crashes. But rendering no
figures is indistinguishable from an option that genuinely costs nothing, and a comment in
`optionConsequence` claiming this was "the honest answer" was corrected during review: it is a
limitation, not honesty.

**Why it is accepted:** runs here are local and disposable, and no long-lived log predates the
change.

**What closing it properly needs:** `schema_ver` threaded from the event frame into the store's
genesis reader, so the surface can render "not recorded" rather than nothing.

---

## Also considered and deliberately not changed

- **`requested_at_tick` is written and never read by the client.** It is on the record so a
  staleness dispute can be diagnosed and so the second plan's citation contract has something
  to point at. Not dead weight — just not client-facing.
- **The new figures render a typographic minus where the HUD's delta chip renders an ASCII
  hyphen.** Unifying it properly means touching the HUD, which is outside this change.
- **The lead-tick guard is duplicated between `submit_ceo_input` and `compare_options`.** A
  helper taking a custom message would be no shorter, and the two rejections say different
  things.
- **The org chart's progress figure is marked but unreachable.** Its row reads
  `PersonView.itemId`, which no event sets — the inherited "staff do not move on the client"
  hole. The marking is in place for when that changes; the test says so rather than implying
  coverage.
