"""The launcher: the same application objects, one process (R16).

It composes the kernel runtime and all five service apps **in one process**, and that
composition is the whole file — it reimplements nothing, because anything it reimplemented is
where drift would start.

**This is no longer a second topology.** It was the mode a contributor used to work on the
simulation without Docker, while compose ran five backend containers that met over gRPC. The
`backend` container now runs this file, so there is one composition and two ways to invoke it:
`docker compose up`, and `uv run python single_process.py` for a contributor with no Docker. The
gRPC servicer that fronted the old split was deleted with it; the proto stays as the command-kind
vocabulary, which is what `COMMAND_KINDS` below reads.

**It lives outside every service on purpose.** R4 forbids a service from importing another
service's internals, and the gateway still must not import the kernel — the boundary is an
import rule, not a transport, and collapsing the deployment did not relax it. But *something* has
to compose them, and that something cannot be any of them. So it is here, at the top of the
backend tree, next to `pyproject.toml` — a launcher, not a service, and outside the directories
the import-boundary tests police. It is the only component in the tree that may see two services
at once, and `tests/test_import_boundaries.py` says so explicitly rather than by omission.

**The report is mounted here rather than reached through the gateway (R28).** M2 puts it among
the surfaces in one process, and the obvious shortcut — the gateway importing `report.fold` and
serving the report itself — is exactly the import R4 forbids. Mounting is what gives one port
five surfaces without any of them learning about another. Each keeps its own prefix, so a caller
still has to say which surface it wants: `/status` is the gateway's, and the kernel's diagnose
call is at `/kernel/runs/{id}/diagnose` rather than on the published path where the client's
own routes live.

**What a green run here does not cover**, when it is run on the default store: the Postgres-only
hazards — JSONB key ordering underneath the state hash, and the sequence and transaction-control
differences — because the default is SQLite. Those are covered by the same launcher under compose,
which points it at Postgres, and by the store suite's two dialects. The value of the SQLite default
is that the kernel, determinism, parity and replay suites run fast and need no Docker.

    uv run python single_process.py            # SQLite at var/company-os.sqlite3
    COMPANY_OS_STORE_URL=postgresql+psycopg://... uv run python single_process.py
    COMPANY_OS_REPORT_STORE_URL=...            # the report's reader; defaults to the writer's
    COMPANY_OS_GATEWAY_PORT=8810 uv run python single_process.py   # a compose stack holds 8800
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

# `packages/` and `services/` are import roots rather than installed distributions, and the
# `pythonpath` setting in pyproject.toml that arranges that belongs to *pytest*. A script run
# directly gets none of it, so it arranges its own — otherwise the command this module's own
# docstring documents fails on `import gateway` before it reaches a single line of its own code.
_BACKEND = Path(__file__).resolve().parent
for _root in ("packages", "services"):
    _path = str(_BACKEND / _root)
    if _path not in sys.path:
        sys.path.insert(0, _path)

from gateway import stream as streaming  # noqa: E402 - after the path bootstrap above
from gateway.commands import CommandResult, Outcome  # noqa: E402
from kernel import lease as lease_module  # noqa: E402
from kernel.loop import KernelRuntime  # noqa: E402
from kernel.store import DdlVersionMismatch, LogStore, make_engine  # noqa: E402
from servicekit import logging as svclog  # noqa: E402
from servicekit.probes import parse_store_url, store_url  # noqa: E402

log = svclog.get_logger("single-process")

#: What the JSON log lines call this process.
#:
#: One label rather than five, and it has to be set *after* the surfaces are imported:
#: `create_service_app` calls `svclog.configure(service)` at import, and configure replaces
#: the root handler set, so five imports would otherwise leave every line in the container
#: stamped with whichever service was imported last. `logger` still carries the module that
#: emitted the record, which is the field that actually distinguishes them.
LOG_SERVICE = "backend"

#: The surfaces mounted beside the gateway, and the prefix each answers under.
#:
#: The gateway keeps the root, because the client's proxy configuration maps `/api/` onto
#: `/` and moving it would break every call the client makes. Everything else is prefixed,
#: which is what keeps the published path unambiguous: `/runs/{id}/report` reaching the
#: report while `/runs/{id}/state` reaches the gateway would be one namespace shared by two
#: surfaces, and the first collision would be silent.
SURFACES: tuple[tuple[str, str], ...] = (
    ("/kernel", "kernel.main"),
    ("/domain", "domain.main"),
    ("/agents", "agents.main"),
    ("/report", "report.main"),
)

#: Command kinds the gateway accepts, mapped to the kernel's proto enum values. The gateway speaks
#: strings because that is what arrives over REST; the kernel speaks the enum.
COMMAND_KINDS = {
    "assign_work": "ASSIGN_WORK",
    "reassign_work": "REASSIGN_WORK",
    "return_to_backlog": "RETURN_TO_BACKLOG",
    "resolve_checkpoint": "RESOLVE_CHECKPOINT",
    "submit_ceo_input": "SUBMIT_CEO_INPUT",
    "set_rate": "SET_RATE",
    "request_hire": "REQUEST_HIRE",
    "ask_person": "ASK_PERSON",
    "compare_options": "COMPARE_OPTIONS",
    "decide_authorization": "DECIDE_AUTHORIZATION",
}


class InProcessKernel:
    """A `KernelClient` backed by a `KernelRuntime` in this process.

    The only implementation there is. There was a second — a gRPC servicer in front of the same
    runtime — and every method here was the same call it made minus the encode/decode, which is
    what made the split cost a container and a hop and buy nothing on a machine with one operator.
    """

    def __init__(self, runtime: KernelRuntime) -> None:
        self.runtime = runtime
        self._subscriptions: dict[int, tuple[str, asyncio.Queue, asyncio.Task]] = {}

    # --- commands ---------------------------------------------------------

    def submit(
        self, run_id: str, kind: str, payload: dict[str, Any], command_id: str
    ) -> CommandResult:
        from contracts.grpc import kernel_pb2
        from simcore.step import CommandRejected

        proto_name = COMMAND_KINDS.get(kind)
        if proto_name is None:
            return CommandResult(status=Outcome.REJECTED, reason=f"unknown command kind {kind!r}")

        run = self.runtime.runs.get(run_id)
        if run is None:
            return CommandResult(status=Outcome.RUN_NOT_FOUND, reason=f"no run {run_id}")

        if kind == "set_rate":
            envelope = self.runtime.set_rate(run_id, int(payload["rate"]))
            return CommandResult(
                status=Outcome.APPLIED,
                applied_tick=run.state.tick,
                produced_seq=[envelope.seq] if envelope else [],
            )

        from contracts import canonical

        try:
            envelopes = self.runtime.apply_command(
                run_id,
                getattr(kernel_pb2, proto_name),
                canonical.encode(payload),
                command_id,
            )
        except CommandRejected as rejected:
            # Mutates nothing, and says why. The reason is the sentence the client shows.
            return CommandResult(status=Outcome.REJECTED, reason=str(rejected))

        return CommandResult(
            status=Outcome.APPLIED,
            applied_tick=run.state.tick,
            produced_seq=[envelope.seq for envelope in envelopes],
        )

    # --- reads ------------------------------------------------------------

    def create_run(
        self,
        run_id: str,
        run_seed: int,
        horizon_tick: int | None = None,
        scenario: str | None = None,
    ) -> dict[str, Any]:
        """Create a run, then start its clock.

        `ensure_loop` is the second half and not optional: without it the run exists, holds a
        genesis event, and never advances — which is exactly the state every run was in before
        this, because nothing outside the test suite had ever called it.

        It must run on the event loop, since it creates a task. The gateway's create route is
        `async` for that reason while its command route is not.

        **A refused scenario is translated here, not caught at the gateway.** The protocol says a
        request the kernel cannot serve arrives as a `ValueError`, and honouring that is what
        keeps the gateway from importing `simcore` to name an exception type — the same reason it
        talks to a `KernelClient` at all. The loader's own sentence is carried through unchanged,
        because it is the one that lists the names that *do* resolve.
        """
        from simcore.scenario import ScenarioInvalid

        try:
            run = self.runtime.create_run(
                run_id, run_seed, horizon_tick=horizon_tick, scenario=scenario
            )
        except ScenarioInvalid as refused:
            raise ValueError(str(refused)) from refused

        if run.rate > 0:
            self.runtime.ensure_loop(run_id)

        status = self.run_status(run_id)
        assert status is not None, "a run just created has a row"
        return {
            **status,
            "run_seed": run_seed,
            "horizon_tick": run.state.horizon_tick,
            # Which company it is a run of. The client asked by name and gets the name back, so a
            # reload that re-attaches by run id can still say whose office it is looking at
            # without the genesis payload growing a field the store and the goldens would carry.
            "scenario": run.state.scenario.scenario_id,
        }

    def fork_run(
        self,
        parent_run_id: str,
        at_seq: int,
        option_index: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Take a past decision differently. The child arrives paused, and stays paused.

        No `ensure_loop` here, unlike `create_run`, and that is the design rather than an
        omission: a fork lands the player in a timeline they have not looked at yet, so it
        starts at rate zero and the first thing they do with it is a `set_rate` they chose.
        Starting the clock here would run the new timeline forward while the Universe stage was
        still animating into it.

        An unknown parent arrives as `KeyError`, which the gateway answers 404 with — the same
        shape `create_run` uses for a refused scenario, translated at this seam so the gateway
        holds no simulation knowledge.
        """
        return self.runtime.fork(
            parent_run_id,
            at_seq,
            option_index,
            idempotency_key,
        ).to_dict()

    def switch_run(
        self, from_run_id: str, to_run_id: str, rate: int | None = None
    ) -> dict[str, Any]:
        """Move the clock between two timelines of one lineage.

        Straight through, like `fork_run`: the ordering guarantee, the per-lineage lock and the
        refusals all belong to the runtime, and a translation layer here would be a second place
        for them to be almost right. An unknown run arrives as `KeyError` and travels untouched,
        because the route turns it into a 404.
        """
        return self.runtime.switch_to(from_run_id, to_run_id, rate).to_dict()

    def lineage(self, run_id: str) -> dict[str, Any] | None:
        """The tree of timelines this run belongs to.

        Straight through to the runtime, like `fork_run` and `switch_run`: the query belongs to
        `logschema` — it is a read over rows, the same shape the report's fold is — and the *overlay*
        of the live fold on top of it belongs to the component that holds the folds. A translation
        layer here would be the second place that rule lived.
        """
        return self.runtime.lineage_tree(run_id)

    def scenarios(self) -> list[dict[str, Any]]:
        """Every company a run could be created against, for the surface that offers the choice.

        **Each file is loaded rather than only listed**, because a name on its own is not an
        offer: the picker shows the title and the summary, and a scenario that would be refused
        at creation has to read as refused *here* rather than as a working choice that fails on
        the button. So the reason travels with the entry and the entry stays in the list —
        dropping it would leave an author who mistyped a key with a file that had silently
        vanished.

        One bad file therefore cannot hide the good ones, which is the property that matters on a
        directory anyone can drop a file into.
        """
        from simcore import scenario as sc

        catalogue: list[dict[str, Any]] = []
        for name in sc.available():
            try:
                company = sc.load(name)
            except sc.ScenarioInvalid as refused:
                catalogue.append({"id": name, "loadable": False, "refusal": str(refused)})
                continue
            catalogue.append(
                {
                    "id": company.scenario_id,
                    "title": company.title,
                    "summary": company.summary,
                    "people": len(company.people),
                    "items": len(company.items),
                    "loadable": True,
                }
            )
        return catalogue

    def run_status(self, run_id: str) -> dict[str, Any] | None:
        run = self.runtime.runs.get(run_id)
        if run is None:
            row = self.runtime.store.run_row(run_id)
            if row is None:
                return None
            return {
                "run_id": run_id,
                "tick": int(row["current_tick"]),
                "rate": int(row["rate"]),
                "terminal_reason": row["terminal_reason"] or "",
                "head_seq": int(row["head_seq"]),
                "active": False,
            }

        return {
            "run_id": run_id,
            "tick": run.state.tick,
            "rate": run.rate,
            "terminal_reason": run.state.terminal_reason,
            "head_seq": self.runtime.store.head_seq(run_id),
            "metrics": dict(run.state.metrics),
            "active": True,
        }

    def read_events(self, run_id: str, after_seq: int, limit: int | None = None) -> list:
        return self.runtime.store.read_events(run_id, after_seq=after_seq, limit=limit)

    def head_seq(self, run_id: str) -> int:
        return self.runtime.store.head_seq(run_id)

    def snapshot(self, run_id: str) -> dict[str, Any]:
        from simcore import step as sim

        run = self.runtime.runs[run_id]
        return sim.snapshot(run.state)

    # --- subscriptions ----------------------------------------------------

    def subscribe(self, run_id: str, connection: streaming.Connection) -> int:
        """Bridge the kernel's queue onto the gateway's bounded connection queue.

        Two queues rather than one, because they have different jobs: the kernel's is the
        publish fan-out, the gateway's is per-connection back-pressure with a drop policy. A
        single shared queue would make one slow client able to stall the tick loop's publish.
        """
        queue = self.runtime.subscribe(run_id)

        async def pump() -> None:
            while not connection.closed:
                item = await queue.get()
                frame = (
                    streaming.envelope_frame(item)
                    if hasattr(item, "kind") and hasattr(item, "seq")
                    else item
                )
                if not connection.offer(frame):
                    await connection.close()
                    return

        task = asyncio.create_task(pump(), name=f"ws-pump-{run_id}")
        token = id(connection)
        self._subscriptions[token] = (run_id, queue, task)
        return token

    def unsubscribe(self, run_id: str, token: int) -> None:
        entry = self._subscriptions.pop(token, None)
        if entry is None:
            return
        _, queue, task = entry
        self.runtime.unsubscribe(run_id, queue)
        task.cancel()


def refuse_a_store_this_build_cannot_read(store: LogStore) -> None:
    """Check the DDL version **before** anything creates or alters a table.

    `KernelRuntime.start` runs creation, then the lease, then the check, and each of those
    orderings had a reason — but the consequence was that a store at another version had
    `create_all` run against it before anything looked at the version row. On a DDL bump that
    adds a table, that half-migrates the store: the new table appears, the column that the
    same bump added to an existing table does not, and the refusal that follows leaves behind
    a schema that is neither version. So the check happens here, first, against a store this
    process has not touched.

    **A store with none of our tables is not a mismatch.** R16 requires `docker compose up`
    to provision an empty database with no manual step, so an empty (or foreign) database
    falls straight through and `create_all` writes the schema and the version row together.
    Anything carrying even one of our tables is checked, which is what catches the case
    `check_ddl_version` names first: a store with a log and no version row was not created by
    this kernel, and creating the row now would assert a version nobody verified.

    Raises `DdlVersionMismatch`, whose message already names both versions and the remedy.
    The remedy is a wipe: the bump is documented as one, because `create_all` can add a table
    but cannot add a column to `runs`, so carry-forward would be a real migration with no
    corpus of old stores to prove itself against — shipped untested on the one component
    whose failure is silent corruption.

    **What this narrows and does not close.** On a store at a *matching* version, `create_all`
    still runs before the lease is taken, and its Postgres path is `DROP TRIGGER IF EXISTS`
    followed by `CREATE TRIGGER` — so a second launcher that is about to be refused by the
    lease briefly drops the append-only guard on a log another kernel is appending to.
    Measured directly: the trigger's OID moves. Closing that needs creation to happen behind
    the lease, which is the ordering `KernelRuntime.start` explains it cannot have, because
    the lease lives in a table creation is what provides. It wants its own unit and a design;
    it is out of scope here, and this check at least means a store this build cannot read is
    never reached by that DDL at all.
    """
    from sqlalchemy import inspect

    from logschema import metadata

    present = set(inspect(store.engine).get_table_names())
    if not present & {table.name for table in metadata.sorted_tables}:
        return

    store.check_ddl_version()


def compose() -> tuple[KernelRuntime, InProcessKernel]:
    """Build the kernel runtime, hand the gateway an in-process client, mount the rest."""
    from gateway import main as gateway_main
    from kernel import main as kernel_main

    store = LogStore(make_engine(store_url()))
    refuse_a_store_this_build_cannot_read(store)

    runtime = KernelRuntime(store)
    client = InProcessKernel(runtime)
    gateway_main.use_kernel(client)
    _publish_model_spend(gateway_main)
    _publish_director_memory(gateway_main, runtime)
    _wire_the_bench(runtime)
    mount_surfaces(gateway_main.app)
    _wire_the_prescription()

    # After the surfaces are imported and before anything logs. Importing a service app
    # calls `svclog.configure` with that service's name, so claiming the label any earlier
    # would have it overwritten by the last import; claiming it any later would stamp the
    # startup lines — the lease acquisition among them — with a service that did not emit
    # them.
    svclog.configure(LOG_SERVICE)

    try:
        runtime.start()
    except DdlVersionMismatch:
        # Unreachable except in a race: the pre-flight above already read the version row,
        # and this fires only if something changed it between that read and the lease. The
        # release is here anyway, because the cost of being wrong is a store nobody can start
        # a kernel against until a thirty-second TTL expires — and the operator's next act
        # after reading the remedy is to try again.
        _release_the_lease(runtime)
        raise

    # After `start`, so what the kernel surface adopts is a runtime that holds the lease.
    # Before it there would be nothing wrong with the object, but `use_runtime` promises a
    # started one and a surface reporting on a runtime that never took the lease is the
    # failure it exists to prevent, one step removed.
    kernel_main.use_runtime(runtime)

    # The parsed target, never the DSN. This line used to carry `store_url()` whole, which in
    # compose is `postgresql+psycopg://companyos:companyos@postgres:5432/companyos` — a
    # password on stdout at every startup, and R6 names stdout explicitly. The redacting
    # filter in `servicekit.logging` would now catch it, and a leak that survives only
    # because a filter is watching is still a call site that should not be making it.
    target = parse_store_url(store_url())
    log.info(
        "one process composed",
        extra={
            "store": f"{target.backend} at {target.describe()}",
            "lease_owner": runtime.lease.owner,
            "surfaces": ",".join(["/"] + [prefix for prefix, _ in SURFACES]),
        },
    )
    return runtime, client


def _release_the_lease(runtime: KernelRuntime) -> None:
    """Give back a lease taken moments before a refusal, so a retry does not wait it out."""
    if runtime.lease is None:
        return
    try:
        with runtime.store.engine.begin() as connection:
            lease_module.release(connection, runtime.lease)
    except Exception as exc:  # noqa: BLE001 - the refusal is the news; this is a courtesy
        log.warning("could not release the lease while refusing", extra={"error": str(exc)})
    finally:
        runtime.lease = None


def _publish_model_spend(gateway_main: Any) -> None:
    """Point the gateway's stream at the agents surface's spend counter (M28).

    The two ends of this shipped separately and could not meet: the counter and its read are
    in the agents service, the client's reducer and its HUD tile are in the browser, and the
    stream between them belongs to the gateway — which may not import the agents service
    (R4). So the launcher hands the gateway a callable, exactly as it hands it a kernel
    client, and neither service learns the other exists.

    The gateway is built once and closed over rather than rebuilt per read: it holds the
    process's one ledger, whose engine is what makes the counter survive a restart, and
    re-reading the environment every two seconds per subscriber would buy nothing.
    """
    from agents import main as agents_main

    bench = agents_main.bench()
    gateway_main.use_spend(lambda run_id: bench.reading(run_id).to_payload())


def _publish_director_memory(gateway_main: Any, runtime: KernelRuntime) -> None:
    """Compose the CEO's memory surface out of the two halves that own it (U14).

    The fourth of these, and the first that needs *both* other components rather than one. A
    director's memory is the log read under a scope: the scope is derived in the kernel, because the
    kernel is what holds folded state (R23), and the read is in the agents service, because that is
    where the engine and the summariser live. Neither may import the other, and the gateway may
    import neither — so the composition is here, in the one file allowed to see all three.

    That shape is also the security property. The agents service has no route of its own for this,
    so there is no way to ask it for a memory without a scope the kernel derived; and the gateway
    holds a callable rather than a client, so it cannot ask for one it invented.

    Each failure keeps its own answer. An unknown run arrives as `KeyError` from `memory_scope` and
    travels untouched, because the route turns it into a 404. A person who is not a director is
    `None`, which is a different 404. A log the agents service could not read is a `RuntimeError`
    here rather than a `None`, so the route can answer 503: "we could not read it" and "they are not
    a director" are the two answers a panel must never confuse.
    """
    from agents import main as agents_main

    def read(run_id: str, director_id: str, with_summary: bool = True) -> dict[str, Any] | None:
        scoped = runtime.memory_scope(run_id, director_id)
        if scoped is None:
            return None
        authorized, as_of_tick = scoped
        memory = agents_main.read_memory(
            run_id,
            authorized=authorized,
            as_of_tick=as_of_tick,
            with_summary=with_summary,
        )
        if memory is None:
            raise RuntimeError(
                f"the log for run {run_id} could not be read, so {director_id}'s memory is "
                "unavailable; the agents service logged the reason"
            )
        return memory

    gateway_main.use_memory(read)


def _wire_the_bench(runtime: KernelRuntime) -> None:
    """Point the kernel's statement dispatch at the agents surface's producer (U10).

    The third of these, and the same shape as the other two for the same reason. The kernel raises a
    statement request inside `step()` and has to carry it to a director; the director lives in the
    agents service; and neither service may import the other (R4). So the launcher hands the kernel a
    callable, exactly as it hands the gateway a kernel client and a spend reader, and neither service
    learns the other exists.

    The direction the proto describes survives the collapse intact: the kernel opens the stream, so
    nothing in the agents service holds a kernel handle or reaches for a runtime. What was a
    bidirectional gRPC stream between two containers is a function call between two modules that
    still may not see each other.
    """
    from agents import main as agents_main

    runtime.use_statement_producer(agents_main.produce_statement)


def _wire_the_prescription() -> None:
    """Point the report's prose seam at the agents surface's producer (U21).

    The fifth of these, and the same shape as the other four for the same reason. The report
    selects an automation proposal from the scenario's authored catalog and computes its payback
    from the fold; the sentences over those figures are a provider call, and a provider call
    lives in the agents service. Neither service may import the other (R4), so the launcher
    hands one a callable and neither learns the other exists.

    **The direction is what makes M57 structural.** What crosses is a *packet* the report built —
    the proposal, its evidence and its figures — and what comes back is prose matched to a
    proposal id the report already made. There is no call the agents service can make that adds
    a proposal, because it is never given the catalog, the fold or the store read that would let
    it find one.

    Ordering is not load-bearing here — `report.main` is a module singleton, so the seam holds
    whichever side of `mount_surfaces` this runs — and it sits after the mount so the install is
    beside the thing that publishes the route it feeds.
    """
    from agents import main as agents_main
    from report import main as report_main

    report_main.use_prescriber(agents_main.write_prescription)


def mount_surfaces(app: Any) -> list[Any]:
    """Mount the four other service apps under their prefixes.

    **The mounts are reconciled, not appended.** `gateway_main.app` is a module-level
    singleton and `compose()` runs once per test as well as once per process, so a second
    call has to replace what the first mounted: two sub-apps on one prefix would mean the
    second's lifespan starting a second copy of everything the first's started, and a suite
    that reloads a service module would leave a stale app object serving the prefix while
    the module's own `app` had moved on.
    """
    import importlib

    from starlette.routing import Mount

    surfaces = {prefix: importlib.import_module(name).app for prefix, name in SURFACES}

    app.routes[:] = [
        route
        for route in app.routes
        if not (isinstance(route, Mount) and route.path in surfaces)
    ]
    for prefix, surface in surfaces.items():
        app.mount(prefix, surface)

    app.state.surfaces = list(surfaces.values())
    return app.state.surfaces


def _wrap_lifespan(app: Any, runtime: KernelRuntime) -> None:
    """Start the runtime's background work inside the app's existing lifespan.

    **Not `@app.on_event("startup")`, and the difference is silent.** `create_service_app`
    constructs every service app with an explicit `lifespan=`, and Starlette runs the
    `on_startup`/`on_shutdown` lists only under its *default* lifespan — so a handler
    registered with `on_event` after the fact is never called and never complains. What that
    cost here was the whole of `start_background`: no lease heartbeat, so the lease became
    reclaimable thirty seconds in; no `resume_all`, so a restart brought the process up holding
    the log and advancing nothing; and no `stop`, so the lease was left to expire rather than
    released. The symptom is a run that is frozen after a restart, which reads as a broken
    kernel and is a startup hook that never ran.

    Wrapping the context the app already has is what keeps `servicekit`'s own logging and
    teardown intact: the runtime starts after the service reports started, and stops before it
    reports stopped.

    **The mounted surfaces' lifespans are entered here too, and they have to be.** Starlette
    runs the lifespan of the *top-level* app only — a `Mount`ed sub-app's `lifespan=` is never
    invoked, silently, in the same way `on_event` was never invoked. What that would cost is
    the same shape of loss as before: the agents surface announces its spend ceiling in
    `on_start`, and an explicitly unlimited ceiling is meant to be the loudest line at
    startup; the kernel surface's store watch is what makes its status endpoint report
    reachability rather than "not probed yet"; and neither would have run. So each surface is
    entered on the stack in mount order and unwound in reverse, which is what a nested
    `async with` per surface would have given without the loop.
    """
    from contextlib import AsyncExitStack, asynccontextmanager

    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(scoped_app: Any) -> Any:
        surfaces = getattr(scoped_app.state, "surfaces", ())

        async with AsyncExitStack() as stack:
            await stack.enter_async_context(inner(scoped_app))
            for surface in surfaces:
                await stack.enter_async_context(surface.router.lifespan_context(surface))

            # Last, so a run is resumed and ticking only once every surface that might be
            # asked about it is answering.
            await runtime.start_background()
            try:
                yield
            finally:
                await runtime.stop()

    app.router.lifespan_context = lifespan


def main() -> None:
    import uvicorn
    from fastapi import FastAPI

    from gateway import main as gateway_main
    from servicekit.runtime import bind_host

    try:
        runtime, _ = compose()
    except (DdlVersionMismatch, lease_module.LeaseHeld) as refusal:
        # A sentence and a non-zero exit, not a traceback. Both of these carry the whole
        # answer already — which versions disagree and that the remedy is a wipe; who holds
        # the lease and when it becomes reclaimable — and a traceback in front of that
        # sentence buries the one line the operator needs under thirty they cannot act on.
        # `from None` is what suppresses the chained frames.
        #
        # The same shape `kernel.main.main` uses for its own fatal reasons, which is where
        # this came from rather than being invented here.
        print(refusal, file=sys.stderr)
        raise SystemExit(1) from None

    app: FastAPI = gateway_main.app
    _wrap_lifespan(app, runtime)

    # The same routes on the same port and path prefix whether this runs in the `backend`
    # container or on a laptop, so the client's proxy configuration is byte-identical either way.
    # The override exists for the one case byte-identity cannot cover: a compose stack already
    # holding 8800, which is exactly when a contributor reaches for the host invocation.
    port = int(os.environ.get("COMPANY_OS_GATEWAY_PORT", "8800"))
    uvicorn.run(app, host=bind_host(), port=port, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
