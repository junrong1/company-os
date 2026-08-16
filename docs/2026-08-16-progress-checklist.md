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
- [x] `CONTEXT.md` — the glossary the PRD is written in

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

## MVP PRD — M1–M67 against what exists today

**Deploy and first run (M1–M8)**
- [ ] M1 `docker compose up` brings up a playable client — currently 503
- [~] M2 one backend container running the single-process launcher — the launcher exists (`1029078`); the one-container image does not
- [~] M3 store provisions itself on first boot — SQLite path created on first run (`1bbab5f`), Postgres provisioned in compose (`1d2d357`); unproven under the new shape
- [x] M4 a run needs no model key — trivially true today, must survive the bench
- [x] M5 client creates its own run, id in the address bar — `c45af79`, `net/runid.ts`
- [ ] M6 a new run opens with a director already holding an unsettled checkpoint — no genesis path assigns work, so nothing is waiting at tick 0
- [ ] M7 two first-run hints
- [ ] M8 Apache-2.0 — no LICENSE file in the repository

**Scenarios (M9–M13)** — [ ] none. People and items are compiled into `simcore/people.py` and `simcore/items.py`.

**The bench (M14–M22)** — [ ] none. `services/agents/stub.py` declines every request; no model call exists anywhere.

**The model gateway (M23–M30)** — [ ] none.

**Determinism (M31–M35)**
- [x] M35 a comparison branch is never written to the store — the branch runner is in memory and discarded
- [~] M31, M32 the substrate exists — agent statements already enter the log as input events through the pending-input contract — but there is no model statement to log
- [ ] M33, M34 response caching on (tick, person, request) and variance isolation

**Memory and Authorization (M36–M43)** — [ ] none.

**Forks, Timelines, Universe (M44–M52)**
- [x] M51 comparison stays an in-place preview, discarded when the checkpoint closes
- [~] M44–M48 `store.fork_run` exists and is replay-tested, but the child id collides at a shared tick and arrives paused
- [ ] M49 the Universe tree, M50 the two-timeline diff, M52 marking inside a diff
- [x] the marking itself exists everywhere else it is required — Phase 3 U2

**The Report (M53–M61)**
- [~] M55 every claim resolves to its event — built in `services/report/fold.py`
- [ ] M53 Universe coverage, M54 standalone HTML export, M56 overload diagnosis, M57 automation proposals, M58 prose over cited figures, M59 the invented-company statement, M60 QR and link, M61 reachable from the client

**Closing the known holes (M62–M65)** — [ ] all four. M64 is mitigated but not closed.

**Launch (M66–M67)** — [ ] neither. The README still leads with `company-os.html` as the demo
artifact, and there is no `.github/workflows` at all, so nothing verifies the first command.

---

## The shape of it

Of the 28 planned units across three plans, 23 are `[x]`, 5 are `[~]` with a named gap, and
none was abandoned. The simulation half of the product is built and covered by four suites
plus golden vectors across two languages.

Of the PRD's 67 requirements, roughly 8 are met today and the remainder is new construction —
concentrated in the agent half (M14–M43), which does not exist at all, and in the deploy path
(M1–M8), which is the one a stranger hits first. The PRD's own sequencing note says deploy and
the known holes come first, the bench second, forking third.
