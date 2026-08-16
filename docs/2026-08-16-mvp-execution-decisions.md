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
