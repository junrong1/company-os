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
| U15. Fold, snapshot, replay, fork, export | `[~]` | `413f101`, `e1e924c`; `simcore/{log,snapshot,verify,export}.py`. **Fork ids derive from parent + sequence alone, so two forks at one tick collide, and the child arrives paused.** |
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
| R44, the in-person claim is never validated | audit gap 4 | **closed as a decision** — an explicit PRD non-goal while the product is local |
| R41, authored-tuning marking on every surface | audit gap 5 | **closed** by Phase 3 U2 |

---

## Phase 5 — the MVP (`docs/plans/2026-08-16-001-feat-company-os-mvp-plan.md`)

Twenty-five units in six phases. **Thirteen are done: Phases A and B are complete, and Phase C has
the bench and its CI.** Execution paused here by decision, with the tree clean and both suites green
— not blocked. The plan's open questions and everything execution resolved or found are in
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

**Phase C — the bench.** Only the cache is left.

| Unit | Status | Evidence |
|---|---|---|
| U8. The model gateway | `[x]` | `fbecec1` — two wires, eight providers, a key type that cannot be printed |
| U9. The ceiling and its counter | `[x]` | `084b529` — 200 calls / 600k tokens per run, DDL 3, the one measured HUD figure |
| U10. The statement contract | `[x]` | `86176c8` — the delivery leg that had no caller, servicer or client; the request derived inside `step()`; four bugs its own review pass caught |
| U11. Four directors who brief, object, and never rank | `[x]` | The two predicates in `simcore/statement.py`, `Offered` with an adapter per process, personas and prompts off the genesis roster and catalog, one fallback exit naming its condition, a fourth marking, and the pending block. 73 new backend tests plus 12 client ones |
| U12. Caching on the situation | `[ ]` | Unblocked by U11 — the assembled prompt exists, and there is one provider call site to wrap |
| U13. Continuous integration for the keyless path | `[x]` | `.github/workflows/ci.yml` — four jobs, no secret of any kind: the keyless suite on both store dialects, the bench against the mock adapter, the client's four steps with the type check separated out, and `docker compose up` from a clean checkout reaching a client that creates a run. Found a live `tsc -b` failure in the tree on its first run |

**Phases D, E, F.** Not started. U14 and U16 are unblocked; U15, U17–U23 and U25 sit behind them.
U14's `Panels.tsx` half no longer collides with anything — U7 put the person's schema on the
conversation rather than in the panel rail, and U11's bench block is its own section below the
decision card.

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
- U7 is the exception that proves the pattern: it found no live defect, because U6 had left it a
  tripwire — a test asserting the scenarios directory offered exactly one company, with a docstring
  saying a second one was U7's. It failed on the first run, which is what it was for.
- `Offered`'s two readings of one authored checkpoint disagreed on the first company they were
  compared over: the genesis-catalog reading admitted `0`, because `catalog_to_state` writes
  `draw_delta: 0` where the state's `effect` simply has no key. A director could have written "0" and
  had it resolve to authored content. Found by U11 writing the cross-adapter equality test before any
  behaviour depended on it, and it is now parametrised over every shipped company.
- Two of U11's own early tests passed for the wrong reason. A "a tick resolves to its sim-day" test
  used tick 540 — day 2 — and both `1` and `2` are authored option deltas on that checkpoint, so it
  passed on the option figures while claiming to prove the day lookup. This is the second unit to hit
  it (U5 found the metric its own stated verification rested on was near-insensitive), which suggests
  asking every unit *why* each new assertion fails when the behaviour is removed.

---

## MVP PRD — M1–M67 as of twelve units of the MVP plan

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
- [x] M35 a comparison branch is never written to the store — and **U5** now proves the clock cannot be starved by one
- [ ] M33, M34 — U12, U19

**Memory and Authorization (M36–M43)** — [ ] none. U14 is unblocked; U15 behind it.

**Forks, Timelines, Universe (M44–M52)**
- [x] M51 comparison stays an in-place preview
- [~] M44–M48 `store.fork_run` is replay-tested and `runs.lineage_root_id` now exists (**U9**), but the child id still collides at a shared tick and arrives paused — U16, which also inherits U10's finding that `statement_request_id` is not run-scoped
- [ ] M49, M50, M52 — U17, U18

**The Report (M53–M61)**
- [~] M55 every claim resolves to its event — `services/report/fold.py`, and the report is now mounted and answering in the one process (**U24**)
- [ ] M53, M54, M56–M61 — U20 through U22

**Closing the known holes (M62–M65)**
- [x] M62 staff movement on the wire — **U3**, and live only because `a69c304` publishes what a command committed; U3 measured that the delegation walk never reached the client before it
- [x] M63 command/tick synchronisation — **U4**
- [x] M64 comparison off the clock's pool — **U5**, closing `docs/residual-review-findings/` §2 and the remaining half of §1
- [ ] M65 determinism over a lineage — U19

**Launch (M66–M67)**
- [x] M67's first-command half — **U1**; the README leads with `docker compose up` and `company-os.html` is demoted to prototype
- [x] M67's CI proof — **U13**. The smoke job runs the README's first command from a clean checkout,
  builds all three images, and asserts the client is served, a run is created through nginx, and the
  bench reports absent with nothing spent
- [ ] M66's hero — U23

---

## The shape of it

Across four completed plans, 28 units: 23 `[x]`, 5 `[~]` with a named gap, none abandoned. The
simulation half of the product is built and covered by four suites plus golden vectors across two
languages, and the daylight visual system is complete on top of it.

The MVP plan is **13 of 25 units in**, with Phases A and B complete and Phase C holding the bench and
its CI. Of the PRD's 67 requirements, roughly 8 were met when that plan was written and about 42 are
met now — M13 is the one U7 closed, M15 and M16 stopped being half-met, U11 closed the five the bench
is made of, and U13 closed M30 and M67's proof half. The suites went from 699 backend tests to
**1,131**, and the client from 404 to **491**.

What remains is still concentrated where the plan said it would be, but the shape has changed. The
bench was the plan's single biggest risk and the unit most likely to be "estimated as an edit"; its
contract, its transport and now the four directors who actually brief and object all exist, and no
event payload, schema version or golden fixture moved to get there. What does not exist is everything
behind it: memory, Authorization, persistent forks, the Universe, the report.

**CI was the thing genuinely overdue, and it is now in.** Four jobs, no secret of any kind, and it
earned itself in its first minute: a second `tsc -b` failure was sitting in a tree whose suites were
all green, the same class as the one `2c38686` fixed. What it does *not* prove is written down rather
than left to the badge — the second boot that reuses the volume, the second writer that names the
lease holder, the DDL wipe path, and anything against a real provider.

Next is a fork in the road rather than a queue. **U12** (the cache) is small and sits behind nothing.
**U14** unlocks one unit; **U16** unlocks five — U17, U18, U19, U20 and U25 — so it is the wider
opening, and it is three defects above a store fork that is already nearly free.
