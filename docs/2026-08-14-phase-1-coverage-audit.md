---
date: 2026-08-14
topic: phase-1-coverage-audit
---

# What Phase 1 requires and what Phase 1 built

An audit of `docs/brainstorms/2026-08-12-company-os-platform-requirements.md` (R1–R63) and
`docs/brainstorms/2026-08-14-playable-loop-requirements.md` (Phase 2 R1–R26) against the code, run
after Phase 2 shipped. Every finding below was checked against the code or reproduced at runtime;
nothing here is inferred from documentation.

The headline: Phase 1's kernel is thorough and well tested, and its **client is not fed**. Three
capabilities were written, tested in isolation, and never connected to anything — the same failure
the Phase 2 brainstorm identified for the CEO avatar. Two of the three are now connected. One is
not.

---

## Open gaps

### 1. Staff do not move on the client (R13, R17) — deferred by decision

**Deferred on 2026-08-14: staff movement is not needed for this phase.** The verdict this
phase exists to produce is about whether the in-person loop is worth playing, and that is
reachable with a static floor — the CEO moves, the beam marks who is waiting, and the
conversation carries the mechanic. Recorded here rather than closed, because the requirement
is still unmet and the next phase should know it inherited this rather than solved it.

**Verified.** No event carries a person's position, path, or state. Reproduced:

```
director moved in kernel state: (21, 13) -> (20, 3)
event kinds emitted: WORK_ASSIGNED — position/path fields: NONE
```

`_walk_to` sets `person.path` and `path_start_tick` in kernel state and emits nothing.
`_advance_walker` moves the person tick by tick and emits nothing. On the client,
`store.people` is written only by `readGenesis` and by a resync's snapshot — there is no
`patch.people` on any event path.

So during a run every staff member is drawn frozen at their desk, in whatever state genesis
recorded. The consequences are wider than they look:

- **R13** ("staff movement is transmitted as intent — an actor, a resolved path, and a start
  sim-time") is not implemented.
- **R17** ("the client interpolates actor movement locally at display framerate") has nothing to
  interpolate. `milliTilesProgressed` and `walkDurationTicks` exist in `render/interpolate.ts` and
  are golden-tested, and — exactly like `ceoStep` before this week — are called only by tests.
- The prototype's proven delegation mechanic is invisible. The Phase 1 brainstorm's own problem
  frame says "delegation is visible as a director walking to his specialist's desk"; in the port
  the director teleports, or rather never appears to move at all.

The design is already specified by R13 and the client half already exists. What is missing is an
event carrying `(person, path, start_tick)` when `_walk_to` fires, and a fold for it.

**Patched in the meantime:** the waiting beam and the conversation's activity line are now derived
from the tray and the item map (commit `d164457`), so R20 works and the conversation no longer
greets a person holding a decision as "free right now". That is a patch over this gap, not a fix
for it.

### 2. The compose stack cannot work — no gRPC client leg (R11 in practice)

**Verified.** `services/kernel/grpc_server.py` exists; nothing anywhere constructs a channel
(`insecure_channel` / `secure_channel` appear nowhere outside the venv). `gateway_main.use_kernel`
has exactly one caller, `single_process.py:226`.

Under `docker compose up` the gateway therefore starts with no kernel client and answers 503 to
every route, including `/status`'s kernel probe. Single-process mode is the only working topology.

Already recorded as deferred in the Phase 2 brainstorm; recorded here because "deferred" and
"the documented demo path does not run" are different facts.

### 3. The report is built and unreachable (R52, R53)

**Verified.** `services/report/main.py` serves `GET /runs/{run_id}/report` and
`services/report/fold.py` builds it. The client never references it — no fetch, no route, no
component.

R53 ("every claim in the report resolves to the event that produced it, and that event is playable
back in the office") has no implementation on either side: nothing links a report claim to a
replay position.

Already recorded as deferred in the Phase 2 brainstorm.

### 4. R44 — the in-person claim is never validated

**Verified.** No `claimed_pos`, `claimed_position` or staleness window anywhere in `packages/` or
`services/`.

`resolve_checkpoint` takes `in_person: bool` and trusts it. The Phase 1 brainstorm is explicit
that this is "a correctness guard, not a trust boundary" and that the residual risk is a
position-versus-state race silently mis-recording tacit knowledge — the product's differentiator.

Accepted knowingly in the Phase 2 brainstorm's "Known holes"; still open.

### 5. R41 — nothing on screen says the numbers are authored tuning

**Verified.** The phrase appears in two code comments and on no rendered surface. R41 requires
that *every* surface showing load, morale, a metric delta, runway or a trajectory marks it as
authored tuning rather than a modelled projection, and says the rule travels with the report,
"whose reader is not the operator". The HUD shows all five of those things and marks none of them.

R61 is partly met: `report/fold.py` carries `rules_ver`, but the authored-tuning marking R61 also
requires is absent.

---

## Gaps found and closed during Phase 2

These were all Phase 1 requirements that no Phase 1 unit discharged. They blocked Phase 2 work and
were fixed in the commits named.

| # | What was wrong | Requirement | Fix |
|---|---|---|---|
| 1 | A held direction moved the CEO one tick's worth — 0.144 tiles — then stopped. The client sends one command per *change* of direction; the kernel read `ceo_inputs` only at the exact tagged tick. Every existing test re-submitted an input every tick, so nothing caught it. `ceo_inputs` also grew unboundedly inside hashed state. | R35, R43; Phase 2 R2 | `26ba36e` |
| 2 | Nothing fed the render clock. It sat at its start tick reporting itself stalled, so the client had no usable notion of the kernel's tick — and every command tagged "a few ticks ahead" of a stale tick lands in the past and is rejected. | R35 | `8083157` |
| 3 | Events are appended only on ticks that produce one. A measured run emits on **about five ticks in twelve hundred** (mean gap 216, max 372), so the client's clock had nothing to chase for hundreds of ticks. The position echo — once per sim-hour — is the only regular signal, and the extrapolation budget was one tick. | R35 | `8083157` |
| 4 | The CEO was written into the kernel, golden-vectored on the client, and drawn nowhere. `actorsFromStore` projected staff only. | R43; Phase 2 R1 | `6c63425` |
| 5 | The genesis roster carried `dept`, `mgr`, `rank`, `seat` and no name or title, so a conversation could only ever have named `stf_ap`. | Phase 2 R6 | `e5ee97d` |
| 6 | The tacit line was on no event and in no payload, so the line the whole in-person mechanic exists for could not be shown before the CEO chose. | R6; Phase 2 R7 | `32f3401` |
| 7 | Unlock announcement lived in item completion, so a Visibility gain from any other source unlocked work silently. | R8 | `9fe4e03` |
| 8 | Nothing in the client reached the run-creation route, so a session began by running a command in a terminal. | Phase 2 R25 | `c45af79` |
| 9 | `typingTarget` could return `undefined` rather than a boolean (`isContentEditable` is absent in jsdom, and `\|\|` yields its last operand). | — | `ae557fd` |

---

## Verified present

Checked and found implemented, with tests.

**Kernel** — R1 pure step; R2/R3 append-only log with input events; R4 headless; R5 work stops at
an unresolved checkpoint; R6 in-person versus tray pricing; R7 deliverable provenance; R8 unlock
gates; R9 fork (`store.fork_run`); R35 fixed quanta; R36 geometry at genesis; R37 producer-agnostic
pending input, with the AE10 stub resolver in `services/agents/stub.py`; R38/R56/R57 per-person
morale, degradation floor, hiring exemption; R58 position derived from tick count.

**Service** — R10 authoritative clock; R12 persistence across restart (38 store tests); R14
pausable clock with multipliers; R15 domain-model seam; R39 loopback bind; R40 rejections carry a
reason; R50 horizon at genesis; R51 insolvency at a tick boundary; R52 report folded from the log
(built — see gap 3 for reachability).

**Client** — R16 imperative renderer outside React; R18 sprite grids and floor generation (visual
parity itself is judged by eye and deferred); R19 panels render from streamed state; R54 HUD
trajectories, runway, capacity heat, decision pressure; R55 configurable composition persisted in
local storage with non-removable tiles; R62 per-metric favourable direction; R63 decision pressure
in neutral chrome.

**Capacity and hiring** — R20–R25, R47, R48, R49, R59, R60: queues, aggregate load, soft ceiling,
hiring through People with lag, desk guarantee with refusal, baseline draws, draw-derived
`manualHours`, draw-to-burn.

**Mid-run steering** — R26 reassign within a line, R27 cross-line rejected, R28 bypass costs
morale and records the director, R42 return to backlog.

**DAG** — R29, R30, R31, R45, R46, plus the chain strip.

**Testing** — R32 (42 parity tests), R33 sprite validation, R34 (38 replay tests).

---

## Phase 2 coverage

All 26 Phase 2 requirements are discharged; see the plan's coverage table. Two carry caveats worth
naming:

- **R20** works only because of the derived patch described in gap 1. It will keep working, but it
  is reading the tray rather than the floor's own state.
- **R3's** divergence detection is real but has never fired against a genuine kernel disagreement,
  because client and kernel now apply identical inputs at identical ticks by construction. That is
  the intended outcome, and it means the reconciliation path is exercised only by its unit tests.

---

## What this suggests doing next

1. **Decide about compose.** Gap 2 is a real blocker for anyone who is not the author, and it is
   the difference between "clone and run" and "clone, read the launcher, and run the launcher".
2. **R41's marking is cheap** and the brainstorm argues it is load-bearing for credibility. It is a
   line of copy per surface.
3. **Staff movement (gap 1) when the loop has earned it.** Deferred for this phase by decision, not
   by oversight. The design is already written (R13) and the client half exists and is
   golden-tested, so it stays cheap to pick up — but it is the phase after the verdict, not before
   it.
