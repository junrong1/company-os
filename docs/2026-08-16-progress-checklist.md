---
date: 2026-08-16
topic: progress-checklist
---

# What has been built so far

A checklist of every unit the three plans in `docs/plans/` define, what closed, what is
carried forward, and where the MVP PRD (`docs/prd/2026-08-16-company-os-mvp-prd.md`)
stands against it. Statuses come from the plan frontmatter, the coverage audit
(`docs/2026-08-14-phase-1-coverage-audit.md`), the residual findings
(`docs/residual-review-findings/`), git history, and the code as it sits today.

**Legend.** `[x]` shipped with tests · `[~]` shipped with a named gap · `[ ]` not built.

---

## Document trail

- [x] `docs/brainstorms/2026-08-12-company-os-platform-requirements.md` — Phase 1 origin, R1–R63, A1–A5, F1–F5, AE1–AE20
- [x] `docs/plans/2026-08-13-001-feat-company-os-phase-1-plan.md` — Phase 1 plan, **frontmatter still says `status: active`** though the units below are done
- [x] `docs/brainstorms/2026-08-14-playable-loop-requirements.md` — Phase 2 origin, R1–R26
- [x] `docs/plans/2026-08-14-001-feat-company-os-playable-loop-plan.md` — `status: completed`
- [x] `docs/brainstorms/2026-08-14-agent-team-requirements.md` — Phase 3 origin
- [x] `docs/plans/2026-08-14-002-feat-option-consequence-comparison-plan.md` — `status: completed`
- [x] `docs/2026-08-14-phase-1-coverage-audit.md` — requirements audited against code after Phase 2
- [x] `docs/residual-review-findings/feat-option-consequence-comparison.md` — three findings accepted knowingly
- [x] `docs/residual-review-findings/feat-company-os-mvp-u16.md` — **the open list from U16's review.** Twelve reviewers, five findings fixed on the branch, twelve carried. Each names the unit it belongs to; U17 owns the fork cap, U19 owns the inherited-statement asymmetry, and two are cheap enough to take next
- [x] `docs/design/art-direction.html` — approved specimen, load-bearing for three client units
- [x] `docs/prd/2026-08-16-company-os-mvp-prd.md` — MVP PRD, M1–M67, `status: active`
- [x] `docs/plans/2026-08-16-1527-feat-company-os-visual-redesign-plan.md` — the daylight redesign's Product Contract, R1–R13 + R9a, F1–F3, AE1–AE4
- [x] `docs/plans/2026-08-16-002-feat-daylight-pixel-visual-system-plan.md` — the implementation plan, U1–U17, `status: active`
- [x] `VISUAL_DESIGN.md` — the rendering specification the redesign is drawn to
- [x] `CONTEXT.md` — the glossary the PRD is written in
- [x] `docs/plans/2026-08-16-001-feat-company-os-mvp-plan.md` — the MVP plan, U1–U25, `status: active`
- [x] `docs/2026-08-16-mvp-execution-decisions.md` — what executing that plan resolved and found: the
  five open questions answered, the deferred-to-implementation items assigned, the per-unit handoffs,
  and the **deferred defect register** of pre-existing defects the units surfaced and deliberately
  did not fix

---

## Phase 1 — event-sourced kernel and service split

| Unit | Status | Evidence |
|---|---|---|
| U1. Toolchain, repo split, compose skeleton | `[~]` | `888ac83`, `af05154`, `1d2d357`. uv + Python 3.12, Vite client, seven services with status endpoints. **The default compose path answers 503** — audit gap 2. |
| U2. Inter-service contracts and event envelope | `[x]` | `884759c`; `test_envelope`, `test_contracts_generated`, `test_import_boundaries` |
| U3. Determinism primitives | `[x]` | `18fe1b9`; `test_determinism`, `test_canonical` — counter RNG, canonical hashing, fixed multiplier order, derived rules version |
| U4. Kernel port (world, people, work, assignment, sim) | `[x]` | `18fe1b9`; `test_kernel_port` |
| U5. Parity suite and golden vectors | `[x]` | `e1e924c`, `a6a9890`; 42 parity tests, `frontend/tests/golden.test.ts` |
| U6. Log store, append-only, writer lease | `[x]` | `2d16e59`, `413f101`; 38 store tests on both dialects |
| U15. Fold, snapshot, replay, fork, export | `[x]` | `413f101`, `e1e924c`; `simcore/{log,snapshot,verify,export}.py`. The colliding fork id is **closed by MVP U16** — it is minted from the fork's idempotency key now. A child still arrives paused, which stopped being a gap and became the design: U25 lands the player in the new timeline at rate zero |
| U7. Capacity, hiring, morale, economy | `[x]` | `test_capacity`; draws, soft ceiling, degradation floor, hiring lag, desk guarantee |
| U8. Run lifecycle and report service | `[~]` | `88997ba`; horizon, insolvency at a tick boundary, report folded from the log. **The client never calls the report** — audit gap 3. |
| U9. Kernel service: tick loop and gRPC surface | `[~]` | `413f101`, `1029078`; tick loop, diagnose, position echo, single-process launcher. **gRPC server exists, no client leg was ever built.** |
| U10. Gateway: commands and event stream | `[x]` | `f39cb2e`; `test_gateway`, idempotency keys, sequence resume |
| U11. Domain and agent services | `[x]` | `c3fa195`; `test_pending_input`, AE10 stub resolver |
| U12. Renderer port with explicit lifecycle | `[x]` | `15bd843`; `render.test.ts` — visual parity itself judged by eye, deferred |
| U13. Client shell, panels, HUD | `[x]` | `265bbfe`, `f7e9659`; `hud.test.ts` — trajectories, runway, capacity heat, decision pressure |
| U14. DAG view and chain strip | `[x]` | `f7e9659`; `dag.test.ts` |

---

## Phase 2 — the playable loop (plan marked completed)

- [x] **U1.** Draw the CEO on the floor, seeded from run state — `6c63425`
- [x] **U2.** Predict and reconcile CEO movement, WASD + echo + paused reason — `26ba36e`, `8083157`, `63c0986`
- [x] **U3.** Proximity and the conversation shell — `e5ee97d`
- [x] **U4.** Decide in person, with the tacit line — `32f3401`
- [x] **U5.** Assign from the conversation — `867d70b`
- [x] **U6.** The ask command in the kernel — `9fe4e03`
- [x] **U7.** The ask box in the conversation — `ae557fd`
- [x] **U8.** Start a run from the client, follow a finished one — `c45af79`

Nine Phase 1 requirements that no Phase 1 unit had discharged were found and fixed along the
way — held-direction movement, the render clock, the extrapolation budget, the undrawn CEO,
the nameless roster, the tacit line's transport, unlock announcement, run creation from the
page. The table in the coverage audit names each with its commit.

---

## Phase 4 — the daylight pixel visual system

All seventeen units shipped with tests. The kernel, the wire and the store are untouched: no
event, command, payload field or schema version changed, and `frontend/tests/golden.test.ts`
never moved.

| Unit | Status | Evidence |
|---|---|---|
| U1. The daylight token set | `[x]` | `tokens.test.ts` — contrast held as assertions, not intentions |
| U2. The chrome, rebuilt bright | `[x]` | `chrome.test.ts` — stylesheet and token module read and compared |
| U3. DAG and chain strip on a light ground | `[x]` | `dag.test.ts` — no colour literal survives in those three files |
| U4. The base resolution, 16 → 32 | `[x]` | `world.test.ts` — written first, and the grid signature did not move |
| U5. The daylight shell | `[x]` | `world.test.ts` — no dark field, no `lighter` blend, light from the glazing |
| U6. Team floor zones | `[x]` | `world.test.ts` — border course and inset rug per room |
| U7. The prop set at 32×32 | `[x]` | `render.test.ts` — sixteen props, a daylight set with dark outlines |
| U8. The rig format and its converter | `[x]` | `cast.test.ts` — exact round trip, or an error naming the pixel |
| U9. The body rig | `[x]` | `cast.test.ts` — twelve cells, feet on row 63 in every one |
| U10. The feature libraries | `[x]` | 8 hair, 8 outfits, 6 accessories, each with three views |
| U11. Composition and the drawing pass | `[x]` | `cast.test.ts` — one `putImageData`, mirrored left, layered in order |
| U12. The eleven identities, cast | `[x]` | 33 manifests read out of the candidates, silhouettes unique |
| U13. Appearance from the run seed | `[x]` | `cast.test.ts` — stable in a run, and no command carries it |
| U14. The camera | `[x]` | `camera.test.ts` — dead zone, whole pixels, clamped to the building |
| U15. People first, across the chrome | `[x]` | `chrome.test.ts` — panels on a rail above 1400, one warm accent |
| U16. The visual verification harness | `[x]` | `visual.test.ts` plus `npm run verify` artifacts |
| U17. The record | `[x]` | the specimen rebuilt, `VISUAL_DESIGN.md` and `CONTEXT.md` updated |

Three rendering bugs surfaced only under the harness, and none of them could have been caught by
a unit test: `resize()` cleared the canvas on every observation, the canvas fed its own size back
into the box it was measured against, and two grid tracks floored at their content's height and
pushed the office off the screen.

---

## Phase 3 — option consequence and branch comparison (plan marked completed)

- [x] **U1.** Each option's authored consequence carried to the client — `87dad75`
- [x] **U2.** The authored-tuning marking and the sweep that keeps it complete — `d02d3b4`, `247d8f6` (this closed audit gap 5 / R41)
- [x] **U3.** The branch runner — `1f31698`
- [x] **U4.** The comparison command and its record — `1f7953f`
- [x] **U5.** The comparison surface in the conversation and the tray — `0040571`
- [x] **U6.** The keyless path, end to end — `d298e39`
- [x] Review follow-ups: `18fc503` bound the comparison cost, `89ec88a` forks every branch from one instant, `4228c83` removed three duplications, `ce5337e` closed the four narrow findings

---

## Carried forward — open, with where it now lives

| Item | Origin | Now tracked as |
|---|---|---|
| Staff do not move on the client; no event carries person, path, start tick | audit gap 1 (R13, R17) — deferred by decision in Phase 2 | **M62** — back on the list |
| Compose stack cannot work, no gRPC client leg | audit gap 2 | **M1, M2** — resolved by dropping the split, not by building the leg |
| The report is built and unreachable from the client | audit gap 3 (R52, R53) | **M53–M61** |
| Nothing synchronises the command path against the tick loop | residual finding 1, P1 | **M63** |
| A comparison occupies the worker pool every run's clock depends on | residual finding 2, P1 (mitigated, not closed) | **M64** |
| A pre-change run shows every option as costing nothing | residual finding 3, P2 | not in the PRD — still open |
| A fork can be made without limit | U16 review, finding 1 | **closed by U17** — 16 timelines per lineage, refused with a sentence, and the surface says "3 of 16" before anybody meets it |
| No run is ever evicted from `KernelRuntime.runs` | U16 review, finding 1 (the other half) | still open — a full lineage is 16 resident folded states for the life of the process. U17 proved the switch folds what it enters, which is what an eviction policy would rest on |
| Three synchronous routes now share one starved threadpool, and one of them calls a provider | U16 review, finding 2, widened by U14 | the `async` change the register already scopes |
| A departed hire still counts toward their line's headcount and draw | U14, measured | register — it moves a capacity figure, so it wants a unit that can re-baseline one |
| A forked child's inherited bench statements are never asked | U16 review, finding 9 | **closed by U19** — settled by asking rather than by explaining. A fork now dispatches what it inherited, so a briefing on one side and a deadline on the other stops being a difference the option did not cause. Verified live: the child's ask reaches the loop from the *synchronous* fork route, which is the `call_soon_threadsafe` branch finding 11 says the suite cannot take |
| Three names for the fork point's sequence, and a proto that drifts from the shipped JSON | U16 review, finding 5 | open, cheap |
| R44, the in-person claim is never validated | audit gap 4 | **closed as a decision** — an explicit PRD non-goal while the product is local |
| R41, authored-tuning marking on every surface | audit gap 5 | **closed** by Phase 3 U2 |

---

## Phase 5 — the MVP (`docs/plans/2026-08-16-001-feat-company-os-mvp-plan.md`)

Twenty-five units in six phases. **All twenty-five are done, and the MVP is complete** — the
repository runs, scenarios are files, the bench briefs and
refuses and pays for a situation once, a past decision forks into a timeline that plays forward and
survives a restart, every director carries a memory the CEO can read, one line cannot read another's
without the CEO saying so and the work stops until they do, the whole tree of timelines is a surface
the player moves between, the fork is reachable from inside the game rather than from a terminal, the
suites now fold whole lineages rather than single runs, a whole Universe of timelines folds into
one report that says where the company was overloaded and which of its futures escaped it, that
report ends by saying what is worth automating — from a catalog somebody authored, about a line
this run actually overloaded, priced by the company's own arithmetic — and the whole of it exports
as one file that opens on a stranger's laptop with nothing installed and reaches for nothing. The
README leads with eight seconds of the loop, recorded from a run that really happened. The plan's
open questions and everything execution resolved or found are in
[`docs/2026-08-16-mvp-execution-decisions.md`](2026-08-16-mvp-execution-decisions.md), which also
carries the deferred defect register.

**Phase A — the repository runs.** Complete.

| Unit | Status | Evidence |
|---|---|---|
| U1. Collapse the backend to one container | `[x]` | `9956471` — three services, no profile, Apache-2.0. Verified by a real `docker compose up`: 11.6s cold, a run created and drawn through nginx with no console error |
| U2. Open on a person who is waiting | `[x]` | `3eee239` — `wi_hiring` seeded to `dir_hr`, two earned hints in local storage. Proved the golden vectors do *not* move, against R16's expectation |
| U3. Staff movement on the wire | `[x]` | `2737c3c` — one `STAFF_MOVED` per walk, the client's golden-tested interpolation finally wired up, `STATE_SHAPE_VERSION` held at 1 five ways |
| U4. One lock across the command path and the tick | `[x]` | `788e0ec` — per-run `threading.Lock`, per quantum, with a measured floor proven able to fail |
| U5. Comparison off the tick worker pool | `[x]` | `79dd779` — `BRANCH_SLOTS=2` sized by sweep; the capture/restore torn read closed for every caller, 15–19% escape rate to zero |
| U24. Compose the remaining surfaces in one process | `[x]` | `2aba425` — five surfaces, the redacting filter, origin validation, `MODEL_SPEND` published |

**Phase B — scenarios.** Complete.

| Unit | Status | Evidence |
|---|---|---|
| U6. The scenario format, the loader, and the company that moves into it | `[x]` | Two passes: `1a01b44` the format, loader, `default.toml`, `schema.md` and 98 tests; `8bbb91f` the wiring, the genesis identity, the three guard sites, and the constants deleted from six modules |
| U7. Choosing a scenario, and seeing what it says | `[x]` | The name threaded through the gateway body, the `KernelClient` protocol, the launcher and `create_run`; `GET /scenarios`; `ashcroft.toml`, a second company of nine; the picker on the start screen; a person's responsibility and tools on the conversation surface under a third marking. The genesis payload did not move, so no golden fixture did either |

**Phase C — the bench.** Complete.

| Unit | Status | Evidence |
|---|---|---|
| U8. The model gateway | `[x]` | `fbecec1` — two wires, eight providers, a key type that cannot be printed |
| U9. The ceiling and its counter | `[x]` | `084b529` — 200 calls / 600k tokens per run, DDL 3, the one measured HUD figure |
| U10. The statement contract | `[x]` | `86176c8` — the delivery leg that had no caller, servicer or client; the request derived inside `step()`; four bugs its own review pass caught |
| U11. Four directors who brief, object, and never rank | `[x]` | The two predicates in `simcore/statement.py`, `Offered` with an adapter per process, personas and prompts off the genesis roster and catalog, one fallback exit naming its condition, a fourth marking, and the pending block. 73 new backend tests plus 12 client ones |
| U12. Caching on the situation | `[x]` | `modelgw/cache.py` holds the content address — the assembled prompt, the authorization scope and a purpose namespace — and `StoreResponseCache` in the agents service holds the table, scoped to a lineage and filtered on the rules version. The lookup runs in front of the ceiling because a hit is not a call; the *write* waits for `keep()`, because only the caller knows the guards passed. 37 new tests, and the DDL version did not move |
| U13. Continuous integration for the keyless path | `[x]` | `.github/workflows/ci.yml` — four jobs, no secret of any kind: the keyless suite on both store dialects, the bench against the mock adapter, the client's four steps with the type check separated out, and `docker compose up` from a clean checkout reaching a client that creates a run. Found two live defects before it went green: a `tsc -b` failure the suites could not see, and a flaky comparison test only a shared runner loses |

**Phase D — memory and Authorization.** Complete.

| Unit | Status | Evidence |
|---|---|---|
| U14. Director memory and the CEO's reading surface | `[x]` | `GET /runs/{id}/memory/{director}` — a scoped slice of the log, never a second store. `context.scan` is the one admission pass and `memory.select` is a whole-run window onto it, so a memory cannot see what a briefing could not; the selection is derived from an authored salience table, so the panel renders on the keyless path and every citation resolves to a set anybody can recompute. The prose is the only generated half, and a sentence that cites nothing, cites outside the line, invents a figure or recommends anything is refused before the CEO reads it. Two calls per open — the derived half at once, the summary behind a stated pending line — and the regeneration cadence is the content-addressed cache rather than a timer. The scope is derived in the kernel and handed across by the launcher, the fourth composed callable, so no bench module can compute one. 39 new backend tests plus 16 client ones; found a live capacity defect in `present_members` and left it in the register |

| U15. Authorization as the co-work mechanic | `[x]` | `POST /runs/{id}/commands` with `decide_authorization`, a fourth leg on the pending-input contract and a fifth hashed subsystem. The need is **derived** — `step.needed_line` reads folded state and authored content, so no client, bench module or command path can raise one — and neither shipped company authors a cross-line dependency, which is why it is derived from a hire being People's work about another line rather than from a marker nobody wrote. The stall is what gives a refusal consequence: an item whose Authorization is outstanding or refused burns nothing, and it reads the item's own record rather than `state.pending`, so a refusal goes on stalling after the question is gone. A grant widens `authorized_scope` for that item only and never `remembered_scope`, which answers the product question U14 left open; M42 is structural, because the record is keyed by item. `STATE_SHAPE_VERSION` moved to 2 and `verify` now reads the recorded shape **before** it compares a hash, so a deliberate change reports as a version move rather than as corruption (R27). `RULES_VERSION` moved too, for one tuning entry, and the argument for which side of that line it sits on is in the decisions doc. 31 new backend tests plus 16 client ones; verified live on two runs from one seed — granted delivered on day 3, refused delivered nothing by day 4 and asked again on its own at tick 1120. **Two live defects, and the first was not this unit's**: `request_hire` made its own run unfoldable, so any run that ever hired could not be replayed, reported or forked and the report answered 500 — pre-existing since U7, caught by no suite, closed here because M43 lives in the report; and the tray card named a runtime-created item by its id, which no test could see because every test named an authored one |

**Phase E — forks, timelines, the Universe.** Complete.

| Unit | Status | Evidence |
|---|---|---|
| U16. Persistent forks | `[x]` | `POST /runs/{id}/fork` — a verb rather than a command kind, joining `START_RUN` as the second `CommandKind` value with no dispatch entry, because both create a run. The three defects closed: the child id is minted from the idempotency key so two forks of one decision are two timelines; the child row records the tick of the decision it reconsiders rather than the parent's present tick; and the child is registered with the runtime. The prefix copy became one `INSERT..SELECT` inside the same transaction as the divergence, routed through the single writer. `lineage_root_id` now comes from the parent, which switched U12's cache half live. 34 new backend tests, both dialects; verified on the compose path against Postgres — a three-deep lineage, four clocks, one restart |
| U17. The Universe tree | `[x]` | `GET /runs/{id}/lineage` and `POST /runs/{id}/switch`, plus a third stage. The tree is a query over `lineage_root_id`, `parent_run_id` and `forked_at_seq` in the new `logschema/lineage.py` — no new table, no recursion, and one batched log read for the decision that separated each child from its parent, both option labels included. The switch is a verb rather than a command because it names two runs: it pauses the outgoing timeline before resuming the incoming one, takes one lock per lineage outside every run lock, and is built out of `set_rate` so it inherits the row write, the append, the publish and the clock start. `resume_all` now starts one clock per lineage and pauses the rest in their own logs. U16's missing fork cap is closed at 16 timelines, checked after the retry path so idempotency still holds at the boundary. Two live defects found: the tree's day came from a row that lags the fold (measured — state at 58, row at 1), and the terminated-timeline guard would have been dead code because nothing calls `store.terminate_run`. 20 new backend tests plus 22 client ones; verified live end to end — settle, fork, tree, switch, and one clock across a real restart |
| U18. The timeline diff | `[x]` | `GET /report/runs/{id}/diff/{other}` — a fold across two logs at one sim-day, on the report app the launcher mounts rather than on the gateway, because a diff is a fold and R4 forbids the import. **A day is its first tick**, which is what makes "at the same sim-day" decidable: `fold` advances past a log's last event by design, so folding to a day's *end* would run a paused timeline through ticks it never took and print the result beside the other side's history. A day's opening tick is reached by both or by neither — and it is the tick the kernel checkpoints its own state hash on, so R12 became a check rather than a claim: each side's reported hash is byte-identical to the `DAY_CHECKPOINT` in the log, verified against Postgres, and at a day before the fork both sides hash the same. The separating decision is named at the nearest common ancestor, so two cousins are described by the decisions they took rather than by one neither did. Entered by picking two nodes on the tree; the held node wears a second marker on the opposite edge from the standing one, so a node can be both. 35 new backend tests plus 25 client ones. **Two live defects, both on this unit's own surface and neither visible to any suite**: every figure was rendered under the *other* timeline's heading (measured — the left card at x 40–358, its own figures at 462–532), and two timelines that had ended read as "running" because `runs.terminal_reason` was NULL and neither log held a terminal event. Nothing appended, no event kind, no payload field, no shape version, no fixture |
| U25. Forking from the client | `[x]` | The decisions projection the store never had — `DECISION_RESOLVED` advanced a count and dropped the option, the tick and the sequence, which is the field a fork is addressed by — plus a Decided panel that lists what was settled with the option taken marked and offers a fork per alternative, priced by the same `OptionConsequence` the tray and the conversation render. A fork is two calls: `POST /fork` then a switch at rate zero, and the shell opens the Universe on the new node, so the beat has a visible outcome instead of the same office at the same tick. The idempotency key is derived from the decision and the option — once per intent, not per attempt, which is U16's review finding 1 on the client side. A resync fills the list in from the snapshot's per-item `decisions` (the client's records are a suffix, so the missing ones are the leading `n − k`) and those entries are listed, honest about carrying sequence zero, and offer no fork — a snapshot is folded state and folded state holds no log positions. 32 new client tests. **Two live defects found, both on U17's entry path and neither visible to any suite**: the client posted a command nobody issued into the timeline it had just entered and showed its refusal as a banner, and the clock control read ×1 over a paused world. Nothing appended, no event kind, no payload field, no shape version, no fixture |

**U19 is in**, and it is the first unit of the determinism work.

| Unit | Status | Evidence |
|---|---|---|
| U19. Determinism over the lineage | `[x]` | The suites now fold *lineages*, on both dialects, built through the kernel's own surfaces by `tests/conftest.py` — a parent walked to a briefing, a decision, and a fork per option. Every day boundary of every timeline re-folds to the hash the kernel wrote (M65); the three are one history below the divergence and three distinct futures above it, which is what stops that being vacuous; a fork exported and replayed in another process reproduces its hash; two fresh lineages from one seed are the same lineage timeline by timeline; emptying `model_cache` between the fork and the re-fold moves no hash and a child holds its parent's statements byte for byte (M34); and a comparison is diffed against **every table in `metadata`** rather than a list — one `OPTIONS_COMPARED` row for one that runs, nothing at all for one that is refused (M35). **The registered `verify` defect was not in `verify`, and not a diagnosis defect**: one expression in `simcore.log.fold` measured the run's reach by the largest tick an event *named* while the replay schedules by `issued_at_tick`, so `fork` refused a decision taken inside a scheduled input's lead and `resume_run` could not rebuild a run whose bench answer was in flight. Measured live on the compose path, the same three HTTP calls refused and then accepted. A second defect closed beside it: `verify` never called `compare_checkpoint`, so a divergence named ten subsystems instead of one. It also settled U16's review finding 9, which named U19 as its owner: a statement outstanding at the fork point was copied onto the child and asked by nobody, so the parent got a briefing and the child waited out a deadline — a difference between two timelines that the option did not cause, which is the one thing M34 forbids. 26 new backend tests plus a lineage harness; no event kind, no payload field, no shape version, no fixture |

**U20 is in**, and the Universe has a report.

| Unit | Status | Evidence |
|---|---|---|
| U20. The Universe report | `[x]` | `GET /report/runs/{id}/universe` — one artifact over a whole tree, **identified by its lineage root**, which is asserted by popping `asked_about` from two payloads and comparing them whole: asking from a child and from its parent produce the same document, because U22 mails it to somebody. `Claim` and `DecisionRecord` carry the run id, because a fork copies its parent's rows and a bare sequence stopped being an address — tested in both directions, that every claim resolves *and* that some sequence really is claimed by two timelines. **The plan's claim that the load events already carry overload is wrong**: `LOAD_CHANGED` fires only on a day that produced morale counters, measured at twelve consecutive days over the ceiling named by the log on three. So M56 is folded, and every reading cites the `DAY_CHECKPOINT` whose state hash it was read off — a boundary the log cannot cite produces no reading rather than an uncited one. `fold.walk_days` makes that one pass per timeline instead of one fold per day: 2617 ms → 254 ms on a 30-day timeline, ~35 s → 3.5 s at the sixteen-timeline fork cap, byte-identical at every boundary and asserted so. Days are never summed across a tree — timelines share a prefix — so the tree-wide figure is how many timelines a line went over in, and `everywhere` is the one U21 wants. `verify`'s two cheap halves get their first production callers; its O(days²) loop deliberately does not, with the measurement. 29 new backend tests on both dialects; verified live over Postgres on a three-timeline `ashcroft` lineage at day 21 — `hr` over ceiling days 1–12 in all three, 33 claims and 258 folded figures each resolved back to a row. **One live defect closed, and it was not this unit's**: `test_status.py` hard-coded the store's port while `conftest` resolves it from the environment, so the only assertion that the report's credential cannot write had been skipping for anybody whose Postgres is elsewhere |

**U21 is in**, and the report prescribes.

| Unit | Status | Evidence |
|---|---|---|
| U21. What is worth automating | `[x]` | `[[automation]]` in the scenario, which took `SCENARIO_SCHEMA_VERSION` to 2 — the table is optional and a version-1 file would load, and it is refused anyway because the declared version is inside the content hash, so accepting one would defer the failure to a guard that can only say "the file has changed". **The golden fixture moved by exactly one line**, the content hash: nothing puts the catalog on the wire, which is asserted by a tripwire requiring its only two readers to be the loader and `report/proposals.py`. A candidate is proposed only where the fold found its line over the ceiling for three consecutive sim-days — a reporting threshold, deliberately *not* in `rates.TUNING`, because moving it changes no run's log, hash or readings. The payback is recomputed from the company's own arithmetic at the day boundary it cites, never stored: `step.draw_cost_of` was split out of `day_cost_terms` because integer division means the hours removed cannot be priced on their own (18 h/mo is `18 * 5 // 100 = 0`, while the difference of the two totals is 1). **It answers in load as well as in money**, because the evidence that motivates a proposal is an overloaded line and two numbers printed together read as the same size — `load_permille_before` is the kernel's recorded figure and `after` is `capacity.load_permille` over a smaller draw, with `clears_the_ceiling` saying outright. Measured over both shipped companies: the draw is at most 222 per mille of a line against a ceiling of 1000 and the largest candidate moves it by 100, so **no authored automation can clear an overloaded line on this content** — stated, pinned per line, and not fixed by re-tuning two companies until the demo looked better. Prose is the only generated half, and a sentence that cites nothing, cites outside the proposal's own evidence, or carries a figure the report did not compute is refused **twice** — at the prompt, so it is never cached, and at publication, because that copy is exported. A producer cannot add a proposal by naming one. The fifth composed callable joins the two services, and what crosses is a packet the report built. 38 new test functions; verified live over Postgres on a 21-day `ashcroft` run — People over ceiling all 21 days, load 1287 → 1187 against a ceiling of 1000 and reported as not clearing it, burn 31 → 30, runway 134 → 139, with 31 byte-equal to the `total_cost` the log says that day was charged and the cited sequence 95 read back as a `DAY_CHECKPOINT`. **No live defect of its own**; it knowingly widens a registered one — the universe route is synchronous and now makes a provider call per proposal in series, which the register now names as the worst of the three |

**U22 is in, and the report leaves the machine.**

| Unit | Status | Evidence |
|---|---|---|
| U22. The standalone export | `[x]` | `GET /report/runs/{id}/universe.html` — the same fold the JSON route answers, rendered rather than serialised, and `_universe` is the one function both call so the page a player is looking at and the file they mailed cannot disagree about a company. **It opens with no server and reaches for nothing**, verified from a filesystem with the browser offline: one request, for the document; nothing failed, no console error, zero scripts, and the stylesheet applied under its own `sha256-` policy on `file://`. **Escaping is a type rather than a discipline** — `markup.raw` is the only door for unescaped bytes and the suite asserts it has exactly two call sites in the service, the checked-in QR asset and the stylesheet; the type earned itself on the first render, when a definition list built by concatenation came out as the visible characters `<dt>Lineage root</dt>`. **The manifest is load-bearing**: `render` refuses a payload carrying a field no content class declares, so a new figure on the report fails the suite until somebody decides what it means to mail it — and the declaration is checked the other way too, so a pattern nothing produces fails as well. One field is declared and deliberately withheld, the model that wrote the prose. **The QR code is decoded by the suite** rather than assumed: `tests/qrread.py` reads the SVG's own path data back into a module grid, undoes the mask the symbol declares and parses the segment out of it. Nothing is time-stamped, so two exports of one Universe are byte-identical; the filename derives from a validated identifier, because a run id is whatever `POST /runs` accepted and this one lands in a header. 26 new backend tests plus 9 client ones. **One live defect, on its own new surface and invisible to every suite**: a timeline that would not fold rendered `None ≈ taken of None ≈ offered`, a `Shipped` of `None ≈` and an event at `seq None` — four fabrications wearing the authored-tuning marking, on the one timeline whose point is that it has no figures. Every assertion about a refused timeline was about the payload, where everything was correct. Verified live three ways: offline from a file, on Postgres through the launcher, and `docker compose up` end to end — start, settle, fork, press *Report*, and a two-timeline document named by the lineage root. Nothing appended: no event kind, no payload field, no shape version, no fixture |

**U23 is in, and the README leads with the product.**

| Unit | Status | Evidence |
|---|---|---|
| U23. The README hero and the first command | `[x]` | Eight seconds at the top of the page: the CEO walks to a director, the director says the thing only a conversation yields, the decision is taken in person, and the film cuts to the two timelines it produced and the diff between them at day 3 — *Avg lead time +3, Morale −3, Visibility −4*. **A recording, and the capture is what makes that checkable**: `frontend/scripts/hero.mjs` refuses to start without a kernel, creates its own run through the gateway, walks with the arrow keys and ends on the diff route's own rows, where `screenshots.mjs` next door deliberately falls back to a recorded genesis because what *it* photographs is the art. The two futures are made to differ off camera, because a decision taken inside day one diffs to a column of zeros until both timelines pass the next boundary. **Three things about the client only driving it reveals**: the store's `ceo` is the spawn and never moves — the live position is a `POSITION_ECHO` control frame in `ceoEcho`, and reading the wrong one routes every walk from the spawn; `typingTarget` gives the arrow keys to a focused BUTTON, so clicking the clock first films a CEO standing still with **no `CEO_INPUT` rows in the log at all**; and a held key's length is set by the client's *nominal* render clock rather than the kernel's achieved rate, measured at 36 against 20.4 and legs landing 1.8× too far. That last gap also stalls the render clock between echoes and raises a refusal banner the player did not cause, which is now in the register. `scripts/gif.mjs` is a dependency-free GIF89a writer with a median-cut quantiser and per-frame differencing — 94 frames of a 1200×675 UI in 0.34 MB — and `tests/hero.test.ts` **decodes** it with a reader written from the spec that shares nothing with the writer, pixel for pixel. 9 new backend tests plus 9 client ones. Nothing appended |

**One fix outside the plan.** `a69c304` — a command's events were never published to a connected
client. `_publish` had one caller inside the tick loop and published only what that batch returned,
so a client learned its own effects as a sequence gap on a later tick. Pre-existing since Phase 2,
and taken as its own unit rather than deferred because it sat under U10, U15, U16 and U25 — four
units that would have passed their tests and not worked live.

### What the shipped units found that no test was failing on

Every shipped unit but one turned up a live defect on its path. The pattern is worth keeping:

- The launcher's lifespan handlers were never called (`on_event` under an explicit `lifespan=`), and
  then the four mounted sub-apps' lifespans were dead for the same reason one level down. Two units,
  same bug class, same file.
- `modelgw`'s key scrubber passed only because the test key happened to be `sk-`-shaped.
- An exception inside a `logging.Filter` killed the container at startup while every test passed.
- `achieved_multiplier_permille` reports `1000` while the clock runs at ~55% of nominal.
- Postgres's append-only triggers are dropped and recreated *outside* the writer lease.
- The retrieved context was latency-dependent, so the *logged* evidence differed between two runs of
  one seed — M32 failing quietly, caught by U10 reviewing its own work.
- `RATE_CHANGED` has a live client reducer branch that nothing had ever reached.
- Five lookups raised `KeyError` for an arrived hire; `request_hire` hardcoded two default-scenario
  person ids.
- The tree was failing `tsc` while `npm test` stayed green, because vitest does not typecheck and
  there is no CI (`2c38686`) — **and it happened again**. U13's first `npm run typecheck` found a
  second one, a parameter property under `erasableSyntaxOnly` in `e796ebf`'s test double, sitting in
  a tree whose suites were all green. Two units in a row shipped over a guard that was somebody
  remembering to run a command by hand; it is a CI step now.
- A comparison test was flaky and nobody could have known: its ticker thread steps 120 ticks per 10ms
  against a 10,800-tick horizon, so it ends the run in under a second and the next comparison is
  refused rather than measured. It is a race between six comparisons and one ticker, this laptop wins
  it and a shared runner loses it — so U13's **first CI run** is what surfaced it. Bounding the ticker
  fixes it and weakens it, so the property now also has a deterministic test that forces the
  interleaving instead of racing for it.
- **`request_hire` made its own run unfoldable, and it had been that way since U7.** Re-issuing
  `HIRE_REQUESTED` calls `assign_direct`, which produces a `WORK_ASSIGNED` the fold *also* treated as
  an input and applied a second time — so any run that ever hired could not be replayed, reported or
  forked, and the report route answered 500. Found by U15 opening the report on a live run one
  command after the hire; no suite folded a run that hired.
- The plan's DDL bump for the cache table was not needed, and U12 is where that was noticed rather
  than paid: `create_all` is check-first and runs at every startup, so a *new table* arrives on the
  next boot of an existing store with nothing to migrate. DDL 3 was a wipe because the same bump
  added a column to `runs`; a table on its own is the carry-forward the plan wanted and could not
  have then. `test_store.py` drops the table from a provisioned store and reopens it, so the claim
  is a test rather than this paragraph.
- U7 is the exception that proves the pattern: it found no live defect, because U6 had left it a
  tripwire — a test asserting the scenarios directory offered exactly one company, with a docstring
  saying a second one was U7's. It failed on the first run, which is what it was for.
- `Offered`'s two readings of one authored checkpoint disagreed on the first company they were
  compared over: the genesis-catalog reading admitted `0`, because `catalog_to_state` writes
  `draw_delta: 0` where the state's `effect` simply has no key. A director could have written "0" and
  had it resolve to authored content. Found by U11 writing the cross-adapter equality test before any
  behaviour depended on it, and it is now parametrised over every shipped company.
- A forked child's clock could not be started, and every observable said it was running. U16 found
  it on the compose path rather than in its own suite: `set_rate` answered `applied`, appended
  `RATE_CHANGED`, wrote the rate to the row and reported it on `/runs/{id}/state`, and sim-time did
  not move. A fork is the first run in the system with **no tick task** — it arrives paused, and
  every other run got its task at creation — so `set_rate` had never had to build one. It started
  on the next restart, when `resume_all` did. This is the strongest argument yet for the plan's own
  rule that a unit ends at a real run and not at a green suite.
- A log `extra=` key that names a `LogRecord` attribute raises out of the `log.info()` that made
  it, before any filter. U16 wrote `extra={"created": ...}` — the obvious word — and `created` is
  the record's timestamp. Same family as the filter-that-killed-the-container, so the guard went
  beside it and now reads every `extra=` dict in the tree.
- **The client posted a command nobody issued, into the timeline it had just entered.** Found by
  U25 on the compose path, and it is the entry path U17 built rather than anything U25 wrote:
  clearing the store returns `rate` to the empty state's `1`, so leaving a *paused* timeline reads
  as a resume, and the shell re-states the held direction into a child that arrives paused and
  refuses it. Measured as `POST /runs/<child>/commands` two milliseconds after the lineage read,
  with **no matching line in the backend log** — a rejection is a successful request whose answer is
  no. Beside it, the same root made the clock control read ×1 over a world standing still. Nothing
  in either suite could have seen either: both need a real fork, a real switch and a real clock.
- **Comparing a React prop against a store value cannot see a transition.** U25's first fix was a
  guard on the run id, and it did not fire: the store is not React state, so clearing it and handing
  the run id up do not land in one commit, and the shell observes the new rate while still holding
  the old run id. What works is stating the baseline at the moment of the transition, which depends
  on no ordering at all. Worth keeping because the shape recurs anywhere a component watches both.
- **Every figure in the diff was rendered under the other timeline's name.** Found by U18 in the
  browser, and the worst thing that surface can do. The two column headings were a flex row of equal
  cards and the figure rows were a four-column grid of their own, so nothing made them agree:
  measured at 1568px, the *left* timeline's card spanned x 40 to 358 while its own figures sat at 462
  to 532, entirely under the *right* timeline's card. Every client assertion passed, because they are
  all about which figure carries which attribute rather than where it lands. jsdom computes no
  layout, so the regression test is about what made the drift possible — one column template both
  grids must read — and it fails when either declares its own.
- **Two timelines that had ended read as "running".** Also U18, and the row was not merely stale:
  `runs.terminal_reason` was **NULL** for both with rates of 1 and 3, and **neither log held a
  terminal event**. The fact exists only in the kernel's folded state, which is why the Universe tree
  gets it right — `lineage_tree` overlays its own fold — and why the report, which may not import the
  kernel, cannot. It reads it from its own fold instead, where it is exact and free. Third time a
  read surface has met U17's lesson that the row is a projection and the fold is the authority.
- **A suite that leaks window listeners fails the test written to catch the defect it imitates.**
  U25's `mount` removed the host and never unmounted the root, so every shell the file mounted kept
  its keyboard bindings — and a `keydown` in a later test made each of them submit a command for the
  run *it* was attached to. It failed in the full file and passed under `-t`, which is exactly how
  it was found.
- U12 is the second unit to find its defect in its own first shape rather than in the tree. Writing
  the entry where the answer arrives — inside the gateway, on any `Completion` — is the obvious
  placement and it silently breaks a decision this plan had already made: a reply that *ranks the
  options* is a perfectly good HTTP response, so a guard rejection would have been cached and served
  back forever, with "switch to a better model" as the remedy that changes nothing, because the
  address is the situation and not the model. Execution decision §3 says "guard rejections write
  nothing" and the first implementation passed every test while violating it. The write is now
  staged by `complete` and committed by `keep()`, which the leg calls only after the guards have
  passed — and the reason the seam moved is worth keeping: the gateway's lifetime was shorter than
  the decision that depends on it.
- Two of U11's own early tests passed for the wrong reason. A "a tick resolves to its sim-day" test
  used tick 540 — day 2 — and both `1` and `2` are authored option deltas on that checkpoint, so it
  passed on the option figures while claiming to prove the day lookup. This is the second unit to hit
  it (U5 found the metric its own stated verification rested on was near-insensitive), which suggests
  asking every unit *why* each new assertion fails when the behaviour is removed.

---

## MVP PRD — M1–M67 as of all twenty-five units of the MVP plan

Requirements this plan has moved are marked with the unit that moved them.

**Deploy and first run (M1–M8)**
- [x] M1 `docker compose up` brings up a playable client — **U1**, verified end to end
- [x] M2 one backend container running the launcher, all five surfaces in it — **U1**, **U24**
- [x] M3 store provisions itself on first boot; second boot reuses it — **U1**
- [x] M4 a run needs no model key — still true with the gateway present (**U8** reports absence rather than raising)
- [x] M5 client creates its own run, id in the address bar — `c45af79`
- [x] M6 a new run opens with a director already holding an unsettled checkpoint — **U2**
- [x] M7 two first-run hints — **U2**
- [x] M8 Apache-2.0 — **U1**

**Scenarios (M9–M13)**
- [x] M9 a company is a file — **U6**, `backend/scenarios/default.toml` and `schema.md`
- [x] M10 a run from the file is identical to a run from the compiled roster — **U6**, established by four digests taken from the constants before they were deleted
- [x] M11 the four lines and eight rooms stay fixed — **U6**, a fifth department or a new room is refused
- [x] M12 a scenario loads whole or not at all — **U6**, validation completes before anything is constructed
- [x] M13 a second scenario is selectable with no code change — **U7**. `ashcroft.toml` is the second company, chosen by name at run creation and offered by the client; the only thing that made it reachable was writing it

**The bench (M14–M22)**
- [x] M17 opening a checkpoint in person raises one statement request — **U10**
- [x] M22 no metric moves between the request and the statement — **U10**
- [x] M14, M18, M19, M20, M21 — **U11**. A director briefs and objects as two fields; a statement that
  prefers an option, ranks them, or compares two of them against each other is refused before the CEO
  sees it; a figure that resolves to no event, option or authored line is refused; every provider
  failure takes one exit naming its closed-enum condition; and with no key configured the conversation
  is the Phase 2 conversation exactly, asserted from both sides of the wire
- [x] M15, M16 — **U6** puts responsibility, tools, MCP servers and skills on the wire per person; **U7** renders them on the conversation surface under a `described, not wired up` marking, with nothing in the section to click. A third marking attribute rather than a reuse of either figure marking, so a tool list cannot satisfy R27's sweep over numbers

**The model gateway (M23–M30)**
- [x] M23, M24, M25, M26, M29 — **U8**
- [x] M27, M28 — **U9**
- [x] M30 the keyless path proven in CI — **U13**. Two of the four jobs run the whole suite with no
  provider configured, and the claim is asserted twice over: `test_bench.py` reads the workflow and
  refuses any provider key in it, and the keyless job exports a marker so the suite asserts the same
  of the process it is running in — which is the only place an organisation-level default would show

**Determinism (M31–M35)**
- [x] M31 a statement replays exactly — **U10**, strict replay passes because the request is derived inside `step()` rather than read from the log. Still true with the bench live: **U11**'s guards are lexical over a closed vocabulary precisely because their refusal is an output event the fold regenerates
- [x] M32 the retrieved context is logged and identical on replay — **U10**, after it caught its own window being latency-dependent
- [x] M35 a comparison branch is never written to the store — **U5** proved the clock cannot be starved by one, and **U19** made it a diff over every table in `metadata` around the command path, on both dialects: one row for a comparison that runs, none at all for one that is refused
- [x] M33 — **U12**, closed by **U16**. Responses are cached on the situation, which R29 already
  replaced M33's "tick, person, request" with: a digest of the assembled prompt, the authorization
  scope the context was drawn under, and a purpose namespace. The *shared by every fork* half was
  built, tested and inert until U16 copied the parent's `lineage_root_id` at fork; the test that
  pinned both sides of that line no longer needs its hand-written `UPDATE`
- [x] M34 — **U19**. Pre-divergence statements are byte-identical because the fork copies the
  parent's event rows and replay reads the log, so the cache is a cost optimisation and
  authoritative for nothing. Both halves are now assertions on a real lineage: `model_cache` is
  filled through the component that owns it, every timeline's boundary hashes are re-folded, the
  table is emptied and they are re-folded again — and the filled half is checked first, because a
  test that emptied an already empty table would pass forever. Beside it, two fresh lineages from one
  seed are compared timeline by timeline under R11's projection

**Memory and Authorization (M36–M43)**
- [x] M36, M37, M38 — **U14**. A director's memory is the events touching their line, read through
  the same admission pass a statement's evidence goes through, under a scope the kernel derives from
  folded state and the agents service cannot compute. The CEO reads a rolling summary and the events
  behind it; raw memory is not reachable, because the route answers with the *selection* and the
  whole scoped slice never crosses the wire. With no model configured the derived half renders whole
  and the summary reports itself absent — asserted through the published route, on the keyless path
  the suite already runs on
- [x] M39–M43 — **U15**. A director whose work is about another line asks the CEO, and the item stops
  until they answer. The need is derived inside `step()` from folded state, so nothing outside the
  kernel can raise one; a grant widens `authorized_scope` for **that item only** and never
  `remembered_scope`, which answers the product question U14 left open — a permission to read is not
  a change of who you are. M42 is structural rather than enforced, because the record is keyed by the
  item that asked, so a grant cannot become a standing permission and there is no settings surface
  that could make one. An abandoned request is a refusal and the record keeps which of the two it
  was, because the report prints it: every ask is a row carrying the asker, the line, the verdict and
  the sim-ticks the work stood still

**Forks, Timelines, Universe (M44–M52)**
- [x] M51 comparison stays an in-place preview
- [x] M44–M48 — **U16**, and M44 **reached from the game by U25**: the Decided panel lists every
  settled decision with the option taken marked and a fork per alternative, and pressing one lands
  the player in the child on the Universe stage with the new node open. A fork is a run: `POST /runs/{id}/fork` takes a past decision differently and hands back a timeline the client can switch into. The child id is minted from the fork's idempotency key, so two forks of one decision are two timelines and a retry is the first one's answer, including after a restart that emptied the ledger (M47); the child is born at the tick of the decision it reconsiders rather than at the parent's present tick, and resumes there (M45, R20); a fork of a fork of a fork folds and reports its whole lineage (M46); and the parent's log is byte-identical before and after, compared on the stored rows (M48). U10's finding that `statement_request_id` is not run-scoped is **not** closed here — closing it needs a run identifier the fold reproduces, which is a `State` shape change, and **U15** is the unit that already owns one
- [x] M49 — **U17**. The tree is the query the plan said it would be: `lineage_root_id` flat across a
  tree, `parent_run_id` the chain, one batched log read for the decision that separated each child
  from its parent. Three node states, a third stage drawn in the DAG's idiom, and one call that moves
  the clock — pausing the outgoing timeline before resuming the incoming one, so a crash between the
  two appends leaves nothing ticking. A restart starts one clock per lineage and pauses the rest in
  their own logs
- [x] M50, M52 — **U18**. `GET /report/runs/{id}/diff/{other}`, served by the report app the
  launcher mounts rather than by the gateway, because a diff is a fold and the gateway may not
  import one. Both sides fold through the kernel's own fold at **one tick** — a day's opening
  tick, which either timeline has reached or has not, so a day one side never got to is a refusal
  with the reason rather than a column of simulated ticks beside a column of history. The
  separating decision is named at the two timelines' nearest common ancestor, so two cousins are
  described by the decisions they actually took rather than by one neither of them did. Every
  figure carries the marking on the wire and on the surface, swept the way the HUD's tiles are

**The Report (M53–M61)**
- [x] M53 one report over the whole Universe — **U20**, identified by the lineage root rather than by the timeline that asked
- [x] M55 every claim resolves to its event — `services/report/fold.py` (**U24** mounted it), and **U20** made a claim's address the run *and* the sequence, because a fork copies its parent's rows
- [x] M56 where the company was overloaded, by line and over time — **U20**, folded at each day boundary because `LOAD_CHANGED` does not carry it
- [x] M57 what is worth automating, from the authored catalog, citing events and stating payback — **U21**. The catalog is `[[automation]]` in the scenario; a candidate is proposed only where the fold found its line over the ceiling for three consecutive sim-days; the payback is recomputed from the company's own cost arithmetic at the boundary it cites
- [x] M58 prose over cited figures only, and no figure from a model — **U21**, refused at the prompt and again at publication, with a test that the two refuse the same replies
- [x] M59 the marking on every figure, and the report says the company is invented — **U20**
- [x] M54, M60, M61 — **U22**. One standalone HTML file that opens with no server and makes no
  network request — verified from a filesystem with the browser offline, one request for the
  document and nothing else asked for. It carries its own data, styling and QR code; contains no
  script and says so in a policy that travels inside it; escapes by construction rather than by
  care; declares what it contains and refuses a payload carrying a field no content class claims.
  The QR code is a checked-in asset the suite *decodes* back to the repository URL. Reachable in
  one action from a live run, from any stage

**Closing the known holes (M62–M65)**
- [x] M62 staff movement on the wire — **U3**, and live only because `a69c304` publishes what a command committed; U3 measured that the delegation walk never reached the client before it
- [x] M63 command/tick synchronisation — **U4**
- [x] M64 comparison off the clock's pool — **U5**, closing `docs/residual-review-findings/` §2 and the remaining half of §1
- [x] M65 determinism over a lineage — **U19**. Every day boundary of every timeline re-folded from
  zero and compared, independently of `verify` and then through it; three timelines are one history
  below their divergence and three distinct futures above it; and a fork exported and replayed in a
  second interpreter reproduces its hash

**Launch (M66–M67)**
- [x] M67's first-command half — **U1**; the README leads with `docker compose up` and `company-os.html` is demoted to prototype
- [x] M67's CI proof — **U13**. The smoke job runs the README's first command from a clean checkout,
  builds all three images, and asserts the client is served, a run is created through nginx, and the
  bench reports absent with nothing spent
- [x] M66's hero — **U23**. Eight seconds at the top of the README: the walk, the conversation,
  the decision taken in person, then a cut to the two timelines it produced and the diff between
  them. A recording of one real run rather than a montage, and the capture is what says so —
  it refuses to start without a kernel, creates its own run, walks the CEO with the arrow keys,
  and ends on the diff route's own rows

---

## The shape of it

Across four completed plans, 28 units: 24 `[x]`, 4 `[~]` with a named gap, none abandoned — Phase 1's
U15 closed when MVP U16 minted a fork id that cannot collide. The
simulation half of the product is built and covered by four suites plus golden vectors across two
languages, and the daylight visual system is complete on top of it.

The MVP plan is **25 of 25 units in, and every one of the PRD's 67 requirements is met.** Roughly
8 were met when that plan was written — M13 is the one U7 closed, M15 and M16 stopped being
half-met, U11 closed the five the bench is made of, U13 closed M30 and M67's proof half, U12 built
all of M33 but the one line U16 owned, U16 closed that line plus the five forks are made of, U14
closed the three memory rests on, U17 closed M49, U18 closed M50 and M52, U15 closed M39 through
M43, U19 closed M34 and M65 and made M35 a store diff, U20 and U21 closed the report's seven, U22
closed the export's three, and U23 closed the hero. The suites went from 699 backend tests to
**1,520**, and the client from 404 to **617**.

What remains is still concentrated where the plan said it would be, but the shape has changed three
times. The bench was the plan's single biggest risk and the unit most likely to be "estimated as an
edit"; its contract, its transport and the four directors who actually brief and object all exist,
and no event payload, schema version or golden fixture moved to get there. **Forks were the plan's
second risk and are now in**, on the same terms. **The two read surfaces on top of them are in too**,
and on stronger terms than either: a memory and a Universe tree are both *queries* — no event kind,
no payload field, no shape version, no fixture, and nothing appended by either of them. **Authorization is in, and it is the one that cost the log something** — a fourth leg, a fifth hashed
subsystem, a state-shape move and a rules-version move, all of it for a mechanic whose whole point is
that a refusal stops work. It is also the unit that put the *stall* behind a request for the first
time: `raise_request` had claimed one since U11 and never had one. **The diff is in on the same
terms**, and it is the fourth read surface in a row that cost the log
nothing. **The report and its export are in on the same terms again** — five read surfaces in a row
that appended nothing, the last of which is a file that leaves the machine. What does not exist is
in *Scope Boundaries*, deliberately, and in the deferred defect register, knowingly.

**CI was the thing genuinely overdue, and it is now in.** Four jobs, no secret of any kind, and it
earned itself twice before it was ever green: a second `tsc -b` failure sitting in a tree whose suites
were all green, and a flaky comparison test whose race this laptop wins and a shared runner loses.
Both had been in the tree for units. What CI does *not* prove is written down rather than left to the
badge — the second boot that reuses the volume, the second writer that names the lease holder, the DDL
wipe path, and anything against a real provider.

**U16 is in, and it moved the bottleneck.** Five units came unblocked with it — U17, U18, U19, U20
and U25 — so Phase E and most of Phase F are now reachable, and the response cache U12 left inert is
live. It also cost the pattern its clearest illustration yet: the one defect the unit's own suite
could not see was a forked child whose clock would not start, and every observable — the command
outcome, the run row, the state endpoint — reported a running clock while sim-time stood still. It
took a `docker compose up` to find.

**Both read surfaces earned the pattern again, and neither found its defect in the tree.** U14's was
a *scoping* one it deliberately did not fix: `present_members` counts a hire who has left, so a line
that lost one keeps their headcount and goes on drawing 10,800 units a day for somebody who is gone —
measured, registered, and left to a unit that can re-baseline a metric. U17's was a *reading* one it
did fix, and it took running the thing to see: the tree drew its day from `runs.current_tick`, which
moves only on append, so a run at tick 58 had a row saying tick 1 and the Universe would have
contradicted the office it sat beside. Both are the same shape as U16's: every observable agreed, and
the disagreement was with sim-time.

**U25 is in, and it cost nothing the backend did not already answer** — no event kind, no payload
field, no shape version, no fixture, nothing appended. The third read-and-write-through surface in a
row built entirely out of routes that existed. It also earned the pattern for the ninth unit
running, and this time on somebody else's code: both live defects were on U17's entry path, both
were invisible to every suite, and one of them — a command the client posted that nobody issued,
into the timeline it had just entered — reached the player as a banner about a refusal they could
not have caused. The other made the clock control read ×1 over a world standing still. The root of
both is that a run's *rate* is the one field the client never learns from its log, and the half of
that which is not on the entry path is now in the register with a measurement beside it.

**U18 is in, and it turned the plan's central claim into a check.** R12 says every fold that crosses
timelines reads through the kernel's own fold, and until now that was enforced by an import sweep
and a docstring. Comparing at a day's *opening* tick — chosen because it is the only tick both
timelines have reached or neither has — put the comparison on the tick the kernel already
checkpoints its own state hash at. So each side's reported hash is the same string the log holds
for that tick, verified against Postgres on the compose path, and a second reconstruction anywhere
on that path would have to reproduce the kernel's hash to pass. It also earned the pattern for the
tenth unit running, and this time both defects were on its own new surface rather than on somebody
else's: a column of figures rendered under the *other* timeline's heading, and two ended timelines
described as running because the fact that they had ended is in neither the row nor the log.

**U19 is in, and the pattern held on somebody else's code for the second time running.** It was
written down as a suites unit and it closed three live defects, all of them on the fork and restart
paths and none of them visible to any suite. The registered defect turned out not to be in `verify` and not to be
about diagnosis at all: `simcore.log.fold` measured how far a run had got by the largest tick an
event *named*, while the replay schedules inputs by the tick they were *issued* at, so the two
disagreed by exactly the lead a scheduled input carries. What that refused was `fork` — the product's
central beat, M44 — for any decision taken inside that lead, and `resume_run` for any run whose bench
answer was in flight, which with a provider configured is two sim-days after every briefing.
**Measured live on the compose path, before and after, on the same three HTTP calls**, by mounting
the pre-U19 file over the built image. Thirty fork tests could not see it because every one of them
reaches its decision through a fixture that never walks the CEO, so none of them ever had a scheduled
input in the prefix it forked — the suite was uniform rather than thin, and the uniformity was in
the fixture.

It also settled the last of U16's review findings that named a unit: a fork now asks the questions it
inherits, so a briefing on one side of a fork and a deadline on the other stops being a difference
the option did not cause. That one is worth noting for *where* it was proved — the dispatch hop the
suite takes is a direct call, and the one deployment takes is `call_soon_threadsafe` from a
synchronous route, so it was verified in the container's own log rather than in the suite.

**Next is U20**, then U21 and U22 behind it; **U23** is unblocked too, since U18 shipped the diff its
hero ends on. U20 inherits a lineage harness, a `state_at_day` that now returns the kernel's own hash
in every case, and the open question U19 registered rather than took: `simcore.verify` has no
production caller at all, and a report over a lineage is where one belongs.

**U22 is in, and it is the fifth read surface in a row that cost the log nothing.** What it added
is a *refusal*: the export declares the content classes it may carry and `render` will not write a
payload holding a field none of them claims, so a new figure on the Universe report is a decision
about what this artifact mails to somebody rather than a diff nobody read. The declaration is
checked from both ends, so it cannot rot in either direction. Escaping stopped being a discipline
and became a type with two trusted call sites, and it earned itself on the first render — a
definition list built by concatenation came out as visible `<dt>` characters, because `Html + Html`
is a plain `str`. The QR code is checked in, as execution decision §5 settled, and the suite
*decodes* the asset back to the repository URL rather than trusting a picture. Its live defect is
the pattern's cleanest instance yet: a timeline that would not fold rendered four `None`s wearing
the authored-tuning marking, and no suite could have seen it, because every assertion about a
refused timeline is about the payload — where everything was correct.

**U23 is in, and it is the only unit whose deliverable is a picture.** The interesting part is that
"this is a recording, not a montage" is not visible in the file, so it is asserted in the capture:
the script refuses to start without a kernel, creates its own run, walks the CEO with the arrow
keys and ends on the diff route's own rows. It also turned up three things about the client that
only driving it from outside reveals — the store's `ceo` is the spawn and the live position is a
control frame in `ceoEcho`; a focused BUTTON owns the arrow keys, so clicking the clock first films
a CEO standing still with no `CEO_INPUT` in the log at all; and a held key's length is set by the
client's nominal render clock rather than the kernel's achieved rate, measured at 36 against 20.4.
The third of those is now a register entry, because the same gap stalls the clock between echoes
and shows the player a refusal they did not cause.

**The MVP is done.** Twenty-five units, six phases, sixty-seven requirements. **Every shipped unit
but one turned up a live defect no suite was failing on**, and the pattern never changed: every
observable agreed, and the disagreement was with something no observable reported.
A clock that said it was running over a world standing still. A tree whose day came from a row that
lags the fold. A column of figures under the other timeline's heading. Four fabricated numbers on
the one timeline that has none. The suite is 1,520 backend tests and 617 client ones and it did not
find any of them; `docker compose up` and reading the page did.
