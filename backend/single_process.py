"""The launcher: the same application objects, one process (R16).

It composes the kernel runtime and the gateway app **in one process**, and that composition is
the whole file — it reimplements nothing, because anything it reimplemented is where drift would
start.

**This is no longer a second topology.** It was the mode a contributor used to work on the
simulation without Docker, while compose ran five backend containers that met over gRPC. The
`backend` container now runs this file, so there is one composition and two ways to invoke it:
`docker compose up`, and `uv run python single_process.py` for a contributor with no Docker. The
gRPC servicer that fronted the old split was deleted with it; the proto stays as the command-kind
vocabulary, which is what `COMMAND_KINDS` below reads.

**It lives outside both services on purpose.** R4 forbids a service from importing another
service's internals, and the gateway still must not import the kernel — the boundary is an
import rule, not a transport, and collapsing the deployment did not relax it. But *something* has
to compose them, and that something cannot be either of them. So it is here, at the top of the
backend tree, next to `pyproject.toml` — a launcher, not a service, and outside the directories
the import-boundary tests police.

**What a green run here does not cover**, when it is run on the default store: the Postgres-only
hazards — JSONB key ordering underneath the state hash, and the sequence and transaction-control
differences — because the default is SQLite. Those are covered by the same launcher under compose,
which points it at Postgres, and by the store suite's two dialects. The value of the SQLite default
is that the kernel, determinism, parity and replay suites run fast and need no Docker.

    uv run python single_process.py            # SQLite at var/company-os.sqlite3
    COMPANY_OS_STORE_URL=postgresql+psycopg://... uv run python single_process.py
    COMPANY_OS_GATEWAY_PORT=8810 uv run python single_process.py   # a compose stack holds 8800
"""

from __future__ import annotations

import asyncio
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
from kernel.loop import KernelRuntime  # noqa: E402
from kernel.store import LogStore, make_engine  # noqa: E402
from servicekit import logging as svclog  # noqa: E402
from servicekit.probes import store_url  # noqa: E402

log = svclog.get_logger("single-process")

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
        self, run_id: str, run_seed: int, horizon_tick: int | None = None
    ) -> dict[str, Any]:
        """Create a run, then start its clock.

        `ensure_loop` is the second half and not optional: without it the run exists, holds a
        genesis event, and never advances — which is exactly the state every run was in before
        this, because nothing outside the test suite had ever called it.

        It must run on the event loop, since it creates a task. The gateway's create route is
        `async` for that reason while its command route is not.
        """
        run = self.runtime.create_run(run_id, run_seed, horizon_tick=horizon_tick)
        if run.rate > 0:
            self.runtime.ensure_loop(run_id)

        status = self.run_status(run_id)
        assert status is not None, "a run just created has a row"
        return {**status, "run_seed": run_seed, "horizon_tick": run.state.horizon_tick}

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


def compose() -> tuple[KernelRuntime, InProcessKernel]:
    """Build the kernel runtime and hand the gateway an in-process client."""
    from gateway import main as gateway_main

    store = LogStore(make_engine(store_url()))
    runtime = KernelRuntime(store)
    runtime.start()

    client = InProcessKernel(runtime)
    gateway_main.use_kernel(client)

    log.info(
        "single-process mode composed",
        extra={"store": store_url(), "lease_owner": runtime.lease.owner},
    )
    return runtime, client


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
    """
    from contextlib import asynccontextmanager

    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(scoped_app: Any) -> Any:
        async with inner(scoped_app):
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

    runtime, _ = compose()

    app: FastAPI = gateway_main.app
    _wrap_lifespan(app, runtime)

    # The same routes on the same port and path prefix whether this runs in the `backend`
    # container or on a laptop, so the client's proxy configuration is byte-identical either way.
    # The override exists for the one case byte-identity cannot cover: a compose stack already
    # holding 8800, which is exactly when a contributor reaches for the host invocation.
    import os

    port = int(os.environ.get("COMPANY_OS_GATEWAY_PORT", "8800"))
    uvicorn.run(app, host=bind_host(), port=port, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
