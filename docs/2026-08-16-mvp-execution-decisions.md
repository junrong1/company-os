---
date: 2026-08-16
topic: mvp-execution-decisions
plan: docs/plans/2026-08-16-001-feat-company-os-mvp-plan.md
---

# The MVP plan's open questions, answered

The plan (`docs/plans/2026-08-16-001-feat-company-os-mvp-plan.md`) carries five questions under
**Open Questions → Needs a decision before Phase C is scheduled**, and five more under **Deferred
to implementation**. Twenty-five units and a different implementer per unit means each of these
would otherwise be answered five times, differently. They are answered here once.

This document is not the plan. The plan is the decision artifact and is not edited during
execution; this is the record of what execution resolved, and where.

---

## 1. Which process runs the bench guards?

**Both, with one implementation.**

The plan contradicts itself: the U11 file list puts the guards in
`backend/services/agents/bench/guards.py`, and the sequence diagram under "A briefing, end to end"
puts the rejection in the kernel (`K->>K: reject a ranking, an uncited figure, an out-of-scope
citation`).

Both are right for different reasons, and the reasons do not compete:

- In the **agents service**, a rejected statement never crosses the wire, and the guard sits next
  to the prompt that produced it. That is the cheap rejection.
- In the **kernel**, at answer-application time, the verdict is reproducible from the log. That is
  what makes M18 and M19 *auditable* rather than merely enforced — you can re-derive from a log
  that a ranking was rejected, without trusting the service that rejected it.

The cost the plan names for "both" is two copies drifting apart. That is avoided by putting the
guard logic in **`packages/`**, not in either service. The predicates are pure functions over
(statement, citations, authorized scope) with no transport and no store, so they belong in
`simcore` — which `backend/tests/test_import_boundaries.py` already requires to be pure, and which
both `services/kernel` and `services/agents` may import. Neither service may import the other, so
a shared module in `packages/` is the only shape that gives one implementation and two call sites.

Owning units: **U10** places the module and the kernel call site; **U11** adds the agents-side call
site and the persona/prompt material. Neither writes a second copy of a predicate.

## 2. Does a briefing arrive on a paused run?

**Yes — a statement answered while the run is paused applies at the current tick.**

The precedent the plan names holds: `NEEDS_NO_TICK_BOUNDARY` in
`backend/services/gateway/commands.py:49` already exempts `compare_options` from the paused guard,
with the comment that "pausing to weigh two options is also exactly when a CEO wants one".

The scenario is narrower than the plan's phrasing suggests, and the narrowing matters. A fully
paused run raises no statement request at all, because R17 derives the request inside `step()` and
`step()` does not run at rate zero — and the conversation commands are themselves rejected on a
paused run by the guard above. So the hang is not "open a checkpoint while paused". It is: the
request is raised at tick T while the run ticks, the player pauses at T+2, the provider answers at
T+5 wall-clock, and the landing tick T+offset is never reached. The deadline is also counted in
sim-ticks, so the fallback does not fire either. The conversation hangs with nothing to recover it.

The landing-tick rule is therefore:

| the run is | `applies_at` |
|---|---|
| ticking | `raised_at_tick + STATEMENT_OFFSET_TICKS` |
| paused | the current tick |

**This stays deterministic, and it costs no state-shape move.** Provider latency never enters
either branch, which is the whole of R17 — `receive_answer` today computes
`max(state.tick + 1, ...)` from the *live* tick (`backend/packages/simcore/step.py:634`), and that
is the defect R17 names. And the landing tick does not need to be re-derived at replay: it is
recorded on `INPUT_RECEIVED`, which is in `INPUT_KINDS`
(`backend/packages/simcore/log.py:79-95`) and so is read from the log rather than regenerated.
A pause is in the log independently — `RATE_CHANGED` is also an input kind — so two fresh runs from
one seed with the same player actions agree on every landing tick, at any provider speed.

Do **not** add a `rate` field to `State` for this. It would move the state-shape version and
regenerate every golden fixture to buy something the input event already carries.

## 3. Is a scripted fallback a cache entry?

**No. A fallback is never written to the cache.**

The plan states the risk on both sides: if a fallback is not cached, "a fork replaying a
ceiling-exhausted stretch can get a real statement where its parent got a fallback"; if it is,
"raising the ceiling never recovers the bench".

The first risk does not survive contact with how a fork actually works. A fork copies the parent's
event rows as a prefix (`LogStore.fork_run`, `backend/services/kernel/store.py:387`), so every
pre-divergence statement in the child *is* the parent's statement, byte-for-byte, read from the
log. The cache is not consulted for those ticks at all. Post-divergence the situation differs by
construction, so the assembled prompt differs, so it is a different cache key regardless. R4's
per-run ceiling then means the child's own budget is its own.

The second risk is real and permanent: a cached fallback is a wrong answer with a long life, and
the operator's remedy — raise the ceiling, configure a key — would silently not work.

So: the cache stores provider responses. Guard rejections, provider errors, timeouts and
exhausted ceilings produce the scripted reply and write nothing. Owning unit: **U12**.

## 4. Does the store carry forward across the DDL bump?

**No. The bump is a documented wipe.**

The plan describes an existing store being "carried forward by creating the table and advancing the
version row", then admits the work "no unit currently owns". Rather than assign it, execution drops
it, because:

- `create_all` adds the cache table but cannot add `lineage_root_id` to `runs`, so carry-forward is
  a real migration, not a table creation.
- A migration path has no corpus of old stores to prove itself against. It would ship untested on
  the one component whose failure is silent data corruption.
- "Runs are local and disposable" is stated throughout the plan and the README, and the plan's own
  Risks section already accepts that "every scenario edit invalidates every run written against
  it". A store that survives a DDL bump but not a scenario edit is not buying much.

What is required instead: a version mismatch **refuses, does not create or alter anything, leaves
the writer lease unheld, and prints the remedy as a sentence rather than a traceback** (U24 owns
this), and the README's stranger path says plainly that upgrading drops the volume (**U1** owns
this).

The one thing genuinely expensive to rebuild is the response cache, and it starts empty, so nothing
is lost by wiping it.

## 5. Where does the report's QR code come from?

**A checked-in inline SVG for the fixed repository URL.**

R8's no-new-dependency posture rules out an encoder, U22's export must contain no script and make
no network request, and the URL is fixed at authoring time — so there is nothing for a runtime
encoder to do that a checked-in asset does not already do. Owning unit: **U22**.

---

## Deferred-to-implementation items, and who resolves them

| Item | Resolved by | How |
|---|---|---|
| Call and token ceiling values; statement deadline in sim-ticks | U9, U10 | A finite shipped default. An explicitly unlimited setting is logged loudly at startup; an absent setting means the default, never unbounded. R18 sizes the statement deadline against the *fastest* clock rate the client offers, not ×1. |
| Tick-lag bound and achieved-multiplier floor | U4, U5 | Measured on the current build before the change, written into the test with the measurement recorded, with enough headroom not to flake and little enough to still fail. |
| How far a director's memory query reaches; summary regeneration cadence | U14 | Either cadence must carry an as-of-day stamp, which the plan already requires. |
| The diff's default metric set, and whether it is configurable | U18 | Follow the comparison surface's existing column set rather than inventing a second one. |
| Hint dismissal: per browser or per run | U2 | Per browser, in local storage next to the HUD composition. Hint state never enters the log. |

---

## Two plan inaccuracies found while executing

- **U1's file list names `backend/services/kernel/grpc_server.py`.** The file did exist (187 lines,
  added in `413f101`) and the plan's reasoning for deleting it holds — `git grep grpc_server` over
  `backend/` at that commit returns no callers, so it really was the last consumer of the split.
- **`raise_request`'s docstring claims it stalls the owning item.** It does not; it only records
  the request in `state.pending` (`backend/packages/simcore/step.py:554-603`). The plan already
  catches this in U15 ("raising a request does not stall an item today"), and U15 owns building the
  stall. The docstring should be corrected in whichever unit touches it first.

---

## What U4 found, that U5 and U19 need

U4 (`788e0ec`) took the before-change measurements and turned up three things that change how the
next units should be written.

**The plan's U5 verification criterion measures a near-insensitive metric, and the reason is a
defect.** `achieved_multiplier_permille` computes `wanted = int(elapsed * 36 * rate)`, which
truncates a fraction of a tick at every wake and never accumulates the remainder. At rate 1 the
clock runs at roughly 55% of nominal while the field reports `1000`; it only moves once the catch-up
clamp binds. So U5's stated verification — "a six-option comparison against a live ticker leaves the
achieved clock multiplier above the permille floor U4 records" — can pass while the clock is in
fact half speed. **U5 must assert on observed-ticks-against-nominal as well**, which is what U4's own
test does and why it carries both numbers. The truncation itself is pre-existing and out of scope for
Phase A; it wants its own unit, and it is not in the PRD.

**The standing torn-read mitigation never sees the failure mode that actually flakes.**
`snapshot.capture` hashes the state and then encodes it. A tick landing between those two reads
produces a `Snapshot` that raises nothing at capture time — it fails later, inside
`snapshot.restore`, called from `compare._branch_from`, which no `except` covers. `_capture_of`
retries only `capture`, so its three attempts catch the dictionary-changed-size variant and never
this one. That is why `test_every_branch_of_a_comparison_forks_from_one_instant` flakes under suite
load and passes in isolation. U4 demonstrated the split directly: `capture()` raised no; `restore()`
raised `SnapshotInvalid`.

U5 therefore has two ways to close it, and should pick deliberately: split `run_comparison` into
"take a capture" and "run from a capture", so the capture can be taken under the caller's lock — the
shape `loop.py` now uses — or move `_capture_of`'s retry so that it wraps the restore rather than
the capture.

**Branch execution must not go back under the per-run lock.** Measured: 779 permille with the
comparison handler under the lock, against 944 with it outside. The capacity limiter U5 adds would
buy nothing while the tick loop is blocked on the lock.

### Two pre-existing defects recorded and deliberately not fixed

Neither is in the PRD, and both want their own unit rather than a ride-along in a Phase A unit.

- **`export` hashes a live run's whole state without the lock** — the same class of torn read.
  Locking it is not enough: it reads events from the store *before* hashing, so making the two agree
  needs a store read and a state read under one lock, which R13 forbids. It wants a
  snapshot-then-read-through-that-sequence design.
- **`_echo_position` enqueues onto an `asyncio.Queue` from a worker thread**, which is not
  thread-safe. Unchanged; it now at least sits inside the locked region, so the echoed tick and the
  echoed position come from one quantum.

---

## What U9 found, that U12 and U24 need

**U12's file list contradicts a test U8 shipped, and the test is right.**
`test_modelgw.py::test_the_package_imports_nothing_outside_the_stdlib_but_httpx` walks the AST of
every `packages/modelgw/*.py` — function bodies included — and requires the third-party import set
to be exactly `{"httpx"}`. U12's plan file list puts a **store-backed** cache at
`backend/packages/modelgw/cache.py`, which needs SQLAlchemy and would fail that test.

Do not argue the test down. It is what keeps `modelgw` a package a keyless run imports for free.
Split the unit the way U9 split its own ledger:

- `backend/packages/modelgw/cache.py` holds the **pure** half — deriving the cache key from the
  assembled prompt bytes, the authorization scope and the purpose namespace (R3). No store, no
  SQLAlchemy, stdlib only.
- The **store-backed** half lives in `backend/services/agents/`, next to `StoreSpendLedger`, which
  is the precedent U9 set for exactly this reason and which U9's own file list sanctioned.

`SpendLedger` is a `Protocol` with a `MemorySpendLedger` beside it, so the cache should follow the
same shape: a protocol in `modelgw`, an implementation in the service.

U9 also left U12 a signature rather than a comment: `note_cache_hit(run_id, served: Completion)`
takes a `Completion` specifically, so a `Failure` **cannot** be recorded as a hit. That is §3 of this
document expressed as a type — a scripted fallback cannot become a cache entry by accident.

**Nothing publishes the `MODEL_SPEND` control frame, and U24 should own it.** U9 built both ends —
the `model_spend` table with a `GET /runs/{run_id}/spend` read on the agents service, and the client
reducer branch plus the HUD tile — but the publisher belongs to the stream that already sends
`POSITION_ECHO`, in `backend/services/gateway/`, which U9 was told not to touch. Until it exists the
tile renders its zero-and-absent state, which is the same state a keyless run shows for its whole
life, so the surface is quiet rather than wrong.

U24 is the right owner: it already composes the gateway and the agents surface into one process, and
it is the only unit allowed to see both. M28 says the HUD "updates during a run", which is not
discharged until the frame is published — so this is part of U24's scope, not a nice-to-have.

---

## What U5 found, that U19 and U24 need

**No authored checkpoint offers six options, so "a six-option comparison" has to be synthesised.**
All nine offer three. `MAX_BRANCHES_PER_COMPARISON` is six, so six is what one command may cost and
six is the width worth defending a floor at — but the plan's U5 scenario and its 1.6s figure cannot
be produced from the shipped scenario. U5's load tests widen `items.ITEMS_BY_ID` for the duration.

Widening `state.dynamic_items` instead does **not** work, and the reason is a real limitation U19
should know about: `snapshot.to_wire` writes eight fields per dynamic item and no `checkpoints`
tuple, so a dynamic item's checkpoints do not survive a snapshot round-trip. A branch forked from a
state holding one comes back with an empty checkpoint tuple and fails on an index. Nothing reaches
that today — hiring items are the only dynamic items and they carry no checkpoints — but a scenario
format that authors checkpoints onto a runtime-created item (U6) would make branch comparison of
that item impossible until `to_wire` carries them.

**The permille metric is confounded in a second way, on top of the truncation U4 recorded.** Because
`wanted = int(elapsed * 36 * rate)` truncates per wake, a run whose event loop is *delayed* loses a
smaller fraction: fewer, longer wakes each throw away less than one tick. So a loaded run can measure
a **higher** observed-against-nominal figure than an idle one — 967-982 permille under sixteen
concurrent comparisons against an idle reference of 908-910. The figure is still directionally right
where it matters (683-744 permille when the clock is genuinely starved), but a unit reading it as a
quality score rather than as a floor will draw the wrong conclusion from a two-percent difference.
`sim_time_lag_ticks` is the unconfounded figure: it counts quanta the clamp refused to run.

**Two things U5 closed that are recorded as accepted risks elsewhere.**
`docs/residual-review-findings/feat-option-consequence-comparison.md` §2 — "a comparison occupies the
worker pool every run's clock depends on" — is discharged by R14, and §1's remaining half (the
retry that never saw the failure mode it was written for) is discharged with it. The README states
the old cost as a limitation and is now out of date on it; **U1 owns the README** and should drop or
amend that sentence.

**`healthy()`'s message text changed**, from "every run with a non-zero rate has a live tick task"
to "…has a clock that is still moving", and `Diagnosis` gained `seconds_since_last_tick`. Nothing
outside `backend/` reads either — the client never fetches `/runs/{id}/diagnose` — but **U24** owns
the status composition and should know the string moved.

---

## The deferred defect register

Pre-existing defects found while executing this plan, none of them in the PRD's M-list, each
deliberately not fixed by the unit that found it. They are collected here so they are a list
somebody can schedule rather than five comments in five files.

**`create_all` runs before the writer lease, and on Postgres it drops the append-only triggers.**
Found by U24 (`2aba425`), and the most serious of these. `LogStore.create_all`'s Postgres path is
`DROP TRIGGER IF EXISTS` followed by `CREATE TRIGGER`, and the ordering in `KernelRuntime.start`
puts it before the lease is taken. So a second launcher — one the lease is *about to refuse* —
briefly drops the append-only guard on a log another kernel is actively appending to. **Measured:
the trigger OID moved 25640 → 25641.** U24 narrowed the window with a pre-flight DDL check but
could not close it: closing it needs table creation to happen behind the lease, which
`KernelRuntime.start`'s own docstring explains it cannot have. Recorded in
`refuse_a_store_this_build_cannot_read`'s docstring. This is a data-integrity window, not a
tidiness point, and it wants its own unit.

**`achieved_multiplier_permille` truncates a fraction of a tick at every wake and never accumulates
it.** Found by U4, confirmed and deepened by U5. At rate 1 the clock runs at roughly 55% of nominal
while the field reports `1000`, and because the truncation is per-wake a *delayed* loop loses
proportionally less — so a loaded run can measure higher than an idle one. Two units now assert
around it rather than on it. Fixing it changes a reported figure, so it wants a unit that can also
re-baseline whatever reads it.

**`export` hashes a live run's whole state without the per-run lock.** Found by U4. The same class
of torn read U4 closed on the command path. Locking alone is not the fix: it reads events from the
store *before* hashing, so making the two agree needs a store read and a state read under one lock,
which R13 forbids. It wants a snapshot-then-read-through-that-sequence design.

**`_echo_position` enqueues onto an `asyncio.Queue` from a worker thread**, which is not
thread-safe. Found by U4, which left it inside the locked region so the echoed tick and position at
least come from one quantum.

**`snapshot.to_wire` writes no `checkpoints` tuple for a dynamic item.** Found by U5. A branch
forked from a state holding a dynamic item with checkpoints returns from `restore` with an empty
tuple and fails on an index. Nothing reaches it today, because hiring items are the only dynamic
items and they carry none — but it constrains U6: an authored catalog must stay on the static path.

**A queued comparison still holds a slot in the *route* pool while it waits.** Found by U5. R14 asks
for the clock to be protected and it now is, but `post_command` is a synchronous FastAPI route, so
40 concurrent comparisons still exhaust the route pool. Making it `async` reaches into the gateway
and the launcher.

**No authored checkpoint offers six options.** Found by U5. All nine offer three, while
`MAX_BRANCHES_PER_COMPARISON` is 6 — so the plan's "six-option comparison" and its 1.6s figure
cannot be produced from the shipped scenario, and U5's load tests synthesise the width. Not a
defect so much as a gap between the plan's numbers and the authored content; U6 or U21 could close
it by authoring a wider checkpoint.

**`frontend/scripts/screenshots.mjs` disagrees with M6.** Found by U2. The harness injects
`WAITING_PERSON = 'dir_sales'` with a synthetic `wi_ap_map` tray entry, but a real day-zero run now
waits on `dir_hr`/`wi_hiring`. Its three checked-in PNGs predate the hints. Whoever owns that
harness should either switch it to the real seed or keep the injection with a note saying why.
