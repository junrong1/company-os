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

## What U25 found, that U18 and the rest of Phase E need

U25 is the client half of a fork: a decisions projection, a panel that lists what was settled and
offers every option that was not, and the two calls that turn "take this instead" into a timeline
you are standing in. No event kind, no payload field, no shape version, no golden fixture, and
nothing appended by any of it — the third read surface in a row built entirely out of what the
backend already answers.

**Two live defects, both on the entry path U17 built and neither visible to any suite.** The
pattern holds for the ninth unit running, and this time both were on the *landing* rather than in
the unit's own new code — which is what the plan's rule about ending at a real run is for.

- **The client posted a command the player never issued, into the timeline it had just entered,
  and showed its refusal as a banner.** `emptyRun()` defaults `rate` to `1`, so clearing the store
  on the way into a timeline moved the observed rate from the parent's `0` to `1` — which
  `shouldRestateHeldInput` reads as a resume, because that is exactly what a resume looks like. It
  re-stated the held direction into a child that arrives paused, the paused-run guard refused it,
  and the sentence landed in the shell's banner over a Universe stage the player had reached by
  pressing one button. Measured on the compose path: `POST /runs/<child>/commands`, 2 ms after the
  lineage read, with no matching line in the backend log because a rejection is not an error.
- **The clock control read ×1 over a timeline standing still.** Same root, other half: a run's
  rate is the one field this client never learns from its log. `RATE_CHANGED` is appended when a
  rate *moves*, and neither a run created at ×1 nor a child forked at zero has moved — the child's
  log held ten events and not one of them was a rate. So the empty state's `1` stood in, the player
  saw ×1 highlighted over a world that was not advancing, and the only recovery was pressing Pause
  and then ×1.

The fix for both is one line each and the second one is the interesting one. `reset` now takes the
rate the caller was told — `SwitchOutcome.rate` is already on the wire for exactly this, and it is
written in the *same* `set` as the clear, because two writes leave a frame in which the store
reports the default and that frame is the one the restatement effect reads.

**Comparing run ids does not work, and it is worth writing down why.** The first fix was a guard on
the restatement effect: skip it when the run id changed. It does not fire. The store is not React
state, so clearing it and handing the run id up do not land in one commit — the shell observes the
new rate while still holding the old run id, and the guard sees no change. What works is stating
the baseline at the transition: `land` writes `previousRate.current` before it touches anything, so
the effect has nothing to read as a change and no ordering can make it read one. Any future
component that watches a store value against a React prop has the same hazard.

- **The held-key case is separately real and worse.** Entering a timeline left *running* from a
  paused one moves the rate `0 → 1` legitimately, so carrying the real rate does not stop the
  restatement — and the command is then *accepted*. The CEO sets off across a world the player has
  only just arrived in, in a direction they were holding somewhere else. Pinned by its own test.

**A resolution's sequence cannot be recovered from a snapshot, and the panel says so.** A fork is
addressed by the sequence of the `DECISION_RESOLVED` it reconsiders; a snapshot is folded state and
folded state holds no log positions. So a resync fills the list in from the snapshot's per-item
`decisions` — the client's own records are a *suffix* of that list, so the missing ones are the
leading `n − k` — and those entries are listed, are honest about carrying sequence zero, and offer
no fork. Two things were deliberately not done: the list is not *dropped* the way `comparisons` is,
because a comparison is a projection whose basis is gone while a settled decision is history; and
the checkpoint index is not reconstructed from the snapshot's ordering, because `resolve_checkpoint`
refuses an unreached or already-resolved checkpoint and checks nothing else — ascending resolution
order is a property of the two routes the client offers, not a rule the kernel enforces, so the
alignment would be quietly wrong in the one case it was built for. The refusal sentence names the
fact and no remedy, because the remedy a reader reaches for is reloading to replay the run, which is
the very thing that resynced — U16's finding 8 in a new place.

**The fork's idempotency key is derived, and U16's review is why.** `forkIdempotencyKey(atSeq,
optionIndex)` — once per *intent*, not once per attempt, because the child's id is minted from it.
A random key per HTTP call makes a double-click two identical timelines and a lost response a
third, and sixteen is what the player meets for it. Two *different* alternatives at one decision are
two keys and therefore two timelines, which is the case U16's own id fix exists for.

**One suite hazard, found by the test it corrupted.** `mount` removed the host element and never
unmounted the React root, so every shell the file had mounted kept its `window` keydown, keyup and
blur listeners for the rest of the run. A keyboard event in a later test reached all of them, and
each submitted a command for the run *it* was attached to — indistinguishable from the defect the
test was written to catch, and it failed in the full file while passing under `-t`. The same shape
is in `universe.test.ts`'s `mount`, which nothing currently depends on.

### For U18

- **The Decided panel is a second entry point to the tree**, and it hands the stage a node to open
  on. `Tree` now takes `selected`, applied as a hint rather than as a controlled value — clicking
  another node still wins. U18's diff is entered by selecting *two* nodes, so it inherits a
  selection that something other than a click can set.
- **`land(runId, rate)` is the whole entry path now**, shared by the tree's switch and the fork's.
  Anything else that moves the player between timelines should go through it rather than calling
  `reset()` and `onEnterTimeline` itself, which is how both defects above got in.

---

## What U18 found, that U19 and U20 need

U18 is the timeline diff: a fold across two logs at one sim-day, served by the report app the
launcher mounts, entered by picking two nodes on the Universe tree. One new route, one new query
helper, one new client surface. No event kind, no payload field, no shape version, no golden
fixture, and nothing appended — the fourth read surface in a row built entirely out of what the
backend already holds.

**A day is its first tick, and that decided the whole shape.** The plan says the diff defaults to
the lesser of the two timelines' current sim-days and refuses a day one side has not reached. What
makes that a *decidable* rule rather than a judgement call is which tick "at day N" means. Folding
to a day's **last** tick would mean folding a timeline whose clock is standing still somewhere
inside that day forward through ticks it never ran — `fold` advances past the log's final event by
design, which is what lets the report cover a quiet office, and here it would print an invention
beside the other side's history. A day's **first** tick is reached by both timelines or by neither.
It is also the tick the kernel checkpoints its own state hash on, which turned R12 from a claim into
a check: **the hash the diff reports for each side is byte-identical to the `DAY_CHECKPOINT` the
kernel wrote at that tick**, verified against Postgres on the compose path, and at a day before the
fork both sides hash the same.

**Two live defects, both on this unit's own surface, and neither visible to any suite.** The pattern
holds for the tenth unit running.

- **Every figure was read under the other timeline's name.** The two column headings were a flex row
  of equal cards and the figure rows were a four-column grid of their own, so nothing made the two
  agree. Measured in the browser at 1568px: the *left* timeline's card spanned x 40 to 358 while its
  own figures sat at 462 to 532 — entirely underneath the *right* timeline's card. It is the worst
  thing this surface can do, and every client assertion passed, because they are all about which
  figure carries which attribute rather than where it lands. Fixed by one `--diff-columns` template
  that the heading row and every figure row both read; the regression test asserts that neither rule
  declares columns of its own, and fails when one does.
- **Two timelines that had ended read as "running".** `runs.terminal_reason` was **NULL** for both,
  with rates of 1 and 3, and **neither log held a terminal event** — the fact exists only in the
  kernel's folded state, and the Universe tree shows it correctly only because
  `KernelRuntime.lineage_tree` overlays its own fold on the rows. The report cannot do that (R4). So
  the diff reads it from *its own* fold, where it is exact and free, and the column now answers the
  better question: whether that timeline had ended **by the day being compared at**. The row's
  `rate` was dropped from the payload in the same change — a running-or-paused claim is about *now*,
  now is the tree's to report, and a field the surface cannot read honestly is worse than an absent
  one.

**A run row is a projection and the log is a floor under it.** U17's lag turns up again here, and
the report has no live fold to overlay — so `TimelineLog.reached_tick` takes the larger of the row's
tick and the newest tick the log actually *proves*, using the envelope's own tick rather than its
payload's (a `CEO_INPUT` names a tick the run may not have got to). The bound stays conservative in
the direction that matters: a day this diff offers is a day both timelines really reached, and the
surface says where the bound came from rather than leaving a refusal unaccountable.

### For U19 and U20

- **The state-hash-against-`DAY_CHECKPOINT` check is the cheap version of what U19 wants**, and it
  is already written twice — in `test_diff.py` against a driven log, and confirmed live against
  Postgres. U19's re-fold over a lineage is the same comparison at every boundary of every timeline.
- **`verify` has a defect on that exact path**, registered below with its measurement. U19 owns it.
- **`logschema.lineage.separating_decision` answers "what separated these two" for any pair**, at
  their nearest common ancestor rather than at the root — U20's report over a lineage needs the same
  naming, and it is a walk over rows with no fold in it.
- **`report.fold.state_at_day` is the one place that decides where a fold starts.** Every day step
  of the control is a fresh pair of folds from zero — measured at about 250 ms a step over a
  21-day lineage of 88 events a side. Persisted snapshots are the lever if that binds, and the
  shape does not have to change for them.

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

**`simcore.verify` calls a healthy run unhealthy when the CEO walked across a day boundary.** Found
by U18. `verify` folds a *sequence* prefix ending at each `DAY_CHECKPOINT` and passes that
boundary's tick as `through_tick`; `fold` compares `through_tick` against the largest tick any event
in the prefix **names**, and a `CEO_INPUT` names the tick it *applies* at, deliberately a few ticks
ahead of the one it was submitted on. So a player holding a direction across a boundary leaves an
event the prefix keeps and the fold refuses. **Measured on this build:** an otherwise identical run
verifies healthy with the office quiet and comes back `healthy=False` with `asked to fold through
tick 540, but the log holds an event at tick 542` once one `submit_ceo_input` straddles the
boundary — a sentence about a run row lagging its log, describing something else entirely. It
reaches the player through `POST /runs/{id}/diagnose`, which is the call an operator makes when
things look wrong. U18 sidestepped it (`state_at_day` filters by the fold's own tick rule instead of
cutting by sequence, and says so) rather than fixing it, because `verify` is **U19**'s — the unit
that already owns the determinism suites over a lineage and would have to re-baseline them.

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

> **U16 widened this, knowingly.** `post_fork` is a second synchronous route whose handler parks on
> the same two-slot `BRANCH_LIMITER`, so the exposure is no longer comparisons alone — and
> `diagnose`, the call an operator makes when things are stalling, is one of the routes that then
> cannot be served. Recorded here rather than left in the code comment that says it, so whoever
> makes the `async` change knows it has two callers to move.

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

**`store.terminate_run` has no production caller.** Found by U16 while writing the
terminated-parent test. A run's state ends when the fold reaches its horizon and `RUN_TERMINATED` is
logged, but nothing in the kernel writes `runs.terminal_seq` or `runs.terminal_reason` — only tests
do. That leaves two guards unreachable in deployment: `append_tick`'s `RunAlreadyTerminated`
refusal, which reads `runs.terminal_seq`, and `resume_all`'s skip of an ended run, which reads
`runs.terminal_reason`. Nothing misbehaves today because both facts are re-derived from the folded
log — a resumed terminated run's loop returns on its first wake, and the gateway reads
`state.terminal_reason` for a live run. What it costs is that the *store*-level protection everyone
assumes is there is not, and the fix is one call in the tick loop's termination path plus whatever
turning two dormant guards live surfaces. U16 asserted around it rather than on it, and the fork
path deliberately does not consult either column.

**`State.present_members` counts a hire who has left.** Found by U14, measured on the shipped
company: it filters `scenario.lines` on `departed` and then appends arrived hires with no such
check, so a line that lost a hire keeps their headcount — 3 → 3 — and the department goes on drawing
**10,800 units a day** for somebody who is gone. It also feeds the per-day morale penalty and the
`remaining_in_line` figure on `ATTRITION`. Not fixed by U14, because it moves a metric and a read
surface is the wrong unit to move one from; `remembered_scope` is written so that fixing it makes a
memory correct rather than breaking it, and `test_present_members_still_counts_a_hire_who_left` is a
tripwire that says so when it lands.

**`emptyRun()` defaults the client's rate to 1, and a run's rate is never on its log.** Found by
U25, and fixed at the one call site that knows better rather than at the root. `RATE_CHANGED` is
appended when a rate *moves*, so a run created at ×1 and a child forked at 0 both arrive with no
rate event at all — the client's figure has always been a default that happened to be right for a
created run. `reset(rate)` closes it for the two callers that enter a timeline, because the switch
already reports the rate. What is left open is every *other* way a client can attach, and it is
**measured**: attaching straight to a forked child nobody has entered — `GET /state` says rate 0 at
tick 439, its log is `GENESIS, CHECKPOINT_RAISED, DECISION_RESOLVED` and nothing else — the clock
control reads ×1 over a world standing still, because nothing on the attach path states the rate
either. Closing it properly wants the rate on the stream — the frame a
client gets when it connects — or a `rate` the store holds as "not yet told", which is a shape
change across the HUD, the render clock and the input lead.

**A synchronous route now makes a provider call.** Widens the `async` entry above a third time,
after U16 added `post_fork` beside the comparison path. `GET /runs/{id}/memory/{director}?summary=1`
runs a bounded model call on FastAPI's threadpool, so forty concurrent panel opens against a slow
provider occupy forty slots for as long as the provider takes — and `diagnose` is one of the routes
that then cannot be served. Bounded in practice by the ceiling and by the response cache (a second
open of one selection is a hit), and the panel's first call — the one every open makes — reaches no
provider at all. Closing it is the same `async` change the register already scopes.

**An `extra=` key colliding with a `LogRecord` attribute raises out of the logging call.** Found by
U16 on `created`, and **closed in the same change** — the field is `minted`, and
`test_no_log_call_names_an_extra_field_that_logging_reserves` now reads every `extra=` dict in the
tree against a set computed from a real `LogRecord`. Recorded here because the *class* is what
matters: `Logger.makeRecord` raises before any filter, so nothing `servicekit` installs can catch
it, and inside a route it is a 500 on work that already committed. The tree is clean; the guard is
what keeps it so.

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

---

## What U16 found, that U17, U18, U19, U20 and U25 need

Fifteen units in. A fork is a run: it has a command path, an id that does not collide, a tick at its
own fork point, and a place in the runtime. The three defects the plan named are closed, the cache
half U12 left inert is live, and **the plan's shape for the fork was not the shape it took** — twice,
each time for a reason worth carrying rather than a preference.

### A fork is a verb, not a command kind — and `START_RUN` is the precedent

The plan's approach says "the dispatch entry, the gateway route and the launcher's command mapping",
which reads as a fork travelling as `CommandKind.FORK_RUN` through `POST /runs/{id}/commands`. It
does not. It is `POST /runs/{id}/fork`, with its own request and response message, and the argument
is one the repository had already made and written down.

`post_run`'s docstring: *"a command must never bring a simulation into being, or a typo'd id in a
client would silently start one. So creation is a different verb on a different path."* A fork
creates a run. And the enum agrees: **`START_RUN = 1` has sat in `CommandKind` with no dispatch entry
since run creation became its own verb.** `FORK_RUN = 9` joins it. The two values with no dispatch
entry are exactly the two that create a run, which is a rule rather than two omissions —
`test_forking_is_not_a_command_kind` states it and fails if either is ever mapped.

Making it a kind would also have needed two carve-outs in `submit`'s guards. A fork is legal on a
**paused** run and on a **terminated** one — going back from an ended timeline is the demo's last
beat — and each carve-out would have been true for a reason that is not the guard's premise. The
guards would have ended up describing forks rather than commands.

The idempotency key stayed, and it does more work here than it does for a command: the child's id is
`uuid5` over `(parent_run_id, key)`, so the **store** is what deduplicates a retry rather than the
gateway's ledger — which is in memory and does not survive the restart a retry may cross. There is no
ledger entry for a fork at all. `POST /runs` makes the same argument for creation: the run id is its
own key.

### The plan's fourth refusal would have refused most real forks

The plan names four: a still-open checkpoint, the prefix bound, **"forking at a sequence with an
outstanding request"**, and the inherited horizon. The third one, taken literally, is unusable.
Measured on a real run: at the fork point of the first authored decision, `state.pending` holds the
**domain period consult** — raised at tick 540 with a deadline of 1080, and outstanding for a large
fraction of every sim-day. A blanket rule would refuse forks for a reason having nothing to do with
the decision being reconsidered.

Checking what the blanket version was defending against turned up nothing that survives contact with
the code. Its origin is U10's finding that `statement_request_id` derives from
`(director, item, cp_index, tick)` and is therefore shared by a parent and a fork at one tick — and
U10 itself recorded that this *is not reachable through the kernel*: an answer names the run it is
for and `deliver_statement` looks it up in that run's `pending`. The store's one-answer index is
`(run_id, request_id)`. Pre-divergence the two runs are the same run, so a shared cache entry is
correct rather than a leak. **Nothing breaks.**

So the refusal is narrowed to what is reachable and is actually wrong: **a request raised about the
item being re-decided**. The child settles that checkpoint at its own first tick, so a copied
question about it can only ever be answered against a decision that has already been taken. The
remedy is in the sentence — wait for the answer, or let the request reach its deadline.

This leaves U10's original note open rather than closed. Closing it properly still needs a run
identifier the fold reproduces, which is a `State` shape change; **U15 is the unit that already owns
a shape move** and is the right place for it, not this one.

### `RUN_FORKED` is still not emitted, and that buys U18 something

The kind exists, `KIND_SCHEMA_VERSIONS` gives it version 1, and `log.py` folds it as operational.
Emitting it as the child's first divergent event was the obvious move and was **not** taken, because
not inserting anything ahead of the divergence has a property worth more:

**The child's `DECISION_RESOLVED` takes the same sequence number as its parent's.** Both timelines
hold an event at sequence *n*, and they differ there and nowhere before. Parentage stays in `runs`,
where it belongs; the log carries the divergence itself. `test_a_child_takes_the_other_option_and_
resolves_the_checkpoint_once` asserts the sequence as well as the option.

> **Corrected after review.** This paragraph originally credited that alignment with making "the
> decision that separated them" cheap for U18 and U20 — a pair `(parent, n)` and `(child, n)` rather
> than a join through two mutable columns. That is not true, and the architecture review said so:
> both units address through `runs.parent_run_id` and `forked_at_seq`, and `forked_at_seq + 1` is a
> fixed computable offset whether or not a marker event sits in front of it. The decision not to
> emit `RUN_FORKED` is still right, on the simpler ground it should have rested on from the start:
> the fact is already durable in `runs`, and a zero-content event that the fold skips as operational
> adds a row and no information. The alignment is a pleasant consequence, not the reason.

### The copy and the divergence are one transaction, and the reason is a bad retry

`fork_run` takes the divergence events and appends them inside the transaction that copies the
prefix. Two transactions would leave a door open that is worse than a failure: a child holding its
parent's prefix and nothing else is a **paused copy, not a fork**, and because the id is minted from
the caller's key the retry that should repair it finds the child row already there and reports
success. One transaction makes "a child row exists" mean "a complete fork exists", which is what the
retry path is allowed to rely on.

### The routing through the single writer is load-bearing, and a five-second default is hiding it

The plan predicted that an out-of-band fork "contends with the writer for SQLite's write lock, and
the loser surfaces as a killed tick loop". That is exactly right and **the shipped configuration
hides it entirely**: pysqlite's busy timeout defaults to five seconds, so the loser *waits* rather
than failing, and a property test cannot tell the two designs apart. Measured — an out-of-band fork
racing 198 appends passes the same test the routed one does, on both dialects.

At `?timeout=0` the prediction is exact and immediate: `OperationalError: database is locked` on
`BEGIN IMMEDIATE`, the transaction rather than a statement inside it. **The loser is whichever party
asked second, and across runs of that race it was the fork in some and the tick loop in others.**
Half of that distribution is a stopped simulation with nothing wrong on the store side.

That race is not what the suite asserts, because asserting the outcome of a race is how a test
becomes the flake U13 spent a CI run finding — the first draft of it did exactly that and failed one
run in four. `test_a_second_writer_loses_the_lock_rather_than_waiting_for_it` forces the interleaving
instead: one connection holds the write lock, and both of the things that would have raced are shown
to lose to it. The property test alongside it pins the *mechanism* — `writer.forks == 1` — because on
the shipped configuration the property alone does not distinguish the designs.

There is a second and better reason for the routing besides the contention, and it is the one to
quote: **R35 says every append serialises through one writer, and a fork appends.**

### What U16 found on its own path

Three, and two of them are U16's own.

**A forked child's clock could not be started.** Found on the compose path, not in the suite, and it
is the sharpest thing here. A fork arrives paused and — unlike every other run in the system — with
**no tick task**: creating one at fork would be a task with nothing to do. `set_rate` did not create
one either, because it never had to; every run got its task at creation and a paused run's task stays
alive and idle. So `set_rate` on a child returned `applied`, appended `RATE_CHANGED`, wrote rate 3 to
the row, and `/runs/{id}/state` reported rate 3 — **and sim-time did not move.** It started on the
next restart, when `resume_all` built the task. Every observable said the clock was running.

`ensure_loop`'s own docstring had claimed the property all along — "the task's existence follows the
rate" — and nothing had ever made it true for a run with no task. `set_rate` now starts the clock
when it lifts a pause, through the same event-loop hop `_publish` uses and for the same reason. And
`start_background` records the loop handle, because a process whose every stored run is paused calls
neither `subscribe` nor `ensure_loop` and would have had nothing to schedule onto.

**A log `extra=` key that collides with a `LogRecord` attribute raises out of the logging call.** The
fork route logged `extra={"created": ...}` — the obvious word for "did this call make the child or
find it" — and `created` is the record's own timestamp. `Logger.makeRecord` raises `KeyError:
Attempt to overwrite 'created' in LogRecord`, *before* any filter, so nothing `servicekit` installs
can catch it; inside a route that is a 500 on work that already committed. The field is `minted` now.
This is the same failure family as `test_the_filter_never_raises_out_of_the_logging_call_that_
triggered_it`, so the guard went beside it: `test_no_log_call_names_an_extra_field_that_logging_
reserves` reads every `extra=` dict in the tree against a set computed from a real `LogRecord` — a
literal set would have missed `taskName`, which arrived in 3.12. The tree is otherwise clean.

**`store.terminate_run` has no production caller.** Pre-existing, found while writing the
terminated-parent test. A run's *state* ends when the fold reaches the horizon and `RUN_TERMINATED`
is logged, but nothing in the kernel writes `runs.terminal_seq` or `runs.terminal_reason` — only
tests do. Two guards are therefore unreachable in deployment: `append_tick`'s
`RunAlreadyTerminated` refusal, and `resume_all`'s skip of an ended run. The behaviour is right
anyway, because both facts are re-derived from the folded log; what is missing is the row. Deferred,
and in the register below.

### For U17, U18 and U25

- **The tree is a query and the columns are now interesting.** `lineage_root_id` is flat — every
  timeline in a tree names the same root, asserted three deep — and `parent_run_id` is the chain.
  `test_a_fork_of_a_fork_of_a_fork_reports_its_whole_lineage` pins both shapes at once, which is
  what stops a future reader collapsing them.
- **`ForkOutcome.to_dict()` is already the shape U25's panel needs**: the child, the decision
  sequence, the fork point, the tick, the lineage root, the item and cp index, and *both* options —
  the one taken and the parent's. A fork that returned only an id would leave the client fetching
  three things to render what it just did.
- **`decision_seq` is the decision, not the fork point.** The request names the
  `DECISION_RESOLVED` to reconsider and the copy stops one short of it. U25's decisions projection
  should carry the resolution sequence and send that.
- **A child arrives paused and stays paused**, deliberately: U25 lands the player in the child at
  rate zero with the Universe stage opening on the new node. `InProcessKernel.fork_run` calls no
  `ensure_loop`, unlike `create_run`. Starting it is the player's `set_rate` — which now works.
- **A refusal is a 200 with a sentence**, an unknown parent is a 404, a malformed body is a 400.
  U25's "shows the reason rather than failing silently" reads `refusal`.
- **U17 will want per-run locks in run-id order** for its switch command; `RunLoop.lock` is still
  public and still orderable, and `fork` takes none at all — it reads the parent's *log*, which is
  append-only and therefore immutable behind it.

### For U19 and U20

- M34's fork half is real now: two timelines differing only in a decision share a byte-identical
  prefix, because the copy is `INSERT..SELECT` over every column but `run_id` —
  `test_the_copy_carries_every_column_but_the_run_id` compares the stored rows including
  `command_id`, `request_id` and `ingested_at`, so a copy that regenerated metadata would fail.
- The child's `current_tick` is the tick of the decision it reconsiders. A re-fold of the child's
  log through that tick reproduces the state the fork produced —
  `test_a_child_survives_a_restart_at_its_own_tick_with_its_own_state` compares state hashes across
  a second `KernelRuntime` on the same store, which is what a restart is.
- One caution for U20's report fold: `runs.current_tick` is written by `append_tick`, so it tracks
  the last tick that *emitted* something rather than the clock. A restart resumes a run at that tick
  and loses the quiet ticks after it. Pre-existing, visible on the compose path (a parent at 4782
  came back at 4539), and not this unit's — but a report that folds through `current_tick` inherits
  it.

---

## What U14 found, that U15 needs

Sixteen units. A director's memory is a scoped slice of the log, and the CEO reads it as a rolling
summary over the events that mattered.

### The plan's file list was short by a wire, and the shape it needed already existed

U14's stated files are `bench/memory.py`, three client files and two suites. What it actually needs
is a *path from the kernel's folded state to a panel*, because a memory is the log read under a
scope and R23 forbids the reader from computing one. That is five more files, and each of them is
the same seam three other things already use:

- `simcore/step.py` — `_authorized_scope` is now public `authorized_scope`, and `remembered_scope`
  sits beside it. One module owns both derivations, so "what may this director read" has one home.
- `kernel/loop.py` — `KernelRuntime.memory_scope` derives the scope and the as-of tick **under the
  run lock**, because they are one fact: read a quantum apart they describe a line that never
  existed, since a hire could arrive between the two.
- `agents/main.py` — `read_memory` requires an `Authorized` it cannot make.
- `gateway/main.py` — `GET /runs/{id}/memory/{director}`, plus `use_memory`, the fourth installed
  callable beside `use_kernel`, `use_spend` and the statement producer.
- `single_process.py` — `_publish_director_memory`, and this one is the first composition that needs
  **both** other components rather than one. The kernel derives, the agents service reads, neither
  may import the other, and the gateway may import neither.

**There is deliberately no route for this on the agents surface.** A mounted endpoint taking a
director id would have to derive its own scope, which is the one thing R23 exists to prevent. So the
only way to ask for a memory is through a callable the launcher composed, and the only way to get a
scope is from the component that holds folded state.

### The two scopes return the same set today, and that is a defect over there

`remembered_scope` is "everyone the scenario ever put in this line, plus every hire that ever
arrived into it". `authorized_scope` is "the line as it stands". They should differ over a hire who
arrived and left — and they do not, because `present_members` retains one (register entry above,
measured). A departed *authored* member is retained by `authorized_for` itself, which starts from
`scenario.lines`: the roster rather than the roll call, and right for a statement whose evidence
window is three sim-days.

**U15 inherits both functions and has to widen the right one.** An Authorization grant widens what a
director may read *now*, which is `authorized_scope`. Whether a grant also widens a *memory* is a
product question this unit did not answer and U15 owns: a granted cross-line read that stayed in the
CEO's memory panel after the grant expired would be a standing permission arriving through a read
surface, which is exactly what "no standing permission" is meant to prevent.

### The window is the run, the cap is on what is shown, and the ranking is authored

`context.LOOKBACK_TICKS` bounds a statement's evidence to three sim-days because it becomes a
permanent logged fact. A memory is the opposite question, so it starts at genesis and
`MAX_SELECTED = 12` decides how much of it a person reads at once. `SALIENCE` is a table in
`bench/memory.py` — attrition above hiring above decisions above deliveries above assignment — and
it is authored rather than modelled for a reason the summary depends on: a model deciding what
mattered would make the *selection* non-reproducible, and then the citations under a sentence would
point at a set nobody could recompute. `test_the_salience_table_names_every_kind_the_admission_pass_admits`
keeps the two tables in step, because a kind admitted and unranked sorts last silently.

### The admission pass is now a function, and it is the only door

`context.scan` is the kind table, the person and item keys, and the default-deny that drops an event
naming neither. `retrieve` is a three-day window onto it and `memory.select` is a whole-run window
onto it, and neither can be called without an `Authorized`. A memory that re-implemented the filter
would be the second place the line-scoping boundary lived, and the first divergence would be silent.
`test_no_bench_module_can_reach_the_store` already covers the other half of that door and needed no
change: `memory.py` takes its events as an argument, like everything else under `bench/`.

### Four statuses, two calls, and the cadence is the cache

The derived selection is a log read and the prose is a provider call, so the panel asks twice:
`?summary=0` (the default) answers the selection with the summary marked `pending`, and `?summary=1`
answers the same selection with the prose. **`pending` is only ever reported when a bench is
actually present** — a keyless run's first read already says `absent`, so the client makes no second
call and never renders a line that cannot resolve, which is the M38 failure that would be easiest to
ship.

Regeneration needs no timer and has none. The cache key is the assembled prompt, which holds the
selection, so opening the panel twice on one sim-day costs one call and the summary is re-asked
exactly when what mattered changes. The consequence is two day stamps on the panel and both are
true: `as_of_day` is the day being read, `through_day` is the newest event the prose was written
over. A summary written on day four and read on day seven has not gone stale — the selection it
describes has not moved.

### The summary's guard lives beside its producer, and one rule is shared

A memory is never appended: not an event, not folded, not replayed, not copied by a fork. So unlike
a statement's guard it cannot break replay, and it lives in `bench/memory.py` rather than in
`simcore.statement`. What *is* shared is what a figure is — `stmt.figures_in` is public now — because
"a figure is a numeral that resolves to something you were shown" must mean one thing in both
places, and two regexes would drift the first time somebody taught one of them about a thousands
separator.

Four rules, and each exists because of what its absence would allow: a sentence with no citation is
a sentence about nothing; a citation outside the selection is R23 arriving through the read surface;
a figure that resolves to nothing is M19 applied to prose the CEO reads as fact; and a
recommendation is M18 — the panel opens beside an open decision, so a summary that says what to do
is the ranking a briefing is refused for, taking a different door.

### The measured marking got a second population rather than a fifth marking

The panel renders sim-days, log sequences and a count of events. None of them is authored tuning and
all of them resolve to a row, so they carry `data-measured` — and `MEASURED.description` now says
"counted rather than authored" instead of naming spend. A fifth marking for "counted, but not money"
would have split the sweep without telling a reader anything they could act on.

---

## What U17 found, that U18, U19 and U25 need

Seventeen units. The Universe is a query over three columns, a third stage, and one call that moves
the clock.

### The tree is a query, and the day on it cannot come from the row

`logschema/lineage.py` reads `lineage_root_id`, `parent_run_id` and `forked_at_seq`, with one batched
read of the log for the decision that separated each child from its parent — a child's divergence is
at `forked_at_seq + 1` in the child and the decision it reconsidered is at the same sequence in the
parent, which is U16's deliberate shape. No new table, no recursion, and `ticks_per_day` is a
parameter because that package may not import `simcore`.

**What a row cannot know is where a running clock has got to.** `runs.current_tick` moves only on
append, and a tick that emits nothing appends nothing — U16 already recorded the consequence for
U20's fold ("a parent at 4782 came back at 4539"), and it surfaces here as a tree that would draw
"running · day 1" beside an office that had moved on. Measured on the compose path while writing
this: **state at tick 58, row at tick 1.** So `KernelRuntime.lineage_tree` overlays the live fold's
tick, day, rate and terminal reason for every timeline this process is holding, and leaves the row's
values for one it is not — because nothing has folded that one, and inventing a tick for it would be
worse than reporting the last one written. This is a mitigation for readers, not a fix for the row;
the row still lags and the register still carries it.

### A switch is a verb because it names two runs

Creation and fork are not commands because they *make* a run. A switch is not a command for a
different reason: a command is addressed to one run and dispatched against its state, and this
belongs to neither of the two runs it touches — it belongs to the lineage.

Three things make it safe, and the first is the only guarantee available:

- **Pause the outgoing before resuming the incoming.** There is no transaction across two logs, so
  the ordering *is* the guarantee: a crash in between leaves nothing ticking rather than two things
  ticking, and the timeline the player left is recoverable at exactly the tick it stopped.
- **It is built out of `set_rate`.** That is what makes it inherit the row write, the `RATE_CHANGED`
  append, the publish and — through `_start_the_clock_soon` — the tick task for a run coming off
  zero. A second implementation of any of those is how one path ends up with a run whose row says
  rate 3 and whose clock does not move, which is the defect U16 found on the compose path.
- **One lock per lineage, taken outside every run lock.** `set_rate` takes each run's own lock for
  the length of a rate assignment, so no two run locks are ever held at once and there is no order
  between them to get wrong. What the lineage lock closes is the pair-wise race: two concurrent
  switches could otherwise both pause and both resume.

`_terminal_reason_of` reads the live fold before the row, and that is not belt-and-braces: **nothing
in the kernel calls `store.terminate_run`** (register entry, pre-existing), so a guard on the row
alone would have refused a switch into a finished timeline only in the tests that set the row by
hand, and accepted every one in production.
`test_a_terminated_timeline_cannot_be_switched_into_and_can_still_be_forked` asserts the row is
*still* null while the guard refuses, so it fails loudly when that gap is closed.

### The fork cap is U16's finding, and where it is checked matters

`MAX_TIMELINES_PER_LINEAGE = 16`, chosen for the surface rather than for memory: it is what a tree
can be drawn at and still read. It is checked **after** the existing-child branch, so a retry under
a key that already made a child keeps answering with that child in a full lineage — otherwise M47's
idempotency stops holding exactly where a client is most likely to be retrying. Nothing is deleted
to make room: eviction and deletion are decisions about a player's history, and refusing to make
more is the honest half that does not need one. The tree carries `cap` so the surface says "3 of 16"
before anybody meets a refusal.

### One clock per lineage after a restart, and the client's URL is the real record

`resume_all` starts the first running timeline in creation order and **pauses every other in its own
log** — not merely declining to start it, because a run with no task whose row says rate 3 is the
observable that lies. Verified live across a real restart: the log line names both run ids, the
older kept the clock, and the other came back at rate 0 with a `RATE_CHANGED` in its own log.

The consequence worth stating: the timeline the kernel pauses may be the one the player was in,
because nothing server-side records where they were standing. That is fine and self-healing — the
run id is in the address bar, so the client re-attaches to the timeline it was on, finds it paused,
and the player presses play. A server-side "current timeline" would be a second source of truth for
something the URL already holds.

### The client's switch is subtle for one reason, and it is in the store

`RunStore.apply` drops any event at or below the sequence it has already applied, which is correct
for a resume and fatal for a switch: a new timeline's frames start at 1, so **without a reset the
incoming timeline's whole log is dropped silently.** The reset happens in the shell, at the moment
the backend says the switch happened, because the shell is the component the incoming stream writes
through.

Two consequences fell out of that, and both are improvements the plan asked for:

- **The shell is no longer keyed on the run id.** A remount tears down the canvas, the renderer and
  the keyboard bindings, so entering a timeline would blank the stage and rebuild every sprite
  sheet. The shell was already built to survive a run id change — the stream effect depends on it,
  the prediction is rebuilt at render when it moves, the renderer rebuilds when genesis lands.
- **The HUD is gated on genesis.** With an empty store every tile renders a real-looking zero, and a
  player cannot tell that from a company with no cash. That was true of every attach, not only of a
  switch; the stated line replaces it in both.

`TimelinePlacement` holds **pixels**, not the grid units the DAG's `Placement` holds. That is the one
deliberate departure from the DAG's shape and it is because this tree is hit-tested: the first
version kept grid units, drew every node 18px apart instead of 288, and its own click test caught it.

### What U18, U19 and U25 inherit

- **U18's separating decision is already on the tree.** Each node carries the item, the checkpoint
  index, both option indices and **both option labels** — so the diff can name what separated two
  timelines without a catalog lookup, and a cousin pair can walk `parent_run_id` to their nearest
  common ancestor with no second query. The diff endpoint still belongs to the report app (R28), not
  to the gateway.
- **U25 can reuse the whole entry path.** `switchTimeline` and the shell's `onEnterTimeline` are what
  "a successful fork lands the player in the child, on the Universe stage" is made of: fork, then
  switch at rate zero, then set the stage. U16's review finding stands — **mint the fork's
  idempotency key once per user intent, not once per HTTP attempt**, or every retry is a new
  timeline and the cap above is what the player meets.
- **U19's lineage-wide fold has a size bound now** and a reason to use it: sixteen timelines is what
  `resume_all` folds at startup, which is also the shape of its own O(N × prefix) cost.
- **Eviction is still open**, and it is the half of U16's finding this unit did not close: nothing
  ever removes a `RunLoop` from `KernelRuntime.runs`, so a full lineage is sixteen resident folded
  states for the life of the process. `test_switching_into_a_timeline_nobody_has_folded_yet` drops a
  registration deliberately to prove the switch folds what it enters, which is the behaviour an
  eviction policy would depend on.

---

## What U15 found, that U19 and U20 need

Twenty units. A director who needs another line's knowledge has to ask, the CEO's answer stops the
work or lets it go, and both answers are on the log.

### The need is derived, because a request the CEO answers has to be one the run produced

`REQUEST_RAISED` is in the fold's output set and is compared byte-for-byte against what `step()`
reproduces, so an Authorization raised by a client, a bench module or any command path would sit in
the log with nothing to match it. `step.needed_line` is therefore the whole of what decides who asks
and for what, and it reads folded state and authored content only. **Nothing outside `simcore` can
cause an Authorization to exist.**

Two sources, and neither needs a scenario to author anything:

- **a hire is always about the line it hires into.** `request_hire` routes recruitment through
  People — the item is the recruiter's work — while what it is *for* is another director's line: who
  left, what is queued, how far over the ceiling they are. It is two clicks from the shipped client
  and it fires on both shipped companies;
- **work handed outside its authored line takes its history with it.** An item's `want` says whose
  work it is; when the CEO gives it to a specialist in another line, the director now holding it has
  none of what was learned about it.

The alternative was an authored marker — a `needs_line` field on an item, or a counterpart on
`visit_meeting`. It was rejected for a reason worth writing down: **neither shipped company authors a
single cross-line dependency.** Every `requires.items` edge in `default.toml` and `ashcroft.toml` is
inside one line, so a mechanic keyed to authored content would have been a mechanic that never
fired, verified only by tests that authored their own scenario. The derivation makes the first
Authorization arrive on the path a player is already pushed down — the one that fixes overload.

**It changes what hiring is, and that is a real consequence rather than a side effect.** A hire into
another line now stops until the CEO answers. Seven existing tests had to grant it, and they read
better for it: `hire_for()` in `test_capacity.py` and `_a_hire_arrives` in `test_scenario.py` now
answer the question the way a player does. What the plan asked for is a refusal with teeth, and the
only work a run can produce twice on demand is a hire — which is also what makes the unit's own
verification a *measurement* rather than a claim.

### The stall is a status, not a burn multiplier, and that is what kept the rules version cheap

The plan offers both: "a blocked-on-request status or a burn multiplier". The multiplier is the more
elegant answer and it costs a `MULTIPLIER_SLOTS` entry, which is part of the rules identity — so it
would have moved `RULES_VERSION` *and* needed a tuning fraction nobody could justify. The status
costs one field's worth of new state and answers M41 exactly: an item whose Authorization is
outstanding or refused burns nothing, walks nowhere and delivers nothing. `_advance_work` returns
before the burn.

It is read off `state.authorizations[item]` rather than off `state.pending`, and the difference is
the mechanic: a refusal goes on stalling after the question has left the outstanding table. That is
"we are waiting for an answer" against "the answer was no", and a projection of open questions
cannot say the second.

**The item's *status* is deliberately untouched.** `blocked` means stopped at a checkpoint and the
tray renders exactly that set; borrowing it would have put a decision on the rail with no options on
it.

### The rules version moved anyway, for one number, and the argument is worth keeping

`TUNING` gained `authorization_ceo_answer_seconds`, from which `pending.py` derives the window and
the re-ask interval. That moves `RULES_VERSION`, which invalidates every run written before this
unit — correctly, because a run under a different value does different work.

The reason it belongs in the table while the three windows above it in `pending.py` do not:
`REQUEST_DEADLINE_TICKS` and `STATEMENT_DEADLINE_TICKS` size how long a *service* is given to answer
the same question, so changing one makes a run wait longer for the same outcome. This one decides
how long an item is stopped and when a refusal happens by default. Two runs of one seed under two
values produce different work, different metrics and different logs, which is precisely what the
rules version identifies.

### The shape version moved to 2, and `verify` now reads it before it believes a hash

`authorization` is a declared subsystem, so `SHAPE_HISTORY[2]` is `SHAPE_HISTORY[1]` plus one entry
and `STATE_SHAPE_VERSION` is 2. The version is inside the overall digest by construction, so **every
hash in the tree moved even though a run that asks nobody anything carries an empty table** —
`test_scenario.DAY_ZERO_STATE_HASH` moved for exactly that reason and the old value is kept beside
the new one.

R27's other half is in `verify`: a recorded `state_shape_ver` that is not the running one is
reported as a **version move**, the hashes are declared incomparable, and sequence density stands on
its own. Without it U15 would have made every historical run report as corruption, with a
"truncate here" remedy attached to a run that is fine.

**One nuance, because it decides what the branch is for.** U15 moved the rules version *and* the
shape version together, so a pre-U15 log is refused by the rules guard first and never reaches the
shape check. What the check is for is a shape move that does *not* change what the numbers mean — a
hashed subsystem added to a run that plays identically — which R27 asks be legible whichever unit
eventually makes one. The fold still runs at each checkpoint and only the comparison is skipped, so
`last_good_seq` keeps meaning "the last sequence that folds cleanly".

### The CEO is a fourth leg, and their answer is a command

An Authorization rides the pending-input contract: `service = "ceo"`, the same caps, the same
deadline, the same abandonment, and the same projection that lets a restarted kernel remember what it
asked. What it does not share is the delivery path. A statement is delivered by a leg on a thread and
lands at a derived tick two sim-days later; this is a person pressing a button, so it is
`DECIDE_AUTHORIZATION`, applied at the tick it is issued at, with an idempotency key.

**That choice also steps around a defect in the register rather than into it.** `receive_answer`'s
`request is None` branch emits `ANSWER_REJECTED` from a command path with no `INPUT_RECEIVED` beside
it, which makes the log unreplayable — and a player double-clicking Grant is a far likelier duplicate
than a service answering twice. `decide_authorization` refuses a duplicate by raising
`CommandRejected`, which mutates nothing and appends nothing; the client derives the key from the
request and the verdict, so the second press is answered with the first press's outcome.

### U14's open product question, answered: the grant widens `authorized_scope` and not `remembered_scope`

U14 left it explicitly. The answer is that a grant widens what a director may *speak for now*, per
item, and never what they carry about their own line:

- `authorized_scope(state, director, for_item=...)` adds the other line's present members and items
  when that item's record is granted and names this director as the asker. `for_item` is a parameter
  rather than a field, so the default is the unwidened line and R23's "no reachable unscoped
  variant" survives a caller who has never heard of this mechanic;
- `remembered_scope` is untouched. A permission to read is not a change of who you are, and a
  granted cross-line read that stayed in the CEO's memory panel afterwards would be a standing
  permission arriving through a read surface.

**M42 is structural rather than enforced.** The record is keyed by *item*, so a grant cannot carry to
a second item, and "no standing permission" is a property of where the table is keyed rather than a
check somebody has to remember to write. A refusal is asked again one window later as a fresh request
with its own id, its own deadline and its own row in the report — which is the distinction between
"ask again" and "keep asking".

### Where this unit is narrower than the plan's wording, and why

The plan asks that a request refused by the per-item outstanding cap "degrades visibly rather than
disappearing". What happens is that the **ask is deferred** and the item keeps working until it
lands. Stalling on a question that was never asked was the alternative and it is worse: the item
would stop with no card anywhere to answer it, which is the one failure mode with no surface at all.
The cap is transient — a statement's window is two sim-days — so the ask arrives, and `diagnose()`
shows three requests on one item meanwhile, which is where the visibility is.

### The un-run-scoped request id is now a player-facing one, and it is still safe for the same reason

`authorization_request_id` derives from `(item, needs, tick)` and inherits
`statement_request_id`'s property exactly: `State` carries no run id, so a parent and a fork standing
in the same state mint the same id. What is new is that the id is now on a *button a person presses*
rather than only on a service's answer, so it is worth restating why that is still safe. The verdict
names the run it is for and `decide_authorization` looks the request up in **that** run's `pending`
— verified live by posting a valid request id to the wrong run, which came back
`rejected: no outstanding Authorization request ... Nothing was changed.` Closing it properly still
needs a run identifier the fold reproduces, which is still a `State` shape change.

### Two live defects, and the first one was not this unit's

**`request_hire` made its run unfoldable, and every suite passed.** `HIRE_REQUESTED` is an input the
fold re-issues, and re-issuing it calls `assign_direct` — which produces a `WORK_ASSIGNED` the fold
*also* classified as an input and applied a second time, where it found the item already active and
raised `CommandRejected` out of the middle of the fold. **Any run that ever hired could not be
replayed, reported or forked, and `GET /report/runs/{id}/report` answered 500.** Pre-existing since
U7; nothing caught it because no test folded a run that hired. Found by opening the report on a live
run, one command after the hire.

Closed here rather than registered, because U15's own trigger makes hiring the commonest way to raise
an Authorization and M43 lives in the report: leaving it would have shipped a mechanic whose main
path made the record of it unreachable. The fix is the mark `handoff_completed` already models — the
hire's assignment carries `for_hire`, and `is_output` reads it — so the event is regenerated and
compared rather than applied. `test_a_run_that_hired_folds_at_all` is the test that would have failed
before it, and `test_the_hires_assignment_is_regenerated_rather_than_applied` tampers with it to
prove the fix did not buy silence.

**The tray card named the work by its id.** A hiring item is created at runtime and is in no catalog
the client holds, so `item?.title ?? entry.itemId` showed the CEO `wi_hire-hire_sales_1` and asked
them to decide about it. Invisible to every suite, because every test named an authored item. The
request payload carries `title` now. Found by opening the page — which is the fifth unit in a row
where the defect on its own surface needed the thing to be running.

### Verified live, end to end, twice

`docker compose`'s single-process path, SQLite, two runs from one seed differing only in the answer:

- **granted**: the hire delivered on sim-day 3; the report row reads `granted`, 39 ticks stalled;
- **refused**: nothing delivered by day 4, and the director had **asked again on its own** at tick
  1120 — exactly one `AUTHORIZATION_REASK_TICKS` after the refusal landed at 40 — with the second
  row open and 500 ticks of stall against it.

Through the real client: the card renders in the tray with the amber edge, the beam lights on
`dir_hr` in the org rail and on the office canvas (66 pixels of `#f2c46b` in one marker, measured off
the canvas), decision pressure stays at `1 of 9` with the ask counted separately under `data-asks`,
and a mouse click on Grant applies the command and clears the card.

### What U19 and U20 inherit

- **U19 gets a second reason its re-fold matters, and a new shape to check.** `authorization` is a
  hashed subsystem, so a lineage-wide determinism check now covers who was allowed to read what.
  `verify`'s day-boundary false negative on a CEO input across a boundary is still U19's — this unit
  did not touch it, and `state_shape_ver` is now read before the hash in the same function, so the
  fix lands beside a check that already partitions "this cannot be compared" from "this does not
  match".
- **U20 gets the rows and the measurement.** `report.authorizations` is one row per ask carrying the
  asker, the line, the item, the verdict, whether it was the CEO's or the deadline's, and
  `stalled_ticks` — which is the figure that makes "a refusal has consequences" checkable by
  somebody who does not trust the sentence above it. An open ask reports what it is *still* costing,
  measured to the head of the log.
- **The report is reachable for a run that hired**, which it was not before this unit.
