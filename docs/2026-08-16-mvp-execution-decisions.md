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

## What U3 found, that U24 needs

**A command's events are never published to a connected client.** `KernelRuntime._publish` is called
from one place — the tick loop, with the envelopes that batch produced (`services/kernel/loop.py`).
`apply_command` appends through the writer and returns the envelopes to its caller, and nobody
publishes them. Nothing on the client resumes on a gap either: `EventStream.url()` already resumes
from `appliedSeq`, but only a socket *close* triggers a reconnect, so the banner's promise —
"reconnecting will resume from the gap" — is kept by nothing.

Measured on a live run through `single_process.py` and the dev server: `POST /runs/{id}/commands`
answered `produced_seq: [3, 4]`, and the connected client's `appliedSeq` stayed at 2 until the
director's *arrival* forty seconds later, at which point it jumped straight to 6 and set
`sequenceGap`. Both dropped events were the assignment and the walk it caused.

This is why U3's verification is only half a live one. The walk **home** — produced by the step when
the director arrives — travels the real wire and renders: the client interpolates it, the figure
crosses the floor, and the canvas differs on every frame. The walk **out**, which is the delegation
itself and the thing M62 is written about, is produced by the command and therefore dropped. The
event is correct, logged and rendered when injected; the transport does not carry it.

It is deliberately not fixed here, and the reason is not scope alone. `apply_command` runs on a
Starlette worker thread, so calling `_publish` from it would enqueue onto an `asyncio.Queue` from
off the loop — the same thread-safety defect already recorded below for `_echo_position`, which is
not worth a second instance. And the ordering is delicate rather than incidental: `_advance` releases
its lock before appending, so a command's events can already be sequenced ahead of a tick's, and a
publish that ignored sequence order could manufacture the gap it was meant to close. The fix wants
the loop handle and a decision about ordering, which is transport work.

**U24 is the right owner.** It already composes the gateway and the kernel into one process, and it
already owes the publisher for `MODEL_SPEND` for the same reason: the surface exists on both ends
and nothing carries the frame between them. Until it lands, every command a player issues leaves a
permanent sequence gap and its own outcome unrendered — a direct assignment, which produces no
arrival event at all, stays invisible until the item's first checkpoint.

**The screenshot harness's disagreement with M6 is closed.** `frontend/scripts/screenshots.mjs` now
injects `dir_hr` stopped at `wi_hiring` with the label the first `CHECKPOINT_RAISED` of a real
day-zero run carries, and the three PNGs are recaptured — so they also stop predating U2's hints. The
injection *stays*, and the file now says why: the harness runs against a recorded genesis so that
looking at the art needs no Python stack, and a genesis event alone puts nobody in front of a
decision. What it must not do is invent a state the product never reaches, which naming Marcus in
Sales did.

---

## What U10 found, that U11, U12, U14 and U15 need

The plan's Risks section was right: `raise_request` and `receive_answer` were a tested state machine
with no production caller, so most of U10 is construction rather than editing. What is new is the
whole delivery leg — the derivation inside `step()`, the third dispatch branch, the guard module, the
line-scoped retrieval, the kernel's dispatch and its fifth append site, and the launcher's third
handover. What is an *edit* is small: two docstrings, one cap-count helper, and the deadline becoming
per request.

### The numbers, and where they came from

| Constant | Value | Reasoning |
|---|---|---|
| `pend.FASTEST_CLIENT_RATE` | 3 | `frontend/src/ui/Shell.tsx` offers 0, 1, 3. R18 sizes against this, not ×1. |
| `pend.STATEMENT_ANSWER_SECONDS` | 10 | Wall-seconds the *leg* gets, at ×3. A short completion plus its context read plus two thread hops. |
| `pend.STATEMENT_OFFSET_TICKS` | 1080 (2 sim-days) | `10 × 36 × 3`. The landing tick is `raised_at_tick + this`. |
| `pend.STATEMENT_DEADLINE_TICKS` | 2160 (4 sim-days) | Twice the window; four times the shared one-sim-day deadline, which is R18's independence as arithmetic. |
| `sim.STATEMENT_RANGE_MILLI` | 1900 | The client's `OPEN_RADIUS_MILLI`, compared as squared integers because no float may cross the kernel. |
| `pend.MAX_ANSWER_PAYLOAD_BYTES` | 16 KiB | Roughly three times a full statement. See the ordering note below — it is the only bound that can stop bytes reaching the log. |

**The content guard cannot bound what reaches the log, and U11 needs to know why.** `INPUT_RECEIVED`
*is* the statement: it is appended the moment the leg answers, while `stmt.refusal` runs at the
landing tick two sim-days later. So every per-field cap in `simcore.statement` decides whether a
statement *stands*, and none of them decides whether it reaches the log — by the time the guard has
an opinion the row is a permanent fact. `receive_answer` therefore bounds the encoded answer at the
door (`MAX_ANSWER_PAYLOAD_BYTES`). U11's agents-side call site is what makes a rejected statement
never cross the wire at all, which is the only place a bad statement can be stopped before it is
durable.

**A refusal at the door raises; a refusal at the guard is logged. The asymmetry is a replay
requirement.** `ANSWER_REJECTED` is in the fold's *output* set, so the fold expects `step()` to
regenerate every one of them — and it can only regenerate the ones the step derives. A refusal from
`receive_answer` would append an output event with no `INPUT_RECEIVED` beside it, so
`log._apply_input` has nothing to re-issue, nothing regenerates, and `_expect_exhausted` fails the
strict comparison on a log that is a faithful record. Measured: all three of the door refusals
produced `ReplayDiverged` before this was fixed. So they raise `CommandRejected` — which mutates
nothing and appends nothing, exactly as `compare_options` does with
`MAX_COMPARISON_PAYLOAD_BYTES` — and they are checked on the *live* path only, because on replay the
answer is already a logged fact. **U11 must not add an `ANSWER_REJECTED` to a command path.** The
refusals that may be logged are the ones `_apply_statement_answer` emits at the landing tick, inside
the step.

**The budget is the leg's, not the provider's, and U11 has to fit inside it.** `modelgw`'s shipped
`DEFAULT_TIMEOUT_SECONDS` is 30 and `CONNECT_TIMEOUT_SECONDS` is 5, so a bench that waited out a
provider timeout before falling back would answer at ~35 wall-seconds and miss a 10-second window.
R5 already says a timeout goes to the scripted reply; U11 must configure the bench's own timeout
*inside* `STATEMENT_ANSWER_SECONDS`, not beside it. A late answer is refused with a logged reason
(`ANSWER_REJECTED` carrying `late: true`), never applied at whatever tick it arrived at.

### Two clarifications against §2, neither a contradiction

- §2's table says a paused run lands the answer at "the current tick". It is implemented as
  **`state.tick + 1`**, because `step()` increments the clock before it applies queued answers, so an
  answer filed at the tick the run is paused at is never popped. The first tick a paused run reaches
  when it resumes is that tick plus one, and it is equally a player-determined tick.
- The **other two legs keep the tick they have always landed at**. R17 and the plan's decision are
  both phrased about a statement, the shipped domain model is arithmetic in-process, and the shipped
  resolver declines — so neither carries the latency R17 is about, and moving them would change a
  period's metric-application tick for no requirement.

### The discriminator is `service`, and it had to be

R1 wants a distinct request *kind*. `PendingRequest.to_state()` is inside the `pending` subsystem of
the state hash, and R27 makes any `to_state()` shape change a `STATE_SHAPE_VERSION` move — which the
plan's System-Wide Impact reserves for Authorization. So `service` gains a third value, `pend.BENCH`,
read as "which leg answers this" rather than "which process". `PendingRequest.is_statement` is the
predicate; `_apply_queued_answers` has three branches; `STATE_SHAPE_VERSION` is still 1.

The same constraint is why the "already briefed" memory lives in `state.pending` rather than on the
item. The cost is asserted rather than hidden: a CEO who stands at one desk *past* the landing tick
without settling the checkpoint is briefed a second time, bounded at three by the per-item cap
(`test_standing_at_a_settled_checkpoint_past_the_window_asks_again`). **U15 should fold that flag into
the state-shape move it already owns** — it is one field on `ItemRuntime`.

### The guard module, and exactly what U11 adds to it

`backend/packages/simcore/statement.py`. Two call sites, one implementation (§1). The kernel's is
`step._apply_statement_answer` → `_statement_refusal` → `stmt.refusal`, at the landing tick, so the
verdict is an output event the fold regenerates and is therefore re-derivable from the log.

    Authorized(director, people: frozenset[str], items: frozenset[str])   # refuses an empty scope
    authorized_for(scenario, director_id, *, line_members, line_items) -> Authorized
    StatementRequest(run_id, request_id, person, owning_item, cp_index, raised_at_tick, authorized)
    Statement(briefing, objection, citations, producer, producer_kind, model_identity, context)
    refusal(answer: Mapping, *, authorized: Authorized) -> str      # "" means acceptable
    is_statement_answer(answer) -> bool

**U11 adds M18's ranking predicate and M19's figure predicate into this module and calls `refusal`
from `bench/guards.py`.** It must not write a second copy of any predicate — that is the entire
reason the module is in `packages/`. The agents-side call site is the cheap rejection; the kernel's is
the auditable one. `services/agents/main.py::compose_statement` is the seam U11 fills: it returns
`(briefing, objection, citations, model_identity, producer_kind)` or `None` to decline, and U10 ships
it returning `None`, which is why a U10-only build plays exactly as it did before.

**U15 constrains `Authorized` rather than replacing it.** A grant widens `people` and `items`; that is
why they are sets rather than a single line id. There is exactly one derivation of a scope —
`step._authorized_scope` — and the request *carries* it, so no leg computes its own.

### R23 is a three-link chain, and each link is checkable from the log alone

The request records the scope; the answer records the retrieved context; the citations point into the
context. So `refusal` checks that every context entry names a person or an item inside the scope, and
that every cited sequence is in the context. A director can only cite what it was shown. The scope is
**re-derived from folded state** at the landing tick rather than read off the payload, and it is
derived from the *owning item's* line rather than from the producer the answer names — deriving it
from the producer would make the attribution check compare a value to itself.

`backend/services/agents/bench/context.py::retrieve` is the only door onto the log in `bench/`, and it
cannot be called without an `Authorized`. `RETRIEVABLE` is an explicit per-kind table naming which
payload keys hold a person and which holds an item; a kind nobody listed is never retrieved, and an
entry naming neither a person nor an item in scope is dropped — including a company-wide one, because
a company-wide fact is not this line's either. `LOOKBACK_TICKS` is three sim-days and `MAX_EVENTS` is
`stmt.MAX_CONTEXT_EVENTS`, so a leg cannot assemble a context the kernel will then refuse for size.
**U14's director memory reaches further than this on purpose** — a statement's working set is shorter
than a memory — and it should reuse `Authorized` rather than inventing a second scope type.

### The statement request id is not run-scoped, and U16 owns closing it

`pend.statement_request_id` derives from `(director, item, cp_index, tick)` — all state — because
`State` carries no run id. So a parent and a fork standing at the same checkpoint at the same tick
mint the *same* id, which `test_a_fork_at_the_same_tick_mints_the_same_request_id` now asserts rather
than leaves to be discovered. `_raise_period_consult` has had the identical property since Phase 1;
what is new is that a statement's answer is a provider's prose rather than in-process arithmetic.

It is not reachable through the kernel: an answer names the run it is for and
`deliver_statement` looks it up in *that* run's `pending`, so nothing routes one run's answer to
another. Pre-divergence it is also harmless, by §3's own argument — the two runs *are* the same run.
Closing it properly needs a run identifier the fold reproduces, which is a `State` shape change, and
it belongs with **U16**, the unit that owns fork identity (R10).

### For U12

The cache key wants "the authorization scope the context was drawn under" (R3). That is
`Authorized.to_payload()`, which is already sorted and canonical-encodable, and it is already recorded
on `REQUEST_RAISED` — so the key can be derived from the log rather than from live state. A fallback is
still never cached (§3), and `produce_statement` returning `None` is the fallback's shape here.

### What this unit touched outside its stated file list

- `backend/single_process.py` gains `_wire_the_bench(runtime)`, one call beside
  `_publish_model_spend`. The kernel may not import the agents service, so the producer is installed
  from outside both, exactly as the kernel client and the spend reader are.
- `backend/packages/simcore/scenario.py`: `_control_character` became **`control_character`**, one
  rename and one call site. §1 forbids a second copy of a predicate, and `simcore.statement` applies
  the identical Unicode-category rule to generated prose. `statement.MAX_PROSE_CHARS` is now
  `sc.MAX_PROSE_CHARS` for the same reason — two 512s are two numbers somebody can move
  independently, and the symptom would be an authored fallback line the guard refused.
- `backend/tests/test_kernel_service.py`'s append-site count moved 4 → 5 (`deliver_statement`), and
  `"complete"` and `"_statement_producer"` joined the forbidden-inside-the-lock set, which is what
  that test's own docstring asks a unit adding a provider call to do. `test_gateway.py`'s `composed`
  fixture also clears the statement leg's per-process log engine, for the reason it already clears the
  spend ledger's, and gains the end-to-end test over `compose()`.
- `backend/tests/test_contracts_generated.py`: the bidirectional-stream test now covers
  `DirectorBench.Brief` beside `AgentResolver.Resolve`.

### One more thing U11 must not assume

**The retrieved context's window is closed at both ends, and the upper end is `at_tick` exclusive.**
Retrieval runs on a worker thread an arbitrary interval after the request was raised, while the tick
loop keeps appending — so a window that ended at "the log as it stands" would make the *logged*
context a function of provider latency, and two fresh runs from one seed would carry different
evidence for the same briefing with nothing comparing unequal until somebody read the two reports
side by side. Exclusive rather than inclusive because the raising tick is not closed when the request
is raised: a command applied at the same tick lands after the step's events.
`test_the_retrieved_context_does_not_move_with_how_long_the_leg_took` fails if the bound is removed.

`diagnose().outstanding_requests` is no longer the empty list the plan noted: it reports the leg, the
item, the director, ticks remaining and whether the bench was ever asked — which is also how the
per-item cap becomes visible rather than merely inferable.

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

**A command's events are never published to a connected client.** Found by U3, and now the most
serious of these: it makes every command's own outcome invisible to the client that issued it, and
leaves a permanent sequence gap behind. `_publish` is called only from the tick loop.
See "What U3 found, that U24 needs" above for the measurement and for why the fix is transport work
rather than a line.

**`receive_answer`'s duplicate-answer rejection makes a log unreplayable.** Found by U10 while
closing the same shape in its own new branches. `ANSWER_REJECTED` is an output kind the fold expects
`step()` to regenerate, and the `request is None` branch of `receive_answer`
(`backend/packages/simcore/step.py`) emits one from a *command* path with no `INPUT_RECEIVED` beside
it — so nothing re-issues it and `_expect_exhausted` fails. **Measured: a log holding one answer and
one duplicate folds to `ReplayDiverged: at tick 542 the log holds 1 output events the replay did not
produce (['ANSWER_REJECTED'])`.** Reachable by any duplicate delivery, which R25 treats as the
ordinary case.

U10 deliberately did not fix it, and the reason is that the obvious fix is worse: raising instead
would break the *fold*, because `log._apply_input` re-issues `receive_answer` for a logged
`INPUT_RECEIVED` whose request is already gone and would then raise mid-fold — taking the report down
with it. Closing it wants either a second refusal kind outside `OUTPUT_KINDS`, or an idempotent
`_apply_input` that recognises an already-applied answer. U10's own branches avoid the hole by
raising `CommandRejected` on the live path only; see "What U10 found" above.

**`frontend/scripts/screenshots.mjs` disagreed with M6.** Found by U2, **closed by U3**: the harness
now injects `dir_hr`/`wi_hiring` with the label a real day-zero raise carries, the injection is kept
with the reason stated, and the three PNGs are recaptured.

---

## U6 is two passes, and this is the handoff between them

U6 was split after two API deaths at the same point. **Pass 1 shipped as `1a01b44`**: the format, the
loader, `default.toml`, `schema.md` and 98 tests. Nothing imports it yet, which is why it moved no
fixture — the plan couples the data move to the schema extension to avoid regenerating golden
fixtures twice, and a loader with no callers regenerates them zero times.

**Pass 2 is the wiring**, and it is still one change: state-parameterise the roster and catalog,
put the scenario identity on the genesis payload, bump `KIND_SCHEMA_VERSIONS[GENESIS]` 4 → 5
**once**, install the guard at three sites, copy `scenarios/` in the `Dockerfile`, route
`generate_golden.py` through the loader, and regenerate the fixtures a single time.

### The seam pass 1 built

```python
load(name=DEFAULT_SCENARIO, *, directory=None) -> Scenario
load_default() -> Scenario
resolve(name, *, directory=None) -> Path        # name rule only, zero filesystem access
available(directory=None) -> tuple[str, ...]    # U7's chooser, and the unknown-name refusal
parse(raw, *, name, origin="") -> Scenario      # no filesystem at all

load_recorded(identity, *, at, directory=None,
              recorded_roster=None, recorded_catalog=None) -> Scenario
verify_unchanged(loaded, *, at, directory=None) -> Scenario
```

`new_run` takes `scenario: Scenario | None = None` defaulting to `load_default()`, so all 42
existing call sites keep working. The three guard sites are `log._apply_genesis` (from-zero fold),
`snapshot.from_wire` (snapshot restore) and `log.fold(..., resume_from=...)` (the path that skips
genesis). `at` is threaded into the message so three guards do not produce one indistinguishable
string.

`State` gains one field. `sim.snapshot()` — the only input to `hashing.state_hash` — omits it, and
`state_hash` refuses an undeclared subsystem, so the omission is **enforced rather than
remembered**: adding it would require a `SHAPE_HISTORY` entry and a deliberate shape bump.
`Scenario.__deepcopy__` returns `self`, so `log._clone`'s `deepcopy` on a resume does not copy the
company. `SNAPSHOT_FORMAT_VERSION` bumps 1 → 2 to carry the identity, which is a documented
drop-and-refold already covered by the existing format-version refusal.

### Exact one-line changes outside U6's stated file list

- `backend/packages/simcore/compare.py:581` — `work.ITEMS` → `state.scenario.items`; the
  `from simcore import items as work` import then becomes unused.
- `backend/services/report/fold.py:242` — `lifecycle.decision_supply()` reads
  `work.TOTAL_CHECKPOINTS` and has `state` in scope, so it becomes
  `decision_supply(state.scenario)`.
- `backend/services/kernel/loop.py` needs **nothing**: `new_run` defaults, and
  `snapshotting.restore(instant)` keeps its signature because the snapshot now carries the identity.

### Two latent `KeyError`s pass 2 should close while it is in there

`PersonRuntime.rank` (`step.py:187`), `roster.spec(person.id).name` (`step.py:1200`) and
`roster.spec(person_id).mgr` (`step.py:1678`, `:1994`) all raise for an **arrived hire** — someone in
`state.people` who is not on the authored roster. Nothing reaches them today only because no test
assigns work to a hire. State-parameterising these lookups is the moment to make them
`state.rank_of` / `state.name_of` / `state.manager_of`, with a hire falling back to `"staff"`, their
own id, and their line's director — the last of which reproduces today's value exactly for every
authored person.

And `request_hire` hardcodes `want="stf_rec"` and `dept="hr"` (`step.py:1815-1820`), which are
**default-scenario person ids** sitting in code. Pass 2 must derive the recruiter from the scenario:
the `hr` line's first non-director in roster order, falling back to its director.
`Scenario.room_of_line` already exists for the `hiring.target_room_for` half.

### Sequencing

**Pass 2's fixture baseline is U3's output, not `3f4a167`.** U3 added `EventKind.STAFF_MOVED = 60`,
a new `time.walk_position_milli` golden vector (`walk.json`), and regenerated logs. Pass 2 must land
after U3 and rebase its regeneration onto it. U3 correctly left `KIND_SCHEMA_VERSIONS[GENESIS]` at 4
— that bump remains pass 2's, and remains exactly one.

### Two things for other units

- **U7** can lift `MINIMAL` from `test_scenario.py` — a four-person, one-item, two-option scenario
  that loads clean — as the second shipped scenario or as the fixture for "a second scenario
  produces a different company".
- **U11 (R19)** inherits the loader as the length cap and the character rule, and should not
  re-derive either: `MAX_PROSE_CHARS = 512`, `MAX_SHORT_PROSE_CHARS = 256`, `MAX_TITLE_CHARS = 96`,
  `MAX_LABEL_CHARS = 48`, `MAX_TAG_CHARS = 64`, `MAX_TAGS = 12`, ids `[a-z][a-z0-9_]*` ≤ 48, and
  every authored string rejects any Unicode category beginning with `C`. Authored copy is therefore
  always one line of printable prose, and U11 can delimit it as data on that basis.
- **The six-option gap is now closable by data alone.** `MAX_OPTIONS_PER_CHECKPOINT = 6` is validated
  at load and a test asserts it equals `step.MAX_BRANCHES_PER_COMPARISON`, so authoring a six-option
  checkpoint is a pure `default.toml` edit. Pass 1 deliberately did not do it: it would change the
  catalog, the genesis payload and the content hash as *content*, blurring the "diff limited to the
  recorded genesis-payload change" statement pass 2 has to make. **Recommended for U21**, after
  pass 2's fixtures settle.

### One clarification against the plan's wording

The plan says the loader validates "in one pass". It is one pass over the file but two *phases* —
field shape, then cross-references — because a cross-reference check reading a room id that failed
its own type check would report an invented failure about the real one. A phase-one refusal says so
explicitly, so an author who fixes the first batch is not surprised by a second. Nothing is
constructed in either phase.

---

## The command-publish defect, closed — and two holes beside it

Fixed in `a69c304`, as its own unit rather than deferred, because it sat under every remaining unit
that derives an event from a command: U10's statement answers, U15's authorizations, U16's forks,
U25's decisions panel. Four more units would have passed their tests and not worked live.

**It reached further than the register recorded.** `set_rate`'s `RATE_CHANGED` and `create_run`'s
`GENESIS` were never published either — found by following the "every append publishes" invariant
rather than by looking for them. `RATE_CHANGED` has had a live reducer branch at
`frontend/src/net/store.ts:792` that **nothing had ever reached**, so a client's rate only ever came
from its initial resume.

The invariant is now structural rather than habitual: a test walks `loop.py`'s AST and requires every
function that submits to the writer to also publish. That is why `_advance` publishes each quantum
beside its own append instead of returning a batch for its caller to publish — a return value a
caller must remember to pass on is a rule, and `_advance` has callers outside the tick loop.

### Two holes it did not close, and one thing it enables

**A connect can still lose events.** `backend/services/gateway/main.py:361-373` sends the resume
backlog and *then* subscribes, so anything appended between the backlog read and the subscribe is
delivered by neither. The publish fix neither creates nor closes this. It wants the subscribe to
happen first and the backlog to be filtered against what the subscription already delivered.

**Nothing auto-reconnects on `sequenceGap`.** The client detects a gap and shows its banner, and the
server-side remedy exists (`read_events(after_seq=…)` returns the lost sequences), but recovery waits
for the *next* reconnect rather than happening immediately. So the publish-failure path — bounded at
`PUBLISH_HELD_BACK_BOUND = 256`, which converts a permanently silent stream into a detectable gap —
leans on a recovery that is not automatic.

**`_echo_position` is now correctable, and deliberately still not corrected.** Its register entry
stands, but the two hard parts are now present: the loop handle is recorded, and there is a proven
thread-crossing pattern. What remains is a control-frame variant of the crossing — a `POSITION_ECHO`
carries no sequence, so it must *skip* the ordering rather than go through it — plus the call-site
swap. The payload is already assembled inside the lock, which is the part that had to stay.

### An orchestration note worth recording

This agent was told the tree was its own, and it was not: U6 pass 2 was live in the same checkout,
edited the same test file, and committed mid-task, moving the agent's baseline HEAD. It absorbed that
by taking every measurement in a throwaway worktree at the old HEAD, which is the right instinct, but
it cost effort and a false suspicion of its own change. Two agents in one checkout need their file
sets checked against each other even when one of them is "just" a fix.

---

## What U7 found, that U11 and U14 need

U7 threaded a scenario *name* through run creation, shipped a second company, and put a person's
authored schema on the conversation surface. The genesis payload did not change, so no golden
fixture moved and `frontend/tests/golden.test.ts` was not touched.

### The tripwire U6 left fired, and what replaced it

`test_the_default_scenario_is_the_only_one_the_directory_offers_yet` asserted `available() ==
("default",)` with a docstring saying a second file was U7's. It failed the moment `ashcroft.toml`
landed, which is exactly what it was for. It is now two tests: one naming both shipped companies,
and one **parametrised over the directory** so that a third file is validated by the suite the
moment it lands. A list of names written in the test would have been the one piece of code that
adding a company still required — which is the claim M13 makes.

### The gateway must not learn to recognise a loader exception

`ScenarioNotFound` is a `simcore` type, and the gateway does not import `simcore` — it talks to a
`KernelClient`, which is what keeps R4 an import rule rather than an intention. So the in-process
client translates the refusal to `ValueError`, which the protocol already documents as "a request
the kernel cannot serve", and the creation route's existing `except (TypeError, ValueError)` turns
it into a 400 carrying the loader's own sentence — the one that lists the names that *do* resolve.
`scenarios()` is on the protocol for the same reason: "which companies exist" is simulation
knowledge that merely happens to be answerable from a directory listing.

### A third marking exists now, and U11 has to use it

M16 says the tools, MCP servers and skills are description and nothing is executed. Rendering them
unmarked would state the opposite, so there is a `Described` marking beside `Mark` and `Measured`,
under a **third** attribute, `data-described`.

Not a reuse of either existing one, and the reason is the completeness sweep: `hud.test.ts`
requires exactly one of `data-authored-tuning` or `data-measured` on every figure. A marking that
shared an attribute would let a tool list satisfy a rule about numbers, or a number satisfy a rule
about descriptions — which is precisely how a completeness claim rots. **When U11 renders what a
director claims it could do, it is the same marking**: the claim is about a capability, and the
capability is still description.

### The catalogue loads every file, and says so when one will not load

`GET /scenarios` parses each file rather than only listing names, because the picker shows a title
and a summary and a name alone is not a choice a person can make. A file that refuses is returned
with `loadable: false` and its reason rather than filtered out — one bad file cannot hide the good
ones, and the author of the bad file is usually the person reading the list. The client preselects
the shipped company where it loads and otherwise the first one that does, so the button never opens
by submitting a name the server has already said it will refuse.

### Two things that cost time and are worth knowing

- **`GET /runs/{id}/state` returns the run *row*, not the state.** Status, tick, rate, head
  sequence. The roster is in the genesis event, so a test asserting which company a run is of reads
  the log — and should, since what matters is that the choice reached *the wire*.
- **jsdom has no usable `WebSocket`, and creating a run swaps the page for the office.** Any test
  that drives `App` through a successful creation has to stub the global, or every assertion about
  the request fails on what happened after it. `tests/app.test.ts` has the stub; `stream.ts` keeps a
  `makeSocket` seam for the same reason.

### `uv run` on this machine rewrites `uv.lock`, and R8 forbids that

The `uv` on PATH here is **0.4.22**; the Dockerfile pins **0.9.17**. The older one writes a
revision-less lockfile with no `upload-time` fields, so a single `uv run pytest` rewrites 454 lines
of `backend/uv.lock` — same versions, same resolution, purely a format downgrade — and R8 says a
unit leaves the lockfile alone. It was reverted with `git checkout backend/uv.lock`, and the suite
was re-run through `backend/.venv/bin/python -m pytest` to confirm it stays clean.

**Check `git status backend/uv.lock` before committing.** Every unit executed on this machine has
the same trap, and the diff is large enough to be waved through as noise. Running the suite through
the venv directly avoids it entirely.

### One live observation, not confirmed as a defect

Driving the client through browser automation, every movement key produced `input is for tick N,
which is not in the future (now M)` and the CEO did not move — the client's predicted tick trailing
the kernel's by hundreds. **It reproduces identically on the shipped company**, so it is not U7's
and not scenario-related. The likely cause is the automation's tab being unfocused and its
`requestAnimationFrame` throttled, which would starve the render clock the input tick is derived
from — an artifact rather than a product defect. It is recorded because U11 and U14 will both drive
the client live, and because if it *does* reproduce in a focused tab it is a serious one.

---

## What U11 found, that U12 and U13 need

U11 filled the prose seam: the personas, the prompt, the provider call, M18's ranking predicate and
M19's figure predicate, the single fallback exit, and the client's pending block. Both suites are
green — backend 1,095 → **1,127**, client 479 → **491** — and no event payload, schema version or
golden fixture moved.

### The plan's file list was wrong twice, and both were already known

- **`bench/guards.py` does not hold the guards.** §1 of this document settled that months of drift
  ago: the predicates live in `packages/simcore/statement.py`, which both services import and neither
  reimplements. `guards.py` is the agents-side *call site* — it calls `stmt.refusal`, maps a
  `FailureKind` to a logged reason, and owns the single fallback constructor. It writes no predicate.
- **`services/agents/stub.py` needed nothing.** It is the Phase 1 *resolver* stub (`NEVER_ANSWERS`)
  and has no connection to statements. M14's "opening one with a specialist produces the scripted
  reply" is the Phase 2 `voice`/`deflection` conversation, which has shipped since `ae557fd`. The
  specialist case U11 does own is the *refusal* — a request naming a non-director is declined on both
  sides — and that is in `main.py` and `step.py`.

### The offer is re-derived, not carried, and that is a deliberate break with the scope

`stmt.refusal` needed the checkpoint's option labels — M18 refuses a statement for *preferring* one —
and its authored figures, which is half of what M19 resolves against. The obvious move was to put
them on the `REQUEST_RAISED` payload beside `scope`, since `statement.py`'s own docstring argues that
scope is *carried* so a leg cannot widen it.

It is not the same case, and the difference is worth writing down. A scope is state-dependent and is a
*permission*; a checkpoint's options are immutable authored content already sent to every client in
the genesis catalog. There is nothing a leg could widen in its own favour. So `stmt.Offered` has **two
adapters in one file** — `from_checkpoint` off folded state for the kernel, `from_catalog` off the
genesis payload for the leg — and `REQUEST_RAISED`'s payload did not move, which keeps this unit off
the replay surface entirely.

**The two adapters disagreed, and the test that compares them is the one thing here worth copying.**
`from_catalog` admitted `0`, because `catalog_to_state` splits the draw out of `effect` and writes
`draw_delta: 0` where an option does not move it, while the state's `effect` simply has no key. A
director could have written "0" and had it resolve. It is caught by
`test_the_two_readings_of_a_checkpoint_agree_on_every_shipped_company`, parametrised over
`sc.available()` and asserted against each company's own `total_checkpoints` — so a third company is
covered completely by being added.

### Five decisions, each of which the next unit could otherwise reopen

1. **The guards are lexical over a closed vocabulary, and they have to be.** The refusal they produce
   is an `ANSWER_REJECTED` output event the fold regenerates on every replay, so a predicate that
   consulted a model would make the *record of a refusal* irreproducible. `RANKING_PHRASES` is
   therefore a stated boundary rather than a completeness claim: what it buys is that the obvious ways
   to rank are refused deterministically and the prompt is told not to try. What escapes it is caught
   by a CEO reading a briefing that argues for one option, which is a product problem rather than a
   silent one.
2. **`not_configured` is not a fallback reason, and its absence is M20.** A run with no key raises the
   request and lets it reach its deadline, exactly as a U10-only build did. The client gates the whole
   block on `spend.benchPresent`, which arrives on the first `MODEL_SPEND` frame — long before the CEO
   can walk to a desk — so "the conversation is the Phase 2 conversation exactly" is literally true
   rather than nearly true. `test_bench.py` asserts the two closed sets differ by exactly that member
   and by `guard_refused`.
3. **The fallback prose is this repository's, not the scenario's.** Each person carries an authored
   `deflection`, which would have given each director their own voice for a turn the bench could not
   answer. It is unreachable: `roster_to_state` does not carry `deflection`, so putting it on the wire
   would move the genesis payload — and the genesis payload is what every golden fixture in two
   languages is taken from. Flavour is not worth regenerating the fixture set, and the honest reading
   is that a fallback is not the director speaking at all.
4. **A derived figure is not a shown figure.** A statement converting `load_permille: 550` into "55%"
   is refused. That is the intended reading of M19 rather than a limitation of it: a figure the CEO
   cannot resolve back to a row is a figure the report cannot either.
5. **Digits, not words.** `"two weeks"` is prose and `"2 weeks"` is a figure. The line is at the
   numeral because that is where every other figure in this product is drawn, and because a guard that
   resolved spelled-out counts would refuse sentences the shipped company already contains — its own
   authored copy says "the listing goes dark for two weeks".

### A fourth marking exists now, and it is the strongest of the four

`data-scripted`, beside `data-authored-tuning`, `data-measured` and U7's `data-described`. Its own
attribute for exactly the reason U7 gave for the third: the sweeps over each are supposed to be
unsatisfiable by the others. It is the strongest claim of the four and the one whose absence would
mislead most — the other three qualify something a reader can see is a figure or a list, while this
one says that a paragraph which reads like a director's considered view is not one. `withLabel`
defaults to *on* for the same reason.

`chrome.test.ts` asserts the bench block spends no hue in any of its four states, so a scripted reply
stays distinguishable from a briefing with every colour removed.

### Two things the plan's approach section says that are not quite right

- **"The settle action stays enabled throughout."** It is disabled until an option is picked, which is
  Phase 2's rule and has nothing to do with the bench. The honest claim, and what the client suite
  asserts, is that *the bench never disables it*: picking an option with a pending block showing
  enables the button.
- **The tacit line stays out of the prompt.** `prompts.build` takes a `tacit` parameter and discards
  it, with the reason at the call site: the tacit line is the in-person reward and it is delivered to
  the *player*, so spending it on a model would be paying the reward to the wrong party. It is a
  parameter rather than an absence so the decision is visible where somebody would otherwise add it.

### What U12 inherits

- **The assembled prompt exists now**, which was the whole of why U12 could not start: `prompts.build`
  returns a `modelgw.Prompt`, and its digest is the input U12's key derivation is content-addressed on.
- **There is exactly one provider call site.** `guards._completed` is the only place
  `BoundedGateway.complete` is reached from the bench, so the cache lookup wraps one function rather
  than being threaded through the leg. `note_cache_hit` is still unused and still typed so a `Failure`
  cannot be recorded as a hit.
- **`Offered` is not part of the key.** The prompt already contains everything the offer describes, so
  hashing both would be hashing the same authored content twice.
- The fork half of M33 is still unreachable until U16: `fork_run` sets the child's `lineage_root_id` to
  its own id, and `test_modelgw.py` shows the `UPDATE`-it-directly workaround U9 used.

### What U13 inherits

- `RANKING_FIXTURE` and `UNCITED_FIXTURE` in `tests/test_bench.py` are the two canned provider replies
  its CI assertion rests on — one that ranks, one that quotes a figure resolving to nothing — kept as
  named constants precisely so a keyless job can assert the guards fire.
- **The whole bench suite runs with no provider configured.** Every test uses `httpx.MockTransport`
  through `test_modelgw`'s harness, so the keyless job covers the bench rather than skipping it.

### Two things that cost time and are worth knowing

- **A test can pass for the wrong reason and read as if it proved something.** The first version of
  "a tick resolves to its sim-day" used tick 540, which is day 2 — and both `1` and `2` are authored
  option deltas on that checkpoint, so it passed on the option figures while claiming to prove the day
  resolution. Moved to tick 2160, which is day 5, a number nothing else on that checkpoint supplies.
- **U10's `statement_answer` fixture had to change meaning, not just fields.** It built a
  `PRODUCER_SCRIPTED` statement because U11 did not exist yet. Scripted now means one specific thing —
  a fallback standing in for a briefing that did not arrive, naming the condition that fired — so the
  fixture is a model's statement, which is what those tests were always about.

---

## Where this stopped, and the order to resume in

Twelve of twenty-five plan units, paused by decision with the tree clean and both suites green. Status
per unit is in [`docs/2026-08-16-progress-checklist.md`](2026-08-16-progress-checklist.md); this is
only the sequencing, because the dependency graph is no longer the plan's phase order.

**Unblocked right now, and mutually disjoint enough to run in parallel:**

- **U13** (continuous integration for the keyless path) — no longer blocked on anything. U11 shipped
  the bench suite, every test in it runs against `httpx.MockTransport` with no provider configured, and
  two named fixtures (`RANKING_FIXTURE`, `UNCITED_FIXTURE`) exist for the assertion that the guards
  fire. All three jobs the plan describes can be written today.
- **U12** (caching on the situation) — unblocked by U11. The assembled prompt exists, and
  `guards._completed` is the single provider call site the lookup wraps. Read "What U12 inherits"
  above before starting: the plan's file list puts a store-backed cache inside `modelgw`, which
  `test_modelgw.py` forbids, and the fork half of M33 stays unreachable until U16.

  > Closed. See *What U12 found*, below. Both warnings held: the pure half is in `modelgw` and the
  > store-backed half beside `StoreSpendLedger`, and the fork half of M33 is written, tested and
  > inert until U16 copies a parent's lineage root.
- **U16** (persistent forks) — file set is disjoint from both. It also inherits two findings: U9
  left `lineage_root_id` set at creation needing only the parent's root copied at fork, and U10 found
  `statement_request_id` is not run-scoped, so a parent and a fork at one tick mint the same id.
- **U14** (director memory and the CEO's reading surface) — writes `test_bench.py`, which now exists
  and is 73 tests, so check the file's own sections before adding to it. Its `Panels.tsx` half
  collides with nothing: U7 put the person's schema on the conversation rather than in the rail, and
  U11's bench block is its own section below the decision card. **Reuse `stmt.Authorized` rather than
  inventing a second scope type**, and reach further back than `context.LOOKBACK_TICKS` on purpose —
  a statement's working set is shorter than a memory.

**Then:** U15 behind U14, and U17/U18/U19/U25 behind U16. Phase F last, as the plan has it.

**The one thing overdue against the plan's own reasoning is still U13.** "The path most users take is
the path CI proves" — and there is still no `.github/workflows`, so nothing verifies the keyless path
or the first command. It has no excuse left: U11 was its last dependency. The `tsc` failure fixed in
`2c38686` — a type error that sat in the tree while `npm test` stayed green, because vitest does not
typecheck — is what the absence costs, and U11 ran `npx tsc --noEmit` by hand for exactly that reason.

> Closed. See *What U13 found*, below — and the cost this paragraph predicted had already been paid a
> second time: there was another `tsc` failure sitting in the tree when U13 first ran the type check.

### Orchestration notes worth carrying forward

- **Five agents died mid-response on oversized tool calls**, one of them twice at the same point.
  Instruct implementers to build large files in successive edits rather than one write. U6 was
  eventually split into two passes for this reason, and the split turned out to cost nothing the plan
  cared about: its reason for coupling the data move to the schema extension was to avoid
  regenerating golden fixtures twice, and a loader with no callers regenerates them zero times.
- **Two agents in one checkout need their file sets checked against each other**, including when one
  of them is "just" a fix. Twice, two agents ended up in one test file; both times it was
  recoverable by staging a single hunk, but both cost a false suspicion first.
- **The most valuable thing a unit produced was usually not its deliverable.** Ask implementers for
  the defects they found and deliberately did not fix, and for the plan claims that turned out to be
  wrong — U2 disproved R16's expectation, U5 found the metric U5's own stated verification rests on is
  near-insensitive, and U10 found four bugs by reviewing its own work.

---

## What U13 found, that U12, U14 and U20 need

CI exists: `.github/workflows/ci.yml`, four jobs, no secret of any kind. The keyless suite on both
store dialects, the bench against the mock adapter, the client's four steps with the type check
separated out, and `docker compose up` from a clean checkout reaching a client that creates a run.
Backend 1,127 → **1,132** tests; the client stayed at 491.

### It found the failure it was built to find, in its first minute

`npm test` was green at 491 and `tsc -b` was failing:

```
tests/app.test.ts(77,15): error TS1294: This syntax is not allowed when 'erasableSyntaxOnly' is enabled.
```

A `constructor(readonly url: string)` parameter property in `e796ebf`'s `SilentSocket` — parameter
properties are not erasable syntax, and vitest transpiles without checking. This is the **second**
instance of the class: `2c38686` fixed the first, and U11 ran `npx tsc --noEmit` by hand precisely
because it knew the gap was there. The fix is three lines; the point is that two units in a row shipped
into a tree where the only guard was somebody remembering. It is a CI step now.

### The smoke job's first draft asserted a 404, and only running it said so

`GET /api/runs/{id}/spend` does not exist. The gateway keeps the root because the client's proxy maps
`/api/` onto `/`, and every other surface is mounted under a prefix — the spend counter is at
`/agents/runs/{id}/spend`, so through nginx it is `/api/agents/runs/{id}/spend`. The assertion was
written from the route decorator in `services/agents/main.py` and was wrong because that file does not
know its own mount point.

**For U20 and U14:** the same trap is waiting. The report is at `/report/...` and anything U14 mounts
gets a prefix too; `SURFACES` in `single_process.py` is the only place the full path exists. Assert a
new surface's path against a running launcher, not against its decorator.

### `--locked` is now the thing that keeps the lockfile honest

This document already records that an older `uv` on PATH rewrites 454 lines of `backend/uv.lock` on
any `uv run`, and that R8 forbids it. It fired again during this unit and was reverted the same way.
CI installs uv **0.9.17** — the version `backend/Dockerfile` uses — and runs `uv run --locked`, which
neither resolves nor writes, and which additionally fails the build if `pyproject.toml` has moved and
nobody re-locked. Verified against 0.9.17 fetched to a scratch directory, since the `uv` on this
machine is still 0.4.22. **The local trap is unchanged**: check `git status backend/uv.lock` before
committing, or run through `backend/.venv/bin/python -m pytest`.

### A skip is not a pass, and the store suite is where that bites

`test_store.py` skips its Postgres half when the store is unreachable, and 43 tests skipping is not
visible in a green summary line. The keyless job publishes the store and then asserts that
`test_postgres_is_reachable_for_this_suite` **passed** rather than skipped — pytest has no flag that
says so, hence the `case` on its summary. Checked in both directions: it passes with the store up
(84 store tests, both dialects) and fails with it down.

**On this machine the store suite's Postgres half cannot run at the documented port.** `avater-db`
from another project holds `127.0.0.1:55432`, so `docker compose -f docker-compose.yml -f
docker-compose.test.yml up -d postgres` fails to bind and the suite skips exactly as it did before.
`COMPANY_OS_TEST_POSTGRES_URL` is the way out — the suite reads it — and it is why U6's verification
has probably been reported from a SQLite-only run more than once.

### What CI still does not prove

Named so nobody reads the badge as covering them:

- **The second boot that reuses the volume**, and **the second writer that names the lease holder**.
  Both are still only in the docstrings of the compose config tests. The smoke job always starts from
  no volume, which is the first-boot case alone.
- **The DDL wipe path.** A stale volume at DDL 2 against a kernel at DDL 3 was hit while verifying
  this unit — the backend refuses to start and names the remedy, exactly as documented — but a job
  that asserts that refusal would have to build a store at an old schema version on purpose.
- **Anything against a real provider.** By design, and the workflow's header says why: a company is a
  file, files arrive by pull request, and a key in this workflow would make a fork's pull request an
  exfiltration primitive. `test_bench.py` asserts the file names no provider key, and the keyless job
  exports `COMPANY_OS_KEYLESS_CI` so the suite can assert the same of the *process* — a variable can
  reach a job from an organisation default that the file never mentions.

### For U12

The append-only coverage test runs in the keyless job on both dialects now. U12's plan text already
says the cache table must be registered as mutable so that test does not silently skip it — with CI
running it against Postgres as well as SQLite, getting that wrong is a red build rather than a quiet
one.

### The first run found a flaky test, which is the second defect CI caught before it was green

Three of four jobs passed first time, including the compose smoke. The keyless job failed on
`test_compare.py::test_every_branch_of_a_comparison_forks_from_one_instant`:

```
E  simcore.step.CommandRejected: the run ended (horizon); there is nothing downstream to compare
```

Not U13's code, and not a new bug — a race that a shared runner lost where this laptop wins. The
test starts a ticker thread to move the parent, and that ticker steps **120 ticks per 10ms against a
10,800-tick horizon**, so it can end the run in under a second. Six comparisons then have to finish
before it does. Locally they do; on CI they do not, and the seventh call is *refused* rather than
measured — a `CommandRejected` out of the loop, not an assertion failure.

Its author half-saw this: `assert spreads, "the ticker ended the run before a single comparison ran"`
covers the run ending *before* the loop and nothing covers it ending *during*.

Fixed by bounding the ticker a sim-day short of the horizon, so it moves the parent without ever
ending the run. **But that bound makes the test weaker in the other direction** — a fast machine can
now finish six comparisons before a tick lands inside one, and the test would pass as the
single-threaded version it was written to replace. So the property gets a deterministic companion,
`test_the_parent_moving_between_branches_moves_no_fork_tick`, which forces a parent step between
branches from inside `_branch_from` rather than waiting for two threads to collide. This is the same
move U5 already made two sections down in the same file, for the same reason, citing this same test
as the thing that taught it — it just was not applied to the test doing the teaching.

Both catch the bug they describe: with the capture moved back inside the per-branch loop, the
threaded test fails and the deterministic one fails with `{613, 614, 615} == {613}`.

**The general lesson, for U16 and U19.** Every threaded test in this repository is a race between
what it asserts and what its helper thread does, and the helper is usually unbounded because bounding
it was not the point. U16 forks under a moving parent and U19 asserts determinism over a lineage; both
will want a thread. Write the deterministic version first and the threaded one as the topology check,
not the other way round.

### The action versions were four majors out of date

The first run resolved all three tags but warned on every job: `actions/checkout@v4`,
`astral-sh/setup-uv@v5` and `actions/setup-node@v4` target Node 20, which is deprecated and being
forced onto Node 24. The current majors are **v7**, **v7** and **v10.0.1**, all on node24, and every
input this workflow passes still exists at those refs — checked against the tagged `action.yml` rather
than assumed.

`setup-uv` is pinned **exactly** rather than to a major, and that is not a style choice: it stopped
publishing floating majors after v7, so `@v10` does not resolve at all and only `@v10.0.1` does.
Worth knowing before the next dependency bump reaches for `@v11`.

---

## What U12 found, that U16, U14 and U19 need

**The DDL version did not have to move, and the plan says it does.** The plan's System-Wide Impact
lists the cache table alongside `lineage_root_id` and the spend counter as one bump, and decision §4
above dropped the carry-forward *because* that bump also added a column to `runs`. That reasoning was
right and it does not transfer: `create_all` is check-first and runs at every startup, so a **new
table** appears on the next boot of an existing store with nothing to migrate and nothing to wipe.
The skew is harmless in both directions — an older build ignores a table it never queries, and the
cache is authoritative for nothing.

So DDL stays at **3**, and the distinction is now written where it will be read: a change to an
existing table's shape moves the version, a new table does not. `test_store.py` drops `model_cache`
from a provisioned store, reopens it, and asserts the table returns and the version check passes —
the claim is a test rather than a comment. The README's wipe section says the same in one paragraph,
because "upgrading drops your runs" is a promise to an operator and it should not be stricter than
it needs to be.

### The write cannot happen where the answer arrives, and that is decision §3

The obvious placement for the cache write is inside `BoundedGateway.complete`, on any `Completion`,
next to the lookup. The first implementation did exactly that, passed every test written for it, and
broke §3 of this document — *guard rejections write nothing* — through a door §3 did not name.

A reply that **ranks the options** is a perfectly good HTTP response. The gateway cannot tell it from
a usable one; only the guards can, and they run a level up in `produce_statement`. Cached on arrival,
that reply is served back on every future visit to the situation, produces the scripted fallback each
time, and costs no call — so the operator's remedy, switch to a model that follows the rule, changes
nothing, because the address is the situation and not the model. At temperature zero the same prompt
produces the same ranking anyway, which is what makes a stored one permanent rather than unlucky.

The fix is a deferred write: `complete` **stages** an entry and `keep()` commits it, and the leg calls
`keep()` only after `refusal_of` comes back empty. A `Failure` never stages, so a fallback cannot be
kept even by a caller that commits unconditionally, and a served hit stages nothing, so committing
one is a no-op. What this cost structurally is worth knowing: **the gateway's lifetime had to grow to
match the decision that depends on it.** `compose_statement` used to build the bench and discard it
inside one call; it now takes one built by `produce_statement`, which is the only place that knows
whether the answer survived. Two test monkeypatches took the new parameter with it.

### What is cached is the reply, not the statement — so the guards keep their grip

The stored text is the provider's reply exactly as it arrived, and `prompts.parse` plus both guard
predicates run over it again on the way out. Storing the parsed, approved statement instead would
have been smaller and would have made every entry a permanent exemption from whatever the rules
became. One narrow residue is left and is named rather than papered over: an entry written before a
rule was tightened is refused on every serve and never re-asked, because a hit reaches no provider.
That takes a code change mid-lineage, the remedy is emptying the table, and the README says so.

### The lookup goes in front of the ceiling, and the off switch survives it

A hit contacts nothing, so it is served even at an exhausted ceiling — refusing one would withhold a
briefing the run already paid for. That does not weaken `COMPANY_OS_MODEL_MAX_CALLS=0`: entries are
scoped to a lineage, so a lineage started at a ceiling of zero has none, and the bench is silent for
its whole life. What it does mean is that *lowering* a ceiling mid-lineage stops new calls rather
than repeated situations.

### For U16

`fork_run` still sets a child's `lineage_root_id` to its own id, so **the fork half of M33 is built,
tested and inert**. `test_a_fork_reaches_its_parents_entry_once_the_lineage_root_is_copied` asserts
both sides of that line — a miss before the column is pointed at the parent, a hit after — using the
same `UPDATE`-it-directly workaround U9 used for the spend aggregate. Copying the root at fork turns
two assertions live and needs nothing else from the cache.

Two smaller things travel with it. `model_cache.lineage_root_id` is a foreign key with `ON DELETE
CASCADE`, so a lineage's entries go when its root run row does — verified on Postgres, and on SQLite
through the kernel engine's `PRAGMA foreign_keys=ON`. And the cache is one more reader that will
notice `statement_request_id` not being run-scoped only if a fork ever mints a *different* prompt for
the same address, which it cannot: the address is the prompt.

### For U14 and U19

`Purpose.CEO_SUMMARY` already exists in `modelgw/cache.py` and is already asserted not to collide
with a director statement at the same tick and person. It is there before its producer deliberately —
a summary served where a briefing was asked for is impossible to notice from the served text, so the
namespace had to be in the key before the second producer was written rather than after somebody saw
the wrong block on screen. U14's summaries should pass it and nothing else.

U19 owns M34, and the cache is not the mechanism: pre-divergence statements are byte-identical
because the fork copies the parent's event rows and replay reads the log.
`test_emptying_the_cache_changes_nothing_about_what_the_log_folds_to` folds a run carrying a statement
with the cache full and then empty and gets one hash, and `test_a_fold_cannot_reach_a_cache_at_all`
says structurally why it could not have gone otherwise — `simcore` does not import `modelgw`.
