"""The tick loop: the kernel's clock, and the only thing that appends.

Ten decisions here are load-bearing, and each prevents a failure that is hard to diagnose
from its symptom.

**One task per active run, and its lifecycle follows the run's persisted rate — not the
subscriber count.** Starting a loop on subscribe would make clock ownership conditional on a
gateway event, and would race two loops into existence on near-simultaneous subscribes. That
is the multiple-tick-loop failure arriving through the front door. Rate is run state, so a
reload does not pause the clock and a disconnect does not either.

**A strong reference is held for the task's lifetime, and cancellation is explicit.** The event
loop keeps only weak references to tasks, so a dropped handle can be collected mid-execution —
and the failure mode is not an exception, it is the simulation silently stopping. Bare
`create_task` also swallows the exception from a crashed loop, so the task is wrapped and its
exception retrieved.

**The kernel is invoked off the event loop.** FastAPI's threadpool applies only to functions
FastAPI itself calls; a synchronous `step()` invoked from inside an `async` loop runs inline and
stalls every request and every WebSocket send. Kernel invocation and every long fold go through
`anyio.to_thread.run_sync`. This keeps the loop responsive; it does not make ticks cheaper,
because the GIL still serialises pure-Python work.

**One lock per run, and it is a `threading.Lock`** (R13). The command route is a synchronous
FastAPI route, so Starlette runs it on a worker thread; `_advance` runs on an anyio worker
thread. Both touch `run.state`, and until this nothing in `backend/` outside tests held a lock
at all. An `asyncio.Lock` would be the wrong primitive rather than a slower one: it is not
thread-safe, and its `acquire` is a coroutine that has to be awaited on the event loop — which
is not where either holder runs, so from a worker thread it would be no lock at all. A plain
`Lock` rather than an `RLock` because neither path re-enters. It is a per-run *attribute* rather
than a private detail so that a multi-run operation can take two of them in run-id order (R22),
which is what U17 will need. And it is taken **per quantum rather than across the batch**: a
partly advanced batch is a consistent state, while holding across 120 quanta would make a
command wait for 120 append round-trips.

**Nothing slow happens while that lock is held.** No append, no store round-trip, no wait, and
— when U10 puts a director's answer on this path — no provider call. The tick loop needs the
lock dozens of times a batch for about ten microseconds each, and a blocking call inside it does
not raise, it just makes the clock run slow, which is the failure this file's diagnostics were
built to explain rather than to cause. The rule has one consequence worth stating: the single
command that reads the *whole* state runs against a copy taken under the lock rather than under
the lock itself. `test_no_store_round_trip_or_wait_happens_inside_the_lock` reads this file to
keep the rule true.

**The clock's worker slots and a comparison's are different pools, and readiness asks whether
the clock is *moving*** (R14). Both halves close one failure. `anyio.to_thread.run_sync` draws
from a process-wide 40-slot limiter, and until this the tick loop, the lease heartbeat and every
synchronous FastAPI route drew from that one limiter — including the comparison route, whose
handler is about a quarter of a second of pure Python per option and up to six options per
command. Measured before the change, at rate 3 with the horizon a branch actually runs to: one
comparison at a time costs the clock nothing (947-960 permille of nominal), eight at once costs
it real time (894-926, and sim-time lag appears), and sixteen at once halves the clock
(683-744 permille, 558-689 ticks of lag). Nothing raises while that happens; the clock just runs
slow.

So there are two limiters and they are deliberately different primitives, because they are
acquired from different worlds. The clock's is an `anyio.CapacityLimiter` sized at one slot per
run plus one for the heartbeat, taken by `to_thread.run_sync` from the event loop — a dedicated
limiter is not merely a bigger allowance, it is a *disjoint* one: with the default limiter fully
borrowed, a hop on its own limiter still starts immediately. Branch execution's is a
`threading.BoundedSemaphore`, because it is acquired on a request thread that may have no event
loop at all — an `anyio.CapacityLimiter` has no blocking synchronous `acquire`, and releasing one
from a worker thread would touch event-loop objects from the wrong thread. Saturating it makes a
comparison *queue*, holding no GIL while it waits, rather than compete.

Readiness is the other half, and without it the first half would be invisible. `healthy()` asked
whether the tick task was `done()`, which a starved clock is not — it is alive, waiting for a
worker slot or for the GIL, and reporting ready. It now asks when sim-time last moved, so a
kernel whose clock has stopped says so and names the tick it stopped at.

**Catch-up is clamped, and read from a monotonic clock.** Elapsed real time decides how many
quanta to run, which taken naively fast-forwards a sim-week after a laptop sleeps. When the
clamp binds, the clock falls behind rather than sprinting — quanta are never skipped, so
falling behind means the clock runs slower than requested and the lag is reported.

**Nothing is published before it is durable** (R20). A committed transaction can still roll back
after power loss, so publishing before appending would leave the client's rendered world ahead
of the authoritative log with nothing able to detect the divergence.

**Every append publishes what it committed, and the order is decided on the event loop rather
than by whichever thread got there first.** `_publish` used to have one caller — the tick loop —
so a command's own events reached a connected client only as the *sequence gap* some later tick
revealed. Measured on a live run: a hand-off answered `produced_seq: [3, 4]` and the client's
applied sequence stayed at 2 until the director's arrival forty wall seconds later, then jumped to
6 with the gap banner showing. Both dropped events were the assignment and the walk it caused,
which is the whole of what makes delegation visible.

Two things make that transport work rather than a missing line, and they are separate problems.

*The thread boundary.* `apply_command` runs on a Starlette worker thread and `_advance` on an
anyio one, while every subscriber queue is an `asyncio.Queue` belonging to the event loop — and
`asyncio.Queue` is not thread-safe. So delivery is marshalled with `loop.call_soon_threadsafe`,
which is the documented way in and the cheapest one: a deque append and, only when the loop is
idle, one byte down its self-pipe. Not `run_coroutine_threadsafe`, which wants a coroutine there
is no need for and hands back a future the committing thread would have to either discard or
*wait* on — and waiting on the command path is what R13 forbids. The loop handle is recorded by
`subscribe` and `ensure_loop`, both of which are on the loop by construction: the first hands out
the loop-bound queue, the second calls `create_task`. (`_echo_position` still enqueues from a
worker thread with no such hop. That is its own entry in the deferred defect register and is
deliberately not fixed here.)

*The ordering, which is the hard half.* `_advance` releases its lock before appending, so a
command's mutation can happen after a tick's and its append can still land first — publish in
whatever order the threads observe their own commits and sequence 4 goes out ahead of 3. The
client treats a hole as a resync trigger, so a publish that ignored order would manufacture the
very gap it was added to close. Order is therefore not left to thread scheduling: every committing
thread hands its envelopes to the loop, and the loop alone decides. It holds back anything above
`published_seq + 1` and releases only the contiguous run below it. One strictly increasing sequence
per run reaches the wire whatever order the threads arrive in.

The cursor sits behind a lock of its own, and the reason is worth stating because the lock does
almost nothing in deployment: funnelling every committer onto the loop already makes the ordering
single-threaded, so `publish_lock` covers only the two cases where the funnel is absent — a caller
with no event loop at all, which the suite has, and the instant a loop is first recorded while
another thread is inside. It is emphatically **not** `run.lock`: it is held for a few dictionary
operations and a non-blocking enqueue, takes nothing else, and nothing inside it can want the run's
lock — so no clock ever waits behind it and there is no order between the two to get wrong.

That rests on one invariant, so it is stated rather than assumed: **every append site in this file
publishes what it committed.** Per-run sequences are gapless by construction — `append_tick` reads
the committed head and adds one, and an aborted tick commits nothing — so the held-back set always
drains as long as nothing appends silently. There are six append sites here and each publishes;
`test_every_append_in_this_file_publishes_what_it_committed` reads the file to keep that true. The
sixth is `fork`, which publishes into the child it just registered rather than into the parent it
copied — a fork appends nothing to its parent at all (M48).
`RATE_CHANGED` is published for that reason as much as for its own sake: an unpublished sequence
is a hole every later frame would wait behind forever.

**The kernel asks the bench, and the asking is a dispatch rather than a call** (U10). A statement
request is raised inside `step()` — from folded state, so strict replay reproduces it — and then
somebody has to carry it to a director and bring the answer back. That somebody is here, and its
shape is dictated by three rules that already existed.

*R2 says no network call happens inside `step()`*, so the dispatch cannot be part of raising the
request. It hangs off the append instead, the way `_publish` does and for the same reason: a
`_advance` that handed its committed envelopes back for a caller to dispatch would be a rule, and
`_advance` has callers outside this loop.

*R13 says nothing slow happens inside the run lock*, and names a provider call specifically. So the
producer runs on a worker thread with no lock held, and only the answer comes back under one — for
the two dictionary operations `receive_answer` performs, plus the rate, which is read there because
the rate and the tick are one fact and execution decision §2's landing-tick branch depends on both.

*And R14 says the clock's worker slots are the clock's.* A statement is answered on the **default**
limiter, never on `clock_slots`: a provider that takes its full timeout while holding a clock slot
is a stopped clock, which is precisely the failure the disjoint limiter was introduced to prevent.

The dispatch is idempotent per request id, because there are two sources for it. A request raised
this tick arrives from `_advance`; a request that was outstanding when the process died arrives from
the fold at resume, which is what makes a restarted kernel able to ask its question again rather
than wait out a deadline it cannot see. Neither source may double-ask, because a second answer to
one request is refused by the partial unique index and would reach the client as a rejection.

The producer itself is installed by the launcher rather than imported, exactly as the kernel client
and the spend reader are: R4 forbids this service from importing the agents service, and a kernel
that reached for one would be the import-boundary failure arriving as a feature.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import anyio.to_thread

from contracts.envelope import Envelope, EventKind
from kernel import lease as lease_module
from kernel.store import (
    FORK_PREFIX_MAX_EVENTS,
    FencedOut,
    LogStore,
    RunAlreadyTerminated,
    StoreError,
    StoreWriter,
    prefix_bound_refusal,
)
from servicekit import logging as svclog
from simcore import log as folder
from simcore import pending as pend
from simcore import scenario as sc
from simcore import snapshot as snapshotting
from simcore import statement as stmt
from simcore import step as sim
from simcore import time as simtime
from simcore import verify as verifier
from simcore.rates import RULES_VERSION

log = svclog.get_logger("kernel")

#: How often the loop wakes. At the base rate this is a couple of quanta per wake, which is
#: enough to amortise the worker-thread hop without holding the GIL long enough to show as
#: stream jitter.
WAKE_INTERVAL_SECONDS = 0.05

#: The most sim-time one wake may cover. One sim-day: a laptop that slept for an hour resumes
#: behind rather than sprinting a sim-week in a single excursion.
MAX_CATCHUP_TICKS = simtime.TICKS_PER_SIM_DAY

#: The most quanta one excursion into the worker thread may run. Bounded above so a single
#: excursion cannot hold the GIL past the point where stream jitter shows.
MAX_BATCH_TICKS = 120

#: After this long with no subscribers, an active run has its rate set to zero — and the change
#: is logged, so nothing happens silently.
#:
#: This closes a semantic hazard rather than an availability one: a run whose observer crashed
#: keeps burning fixed costs and consuming the authored checkpoint supply, and could reach
#: insolvency with zero decisions taken. Replay integrity is unharmed — the log is a faithful
#: record — but the report would present a browser crash and a strategy in the same shape.
IDLE_RATE_ZERO_AFTER_SECONDS = 300.0

#: How often the kernel echoes its own derived CEO position (R33).
#:
#: Golden vectors prove agreement only for the cases someone thought to vector. This is the
#: runtime half: a mis-ported clamp or a numeric drift is caught by comparison against the
#: authority rather than by hoping a vector covered it.
POSITION_ECHO_INTERVAL_TICKS = simtime.TICKS_PER_SIM_HOUR

#: How many comparisons may be stepping branches at the same instant (R14).
#:
#: **The number is small because the constraint is the GIL, not the CPU.** A branch is pure
#: Python, so extra concurrency does not make the work finish sooner; it divides one interpreter
#: further and takes the share out of the tick loop, which wants the GIL for well under a
#: millisecond in every fifty and can only wait its turn for it.
#:
#: Sized by measurement rather than by argument. Sixteen six-option comparisons in flight at rate
#: 3, three samples each, on the machine and the harness U4's floors were taken on — the clock's
#: observed ticks against nominal, and the sim-time lag over the window:
#:
#:     slots  40 (the shipped default limiter)  683, 699, 770 permille   502-603 ticks of lag
#:     slots  16                                686, 686, 703 permille   561-596 ticks of lag
#:     slots   8                                898, 908, 927 permille    82-198 ticks of lag
#:     slots   4                                978, 982, 982 permille         0 ticks of lag
#:     slots   2                                967, 971, 972 permille         0 ticks of lag
#:     slots   1                                959, 962, 965 permille         0 ticks of lag
#:
#: The cliff is between four and eight, so two sits a factor of four inside it. One, two and four
#: are indistinguishable in what matters — no lag at all, and every figure above U4's floors — so
#: the choice among them is settled on the other two counts rather than on the clock:
#:
#: *Throughput says fewer.* Comparisons completed in the same window fell as slots rose: 19 at one
#: slot, 18 at two, 16 at four. Pure-Python parallelism has negative returns, so a bigger pool
#: makes every caller wait longer for the same total work.
#:
#: *Independence says more than one.* A single slot is a process-wide mutex by another name, and it
#: would make a comparison on one run wait for a comparison on another with nothing measured to
#: say that is necessary. Two is the smallest number that keeps two runs from serialising.
#:
#: This bounds concurrency, not cost. One comparison is still about 1.3 seconds of branch stepping
#: at six options; shortening *that* is `MAX_BRANCH_DAYS`' job and not this one.
BRANCH_SLOTS = 2

#: The limiter branch execution draws from, and nothing else does.
#:
#: Process-wide rather than per-runtime, because the resource it rations is process-wide: there is
#: one GIL, and two `KernelRuntime`s in one process would otherwise grant themselves a pool each.
#: A `BoundedSemaphore` rather than a `Semaphore` so an unbalanced release raises here instead of
#: quietly widening the pool.
#:
#: **Acquiring it blocks the calling thread, so it must never be acquired on the event loop.** The
#: command route is a synchronous FastAPI route and therefore runs on a worker thread, which is
#: what makes that safe; a future `async` caller has to hop to a thread first, or it will block
#: every request and every WebSocket send on the queue this limiter is here to create.
#:
#: **A fork's fold draws from it too** (U16), and that is a reuse rather than an overload. What
#: this limiter rations is bounded pure-Python work reached from a request thread; the route pool
#: is forty deep, so forty concurrent forks would put forty folds against one GIL and starve the
#: clock in precisely the shape U5 measured for comparisons. A second limiter would be a second
#: number nobody sized. The fork releases it before it queues its write: this rations the CPU, and
#: the single writer rations the store.
#:
#: **What bounds a fork's fold is the target tick, not `FORK_PREFIX_MAX_EVENTS`**, and this comment
#: said otherwise until a review checked it. `_replay` runs `step()` once per tick from genesis to
#: the fork point regardless of how few events the prefix holds, so a quiet run with a late decision
#: costs what a busy one does, and `horizon_tick` is client-supplied with no upper bound. Unmeasured
#: in both directions — unlike `BRANCH_SLOTS` above, which carries its table — so it is written down
#: as a known gap rather than defended. See `docs/residual-review-findings/feat-company-os-mvp-u16.md`.
BRANCH_LIMITER = threading.BoundedSemaphore(BRANCH_SLOTS)

#: Clock slots beyond one per run: the lease heartbeat's.
#:
#: One is exactly enough and the arithmetic is worth stating. A run's loop awaits its own
#: `_advance`, so a run can never want two slots at once; `_maybe_zero_idle_rate`'s hop is on the
#: same awaited path, so it wants the run's slot rather than another. The heartbeat is the only
#: other holder, and it is one task for the whole process.
CLOCK_SLOTS_BESIDES_RUNS = 1

#: How long sim-time may stand still on a run whose rate is non-zero before readiness calls it
#: stalled.
#:
#: Sized against the readiness probe rather than against the loop. A healthy loop progresses every
#: `WAKE_INTERVAL_SECONDS`, so anything above a second would do on that side; what sets the number
#: is that `docker-compose.yml` probes `/status` every 5s with 6 retries. A threshold below the
#: probe interval reports stalls the prober cannot see twice in a row, which is flapping rather
#: than detection — and a 120-quantum batch against a slow Postgres is 120 append round-trips,
#: which is the legitimate pause this must not call a stall.
#:
#: At 6 retries the container is left alone for 30 seconds before it is restarted, so a transient
#: costs a warning rather than a restart loop.
CLOCK_STALL_SECONDS = 5.0

#: How many committed envelopes may wait for an earlier sequence before the publisher stops
#: waiting for it and puts what it holds on the wire anyway.
#:
#: The valve exists because the alternative failure is silent and permanent. The held-back set
#: drains as long as every committed sequence is published, which every append site here does —
#: but `writer.submit` gives up on its own commit after 30 seconds and raises while the append may
#: still land, and that one case leaves a sequence nobody will ever publish. Waiting for it would
#: mean a stream that goes quiet for the rest of the run with nothing raised. Releasing instead
#: costs a gap, and a gap has a remedy the client already ships: `sequenceGap` shows the banner and
#: a reconnect resumes from the applied sequence.
#:
#: **The number only has to clear the legitimate maximum**, which is small and bounded by the
#: single writer: a sequence is held only while an *earlier* commit is in flight between
#: `writer.submit` returning and the loop running its callback, and the writer serialises commits,
#: so what can pile up behind one is a batch's worth rather than a run's. 256 is two orders of
#: magnitude above anything measured and still trips inside a minute on a real hole.
PUBLISH_HELD_BACK_BOUND = 256


def _the_loop_we_are_on() -> asyncio.AbstractEventLoop | None:
    """The running loop, or `None` on a thread that has none.

    A worker thread and the event loop are told apart here rather than by a flag a caller passes,
    because the caller does not always know: `set_rate` is reached from the tick loop's own awaited
    path, from a synchronous FastAPI route, and from a test, and each is a different answer.
    """
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


@dataclass(slots=True)
class Diagnosis:
    """Everything needed to answer "why did the clock stop", without reading a log."""

    run_id: str
    rate: int
    rate_effective_tick: int
    subscribers: int
    tick_task_state: str
    tick_task_exception: str
    last_wake_at: str
    #: How long sim-time has stood still. The direct answer to "did the clock stop", and the
    #: figure readiness decides on — `achieved_multiplier_permille` reads 1000 through a stall of
    #: several wall seconds, so it cannot be that figure.
    seconds_since_last_tick: int
    sim_time_lag_ticks: int
    achieved_multiplier_permille: int
    unresolved_checkpoints: list[str]
    outstanding_requests: list[dict[str, Any]]
    store_reachable: bool
    lease_held: bool
    terminal_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "rate": self.rate,
            "rate_effective_tick": self.rate_effective_tick,
            "subscribers": self.subscribers,
            "tick_task_state": self.tick_task_state,
            "tick_task_exception": self.tick_task_exception,
            "last_wake_at": self.last_wake_at,
            "seconds_since_last_tick": self.seconds_since_last_tick,
            "sim_time_lag_ticks": self.sim_time_lag_ticks,
            "achieved_multiplier_permille": self.achieved_multiplier_permille,
            "unresolved_checkpoints": list(self.unresolved_checkpoints),
            "outstanding_requests": list(self.outstanding_requests),
            "store_reachable": self.store_reachable,
            "lease_held": self.lease_held,
            "terminal_reason": self.terminal_reason,
        }


#: The namespace fork ids are minted in. Fixed, because the id has to be the same value on a
#: retry that reaches a restarted process — that is the whole of what makes a fork idempotent
#: once the gateway's in-memory ledger is gone (M47).
FORK_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://company-os.invalid/forks")


def child_run_id_for(parent_run_id: str, idempotency_key: str) -> str:
    """The child a fork of this parent under this key produces. M47.

    **Minted from the key, not from `(parent, at_seq)`.** The old derivation hashed the fork
    *point*, so two forks of one decision — which is the entire mechanic: the same moment, two
    different options — collided on one id and the second insert failed. The key is the caller's,
    and two forks of one decision are two calls with two keys.

    Deterministic rather than random for the other half of M47: a client that never saw its
    response retries, the gateway's ledger is in memory and a restart empties it, so the retry
    reaches the store — where a child id it can recompute is what turns a second fork into the
    first one's answer.

    Twelve hex characters, the shape `POST /runs` already mints. A collision would return
    somebody else's child, so `fork` checks the found child's parentage rather than trusting the
    id, and says so instead of answering with the wrong run.

    **The separator is length-prefixed rather than delimited, so no character has to be
    forbidden for the seed to be unambiguous.** It used to be a newline with a comment saying
    neither value may contain one — an invariant nothing enforced, and both values are reachable
    with one: `POST /runs` accepts a client-supplied id and `.strip()` trims only the ends.
    Measured before the fix: `("run-a", "b\\nkey")` and `("run-a\\nb", "key")` both minted
    `run-cc3c2f6565de`. A length prefix cannot be spoofed by any content, so the guard in
    `refuse_an_unusable_identifier` below is defence in depth rather than the mechanism.
    """
    seed = f"{len(parent_run_id)}:{parent_run_id}:{idempotency_key}"
    return f"run-{uuid.uuid5(FORK_ID_NAMESPACE, seed).hex[:12]}"


#: The longest idempotency key a fork will accept. Not a security bound — the key is hashed to
#: twelve hex characters and an enormous one is merely wasteful — but an unbounded string a
#: client can post is a value that ends up in a log line and a refusal sentence, and both have
#: readers. Sized well above any UUID or ULID a client would mint.
MAX_IDEMPOTENCY_KEY_CHARS = 128


def refuse_an_unusable_identifier(label: str, value: str) -> str:
    """Why this run id or idempotency key cannot be used, or "" if it can.

    **The predicate is `simcore.scenario.control_character`, imported rather than restated.**
    Execution decision §1 forbids a second copy of a predicate in the tree, and that function is
    already the one place the Unicode-category rule is written down — it refuses every category
    beginning with C, which is what makes a zero-width joiner or a right-to-left override refused
    alongside a newline. A second `unicodedata` call here is exactly the drift §1 names.

    Applied to a run id as well as a key because the two are concatenated into one digest seed,
    and because a control character in a run id reaches the log line, the URL and the store.
    """
    from simcore import scenario as sc

    if not value:
        return ""
    offending = sc.control_character(value)
    if offending:
        return (
            f"the {label} contains {offending}, which cannot be used: it would reach a log line, "
            "a URL and the store, and it is the kind of character that changes what a reader "
            "sees without changing what is stored."
        )
    if len(value) > MAX_IDEMPOTENCY_KEY_CHARS and label == "idempotency key":
        return (
            f"the idempotency key is {len(value)} characters, above the bound of "
            f"{MAX_IDEMPOTENCY_KEY_CHARS}. It is hashed to twelve, so a longer one buys nothing "
            "and ends up quoted in a log line."
        )
    return ""


@dataclass(slots=True)
class ForkOutcome:
    """A timeline, or the reason there is not one.

    A refusal rather than an exception, for the reason `post_command` answers a rejected command
    with 200 and a sentence: the request was well-formed and the answer is "no", which the client
    renders. The one exception is a parent that does not exist, which is a 404 and is raised.
    """

    child_run_id: str = ""
    parent_run_id: str = ""
    #: The sequence of the decision being reconsidered — the parent's `DECISION_RESOLVED`.
    decision_seq: int = 0
    #: The last sequence copied, which is the one *before* the decision.
    forked_at_seq: int = 0
    forked_at_tick: int = 0
    lineage_root_id: str = ""
    item: str = ""
    cp_index: int = 0
    #: The option this timeline takes, and the one its parent took.
    option_index: int = 0
    parent_option_index: int = 0
    #: False when this call found the child rather than making it — a retry (M47).
    created: bool = False
    refusal: str = ""

    @property
    def forked(self) -> bool:
        return not self.refusal

    def to_dict(self) -> dict[str, Any]:
        return {
            "child_run_id": self.child_run_id,
            "parent_run_id": self.parent_run_id,
            "decision_seq": self.decision_seq,
            "forked_at_seq": self.forked_at_seq,
            "forked_at_tick": self.forked_at_tick,
            "lineage_root_id": self.lineage_root_id,
            "item": self.item,
            "cp_index": self.cp_index,
            "option_index": self.option_index,
            "parent_option_index": self.parent_option_index,
            "created": self.created,
            "refusal": self.refusal,
        }


@dataclass(slots=True)
class RunLoop:
    """One run's clock. Exactly one of these exists per active run."""

    run_id: str
    state: sim.State
    #: The one lock between a command and a tick (R13). See the module docstring for why it is
    #: a `threading.Lock`, why it is taken per quantum, and why nothing slow may happen inside
    #: it. Public, so a multi-run operation can take two in run-id order (R22).
    lock: threading.Lock = field(default_factory=threading.Lock)
    #: Persisted on the run row. 0 is paused.
    rate: int = 1
    rate_effective_tick: int = 0
    #: Wall-clock instant of the last wake, from a monotonic clock.
    last_wake: float = field(default_factory=time.monotonic)
    last_wake_wall: str = ""
    #: The last tick the clock actually reached, and when it reached it, from a monotonic clock.
    #:
    #: Readiness reads these rather than the task's state (R14). "The tick task is alive" and "the
    #: clock is moving" are different facts, and a starved loop satisfies the first: it is sitting
    #: in an `await`, waiting for a worker slot or for its turn at the GIL, with nothing raised
    #: and nothing done. Sim-time is the only witness that cannot be faked by a live task.
    #:
    #: Stamped at loop start and on a resume as well as on progress, so a run that has just been
    #: unpaused gets a full stall interval before anyone calls its clock stopped.
    progress_tick: int = 0
    progress_at: float = field(default_factory=time.monotonic)
    #: Quanta the clamp prevented from running. The user-visible failure is that x3 does not
    #: deliver 3x, so this is the headline number rather than tick duration.
    lag_ticks: int = 0
    requested_ticks: int = 0
    achieved_ticks: int = 0
    #: Set when no subscriber has been attached since this instant.
    idle_since: float | None = field(default_factory=time.monotonic)
    #: The highest sequence handed to this run's subscribers — the point the wire is caught up
    #: to, whether or not anybody was listening. Initialised to the run's head, because that is
    #: where a reconnecting client's resume leaves off too.
    #:
    #: **Read and written on the event loop only**, which is what makes the ordering lock-free.
    #: See `_publish`.
    published_seq: int = 0
    #: Committed envelopes waiting for an earlier sequence, by sequence. Non-empty only while a
    #: commit is in flight from another thread; see `PUBLISH_HELD_BACK_BOUND` for the one case
    #: that would otherwise leave something here forever.
    held_back: dict[int, Envelope] = field(default_factory=dict)
    #: The publisher's own lock, over `published_seq` and `held_back` and nothing else.
    #:
    #: **Not `lock`, and the two must never be confused.** `lock` guards `run.state` and the tick
    #: loop takes it dozens of times a batch, so R13's "nothing slow inside it" applies; this one
    #: is held for a few dictionary operations and a non-blocking enqueue, by publishers only, and
    #: nothing inside it takes `lock` — so there is no order between them to get wrong.
    #:
    #: Nearly always uncontended, because `_publish` normally funnels the ordering onto the event
    #: loop and the loop is one thread. What it closes is the case where there is no loop to funnel
    #: onto — a synchronous caller, which the suite has — and the moment one is recorded while
    #: another thread is already inside.
    publish_lock: threading.Lock = field(default_factory=threading.Lock)
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    task: asyncio.Task | None = None
    exception: BaseException | None = None
    stopped: bool = False
    last_position_echo_tick: int = 0
    #: The leg-specific half of each outstanding statement request's payload, by request id — the
    #: director, the checkpoint and the authorized scope (U10).
    #:
    #: `state.pending` is the projection that matters for the simulation and it deliberately carries
    #: none of this: adding a field to `PendingRequest.to_state()` would change the shape of a hashed
    #: subsystem, which R27 makes a `STATE_SHAPE_VERSION` move. So the subject lives beside the run
    #: rather than inside it, filled from the raising event live and from the fold's projection at
    #: resume, and pruned against `state.pending` so it cannot outgrow what is outstanding.
    statement_subjects: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Request ids already handed to the producer. Two sources dispatch — the append and the resume
    #: — and a second answer to one request is refused by the store's partial unique index, so it
    #: would reach the client as a rejection rather than as a briefing.
    statements_asked: set[str] = field(default_factory=set)
    #: Strong references to in-flight answer tasks, for the reason stated at the top of this file:
    #: the event loop keeps only weak ones, and a collected task stops silently.
    statement_tasks: set[asyncio.Task] = field(default_factory=set)

    @property
    def achieved_multiplier_permille(self) -> int:
        """Achieved against nominal, in per-mille. 1000 is keeping up."""
        if not self.requested_ticks:
            return 1000
        return self.achieved_ticks * 1000 // self.requested_ticks


class KernelRuntime:
    """Owns the store, the lease, the writer, and one loop per active run.

    The kernel's *first* acts are the DDL check and lease acquisition, because both are startup
    failures rather than transients: appending to a schema this kernel does not understand, or
    appending alongside a second kernel, are worse than not starting.
    """

    def __init__(self, store: LogStore) -> None:
        self.store = store
        self.writer = StoreWriter(store)
        self.lease: lease_module.LeaseHandle | None = None
        self.runs: dict[str, RunLoop] = {}
        #: Command outcomes by idempotency key, per run. A projection of what was applied, so a
        #: client whose response never arrived can resolve it rather than retrying blind (R30).
        self._outcomes: dict[str, dict[str, dict[str, Any]]] = {}
        self._heartbeat: asyncio.Task | None = None
        self._store_reachable = True
        #: The event loop every subscriber queue belongs to, recorded by `subscribe` and
        #: `ensure_loop` — the two methods that are on it by construction. A publish from a
        #: worker thread needs it to get back (see `_publish`); nothing else does, which is why
        #: it is discovered rather than passed in through a constructor no caller would fill.
        self._loop: asyncio.AbstractEventLoop | None = None
        #: What answers a statement request, installed by the launcher (U10). `None` means no bench
        #: is composed, which is a supported mode rather than a failure: with no producer the
        #: request is raised, published, and left for its sim-tick deadline, and the run plays
        #: exactly as it did before the bench existed (M20).
        self._statement_producer: Any | None = None
        #: Worker-thread slots the clock and the lease heartbeat draw from, and nothing else
        #: (R14). Grown by `ensure_loop`; never shrunk, because shrinking is where a limiter can
        #: be resized below what is already borrowed.
        #:
        #: Constructible here, outside any event loop, because anyio's limiter binds to a backend
        #: lazily on first *use* — which is the tick loop's own hop. The runtime is already
        #: loop-bound by `task` and by every subscriber queue, so this adds no new constraint.
        self.clock_slots = anyio.CapacityLimiter(1 + CLOCK_SLOTS_BESIDES_RUNS)

    # --- startup and shutdown --------------------------------------------

    def start(self) -> None:
        """Provision if empty, take the lease, check the schema, start the single writer.

        The order is deliberate and each step depends on the one before it.

        **Table creation first, and idempotent.** R16 requires `docker compose up` to bring the
        system up with no manual provisioning, so a kernel pointed at an empty database has to
        create the schema. It cannot be lease-gated in the strict sense, because the lease lives
        in a table that would not exist yet — but creation is `CREATE TABLE`-if-absent and never
        alters an existing table, so two kernels racing here converge rather than conflict.

        **Then the lease.** Only one kernel proceeds past this line.

        **Then the DDL version.** After creation, so a fresh store passes; after the lease, so a
        kernel that lost the race does not report a schema complaint when its real problem is
        that another kernel owns the log.

        Raises rather than degrading. A kernel that cannot hold the lease must exit non-zero and
        name the holder, because the alternative is two writers against one log.
        """
        self.store.create_all()

        with self.store.engine.begin() as connection:
            self.lease = lease_module.acquire(connection)

        self.store.check_ddl_version()

        log.info(
            "lease acquired",
            extra={"owner": self.lease.owner, "token": self.lease.token},
        )
        self.writer.start()

    def use_statement_producer(self, producer: Any) -> None:
        """Install what answers a statement request (U10).

        Handed in by the launcher rather than imported, for the reason `use_kernel` and `use_spend`
        exist: R4 forbids this service from importing the agents service, and the boundary is an
        import rule rather than a transport — collapsing the deployment did not relax it.

        The producer is called as `producer(request) -> dict | None`, off the tick thread and with no
        lock held. `None` declines, which leaves the request outstanding until its deadline — the same
        semantics the stub resolver ships with, and the reason a keyless build needs no second code
        path.
        """
        self._statement_producer = producer

    async def start_background(self) -> None:
        # The loop, recorded from the earliest point there is one. `subscribe` and `ensure_loop`
        # record it too, but neither is guaranteed to have run: a process whose every stored run
        # is paused calls neither, and `_start_the_clock_soon` would then have nothing to
        # schedule a newly unpaused run's task onto. This method is `async`, so it is on the loop
        # by construction.
        self._loop = asyncio.get_running_loop()
        self._heartbeat = asyncio.create_task(self._renew_lease(), name="kernel-lease-heartbeat")
        self.resume_all()
        # After the runs exist, and on the event loop, which is where a task can be created. A
        # statement outstanding when the process died is a question this kernel can still ask, and
        # asking it is strictly better than waiting out a deadline nobody can see the reason for.
        for run in list(self.runs.values()):
            self._ask_outstanding_statements(run)

    def resume_all(self) -> list[str]:
        """Rebuild every run from its log and start the clock for the ones that were running.

        Without this the kernel holds the lease, answers its status endpoint, and advances nothing:
        `ensure_loop` had no production caller at all, so a run's clock only ever ran under test.

        A run's rate is *run state* (R18), so it is read from the row rather than defaulted —
        a restart must not pause a running run, and must not resume a paused one. A terminated run
        is skipped entirely: nothing appends after a terminal event, so a loop for it would wake
        forever with nothing to do.

        Returns the ids whose clocks were started, for the caller to log.

        One resumption failing must not stop the others. A corrupt tail in one run is that run's
        problem — folding it raises, and swallowing that here would be wrong, but so would letting
        it take down every other run in the store. It is recorded against the run instead, where
        readiness reports it.
        """
        started: list[str] = []

        for row in self.store.list_runs():
            run_id = str(row["run_id"])
            if run_id in self.runs:
                continue
            if row["terminal_reason"]:
                continue

            try:
                run = self.resume_run(run_id)
            except Exception as exc:  # noqa: BLE001 - recorded per run, never fatal to the rest
                log.error(
                    "could not resume run",
                    extra={"run": run_id, "error": f"{type(exc).__name__}: {exc}"},
                )
                continue

            if run.rate > 0:
                self.ensure_loop(run_id)
                started.append(run_id)

        if started:
            log.info("resumed run clocks", extra={"runs": ",".join(started)})
        return started

    async def stop(self) -> None:
        """Cancel every task explicitly, after the lifespan yield.

        Explicit because a dropped handle is collected mid-execution and the work stops
        silently; after the yield because cancelling before it would stop the clock while
        requests were still being served.
        """
        for run in list(self.runs.values()):
            await self.stop_run(run.run_id)

        if self._heartbeat is not None:
            self._heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat
            self._heartbeat = None

        self.writer.stop()

        if self.lease is not None:
            with contextlib.suppress(Exception):
                with self.store.engine.begin() as connection:
                    lease_module.release(connection, self.lease)

    async def _renew_lease(self) -> None:
        while True:
            await asyncio.sleep(lease_module.HEARTBEAT_SECONDS)
            if self.lease is None:
                continue
            try:
                # On the clock's own limiter (R14): a heartbeat that cannot get a worker thread
                # because commands hold every default slot lets the lease lapse, and a lapsed
                # lease is another kernel appending alongside this one.
                held = await anyio.to_thread.run_sync(
                    self._heartbeat_once, limiter=self.clock_slots
                )
            except Exception as exc:  # noqa: BLE001 - the store may be down; keep trying
                self._store_reachable = False
                log.warning("lease heartbeat failed", extra={"error": str(exc)})
                continue

            self._store_reachable = True
            if not held:
                # Someone took the lease. Stop appending immediately: this kernel is fenced.
                log.error(
                    "the writer lease was taken over; halting every tick loop rather than "
                    "appending alongside another writer"
                )
                for run in list(self.runs.values()):
                    run.stopped = True

    def _heartbeat_once(self) -> bool:
        with self.store.engine.begin() as connection:
            return lease_module.heartbeat(connection, self.lease)

    # --- runs -------------------------------------------------------------

    def create_run(
        self,
        run_id: str,
        run_seed: int,
        horizon_tick: int | None = None,
        scenario: str | None = None,
    ) -> RunLoop:
        """Create a run of the named company, or of the shipped one.

        **A name, not a path, and not a `Scenario`.** The loader resolves a name inside the
        scenarios directory and refuses anything that could leave it *before* touching the
        filesystem (R9), so the rule holds by construction as long as nothing above here is
        allowed to hand down a path. A caller that could pass a `Scenario` object would be a
        caller that could have loaded it from anywhere.
        """
        company = sc.load_default() if scenario is None else sc.load(scenario)
        state, genesis = sim.new_run(
            run_seed=run_seed, horizon_tick=horizon_tick, scenario=company
        )

        self.store.create_run(
            run_id=run_id,
            run_seed=run_seed,
            rules_ver=RULES_VERSION,
            quantum_sim_seconds=simtime.QUANTUM_SIM_SECONDS,
            grid=(state.floor.cols, state.floor.rows),
            horizon_tick=state.horizon_tick,
        )
        appended = self.writer.submit(
            run_id=run_id,
            emitted=genesis,
            lease_handle=self.lease,
            rules_ver=RULES_VERSION,
            tick=0,
        )

        run = RunLoop(run_id=run_id, state=state)
        self.runs[run_id] = run
        # Genesis, to the subscribers that cannot exist yet — nobody can subscribe to a run whose
        # creation has not returned. Published anyway, because the invariant the publisher's
        # ordering rests on is that *every* append here publishes, and an exception carved out for
        # the one append that happens to be unobserved is an exception somebody has to keep true.
        self._publish(run, appended.envelopes)
        return run

    def resume_run(self, run_id: str) -> RunLoop:
        """Rebuild a run from its log. Sim-time and mid-path actors survive."""
        row = self.store.run_row(run_id)
        if row is None:
            raise KeyError(f"no such run: {run_id}")

        events = self.store.read_events(run_id)
        folded = folder.fold(
            events, at_live_head=True, through_tick=int(row["current_tick"])
        )

        run = RunLoop(
            run_id=run_id,
            state=folded.state,
            rate=int(row["rate"]),
            # The wire starts caught up to the log, not at zero: a resumed run's first frame is
            # the first thing it appends *after* the resume, and a client attaching to it resumes
            # from this same head. Starting at zero would hold every one of those frames back
            # waiting for sequences that were published — or were nobody's to publish — before
            # this process existed.
            published_seq=int(row["head_seq"]),
        )
        # The bench half of every request this run raised and never got an answer to. Read off the
        # fold's projection rather than off `state.pending`, which carries no director and no scope
        # by design — see `RunLoop.statement_subjects`.
        #
        # Recorded here and *asked* from `start_background`, because creating a task needs the event
        # loop and this method does not have one by construction: it is a plain synchronous method,
        # reached from `resume_all` and from the suite, and a `create_task` here would raise on any
        # caller that happened not to be on the loop.
        run.statement_subjects = {
            request_id: dict(subject)
            for request_id, subject in folded.outstanding_requests.items()
            if subject.get("service") == pend.BENCH
        }
        self.runs[run_id] = run
        return run

    def ensure_loop(self, run_id: str) -> RunLoop:
        """Start the tick task if the run's persisted rate says it should be running.

        Idempotent, and that is the point: fifty concurrent subscribes produce one task,
        because the task's existence follows the rate rather than the subscription.

        This is also where the clock's limiter is sized, because it is the one place that knows a
        run is about to want a slot and it is guaranteed to be on the event loop — it calls
        `create_task`, which has nowhere else to run. Sizing it from `len(self.runs)` rather than
        from a fixed constant matters: a constant would silently cap how many runs can tick at
        once, and the symptom would be a run whose clock is simply slower than the others'.
        """
        run = self.runs[run_id]
        # Recorded here for the same reason the limiter is sized here: this is a place guaranteed
        # to be on the event loop, because `create_task` below has nowhere else to run. `_advance`
        # publishes from a worker thread and needs the handle to get back.
        self._loop = asyncio.get_running_loop()
        self.clock_slots.total_tokens = max(
            self.clock_slots.total_tokens, len(self.runs) + CLOCK_SLOTS_BESIDES_RUNS
        )
        if run.task is not None and not run.task.done():
            return run

        run.stopped = False
        # Before the task exists, not inside it: a `RunLoop` built minutes ago carries a
        # construction-time stamp, and readiness asked between here and the loop's first wake
        # would read that as a clock that has been stopped for minutes.
        run.progress_tick = run.state.tick
        run.progress_at = time.monotonic()
        task = asyncio.create_task(self._run_loop(run), name=f"tick-loop-{run_id}")
        run.task = task  # strong reference, held for the task's lifetime

        def _retrieve(finished: asyncio.Task) -> None:
            # Retrieving the exception is what stops it being swallowed. Readiness then
            # reports unhealthy and names the failure (R29).
            if finished.cancelled():
                return
            exc = finished.exception()
            if exc is not None:
                run.exception = exc
                log.error(
                    "tick loop crashed",
                    extra={"run": run.run_id, "error": f"{type(exc).__name__}: {exc}"},
                )

        task.add_done_callback(_retrieve)
        return run

    async def stop_run(self, run_id: str) -> None:
        run = self.runs.get(run_id)
        if run is None:
            return
        run.stopped = True
        # Cancelled explicitly, for the reason the tick task is: a dropped handle is collected
        # mid-execution and the work stops silently. An in-flight briefing is also the one task here
        # that may be sitting in a provider call, so leaving it to be garbage collected would hold a
        # worker thread past shutdown.
        for task in list(run.statement_tasks):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        run.statement_tasks.clear()

        if run.task is not None:
            run.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run.task
            run.task = None

    def set_rate(self, run_id: str, rate: int) -> Envelope | None:
        """Change a run's rate, and record the tick it took effect at.

        The *multiplier* deliberately never enters kernel state: storing it would make replay
        speed part of the run, and keeping it out means degrading it under lag is free.

        The lock covers the new rate and the tick it took effect at together, and nothing after
        (R13). Those two are one fact — "the clock changed speed here" — and read a quantum
        apart they describe a tick the rate was never in force at. The row update and the append
        are store round-trips and stay outside.
        """
        if rate < 0:
            raise ValueError("rate cannot be negative")

        run = self.runs[run_id]
        with run.lock:
            previous = run.rate
            run.rate = rate
            effective_tick = run.rate_effective_tick = run.state.tick
            if previous == 0 and rate > 0:
                # A resumed clock starts its stall interval here. Without this, a run paused for
                # ten minutes reports its clock stalled the instant it is unpaused — which is
                # true of the ten minutes and false of the run.
                run.progress_tick = run.state.tick
                run.progress_at = time.monotonic()

        with self.store.engine.begin() as connection:
            from sqlalchemy import update

            from logschema import runs as runs_table

            connection.execute(
                update(runs_table).where(runs_table.c.run_id == run_id).values(rate=rate)
            )

        appended = self.writer.submit(
            run_id=run_id,
            emitted=[
                sim.Emitted(
                    kind=EventKind.RATE_CHANGED,
                    payload={
                        "tick": effective_tick,
                        "rate": rate,
                        "previous_rate": previous,
                        "effective_tick": effective_tick,
                    },
                )
            ],
            lease_handle=self.lease,
            rules_ver=RULES_VERSION,
            tick=effective_tick,
        )
        # A change of speed is something a connected client should see, and `RATE_CHANGED` has a
        # reducer branch waiting for it that nothing ever reached. It is also load-bearing for the
        # publisher rather than merely nice: an appended sequence that is never published is a hole
        # the ordering cursor would wait behind for the rest of the run.
        self._publish(run, appended.envelopes)

        if previous == 0 and rate > 0:
            self._start_the_clock_soon(run_id)

        log.info(
            "rate changed",
            extra={"run": run_id, "tick": effective_tick, "rate": rate, "was": previous},
        )
        return appended.envelopes[0] if appended.envelopes else None

    def _start_the_clock_soon(self, run_id: str) -> None:
        """Make a run that has just been unpaused actually tick.

        `ensure_loop` is idempotent and its docstring already says the task's existence follows
        the run's rate — but until U16 nothing *made* that true for a run with no task, because
        every run got one at creation and a paused run's task stays alive and idle. **A fork is
        the first run in the system that has no task**: it arrives at rate zero, so creating one
        at fork would be a task with nothing to do, and the child's clock then had no way to
        start.

        Found on the compose path rather than in the suite, and the shape is worth keeping:
        `set_rate` answered `applied`, `RATE_CHANGED` was appended, the run row said rate 3 and
        `/runs/{id}/state` reported rate 3 — and the tick did not move. It started on the next
        restart, when `resume_all` built the task. Every observable said the clock was running.

        The hop is `_publish`'s, for `_publish`'s reason: `ensure_loop` calls `create_task` and
        must be on the event loop, while `set_rate` is reached from a synchronous FastAPI route,
        from the tick loop's own awaited path, and from a test with no loop at all. With no loop
        recorded there is no clock to start and nothing to schedule onto, which is the test case.
        """
        loop = self._loop
        if loop is None:
            return
        if _the_loop_we_are_on() is loop:
            self.ensure_loop(run_id)
            return
        loop.call_soon_threadsafe(self.ensure_loop, run_id)

    # --- subscriptions ----------------------------------------------------

    def subscribe(self, run_id: str) -> asyncio.Queue:
        """Attach a read-only subscriber. Has no effect on simulation state (R18).

        The queue it hands back belongs to the caller's event loop, so this is also where that
        loop is recorded: it is the moment the runtime acquires something a worker thread must not
        touch directly, and a publisher reaching it from off the loop needs the handle. Recorded
        here as well as in `ensure_loop` because a paused run has no tick task and can still be
        subscribed to and commanded.
        """
        run = self.runs[run_id]
        self._loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=1024)
        run.subscribers.add(queue)
        run.idle_since = None
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        run = self.runs.get(run_id)
        if run is None:
            return
        run.subscribers.discard(queue)
        if not run.subscribers:
            run.idle_since = time.monotonic()

    def _publish(self, run: RunLoop, envelopes: list[Envelope]) -> None:
        """Hand committed envelopes to the run's subscribers. Called after the append (R20).

        **Every append site in this file calls this, and calling it is not optional.** The
        ordering below releases a sequence only once every sequence before it has gone out, so an
        append that returns without publishing is a hole every later frame waits behind. Six
        sites: genesis, `set_rate`, `_advance`, `deliver_statement`, `apply_command`, and the
        divergence a `fork` commits into the child it just made.

        **This is the thread boundary.** `_advance` runs on an anyio worker thread and
        `apply_command` on a Starlette one; subscriber queues are `asyncio.Queue`, which is not
        thread-safe. So the delivery is handed to the loop with `call_soon_threadsafe` — a deque
        append and, when the loop is idle, one byte down its self-pipe. Not
        `run_coroutine_threadsafe`: there is no coroutine to run, and its future would leave the
        committing thread choosing between discarding it and waiting on it, and waiting here is
        what R13 forbids.

        The inline branch is not a shortcut, it is the same guarantee by a shorter route: when the
        caller is already the loop, scheduling would only defer the identical call, and the
        ordering state stays single-threaded either way.

        With no loop recorded there is nothing loop-bound to be unsafe about — `subscribe` records
        one before it hands out a queue — so the ordering runs on the calling thread. That is the
        path a synchronous test takes, and it is the one `run.publish_lock` exists for: it is the
        only path on which two threads can reach the cursor.

        **A publish that fails must not fail the append.** The events are durable by the time this
        runs, the run has moved on, and the client's remedy for a missing frame is the resync it
        already ships. So nothing here propagates: a raise would otherwise turn a committed command
        into a 500, or kill the tick task and stop the clock over an undelivered frame.
        """
        if not envelopes:
            return

        loop = self._loop
        if loop is None or _the_loop_we_are_on() is loop:
            self._sequence_and_deliver(run, tuple(envelopes))
            return

        try:
            loop.call_soon_threadsafe(self._sequence_and_deliver, run, tuple(envelopes))
        except RuntimeError as exc:
            # The loop is closed, which means the process is going down; the events are in the log
            # and no client is reading. Logged rather than raised, because the append succeeded.
            log.warning(
                "could not reach the event loop to publish; the events are durable and a "
                "reconnect resumes from the sequence",
                extra={"run": run.run_id, "error": str(exc)},
            )

    def _sequence_and_deliver(self, run: RunLoop, envelopes: tuple[Envelope, ...]) -> None:
        """Release the contiguous run of committed sequences, and hold back the rest.

        **This is the ordering guarantee, and it is the whole of it.** `_advance` releases the run
        lock before it appends, so a command's mutation can happen after a tick's while its append
        lands first — and the client treats any hole as a resync trigger, so publishing in the
        order threads happen to observe their commits would manufacture the gap this was written to
        close. Nothing here depends on which thread arrived first: anything above `published_seq`
        is parked, and only the unbroken run from `published_seq + 1` upwards goes out. Since each
        call starts where the last one finished, the frames a run's subscribers see are one
        strictly increasing sequence with no hole in it.

        **Under the publisher's own lock, never the run's.** In deployment this is redundant:
        `_publish` funnels every off-loop caller onto the event loop, and the loop is one thread. It
        is here for the two cases where that funnel does not exist — a caller with no event loop at
        all, which the suite has, and the instant a loop is first recorded while another thread is
        already inside. Leaving those uncovered would make the ordering claim above true of
        deployment and false of the code, which is not a distinction worth relying on. It is a
        different lock from `run.lock` for a reason R13 makes concrete: this one is held for a few
        dictionary operations and a non-blocking enqueue and takes nothing else, so no clock waits
        behind it, and nothing inside it can want the run's lock.

        Delivery happens inside the lock as well, so what reaches the wire is in cursor order and
        not merely computed in it — releasing the lock first would let two threads compute correct
        batches and then hand them over backwards.

        The cursor advances **before** delivery, deliberately. If delivery raises, the frame is
        lost and the client sees a gap it already knows how to recover from; leaving the cursor
        behind instead would hold every later frame back forever, which is a stream that goes
        quiet with nothing raised.
        """
        try:
            with run.publish_lock:
                self._release_what_is_ready(run, envelopes)
        except Exception as exc:  # noqa: BLE001 - a publish never fails what is already durable
            log.error(
                "could not publish a run's events; they are durable and a reconnect resumes "
                "from the sequence",
                extra={"run": run.run_id, "error": f"{type(exc).__name__}: {exc}"},
            )

    def _release_what_is_ready(self, run: RunLoop, envelopes: tuple[Envelope, ...]) -> None:
        """Park what is early, hand over the unbroken run. Called under `run.publish_lock`."""
        for envelope in envelopes:
            if envelope.seq > run.published_seq:
                run.held_back[envelope.seq] = envelope

        ready: list[Envelope] = []
        while (following := run.published_seq + 1) in run.held_back:
            ready.append(run.held_back.pop(following))
            run.published_seq = following

        if len(run.held_back) > PUBLISH_HELD_BACK_BOUND:
            ready.extend(self._stop_waiting_for_a_lost_sequence(run))

        if ready:
            self._deliver(run, ready)

    def _stop_waiting_for_a_lost_sequence(self, run: RunLoop) -> list[Envelope]:
        """Give up on a sequence that is never coming, and say so.

        Reachable through one door: `writer.submit` abandons its own commit after 30 seconds and
        raises while the append may still land, so its caller never publishes what the store
        nonetheless holds. Waiting for that sequence forever means a stream that goes silent for
        the rest of the run with nothing raised — strictly worse than a gap, which the client
        detects and recovers from. See `PUBLISH_HELD_BACK_BOUND` for the size.
        """
        released = [run.held_back.pop(seq) for seq in sorted(run.held_back)]
        run.published_seq = released[-1].seq
        log.warning(
            "a committed sequence was never published, so the events behind it are being "
            "released with a gap; the client detects it and a reconnect resumes from there",
            extra={
                "run": run.run_id,
                "released": len(released),
                "through_seq": run.published_seq,
            },
        )
        return released

    def _deliver(self, run: RunLoop, envelopes: list[Envelope]) -> None:
        """Fan one ordered batch out to every subscriber."""
        for envelope in envelopes:
            for queue in list(run.subscribers):
                try:
                    queue.put_nowait(envelope)
                except asyncio.QueueFull:
                    # The recovery path already exists: the subscriber resumes from its
                    # sequence. Buffering unboundedly would buy nothing.
                    run.subscribers.discard(queue)
                    log.warning(
                        "subscriber fell behind and was dropped; it can resume from its "
                        "sequence",
                        extra={"run": run.run_id},
                    )

    # --- the loop itself --------------------------------------------------

    async def _run_loop(self, run: RunLoop) -> None:
        run.last_wake = time.monotonic()

        while not run.stopped:
            await asyncio.sleep(WAKE_INTERVAL_SECONDS)

            now = time.monotonic()
            elapsed = now - run.last_wake
            run.last_wake = now
            run.last_wake_wall = lease_module.utc_now_iso()

            if run.state.terminal_reason:
                return

            if run.rate == 0:
                await self._maybe_zero_idle_rate(run)
                continue

            await self._maybe_zero_idle_rate(run)
            if run.rate == 0:
                continue

            wanted = int(elapsed * simtime.TICKS_PER_WALL_SECOND_AT_BASE_RATE * run.rate)
            if wanted <= 0:
                continue

            run.requested_ticks += wanted

            clamped = min(wanted, MAX_CATCHUP_TICKS)
            if clamped < wanted:
                run.lag_ticks += wanted - clamped
                log.warning(
                    "catch-up clamped; the clock falls behind rather than sprinting",
                    extra={"run": run.run_id, "wanted": wanted, "ran": clamped},
                )

            batch = min(clamped, MAX_BATCH_TICKS)
            if batch < clamped:
                run.lag_ticks += clamped - batch

            # Off the event loop: a synchronous step invoked inline would stall every request
            # and every WebSocket send for the duration of the batch.
            #
            # On the clock's own limiter, never the shared default one (R14). The default limiter
            # is what every synchronous FastAPI route draws from, so a burst of comparisons can
            # borrow all forty of its slots and leave the clock waiting for a thread — which does
            # not raise and does not appear as lag until the catch-up clamp binds. A disjoint
            # limiter is not a larger allowance, it is one commands cannot reach into.
            #
            # The batch publishes its own quanta as it commits them, rather than handing them
            # back here to be published together. Two reasons, and the second is the load-bearing
            # one. A batch is up to 120 quanta, so publishing at the end delays a client's frames
            # by the whole batch for no gain. And the publisher's ordering rests on every append
            # publishing what it committed — a return value the caller has to remember to pass on
            # is a rule, while a publish beside the append is a fact, and `_advance` has callers
            # outside this loop.
            try:
                await anyio.to_thread.run_sync(
                    self._advance, run, batch, limiter=self.clock_slots
                )
            except Exception:
                # Re-raised so the task's done callback records it and readiness reports
                # unhealthy naming the failure, rather than the loop dying quietly.
                raise

            run.achieved_ticks += batch
            if run.state.tick > run.progress_tick:
                # Sim-time moved, so the clock is alive in the only sense readiness cares about.
                # Recorded from the tick rather than from the batch size, because a batch cut
                # short by `run.stopped` or a terminal reason advanced fewer quanta than it asked
                # for and the tick is what actually happened.
                run.progress_tick = run.state.tick
                run.progress_at = time.monotonic()

    def _advance(self, run: RunLoop, ticks: int) -> list[Envelope]:
        """Run `ticks` quanta, appending each one in its own transaction.

        One transaction per tick (R21), through the single writer (R35). Each quantum is published
        as soon as it is durable and never before, and the committed envelopes are returned as
        well, for the callers that assert on what a batch produced.

        **Publishing beside the append rather than through the return value** is what makes "every
        append publishes" a fact instead of a convention every caller has to honour — and
        `_advance` has callers outside the tick loop, including in the suite, which is precisely
        where a forgotten publish would leave a sequence nobody ever puts on the wire.

        **The lock is taken once per quantum and released before the append** (R13). Per quantum
        because a partly advanced batch is a consistent state, so there is nothing to gain from
        holding across up to 120 of them and a command waiting on 120 append round-trips to
        lose. Released before the append because `writer.submit` blocks on the store writer's
        commit: inside the lock, every command on this run would queue behind the store, and a
        second run's ticks would too by way of the single writer they share.

        Releasing before the append does mean a command's events can be sequenced ahead of a
        tick's even though its mutation happened after — the same window that exists today,
        since nothing held a lock across the handler and its append either. Closing *that* needs
        commands enqueued onto the tick loop rather than applied from the request thread, which
        is a different design and not this one. What is closed here is the one that corrupts: two
        threads inside `run.state` at the same instant.

        The *transport* consequence of that window is closed, though, and separately: two threads
        committing on one run can reach `_publish` in either order, and it releases sequences in
        sequence order regardless. See `_sequence_and_deliver`.

        The tick each append is stamped with is read under the lock, so a command applied at
        tick 100 is recorded at tick 100 rather than at whatever the clock reached while its
        events were in the writer's queue.
        """
        committed: list[Envelope] = []

        for _ in range(ticks):
            if run.stopped or run.state.terminal_reason:
                break

            with run.lock:
                emitted = sim.step(run.state)
                at_tick = run.state.tick

                if simtime.is_day_boundary(at_tick) and at_tick > 0:
                    # A whole-state walk, so it belongs inside the lock rather than beside it:
                    # a checkpoint payload assembled across two quanta would hash to a state
                    # the run was never in, and the day checkpoint is the one event whose whole
                    # job is to be that hash.
                    emitted.append(
                        sim.Emitted(
                            kind=EventKind.DAY_CHECKPOINT,
                            payload=verifier.build_checkpoint_payload(run.state),
                        )
                    )

                if at_tick - run.last_position_echo_tick >= POSITION_ECHO_INTERVAL_TICKS:
                    run.last_position_echo_tick = at_tick
                    # The server half of the divergence detector. Not appended to the log: it is
                    # derived state, and a client that disagrees can be told so without making
                    # the disagreement a permanent fact.
                    #
                    # Inside the lock, even though it publishes: it is a state read plus a
                    # non-blocking enqueue, and outside it the echoed tick and the echoed
                    # position could come from different quanta — which is precisely the
                    # disagreement this echo exists to detect, manufactured by the detector.
                    self._echo_position(run)

            if not emitted:
                continue

            result = self.writer.submit(
                run_id=run.run_id,
                emitted=emitted,
                lease_handle=self.lease,
                rules_ver=RULES_VERSION,
                tick=at_tick,
            )
            self._publish(run, result.envelopes)
            # Beside the append for the same reason the publish is: a dispatch the caller has to
            # remember to perform is a rule, and this method has callers outside the tick loop. The
            # request is durable by the time anybody is asked, so a producer answering a request the
            # log does not hold is not a state this can reach.
            self._dispatch_statements(run, result.envelopes)
            committed.extend(result.envelopes)

        return committed

    # --- the statement leg (U10) ------------------------------------------

    def _dispatch_statements(self, run: RunLoop, envelopes: list[Envelope]) -> None:
        """Carry any statement request this append committed to the bench.

        Called from a worker thread, so it does what `_publish` does: hands the work to the event
        loop, which is the only thread that may create a task or touch a subscriber queue. Nothing
        here waits — R13 forbids a wait on this path — and nothing raises: a bench that cannot be
        reached leaves the request outstanding until its deadline, which is a working product.

        **The subject is recorded whether or not a bench is composed.** A keyless build still raises
        the request and still puts it on the wire, so `diagnose()` should still be able to say which
        director the run is waiting on — and a producer installed after a run existed then has
        something to be asked from rather than a projection that starts empty.
        """
        # Anything the run has stopped waiting on, dropped first. A request that reached its deadline
        # is removed from `state.pending` by the step and would otherwise sit in both of these for
        # the life of the process — a keyless build parked at one desk raises one request per window
        # and abandons every one of them, so "bounded by what is outstanding" has to be enforced
        # somewhere rather than merely claimed on the field.
        outstanding = set(run.state.pending)
        for stale in [key for key in run.statement_subjects if key not in outstanding]:
            del run.statement_subjects[stale]
        run.statements_asked &= outstanding

        requests: list[stmt.StatementRequest] = []
        for envelope in envelopes:
            if envelope.kind is not EventKind.REQUEST_RAISED:
                continue
            payload = envelope.decoded_payload()
            if payload.get("service") != pend.BENCH:
                continue
            request_id = envelope.request_id or str(payload.get("request_id", ""))
            run.statement_subjects[request_id] = payload
            asked = self._askable(run, request_id, payload)
            if asked is not None:
                requests.append(asked)

        if requests and self._statement_producer is not None:
            self._ask_soon(run, requests)

    def _askable(
        self, run: RunLoop, request_id: str, payload: dict[str, Any]
    ) -> stmt.StatementRequest | None:
        """This request as something answerable, or `None` with the reason logged.

        `Authorized` refuses a scope naming nobody, which is what makes default-deny the value an
        omission produces (R23) — and it means constructing a request from a payload can raise. That
        exception must not travel: `_dispatch_statements` runs on the tick thread inside `_advance`,
        so an unhandled one there is a stopped clock, and `_ask_outstanding_statements` runs inside
        `start_background`, where it would take down every run's resume rather than one briefing.

        Only a payload `step()` did not write can reach it — a hand-edited log, or a bench request
        from a build that predates the recorded scope. Left for its deadline rather than repaired,
        because inventing a scope here is the one thing R23 exists to prevent.
        """
        try:
            return stmt.StatementRequest.from_raised(run.run_id, request_id, payload)
        except ValueError as refused:
            log.warning(
                "a statement request carries no answerable scope; leaving it for its deadline",
                extra={"run": run.run_id, "request": request_id, "error": str(refused)},
            )
            return None

    def _ask_soon(self, run: RunLoop, requests: list[stmt.StatementRequest]) -> None:
        """Get onto the event loop, then ask. The same hop `_publish` makes, for the same reason.

        A task belongs to a loop, and `_advance` runs on a worker thread that has none. With no loop
        anywhere — a fully synchronous caller, which the suite has — the subjects are recorded and
        nothing is asked: there is no thread to answer on, and inventing one would put a provider
        call on whatever thread happened to call `_advance`.
        """
        loop = self._loop or _the_loop_we_are_on()
        if loop is None:
            return
        if _the_loop_we_are_on() is loop:
            self._ask(run, requests)
            return
        try:
            loop.call_soon_threadsafe(self._ask, run, requests)
        except RuntimeError as exc:
            # The loop is closed, which means the process is going down. The request is durable and
            # a restart asks it again from `start_background`.
            log.warning(
                "could not reach the event loop to ask the bench",
                extra={"run": run.run_id, "error": str(exc)},
            )

    def _ask_outstanding_statements(self, run: RunLoop) -> None:
        """Ask again for every statement this run is still waiting on. Called on the event loop.

        The resume half of the dispatch. A kernel that restarted mid-briefing folds the request back
        to outstanding — that is what `pending` being a projection of the log buys — but the
        question itself was in flight on a thread that no longer exists, so without this the run
        waits out a sim-tick deadline for a reason nothing in the log explains.
        """
        if self._statement_producer is None:
            return

        # Over a copy, because a tick task on this run may be inserting into `statement_subjects`
        # from a worker thread at this instant — `resume_all` starts the clocks and this runs after
        # it. Iterating the live dict raises "dictionary changed size during iteration", which would
        # take down `start_background` rather than lose a briefing.
        pending_now = set(run.state.pending)
        requests = [
            asked
            for request_id, subject in list(run.statement_subjects.items())
            if request_id in pending_now
            and (asked := self._askable(run, request_id, subject)) is not None
        ]
        if requests:
            self._ask(run, requests)

    def _ask(self, run: RunLoop, requests: list[stmt.StatementRequest]) -> None:
        """Start one answer task per unasked request. On the event loop by construction."""
        for request in requests:
            if request.request_id in run.statements_asked:
                continue
            if not request.person:
                # A bench request with no director on it is not answerable, and it is also not
                # something `step()` can produce — so it is a tampered or hand-written log rather
                # than a state to recover from. Logged rather than raised: the run is otherwise fine.
                log.warning(
                    "a statement request names no director; leaving it for its deadline",
                    extra={"run": run.run_id, "request": request.request_id},
                )
                continue

            run.statements_asked.add(request.request_id)
            task = asyncio.create_task(
                self._answer_statement(run, request),
                name=f"statement-{run.run_id}-{request.request_id[:8]}",
            )
            # Strong reference held for the task's lifetime, then dropped. Without it the loop's
            # weak reference lets a briefing be collected mid-flight, and the failure is not an
            # exception — it is a conversation that silently never resolves.
            run.statement_tasks.add(task)
            task.add_done_callback(run.statement_tasks.discard)

    async def _answer_statement(self, run: RunLoop, request: stmt.StatementRequest) -> None:
        """Ask the bench, and bring the answer back through the writer.

        **Both hops are on the default limiter, never `clock_slots`** (R14). The clock's limiter is
        sized at one slot per run plus one for the heartbeat, so a provider holding one for its full
        timeout is a run whose clock cannot get a thread — and that does not raise, it just stops
        the simulation. A statement is exactly the kind of work the disjoint limiter exists to keep
        off that pool.

        Nothing propagates. A producer that raises, a store that refuses and a run that ended between
        the question and the answer are all the same outcome from here: no statement this turn, and a
        request that runs to its deadline. Raising instead would take down a task whose whole purpose
        is to be optional.
        """
        try:
            # `abandon_on_cancel=True`, and it is the difference between a shutdown that returns and
            # one that waits out a provider timeout. anyio's default is to hold the cancellation
            # until the thread comes back, so `stop_run`'s `task.cancel(); await task` would block on
            # a call that has thirty seconds left in it — which is exactly what the explicit cancel
            # was added to avoid. Abandoning is safe here because the answer is addressed by run id
            # and `deliver_statement` already tolerates a run that has gone: a thread that outlives
            # its run finds no run and says so.
            answer = await anyio.to_thread.run_sync(
                self._statement_producer, request, abandon_on_cancel=True
            )
        except Exception as exc:  # noqa: BLE001 - a bench failure is never a kernel failure
            log.warning(
                "the bench could not answer a statement request",
                extra={
                    "run": run.run_id,
                    "request": request.request_id,
                    "person": request.person,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return

        if not answer:
            # Declined. The shipped behaviour with no bench configured, and the same posture the
            # stub resolver takes: the CEO is never blocked on a provider.
            return

        try:
            await anyio.to_thread.run_sync(
                self.deliver_statement, run.run_id, request.request_id, answer
            )
        except sim.CommandRejected as refused:
            # The answer reached the kernel and the kernel would not take it: too late for its
            # window, too large for the log, or the wrong shape. Logged at info rather than warning
            # and carrying the sentence, because it is a refusal with a reason and not a failure —
            # and it is *not* appended, because `ANSWER_REJECTED` is an output kind the fold expects
            # the step to regenerate and this one nothing would. `receive_answer` explains that in
            # full; the log's account of the request is the abandonment at its deadline.
            log.info(
                "a statement was refused at the kernel",
                extra={
                    "run": run.run_id,
                    "request": request.request_id,
                    "reason": str(refused),
                },
            )
        except Exception as exc:  # noqa: BLE001 - see above
            log.warning(
                "could not record a statement",
                extra={
                    "run": run.run_id,
                    "request": request.request_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )

    def deliver_statement(
        self, run_id: str, request_id: str, answer: dict[str, Any]
    ) -> list[Envelope]:
        """Queue a statement for the tick it applies at, and append it. The fifth append site.

        **Under the run's lock, released before the append** (R13). What is inside it is
        `receive_answer` — two dictionary operations — plus the rate, and the rate is read there
        rather than beside it because the rate and the tick are one fact: execution decision §2's
        paused branch lands the answer at the first tick the run will reach, and a rate read a
        quantum away from the tick would answer for a pause that had already ended.

        The answer's *content* is not validated here. It is validated inside `step()`, at the landing
        tick, by the guard both processes share — which is what makes the verdict regenerable from
        the log rather than a claim by whichever service made it (execution decision §1). Rejecting
        here would put the verdict on the command path, where the fold cannot reproduce it.

        What `receive_answer` *does* refuse here is an answer that may not become a logged fact at
        all — too late for its window, too large, or not encodable — and it refuses by raising, for
        the reason its own docstring gives. Raising propagates: the caller logs the sentence.

        **An answer that fails to append is un-queued.** `receive_answer` files it against the
        landing tick before this returns, so a submit that raises would otherwise leave an answer in
        memory that no row in the log accounts for — and it would apply at the landing tick, taking
        `pending` away from what the log says is outstanding and the day-boundary hash with it. That
        is the one divergence this whole contract exists to make impossible, so the rollback is not
        tidiness.
        """
        run = self.runs.get(run_id)
        if run is None:
            raise KeyError(f"no such run: {run_id}")

        with run.lock:
            emitted = sim.receive_answer(
                run.state, request_id, answer, paused=run.rate == 0
            )
            at_tick = run.state.tick

        # Whatever happened to it, the question has been asked and answered once. Pruned here rather
        # than left to grow, and pruned even for a rejection: a rejected answer clears the request
        # from `state.pending` too, so a subject kept for it would describe nothing.
        run.statement_subjects.pop(request_id, None)

        if not emitted:
            return []

        try:
            result = self.writer.submit(
                run_id=run_id,
                emitted=emitted,
                lease_handle=self.lease,
                rules_ver=RULES_VERSION,
                tick=at_tick,
            )
        except RunAlreadyTerminated as ended:
            # The run reached its horizon or went insolvent while the director was thinking. Nothing
            # appends after a terminal event; the briefing is simply too late, and the store is right
            # to refuse it.
            self._unqueue(run, request_id, emitted)
            log.info(
                "a statement arrived after the run ended",
                extra={"run": run_id, "request": request_id, "reason": str(ended)},
            )
            return []
        except Exception:
            self._unqueue(run, request_id, emitted)
            raise

        self._publish(run, result.envelopes)
        return result.envelopes

    def _unqueue(self, run: RunLoop, request_id: str, emitted: list[sim.Emitted]) -> None:
        """Take back an answer whose append did not land, so it cannot apply anyway.

        Under the run's lock, because it edits `run.state`. It is two dictionary operations, which is
        what R13 permits inside it — and the alternative is worse than slow: live state that applies
        an answer the log has no row for is a state hash that diverges from its own log, silently,
        one sim-day later.
        """
        applies_at = int(emitted[0].payload.get("tick", 0))
        with run.lock:
            queued = run.state.queued_answers.get(applies_at, [])
            remaining = [item for item in queued if item.get("request_id") != request_id]
            if remaining:
                run.state.queued_answers[applies_at] = remaining
            else:
                run.state.queued_answers.pop(applies_at, None)

    def _echo_position(self, run: RunLoop) -> None:
        """R33: publish the kernel's own derived CEO position for the client to compare."""
        for queue in list(run.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(
                    {
                        "kind": "POSITION_ECHO",
                        "run_id": run.run_id,
                        "tick": run.state.tick,
                        "x_milli": run.state.ceo.x_milli,
                        "y_milli": run.state.ceo.y_milli,
                    }
                )

    async def _maybe_zero_idle_rate(self, run: RunLoop) -> None:
        """An abandoned run stops rather than burning unwatched.

        Subscribing is read-only and has no effect on state, so this is not "stop when nobody
        is looking" — it is a documented interval after which an unobserved run is paused, and
        the change is logged so nothing happens silently.
        """
        if run.rate == 0 or run.subscribers or run.idle_since is None:
            return
        if time.monotonic() - run.idle_since < IDLE_RATE_ZERO_AFTER_SECONDS:
            return

        log.info(
            "no subscribers for the idle interval; setting rate to zero",
            extra={"run": run.run_id, "tick": run.state.tick},
        )
        # The clock's limiter, for the same reason `_advance` uses it: this is on the tick loop's
        # own awaited path, so a slot it cannot get is the clock not running.
        await anyio.to_thread.run_sync(self.set_rate, run.run_id, 0, limiter=self.clock_slots)
        run.idle_since = None

    # --- commands ---------------------------------------------------------

    def apply_command(
        self, run_id: str, kind: int, payload: bytes, command_id: str = ""
    ) -> list[Envelope]:
        """Apply a command at a tick boundary and append what it produced.

        Validation and application happen together, not in two steps: a command validated
        against a state the tick then changed would be applied against a world that no longer
        matches what it was checked against.

        A rejection raises `CommandRejected` and mutates nothing — the events list is empty,
        which is what "mutates nothing" means concretely.

        **Under the run's lock, and released before the append** (R13). Every handler here is a
        few microseconds of dictionary work, so the tick loop waits for a quantum's worth of
        nothing — with one exception, handled below. The append is outside the lock because
        `writer.submit` blocks on the store: inside it, the tick loop would serialise behind
        every command's commit.

        **Each handler is given the state it runs against, rather than reading `run.state`
        itself.** That is what makes the exception expressible. A comparison is the only command
        that changes nothing and still appends, and it is also the only one that reads the
        *whole* state — `snapshot.capture` walks it twice and the branches then spend about a
        third of a second on it. Holding the lock across that would stop the clock for as long
        as the branches ran, so instead the *copy* is taken under the lock and the comparison
        runs against the copy. The lock is held for the capture, about six tenths of a
        millisecond, and for nothing after it.

        That is strictly more correct than reading the live state, not merely cheaper: the
        branches, the staleness guard and the recorded tick then all describe one instant, which
        is the property `run_comparison` documents as load-bearing and could previously only
        intend. It is also why `compare._capture_of`'s retry has nothing to catch on this path —
        the copy it takes is of a state nothing else can touch.

        **And the branches themselves run inside `BRANCH_LIMITER`, outside the lock** (R14). The
        copy is what makes that possible: with the handler under the lock there would be nothing
        for a limiter to protect, because the clock would already be waiting on the lock rather
        than on a thread. Measured 779 permille under the lock against 944 beside it.
        """
        from contracts import canonical
        from contracts.grpc import kernel_pb2

        run = self.runs[run_id]
        decoded = canonical.decode(payload) if payload else {}

        def whole(key: str, default: int | None = None) -> int:
            """One integer field off a client-supplied payload, or a reason why not.

            Every command here coerces at least one field with `int(...)`, and a bare `int()`
            over a value a browser chose is a 500 waiting to happen: `canonical` rejects floats
            but passes strings and nulls straight through, so `{"cp_index": "abc"}` and
            `{"cp_index": null}` both raise past the point where `CommandRejected` is caught.
            Nothing above this catches anything else, so it reaches FastAPI's default handler
            and the client gets an opaque 500 for what is a client mistake.

            `default` of `None` means the field is required, which is the distinction the older
            `[...]` versus `.get(...)` split was trying to draw and drew only for the
            missing-key half.
            """
            if key not in decoded:
                if default is not None:
                    return default
                raise sim.CommandRejected(f"this command needs {key!r} and the payload has none")
            try:
                return int(decoded[key])
            except (TypeError, ValueError):
                raise sim.CommandRejected(
                    f"{key!r} is {decoded[key]!r}, which is not a whole number"
                ) from None

        dispatch = {
            kernel_pb2.ASSIGN_WORK: lambda state: (
                sim.assign_via_manager(state, decoded["item"])
                if decoded.get("via_manager")
                else sim.assign_direct(state, decoded["item"], decoded["person"])
            ),
            kernel_pb2.REASSIGN_WORK: lambda state: sim.reassign(
                state, decoded["item"], decoded["person"]
            ),
            kernel_pb2.RETURN_TO_BACKLOG: lambda state: sim.return_to_backlog(
                state, decoded["item"]
            ),
            kernel_pb2.RESOLVE_CHECKPOINT: lambda state: sim.resolve_checkpoint(
                state,
                decoded["item"],
                whole("cp_index"),
                whole("option_index"),
                in_person=bool(decoded["in_person"]),
            ),
            kernel_pb2.SUBMIT_CEO_INPUT: lambda state: sim.submit_ceo_input(
                state, whole("bitmask"), whole("at_tick")
            ),
            kernel_pb2.REQUEST_HIRE: lambda state: sim.request_hire(state, decoded["director"]),
            # `.get` rather than `[...]`: this is the one command carrying free-form text a
            # person typed, so a payload missing a key is a client mistake to answer with a
            # reason. A KeyError here would escape as a 500 — nothing above this catches
            # anything but `CommandRejected` — and `ask_person` rejects an empty person id
            # with a sentence of its own.
            kernel_pb2.ASK_PERSON: lambda state: sim.ask_person(
                state, str(decoded.get("person", "")), str(decoded.get("question", ""))
            ),
            # Runs its branches here, in the handler, and therefore *outside* the append
            # transaction the writer opens below. That placement is the whole reason a
            # comparison is affordable: the single writer holds one transaction per tick, and
            # three branches at a tenth of a second each inside it would stall every other
            # run's clock and read as a store outage that is not happening.
            #
            # It is also the reason this handler is given a copy rather than the live state: the
            # branches are outside the append transaction but they are not outside the lock
            # unless something puts them there. See `READ_ONLY` below.
            #
            # `.get` rather than `[...]` for the same reason `ask_person` uses it: a payload
            # missing a key is a client mistake to answer with a reason, and a KeyError here
            # would escape as a 500 because nothing above catches anything but CommandRejected.
            kernel_pb2.COMPARE_OPTIONS: lambda state: sim.compare_options(
                state,
                str(decoded.get("item", "")),
                whole("cp_index", -1),
                str(decoded.get("person", "")),
                whole("at_tick", 0),
                in_person=bool(decoded.get("in_person", False)),
            ),
        }

        # Commands that read state and write none of it. A comparison is the only one — step.py
        # calls it "the only command that changes nothing and still appends" — and it is also
        # the only handler costing more than a quantum, so the two facts cancel: a command that
        # mutates nothing *can* run against a copy, and one costing a third of a second must.
        READ_ONLY = frozenset({kernel_pb2.COMPARE_OPTIONS})

        handler = dispatch.get(kind)
        if handler is None:
            raise sim.CommandRejected(f"no handler for command kind {kind}")

        if kind in READ_ONLY:
            with run.lock:
                # The whole state, walked twice, with the tick loop shut out for the duration —
                # which is what makes the capture whole. `restore` re-hashes what it rebuilt and
                # refuses a mismatch, so a `SnapshotInvalid` out of here is no longer a torn read
                # to retry past: it would mean the snapshot round-trip has genuinely lost a
                # field, which is a bug to see rather than to paper over.
                instant = snapshotting.capture(run_id, run.state, through_seq=0)
            # Outside the lock and inside the branch limiter (R14). Outside the lock because U4
            # measured the alternative — the handler under the lock rather than beside it — at 779
            # permille against 944, and a limiter buys nothing while the tick loop is blocked on a
            # lock. Inside the limiter because this is the only handler whose cost is seconds
            # rather than microseconds, and a waiting comparison holds no GIL while it waits.
            with BRANCH_LIMITER:
                emitted = handler(snapshotting.restore(instant))
            applied_tick = instant.tick
        else:
            with run.lock:
                emitted = handler(run.state)
                applied_tick = run.state.tick

        for item in emitted:
            item.command_id = command_id

        if not emitted:
            return []

        try:
            result = self.writer.submit(
                run_id=run_id,
                emitted=emitted,
                lease_handle=self.lease,
                rules_ver=RULES_VERSION,
                # Read under the lock, so a command applied at tick 100 is recorded at tick 100
                # rather than at whatever the clock reached while its events sat in the writer's
                # queue. For a comparison that is a third of a second of drift removed.
                tick=applied_tick,
            )
        except RunAlreadyTerminated as ended:
            # The run ended between this command being checked and its events being written.
            # Every caller already checks for a terminal run before dispatching, so this is the
            # race rather than the ordinary case — and it is a real window for a comparison,
            # which spends over a second between its guard and its append. The store is right
            # to refuse; what was wrong is that the refusal reached the client as an opaque 500
            # instead of the sentence it already carries.
            raise sim.CommandRejected(str(ended)) from None

        # The events this command produced, to the client that issued it. Nothing did this before,
        # so a command's own outcome reached its client only as the sequence gap a later tick
        # exposed — and a hand-off's walk, which is the delegation itself, was never rendered at
        # all. This runs on the request thread, so it hops to the event loop and is ordered
        # against the tick loop's own commits there; see `_publish`.
        self._publish(run, result.envelopes)

        self._outcomes.setdefault(run_id, {})
        return result.envelopes

    def record_outcome(
        self, run_id: str, idempotency_key: str, applied_tick: int, produced: list[int]
    ) -> None:
        """Remember a command's outcome so a reconnecting client can resolve it (R30)."""
        self._outcomes.setdefault(run_id, {})[idempotency_key] = {
            "applied_tick": applied_tick,
            "produced_seq": list(produced),
        }

    def outcome_by_key(self, run_id: str, idempotency_key: str) -> dict[str, Any] | None:
        return self._outcomes.get(run_id, {}).get(idempotency_key)

    # --- fork and export --------------------------------------------------

    def fork(
        self,
        parent_run_id: str,
        at_seq: int,
        option_index: int,
        idempotency_key: str,
        prefix_bound: int = FORK_PREFIX_MAX_EVENTS,
    ) -> ForkOutcome:
        """Take a past decision differently, and get a run back for it (M44-M48).

        **A fork is not a command, and the two enum values with no dispatch entry are the two
        that create a run.** `POST /runs` documents why: a command must never bring a simulation
        into being, or a typo'd id in a client silently starts one. `START_RUN` has sat unused in
        `CommandKind` since creation became its own verb; `FORK_RUN` joins it here for the same
        reason and by the same argument. Making a fork a command would also need two carve-outs
        in the gateway's guards — one for a paused run, one for a terminated one — each true for
        a reason that is not the guard's premise, and the guards would be describing forks rather
        than commands.

        **The fork point is the sequence before the parent's resolution, and the different option
        is applied inside the fork.** `at_seq` names the decision to reconsider; the copy stops
        one short of it, so the child arrives with that checkpoint still open and settles it
        differently. Forking *at* the resolution would copy the original decision and then apply
        a second one to a closed checkpoint. It cannot be a follow-up command either, because a
        child arrives paused and the paused-run guard rejects everything but rate and comparison.

        **The child's divergent event takes the parent's decision sequence**, because nothing is
        inserted ahead of it — no `RUN_FORKED`, though the kind exists and the fold already
        treats it as operational. That is deliberate and it buys something: the divergence is one
        sequence number, and both timelines hold an event at it, so "the decision that separated
        them" is a pair `(parent, n)` and `(child, n)` rather than a join through two mutable
        columns. U17's tree and U18's diff read parentage from `runs`, which is where it belongs;
        what the log carries is the divergence itself.

        **Everything that can refuse happens before anything is written**, which is what makes
        "a refusal mutates nothing" structural rather than a claim: the prefix is folded and the
        option applied to that fold *first*, so a bad option index, a checkpoint that is not
        open, and every guard below all raise or return before the writer is ever asked.

        **`in_person` is inherited from the parent's decision rather than chosen.** A fork is one
        variable moved. Deciding in person is worth two morale and some Visibility, and a child
        that changed the channel as well as the option would show a difference in the diff that
        the option did not cause.

        Runs on a request thread with no run lock held, and it takes none: it reads the parent's
        *log*, which is append-only and therefore immutable behind it, never the parent's live
        state. That is why a fork concurrent with a ticking parent is safe (R22) — the only
        contention left is the store's write lock, and the write goes through the single writer.
        """
        if not idempotency_key:
            return ForkOutcome(
                refusal=(
                    "a fork needs an idempotency key. It is what the child's id is minted from, "
                    "so a fork whose response you never saw can be retried without making a "
                    "second timeline."
                )
            )

        for label, value in (
            ("idempotency key", idempotency_key),
            ("run id", parent_run_id),
        ):
            unusable = refuse_an_unusable_identifier(label, value)
            if unusable:
                return ForkOutcome(refusal=unusable)

        parent_row = self.store.run_row(parent_run_id)
        if parent_row is None:
            raise KeyError(f"no such run: {parent_run_id}")

        child_run_id = child_run_id_for(parent_run_id, idempotency_key)
        through_seq = at_seq - 1

        already = self.store.run_row(child_run_id)
        if already is not None:
            return self._fork_already_taken(already, parent_run_id, at_seq)

        decision = self._decision_at(parent_run_id, at_seq)
        if isinstance(decision, str):
            return ForkOutcome(refusal=decision)

        payload = decision.decoded_payload()
        item_id = str(payload["item"])
        cp_index = int(payload["cp_index"])
        born_at = int(decision.tick)

        horizon = parent_row["horizon_tick"]
        if horizon is not None and born_at >= int(horizon):
            return ForkOutcome(
                refusal=(
                    f"the decision at sequence {at_seq} was taken at tick {born_at}, at or past "
                    f"the horizon of {int(horizon)} this lineage was created with. A child "
                    "inherits its parent's horizon — it is fixed at genesis — so this fork would "
                    "be a timeline with no time left to play."
                )
            )

        # Counted before it is read, so an oversized prefix is refused without being pulled into
        # memory and without paying for the fold below. Checked again inside the transaction,
        # where the count is authoritative; one sentence, from one place.
        size = self.store.prefix_size(parent_run_id, through_seq)
        if size > prefix_bound:
            return ForkOutcome(refusal=prefix_bound_refusal(through_seq, size, prefix_bound))

        prefix = self.store.read_events(parent_run_id, through_seq=through_seq)

        # The fold and the alternative decision, inside the limiter and outside every lock. See
        # `BRANCH_LIMITER` for why a fork's fold belongs in the same pool a comparison's branches
        # draw from, and the module docstring for why nothing slow may go inside `run.lock` —
        # this takes none, because a log prefix cannot change behind it.
        with BRANCH_LIMITER:
            try:
                folded = folder.fold(prefix, at_live_head=True, through_tick=born_at)
            except (folder.FoldRefused, folder.UnknownEventInFold) as refused:
                # A parent whose log this build cannot fold — written under different rules
                # (`check_rules_version`), or holding a kind this fold has no semantics for. The
                # fold's own sentence names both versions and the remedy, and it is a far better
                # answer than the 500 this used to be: `post_fork` catches nothing else, so a
                # rules-version mismatch on the parent reached the client as an opaque error on a
                # request that was well-formed.
                return ForkOutcome(refusal=str(refused))
            child_state = folded.state

            # Read off the fold's projection rather than off `child_state.pending`, and the
            # difference is not cosmetic: `outstanding_requests` is built from the log's own
            # `REQUEST_RAISED` events, while `pending` holds only what `step()` regenerated. A
            # request raised from a command path is in the first and not the second, so the
            # projection is the conservative reading — and it is the one a restarted kernel
            # dispatches from, which is exactly the set the child would inherit.
            blocking = sorted(
                request_id
                for request_id, subject in folded.outstanding_requests.items()
                if subject.get("owning_item") == item_id
            )
            if blocking:
                return ForkOutcome(
                    refusal=(
                        f"{item_id} still has {len(blocking)} unanswered request(s) at sequence "
                        f"{through_seq} ({', '.join(blocking)}). This fork settles that "
                        "checkpoint at its own first tick, so the copied question could only "
                        "ever be answered against a decision that has already been taken. Wait "
                        "for the answer or let the request reach its deadline, then fork."
                    )
                )

            try:
                emitted = sim.resolve_checkpoint(
                    child_state,
                    item_id,
                    cp_index,
                    option_index,
                    in_person=bool(payload["in_person"]),
                )
            except sim.CommandRejected as rejected:
                return ForkOutcome(refusal=str(rejected))

        # **The append stays in this method, beside the publish below**, and that is a constraint
        # rather than a preference: `test_every_append_in_this_file_publishes_what_it_committed`
        # reads this file and requires the function that appends to be the function that
        # publishes, because an append whose caller forgets to publish leaves a sequence the
        # ordering cursor waits behind for the rest of the run. Extracting the error handling into
        # a helper split those two apart and the test said so immediately. Whoever splits `fork`
        # into a plan half and a commit half has to move that guard deliberately, not around.
        #
        # Three things can be thrown here and none of them were caught before: `FencedOut` when
        # the lease changed hands mid-fork, a `StoreError` when the writer does not commit inside
        # its timeout, and any other store failure. `post_fork` catches only `KeyError`, so every
        # one reached the client as an opaque 500 on a well-formed request — the shape
        # `apply_command` already refuses to produce.
        try:
            result = self.writer.submit_fork(
                parent_run_id=parent_run_id,
                through_seq=through_seq,
                child_run_id=child_run_id,
                lease_handle=self.lease,
                child_tick=born_at,
                emitted=emitted,
                rules_ver=RULES_VERSION,
                prefix_bound=prefix_bound,
            )
        except FencedOut as fenced:
            log.error("a fork was refused by the lease", extra={"error": str(fenced)})
            return ForkOutcome(refusal=str(fenced))
        except StoreError as failed:
            # **The timeout is the interesting one, and a bare refusal would be a lie.**
            # `submit_fork` abandons its own wait after thirty seconds *while the writer may still
            # land the transaction* — the case `PUBLISH_HELD_BACK_BOUND` documents for appends.
            # For a fork that leaves a child row committed with no `RunLoop` against it:
            # `/runs/{id}/state` answers from the row and reports the run as existing, while a
            # command against it answers not-found. So this looks before it refuses, and adopts a
            # child that landed — the same reconciliation a retry would perform, done now.
            landed = self.store.run_row(child_run_id)
            if landed is None:
                return ForkOutcome(
                    refusal=(
                        f"the store did not complete this fork: {failed}. Nothing was written — "
                        "the copy and the divergence are one transaction — so retrying under the "
                        "same idempotency key is safe and will not make a second timeline."
                    )
                )
            log.warning(
                "a fork's writer gave up but its transaction committed; adopting the child",
                extra={"child": child_run_id, "error": str(failed)},
            )
            return self._fork_already_taken(landed, parent_run_id, at_seq)

        if not result.forked:
            return ForkOutcome(refusal=result.refusal)

        if result.existed:
            # Two forks under one key, close enough together that both got past the row check
            # above — two clicks on the same button. The writer serialises them, so the second
            # one finds the first one's child, and the honest answer is that child rather than a
            # `RunLoop` built from a fold nobody committed.
            found = self.store.run_row(child_run_id)
            if found is None:
                # Not an `assert`: this is a request path, and `assert` is stripped under `-O`,
                # which would turn a store disagreeing with itself into a `TypeError` one line
                # later inside `_fork_already_taken` instead of the sentence written here.
                raise StoreError(
                    f"the store reported that child {child_run_id} already existed and then "
                    "could not produce its row. Refusing rather than registering a run this "
                    "kernel cannot describe."
                )
            return self._fork_already_taken(found, parent_run_id, at_seq)

        # Registered with the runtime, which the old fork never did — so the next command against
        # a child raised `KeyError` out of `apply_command` and reached the client as a 500. The
        # state handed over is the one the alternative was applied to, so the child's first frame
        # is the fold a restart would rebuild rather than a second reconstruction of it.
        child = RunLoop(
            run_id=child_run_id,
            state=child_state,
            rate=0,
            # Where the wire is caught up to. The copied prefix was published to the parent's
            # subscribers as it happened and nobody is attached to a run that did not exist a
            # moment ago; what must not happen is the publisher holding the divergence back
            # waiting for sequences that were somebody else's.
            published_seq=through_seq,
        )
        child.statement_subjects = {
            request_id: dict(subject)
            for request_id, subject in folded.outstanding_requests.items()
            if subject.get("service") == pend.BENCH
        }
        self.runs[child_run_id] = child
        self._publish(child, result.envelopes)

        log.info(
            "run forked",
            extra={
                "run": child_run_id,
                "parent": parent_run_id,
                "at_seq": at_seq,
                "tick": born_at,
                "lineage": result.lineage_root_id,
            },
        )
        return ForkOutcome(
            child_run_id=child_run_id,
            parent_run_id=parent_run_id,
            decision_seq=at_seq,
            forked_at_seq=through_seq,
            forked_at_tick=born_at,
            lineage_root_id=result.lineage_root_id,
            item=item_id,
            cp_index=cp_index,
            option_index=option_index,
            parent_option_index=int(payload["option_index"]),
            created=True,
        )

    def _decision_at(self, parent_run_id: str, at_seq: int) -> Envelope | str:
        """The `DECISION_RESOLVED` at that sequence, or the sentence refusing it.

        This is where "forking a still-open checkpoint" is answered. A checkpoint that has not
        been settled has no resolution event, so the sequence a caller points at is the
        `CHECKPOINT_RAISED` — and the refusal says so, rather than reporting a fork point that
        happens to hold nothing.
        """
        if at_seq < 2:
            return (
                f"sequence {at_seq} is not a decision: sequence 1 is GENESIS and a fork copies "
                "the prefix before the decision it reconsiders, so there is nothing above it."
            )

        found = self.store.read_events(
            parent_run_id, after_seq=at_seq - 1, through_seq=at_seq
        )
        if not found:
            return f"run {parent_run_id} has no event at sequence {at_seq}"
        if found[0].kind is not EventKind.DECISION_RESOLVED:
            return (
                f"sequence {at_seq} of run {parent_run_id} is {found[0].kind.name}, not a "
                "decision. A fork reconsiders a decision that was taken — point it at the "
                "DECISION_RESOLVED you want to take differently. A checkpoint that is still open "
                "has nothing to reconsider yet: settle it first, then fork it."
            )
        return found[0]

    def _fork_already_taken(
        self, child_row: dict[str, Any], parent_run_id: str, at_seq: int
    ) -> ForkOutcome:
        """A child this key already produced. M47's second half.

        The gateway's ledger is in memory, so a fork retried after a restart arrives here with
        nothing remembering the first attempt. Because the id is recomputable the retry lands on
        the child that exists, and the answer is that child rather than a second timeline or an
        integrity error.

        **The found child's parentage is checked rather than assumed.** Twelve hex characters is
        the id shape `POST /runs` mints and the odds are not the point: a digest collision here
        would hand the caller somebody else's run as though it were their fork, and a stated
        refusal is the only acceptable failure for that.
        """
        expected_seq = at_seq - 1
        if (
            str(child_row["parent_run_id"] or "") != parent_run_id
            or int(child_row["forked_at_seq"] or 0) != expected_seq
        ):
            return ForkOutcome(
                refusal=(
                    f"run {child_row['run_id']} already exists and is not this fork: it is a "
                    f"child of {child_row['parent_run_id']!r} at sequence "
                    f"{child_row['forked_at_seq']}, not of {parent_run_id!r} at {expected_seq}. "
                    "Retry with a different idempotency key."
                )
            )

        child_run_id = str(child_row["run_id"])
        if child_run_id not in self.runs:
            # The fork committed and this process never saw it — it was made before a restart
            # that `resume_all` has not reached, or by a call that died between the commit and
            # the registration. Either way the run exists and a command against it must not
            # raise, which is the third defect this unit closes.
            self.resume_run(child_run_id)

        # Read back off the child's own log rather than echoed from the request, so a retry's
        # answer describes the timeline that exists rather than the one this call asked for. They
        # agree unless the caller reused a key with a different option, which is a mistake worth
        # showing them the truth about.
        taken = self.store.read_events(
            child_run_id, after_seq=expected_seq, through_seq=at_seq
        )
        payload = taken[0].decoded_payload() if taken else {}

        # **The birth tick comes off that event, never off `runs.current_tick`.** The row's tick is
        # rewritten by `append_tick` on every commit the child makes, so a retry that arrives after
        # the child has played forward would answer with the child's *now* — which is this unit's
        # own second defect, reappearing on the idempotent path. Measured before the fix: a child
        # born at 613 reported 1080. The divergence event is immutable and is already in hand.
        born_at = int(taken[0].tick) if taken else int(child_row["current_tick"])

        return ForkOutcome(
            child_run_id=child_run_id,
            parent_run_id=parent_run_id,
            decision_seq=at_seq,
            forked_at_seq=expected_seq,
            forked_at_tick=born_at,
            lineage_root_id=str(child_row["lineage_root_id"]),
            item=str(payload.get("item", "")),
            cp_index=int(payload.get("cp_index", 0)),
            option_index=int(payload.get("option_index", 0)),
            parent_option_index=self._parent_option_at(parent_run_id, at_seq),
            created=False,
        )

    def _parent_option_at(self, parent_run_id: str, at_seq: int) -> int:
        """Which option the parent took at that sequence, for a retry's answer."""
        found = self.store.read_events(
            parent_run_id, after_seq=at_seq - 1, through_seq=at_seq
        )
        if not found or found[0].kind is not EventKind.DECISION_RESOLVED:
            return 0
        return int(found[0].decoded_payload().get("option_index", 0))

    def export(self, run_id: str) -> tuple[bytes, str]:
        from simcore import export as exporter
        from simcore import hashing

        run = self.runs.get(run_id)
        events = self.store.read_events(run_id)
        row = self.store.run_row(run_id)
        through_tick = run.state.tick if run else int(row["current_tick"]) if row else 0

        if run is not None:
            state = run.state
        else:
            state = folder.fold(
                events, at_live_head=False, through_tick=through_tick
            ).state

        state_hash = hashing.state_hash(sim.snapshot(state)).overall
        artifact = exporter.export_run(
            run_id=run_id, events=events, through_tick=through_tick, state_hash=state_hash
        )
        return artifact, state_hash

    # --- diagnosis --------------------------------------------------------

    def diagnose(self, run_id: str) -> Diagnosis:
        """Why did the clock stop? Answered without anyone reading a log."""
        run = self.runs[run_id]

        if run.task is None:
            task_state = "absent"
        elif run.task.cancelled():
            task_state = "cancelled"
        elif run.task.done():
            task_state = "finished"
        else:
            task_state = "running"

        # Under the lock: this walks `state.items`, and a dynamic item arriving on a tick
        # mid-iteration raises "dictionary changed size during iteration". A diagnostic that
        # fails when the run is busy fails exactly when it is wanted.
        with run.lock:
            unresolved = [
                f"{item.id}:cp{item.decisions and len(item.decisions) or 0}"
                for item in run.state.items.values()
                if item.status == sim.STATUS_BLOCKED
            ]
            # What the run is waiting on, and from whom. Empty until this unit, which meant the one
            # field on this payload named after the pending-input contract answered nothing about
            # it — so "why is there no briefing" was a question that needed a log. It also reports
            # the case the per-item cap produces: three statements outstanding on one item is why a
            # fourth was not raised, and it is visible here rather than only inferable.
            outstanding = [
                {
                    "request_id": request_id,
                    "service": request.service,
                    "owning_item": request.owning_item,
                    "raised_at_tick": request.raised_at_tick,
                    "deadline_tick": request.deadline_tick,
                    "ticks_remaining": max(0, request.deadline_tick - run.state.tick),
                    "person": str(run.statement_subjects.get(request_id, {}).get("person", "")),
                    "asked": request_id in run.statements_asked,
                }
                for request_id, request in sorted(run.state.pending.items())
            ]

        return Diagnosis(
            run_id=run_id,
            rate=run.rate,
            rate_effective_tick=run.rate_effective_tick,
            subscribers=len(run.subscribers),
            tick_task_state=task_state,
            tick_task_exception=(
                f"{type(run.exception).__name__}: {run.exception}" if run.exception else ""
            ),
            last_wake_at=run.last_wake_wall,
            seconds_since_last_tick=int(time.monotonic() - run.progress_at),
            sim_time_lag_ticks=run.lag_ticks,
            achieved_multiplier_permille=run.achieved_multiplier_permille,
            unresolved_checkpoints=unresolved,
            outstanding_requests=outstanding,
            store_reachable=self._store_reachable,
            lease_held=self.lease is not None,
            terminal_reason=run.state.terminal_reason,
        )

    def healthy(self) -> tuple[bool, str]:
        """Readiness reflects tick *progress*, not tick-task liveness (R14, amending R29).

        A dead task for a run with a non-zero rate is unhealthy, and the exception is named —
        a kernel that reports healthy while its clock is stopped is worse than one that is
        down, because nothing prompts anyone to look.

        **A live task is not enough, and that was the hole.** A starved loop is neither done nor
        crashed: it is sitting in an `await`, waiting for a worker slot or for its turn at the
        GIL, with nothing raised and nothing advanced. R29 as written reported that as ready. So
        liveness is now the first question and not the only one — the second is when sim-time last
        moved, which no amount of waiting can fake.

        Sim-time rather than `achieved_multiplier_permille`, deliberately. That field computes its
        wanted ticks with `int(elapsed * ...)` and drops the remainder at every wake, so it reads
        1000 through a stall of several wall seconds; a readiness probe resting on it would report
        ready through exactly the failure this is here to catch.
        """
        now = time.monotonic()

        for run in self.runs.values():
            if run.rate == 0 or run.state.terminal_reason:
                continue
            if run.task is None or run.task.done():
                detail = (
                    f"{type(run.exception).__name__}: {run.exception}"
                    if run.exception
                    else "the tick task is not running"
                )
                return False, f"run {run.run_id} has rate {run.rate} but {detail}"

            stalled_for = now - run.progress_at
            if stalled_for > CLOCK_STALL_SECONDS:
                return False, (
                    f"run {run.run_id} has rate {run.rate} and a live tick task, but sim-time "
                    f"has not moved past tick {run.progress_tick} for {stalled_for:.1f}s — the "
                    f"clock is starved rather than stopped, and a clock is called stalled after "
                    f"{CLOCK_STALL_SECONDS:.0f}s"
                )

        return True, "every run with a non-zero rate has a clock that is still moving"
