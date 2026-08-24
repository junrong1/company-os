# Residual review findings — persistent forks (MVP U16)

Accepted knowingly on `feat/company-os-mvp-u16`, from a twelve-reviewer pass over the four
commits since `8cc64ab`: correctness, adversarial, security, reliability, api-contract,
performance, testing, maintainability, project-standards, architecture, simplicity, and a
learnings researcher checking the diff against this repo's own deferred defect register.

**Five findings were fixed on the branch** and are not repeated here: fork failures answering
with a sentence rather than a 500 (`FencedOut`, a writer timeout, `FoldRefused`); a timed-out
fork that commits being adopted rather than stranded; a retry reporting the child's birth tick
rather than its present one; the child-id seed made unambiguous by construction; and the fork
route's integer inputs bounded.

The items below were not. Each is real, each has a named owner or a unit it belongs to, and
none of them makes the shipped fork path wrong on the paths a player takes. They are recorded
here rather than left in a review transcript.

The review's verdict was **ready with fixes**, and no finding was P0.

---

## 1. No cap on children, and no eviction from `KernelRuntime.runs`

**Severity:** P2 · **Where:** `backend/services/kernel/loop.py` (`fork`) · Found by security and
adversarial, reproduced

There is no cap anywhere — not per parent, per lineage, or process-wide. Reproduced: 25
sequential forks produced 25 children, 26 rows and 26 `RunLoop`s, every call answering 200.
Each child costs up to `FORK_PREFIX_MAX_EVENTS` copied rows, a `runs` row, and a fully folded
`State` resident for the life of the process; `self.runs` has no `del` anywhere in the file and
the gateway has no DELETE route. `resume_all` folds every non-terminated run at startup, so N
children make startup O(N x prefix) and resident memory O(N x state).

The damage is nameable without an attacker, which is why it is P2 rather than a note: the
dedup key is the client's, so **the natural retry implementation — a fresh UUID per attempt —
makes every retry a new timeline** rather than the duplicate M47 collapses. U25 must mint the
key once per user intent, not once per HTTP attempt.

**Belongs to U17**, which owns the Universe surface and is the first unit with a place to show
a lineage's size, offer deletion, or refuse a fork that would make the tree unreadable.

## 2. Two synchronous routes now draw on one starved threadpool

**Severity:** P2 · **Where:** `backend/services/gateway/main.py` (`post_fork`),
`backend/services/kernel/loop.py` (`BRANCH_LIMITER`) · Found by learnings and adversarial

The register already carries this for comparisons: *"a queued comparison still holds a slot in
the route pool while it waits ... making it `async` reaches into the gateway and the launcher."*
U16 extends it rather than repeating it blindly — `post_fork` is a second synchronous route
whose handler parks on the same 2-slot `BRANCH_LIMITER`, and the diff's own comment on that
limiter says so out loud. But **the register entry was not updated**, so the next unit that
reaches for `async` would find it describing only half the exposure.

Forty concurrent forks occupy the whole FastAPI threadpool behind a two-slot semaphore and a
thirty-second writer wait. The clocks keep time — that is what the limiter is for — but the HTTP
surface stalls, and `diagnose` is one of the routes that then cannot be served, which is the
call an operator makes precisely when things are stalling.

**Closing it is the `async` change the register already scopes.** The register entry is amended
on this branch to name fork as the second route.

## 3. The fork's fold cost tracks the target tick, not the event bound

**Severity:** P3 · **Where:** `backend/services/kernel/loop.py` (`BRANCH_LIMITER` docstring,
`fork`) · Found by performance

`BRANCH_LIMITER`'s comment justifies sharing the comparison pool on the grounds that a fork
folds "a prefix of up to `FORK_PREFIX_MAX_EVENTS`". That is the wrong quantity. `_replay` runs
`sim.step()` once per tick from genesis to `born_at` regardless of how few events the prefix
holds, so a quiet run with a late decision costs the same as a busy one. `horizon_tick` is
client-supplied with no upper bound.

Not an active problem at the default horizon, and unmeasured either way — which is the point:
`BRANCH_SLOTS` carries a measurement table and this does not. **Wants a measurement before it
wants a change**, and the natural place is U19, which is already building lineage-wide folds.

## 4. `_fork_already_taken`'s `resume_run` fallback is the one unguarded fold

**Severity:** P3 · **Where:** `backend/services/kernel/loop.py` · Found by performance and
adversarial

Every other fold in this diff runs inside `BRANCH_LIMITER` with the prefix counted first. This
one — reached when a retry finds a child this process has never registered — folds the child's
whole log with no limiter and no bound. The trigger window is narrow because `resume_all` loads
every stored run at startup before requests are served, so it is reachable only for a child
committed by a call that died before registering it.

## 5. Three names for the fork point's sequence

**Severity:** P2 · **Where:** `logschema/tables.py` (`forked_at_seq`), `kernel/store.py`
(`ForkResult.copied_through_seq`), `proto/kernel.proto` (`ForkResponse.copied_through_seq`),
`kernel/loop.py` (`ForkOutcome.forked_at_seq`) · Found by maintainability, api-contract and
architecture independently

One value, three spellings, and the proto is additionally missing `parent_run_id` and
`decision_seq` that the shipped JSON carries. No test pins the names together. Because there is
no gRPC servicer the proto is a vocabulary document, so this is a documentation defect today and
a contract defect the day the split kernel returns.

**Align on `forked_at_seq`** — the DB column is oldest and most load-bearing — and add a parity
test. Cheap, and deliberately not done on this branch to keep the fix batch to the five
behavioural items.

## 6. `fork()` mixes validate/fold/decide with commit/register/publish

**Severity:** P2 · **Where:** `backend/services/kernel/loop.py` · Found by maintainability and
architecture

The stated invariant — *everything that can refuse happens before anything is written* — is
enforced by code order and a docstring, not by a boundary. The suggested shape is
`_plan_fork(...) -> _ForkPlan | str` doing every guard, the fold and `resolve_checkpoint`, with
`fork` reduced to plan, commit, register, publish.

**One constraint on whoever does it**, learned the hard way while fixing #1 on this branch:
`test_every_append_in_this_file_publishes_what_it_committed` requires the function that appends
to be the function that publishes, and extracting the commit half broke it within one test run.
That guard has to be moved deliberately, not routed around.

## 7. Duplication that a fix has to apply twice

**Severity:** P3 · Found by architecture and simplicity

- `resume_run` and `fork` compute `published_seq` and the bench filter over
  `folded.outstanding_requests` identically, by hand. A `RunLoop.from_fold(...)` constructor
  would express the invariant once; `create_run`'s genesis path stays the legitimately different
  case.
- `_parent_option_at` runs the same query and the same kind check as `_decision_at`, differing
  only in swallowing the non-decision case as `0`. It has one caller.

## 8. The unanswered-request refusal names a remedy that cannot work

**Severity:** P3 · **Where:** `backend/services/kernel/loop.py` (`fork`) · Found by correctness

The sentence ends *"Wait for the answer or let the request reach its deadline, then fork."* The
blocking set is computed from a **fixed** prefix (`seq <= at_seq - 1`) of an append-only log, and
both events that clear a request can only land above `at_seq` — so waiting can never change the
answer for that fork point. The refusal should say the fork point is refused permanently and that
a later decision is what to point at.

## 9. A forked child's inherited bench statements are never asked

**Severity:** P3 · **Where:** `backend/services/kernel/loop.py` (`fork`) · Found by correctness

`fork` fills the child's `statement_subjects` exactly as `resume_run` does — but `resume_run`'s
are dispatched by `start_background`, which has already run by the time anyone forks. So a
statement outstanding at the fork point is recorded on the child and asked by nobody, and is
abandoned at its deadline: **a parent/child divergence the option did not cause**, which is
precisely what M34 asks U19 to rule out.

Either hop to the loop after registering the child and call `_ask_outstanding_statements`, or
state why a child does not inherit the asking. **U19 should settle it**, since it owns the
property the asymmetry threatens.

## 10. Two narrow races on `self.runs`

**Severity:** P3 · Found by adversarial and correctness

- Two same-key forks close enough together that both pass the row check: the winner's
  unconditional `self.runs[child] = child` can discard the `RunLoop` the loser built through
  `resume_run`, orphaning any subscriber attached in that window. `setdefault` closes it.
- A retried fork calls `resume_run` on a **terminated** child, which `resume_all` deliberately
  skips. Moving the terminal check into `resume_run` gives both callers the same policy — and it
  is currently unreachable anyway, because nothing calls `store.terminate_run` (see the register).

## 11. Test gaps left open

- The clock-start fix's production path is untested. `test_starting_a_forked_child_actually_moves_
  its_clock` calls `set_rate` from the event loop, taking the direct-call branch;
  `call_soon_threadsafe` — how a synchronous route reaches it — is exercised only by a gateway
  test that asserts `status == APPLIED`, which is the same "everything says it's running" blind
  spot the defect lived in. **A test that drives `set_rate` from a worker thread and then asserts
  the tick moved is the one that would have caught it.**
- `submit_fork` is not in `test_no_store_round_trip_or_wait_happens_inside_the_lock`'s forbidden
  set, though it is a blocking store round-trip identical in shape to `submit`. That test's own
  docstring asks units to extend it. **Cheap and worth doing next.**
- `_decision_at`'s other two refusal branches (at or below GENESIS, past the parent's head) and
  `_fork_already_taken`'s digest-collision guard are untested.
- Nothing exercises two concurrent forks, or a fork racing `KernelRuntime.stop`.

## 12. `_start_the_clock_soon` lacks the guard its siblings have

**Severity:** P2 · **Where:** `backend/services/kernel/loop.py` · Found by reliability and
adversarial

It is the only one of this file's `call_soon_threadsafe` sites without `try/except RuntimeError`
for the loop-is-closing case, so a `set_rate` racing shutdown can report a 500 for a command that
already durably succeeded. The scheduled callback also does not re-check `run.stopped`, so it can
rebuild a tick task that `stop_run` has just cancelled.

---

## Corrections owed to the U16 record

Three claims in `docs/2026-08-16-mvp-execution-decisions.md` that the review checked and found
wrong or overstated. Amended on this branch:

1. **The `RUN_FORKED` justification.** The record credits the sequence alignment with making
   "the decision that separated them" cheap for U18 and U20. It does not: those units address
   through `runs.parent_run_id` / `forked_at_seq`, and `forked_at_seq + 1` is a fixed computable
   offset whether or not a marker event exists. The decision not to emit a zero-content event is
   still right, on the simpler ground that the fact is already durable in `runs`.
2. **The `BRANCH_LIMITER` docstring** justifies pool-sharing on the event bound; see finding 3.
3. **The register's route-pool entry** named only comparisons; see finding 2.
