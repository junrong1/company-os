"""Fixtures more than one suite needs, and the one place that decides about Postgres.

Two things live here rather than in a suite.

**Whether Postgres is reachable.** `test_store.py` asked the question first and U19's lineage
suites ask it again, and two probes would be two answers — one suite skipping while the other
runs is worse than either, because the skip message is what tells a contributor the run was
incomplete. One probe, one URL, one sentence.

**How a timeline is driven without a store.** `Recorder` plays a run in-process and records it
exactly as the kernel's loop does, day-boundary checkpoints included. U18 wrote it for the diff
and U20's Universe report needs the same thing — a log it can fold — so it is here rather than
imported across two suites. It is deliberately a *second* writer of the same events: a harness
that called the kernel's own appender would be unable to say anything about a log the kernel
wrote wrongly.

**How a lineage is built.** A lineage is a parent, a decision, and the timelines forked from
it, and nothing below `KernelRuntime` can make one: the fork copies log rows through the single
writer. So the assertions in `test_replay.py`, `test_determinism.py` and `test_compare.py` are
about a real tree that a real kernel wrote, and the machinery that drives one is here instead of
being copied into three files. It is deliberately the *shipped* path — `create_run`,
`apply_command`, `_advance`, `fork` — because a harness that assembled a lineage out of
`simcore` calls would be testing a second implementation of forking.

**The parent is driven through a briefing on the way**, which is not incidental. It puts a
statement in the pre-fork prefix, which is what M34's "identical statements before their
divergence" is about, and it walks the CEO — leaving `CEO_INPUT` events that name a tick in
their own future, which is the shape that used to make `verify` refuse a healthy run.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import dataclass, field

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from contracts import canonical
from contracts.envelope import Envelope, EventKind, build as build_envelope
from contracts.grpc import kernel_pb2
from kernel.loop import KernelRuntime
from kernel.store import LogStore, make_engine
from logschema import metadata
from report import fold as reporting
from simcore import step as sim
from simcore import time as simtime
from simcore import verify as verifier
from simcore.rates import RULES_VERSION

#: A separate database from the application's, provisioned by
#: infra/postgres/init/20-test-database.sql, and published on the host by
#: docker-compose.test.yml.
DEFAULT_POSTGRES_URL = "postgresql+psycopg://companyos:companyos@127.0.0.1:55432/companyos_test"
POSTGRES_URL = os.environ.get("COMPANY_OS_TEST_POSTGRES_URL", DEFAULT_POSTGRES_URL)


def _postgres_reachable() -> tuple[bool, str]:
    try:
        engine = make_engine(POSTGRES_URL)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
        return True, ""
    except Exception as exc:  # noqa: BLE001 - any failure means "not available"
        return False, f"{type(exc).__name__}: {exc}"


POSTGRES_OK, POSTGRES_WHY = _postgres_reachable()

#: Why the Postgres half is being skipped, in the words a contributor can act on. Stated once,
#: because a suite that skips quietly reads exactly like a suite that passed.
POSTGRES_SKIP = (
    f"Postgres not reachable at {POSTGRES_URL} ({POSTGRES_WHY}). Start it with "
    "`docker compose -f docker-compose.yml -f docker-compose.test.yml up -d postgres`. "
    "Both dialects are required by U6's and U19's verification, so a skip here is an "
    "incomplete run, not a pass."
)


#: The run seed every in-process harness here plays from. One value, because two suites
#: comparing "two runs of one seed" must mean the same seed.
SEED = 0xC0FFEE


def engine_for(dialect: str, tmp_path) -> Engine:
    """An engine on the named dialect, with the shared database emptied first.

    SQLite gets a file per test and is therefore fresh by construction. Postgres is one
    database every test shares, so it starts from nothing — dropping the tables takes the
    append-only triggers with them, which is what makes the next `create_all` a real
    provisioning rather than a no-op over somebody else's rows.
    """
    if dialect == "sqlite":
        return make_engine(f"sqlite:///{tmp_path}/kernel.sqlite3")
    if not POSTGRES_OK:
        pytest.skip(POSTGRES_SKIP)
    engine = make_engine(POSTGRES_URL)
    metadata.drop_all(engine)
    return engine


# =========================================================================
# One timeline, driven in-process and recorded as the kernel records it
# =========================================================================


class Recorder:
    """A run played without a store, and the log it would have written.

    The day checkpoint is emitted here for the same reason the kernel's loop emits it: the log is
    what a read surface folds, and a log with no boundary hashes could not be used to check that
    the fold reached the state the kernel was in.

    `scenario` is passed through to `new_run`, so a suite can play a company other than the
    shipped default. The fold resolves it back by the identity genesis records, which means the
    company has to be one that exists on disk — an in-memory scenario would be refused at the
    fold's own guard (R7), which is the correct answer rather than a limitation of this harness.
    """

    def __init__(
        self,
        run_id: str,
        horizon_tick: int | None = None,
        scenario=None,
        seed: int = SEED,
    ) -> None:
        self.run_id = run_id
        self.state, genesis = sim.new_run(
            run_seed=seed, horizon_tick=horizon_tick, scenario=scenario
        )
        self.log: list[Envelope] = []
        self._seq = 0
        self.record(genesis)

    def record(self, emitted: list[sim.Emitted]) -> None:
        for item in emitted:
            self._seq += 1
            self.log.append(
                build_envelope(
                    seq=self._seq,
                    tick=int(item.payload.get("tick", self.state.tick)),
                    kind=item.kind,
                    rules_ver=RULES_VERSION,
                    payload=item.payload,
                    run_id=self.run_id,
                    request_id=item.request_id,
                )
            )

    def advance(self, ticks: int) -> None:
        for _ in range(ticks):
            emitted = sim.step(self.state)
            if simtime.is_day_boundary(self.state.tick) and self.state.tick > 0:
                emitted.append(
                    sim.Emitted(
                        kind=EventKind.DAY_CHECKPOINT,
                        payload=verifier.build_checkpoint_payload(self.state),
                    )
                )
            self.record(emitted)

    def advance_until(self, predicate, limit: int = 60_000) -> None:
        for _ in range(limit):
            if predicate(self.state):
                return
            self.advance(1)
        raise AssertionError(f"condition never held within {limit} ticks")

    def settle(self, option_index: int, item: str = "wi_ap_map", person: str = "stf_ap") -> None:
        """Drive to the first checkpoint of an authored item and settle it."""
        self.record(sim.assign_direct(self.state, item, person))
        self.advance_until(lambda s: s.items[item].status == "blocked")
        self.record(sim.resolve_checkpoint(self.state, item, 0, option_index, in_person=True))

    def timeline(self) -> reporting.TimelineLog:
        return reporting.TimelineLog(
            run_id=self.run_id, events=self.log, current_tick=self.state.tick
        )


def played(run_id: str, option_index: int, days: int) -> Recorder:
    """One timeline: a decision settled one way, then played out to the given day."""
    recorder = Recorder(run_id)
    recorder.settle(option_index)
    recorder.advance_until(lambda s: s.tick >= simtime.tick_of_day_start(days))
    return recorder


# =========================================================================
# A lineage: one parent, one decision, and the timelines forked from it
# =========================================================================

ROOT = "run-lineage"

#: The item the CEO is walked to and the director who briefs on it. `wi_hiring` is assigned at
#: genesis (U2), so it reaches its checkpoint without being assigned first — which keeps the
#: pre-fork prefix down to the events this harness is actually about.
BRIEF_ITEM = "wi_hiring"
BRIEF_DIRECTOR = "dir_hr"

#: The parent takes the first option and the two children take the others, so no two timelines
#: in the lineage are the same run played twice. The scenario authors three.
PARENT_OPTION = 0
CHILD_OPTIONS = (1, 2)

#: Ticks played after the decision, on the parent and on each child. Two sim-days and change, so
#: every timeline writes day-boundary checkpoints of its own past the divergence — which is what
#: there is to compare. A lineage whose children never crossed a boundary would verify happily
#: and prove nothing.
PLAY_ON_TICKS = 2 * simtime.TICKS_PER_SIM_DAY + 20


def scripted_statement(request) -> dict:
    """A well-formed statement citing nothing, which is legal and keeps the fixture keyless.

    Deliberately identical for every request: the point of M34 is that what the bench says is
    the same on both sides of a fork, and a producer that varied would prove the opposite of
    what this harness is for.
    """
    from simcore import statement as statements

    return statements.Statement(
        briefing="The recruiter is the constraint, not the budget.",
        objection="Cutting the review step is how the last two mis-hires got through.",
        citations=(),
        producer=request.person,
        producer_kind=statements.PRODUCER_SCRIPTED,
        model_identity="",
        context={
            "director": request.person,
            "line": sorted(request.authorized.people),
            "since_seq": 0,
            "through_seq": 0,
            "events": [],
            "draw": {},
            "unlocking_note": "",
        },
    ).to_answer()


@dataclass
class Lineage:
    """A built lineage, and the reads the suites make of it."""

    runtime: KernelRuntime
    root: str
    children: list[str] = field(default_factory=list)
    #: The parent sequence the children were forked at — the decision they reconsider.
    decision_seq: int = 0
    #: The tick that decision was taken at. Every timeline is identical below it.
    forked_at_tick: int = 0

    @property
    def timelines(self) -> list[str]:
        return [self.root, *self.children]

    @property
    def store(self) -> LogStore:
        return self.runtime.store

    def events(self, run_id: str) -> list[Envelope]:
        return self.store.read_events(run_id)

    def checkpoints(self, run_id: str) -> dict[int, str]:
        """Every day-boundary hash this timeline's log carries, by tick."""
        return {
            int(envelope.decoded_payload()["tick"]): str(
                envelope.decoded_payload()["state_hash"]
            )
            for envelope in self.events(run_id)
            if envelope.kind is EventKind.DAY_CHECKPOINT
        }


def build_lineage(runtime: KernelRuntime, children: tuple[int, ...] = CHILD_OPTIONS) -> Lineage:
    """Play a run through a briefing and a decision, then fork it once per option given."""

    async def _play() -> Lineage:
        runtime.use_statement_producer(scripted_statement)
        runtime.create_run(ROOT, SEED)
        run = runtime.runs[ROOT]

        _walk_the_ceo_to_the_briefing(runtime, ROOT)
        await _let_the_bench_answer(runtime, ROOT)

        # Past the first day boundary before the decision, so the prefix every child inherits
        # holds a checkpoint the parent wrote and the two can be compared below the divergence.
        runtime._advance(run, simtime.TICKS_PER_SIM_DAY)

        resolved = runtime.apply_command(
            ROOT,
            kernel_pb2.RESOLVE_CHECKPOINT,
            canonical.encode(
                {
                    "item": BRIEF_ITEM,
                    "cp_index": 0,
                    "option_index": PARENT_OPTION,
                    "in_person": True,
                }
            ),
        )
        assert len(resolved) == 1 and resolved[0].kind is EventKind.DECISION_RESOLVED
        lineage = Lineage(
            runtime=runtime,
            root=ROOT,
            decision_seq=resolved[0].seq,
            forked_at_tick=run.state.tick,
        )
        runtime._advance(run, PLAY_ON_TICKS)

        for option in children:
            outcome = runtime.fork(
                ROOT,
                at_seq=lineage.decision_seq,
                option_index=option,
                idempotency_key=f"option-{option}",
            )
            assert outcome.forked, outcome.refusal
            lineage.children.append(outcome.child_run_id)
            runtime._advance(runtime.runs[outcome.child_run_id], PLAY_ON_TICKS)

        return lineage

    # A loop for the length of the build and no longer. The bench leg hands its work to the
    # running loop and `_ask_soon` records the subject and asks nothing when there is none, so a
    # lineage built by a fully synchronous caller would carry a request nobody answered — which
    # is a different fixture from the one these suites are about.
    return asyncio.run(_play())


def _walk_the_ceo_to_the_briefing(runtime: KernelRuntime, run_id: str) -> None:
    """Walk the CEO to the director's desk through the kernel's own surfaces.

    By submitted input rather than by assignment to `run.state.ceo`, because the events it
    leaves are half the point: a `CEO_INPUT` names the tick it *applies* at, and a log holding
    one is the shape `verify` used to refuse.
    """
    from simcore.world import find_path

    run = runtime.runs[run_id]
    while run.state.items[BRIEF_ITEM].status != sim.STATUS_BLOCKED:
        runtime._advance(run, 1)

    target = run.state.seats[BRIEF_DIRECTOR]
    held = 0
    for _ in range(600):
        here = run.state.ceo.tile
        path = find_path(run.state.floor, here, target)
        step_to = here
        if path:
            step_to = path[1] if path[0] == here and len(path) > 1 else path[0]

        mask = 0
        centre_x = step_to[0] * sim.MILLI + sim.MILLI // 2
        centre_y = step_to[1] * sim.MILLI + sim.MILLI // 2
        if run.state.ceo.x_milli < centre_x - 60:
            mask |= sim.INPUT_RIGHT
        elif run.state.ceo.x_milli > centre_x + 60:
            mask |= sim.INPUT_LEFT
        if run.state.ceo.y_milli < centre_y - 60:
            mask |= sim.INPUT_DOWN
        elif run.state.ceo.y_milli > centre_y + 60:
            mask |= sim.INPUT_UP

        if mask != held:
            runtime.apply_command(
                run_id,
                kernel_pb2.SUBMIT_CEO_INPUT,
                canonical.encode({"bitmask": mask, "at_tick": run.state.tick + 1}),
            )
            held = mask
        runtime._advance(run, 1)
        if sim._ceo_is_beside(run.state, run.state.people[BRIEF_DIRECTOR]):
            return
    raise AssertionError("the CEO never reached the director")


async def _let_the_bench_answer(runtime: KernelRuntime, run_id: str) -> None:
    """Wait for every in-flight answer task. Deterministic: it awaits, never sleeps."""
    run = runtime.runs[run_id]
    for _ in range(50):
        if not run.statement_tasks:
            return
        await asyncio.gather(*list(run.statement_tasks), return_exceptions=True)
    raise AssertionError("the answer tasks never drained")


@contextlib.contextmanager
def kernel_on(dialect: str, tmp_path):
    """A started `KernelRuntime` over an empty store, for the length of the block.

    A context manager as well as a fixture because one test needs *two* lineages built from one
    seed, and on Postgres that cannot be two live kernels: the second would be refused the writer
    lease, which is the whole point of the lease. Sequential blocks work — the teardown drops the
    schema, lease row included — so the comparison is between two logs rather than between two
    kernels racing for one store.
    """
    engine = engine_for(dialect, tmp_path)
    store = LogStore(engine)
    runtime = KernelRuntime(store)
    runtime.start()
    try:
        yield runtime
    finally:
        runtime.writer.stop()
        if dialect == "postgresql":
            metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture(params=["sqlite", "postgresql"])
def kernel_on_each_dialect(request, tmp_path):
    """A started `KernelRuntime` over an empty store, once per dialect."""
    with kernel_on(request.param, tmp_path) as runtime:
        yield runtime


@pytest.fixture
def lineage(kernel_on_each_dialect) -> Lineage:
    """A three-timeline lineage on each dialect: the parent and two forks of one decision."""
    return build_lineage(kernel_on_each_dialect)


#: The item driven to an open checkpoint by `stop_at_a_decision`, and who carries it. Not the
#: briefing item: a comparison is refused while the checkpoint has an unanswered request against
#: it, and this run is never walked to a director.
COMPARE_ITEM = "wi_ap_map"
COMPARE_PERSON = "stf_ap"


def stop_at_a_decision(runtime: KernelRuntime, run_id: str = "run-stopped"):
    """A run stopped at an open checkpoint, which is the only state a comparison is legal in."""
    runtime.create_run(run_id, SEED)
    run = runtime.runs[run_id]
    runtime.apply_command(
        run_id,
        kernel_pb2.ASSIGN_WORK,
        canonical.encode({"item": COMPARE_ITEM, "person": COMPARE_PERSON, "via_manager": False}),
    )
    while run.state.items[COMPARE_ITEM].status != sim.STATUS_BLOCKED:
        runtime._advance(run, 1)
    return run


def whole_store(engine) -> dict[str, list[dict]]:
    """Every row of every table, for a diff around a call that must not write.

    Every table rather than the interesting ones, because the claim M35 makes is about the store
    and a list of tables to check is a list somebody has to remember to extend. `metadata` already
    knows them all, and a table added without a thought here shows up as a difference.
    """
    from sqlalchemy import select

    out: dict[str, list[dict]] = {}
    with engine.connect() as connection:
        for table in metadata.sorted_tables:
            rows = connection.execute(select(table)).mappings().all()
            out[table.name] = [dict(row) for row in rows]
    return out
