"""Gateway service entrypoint: the client's only contact surface.

The kernel is a *non-required* dependency, and that is a decision. The gateway is the surface a
contributor already has open when something goes wrong, so it has to stay up and say "the kernel
is unreachable". A gateway that failed its own health alongside the kernel would turn one outage
into two and explain neither.

**The gateway never imports the kernel.** R4: services do not reach into each other. It talks to
a `KernelClient`, and there is one implementation — the in-process one the launcher installs. There
were two, and the other was a gRPC channel across the compose network; the five backend containers
became one, so the channel went with them. The boundary did not: it is an import rule, policed by
`tests/test_import_boundaries.py`, and it holds inside one process exactly as it held across two.
The composition lives in `backend/single_process.py`, outside both services, because composing them
is neither service's job.

Exposure follows R34: bind every interface inside the container, publish only on host loopback.
"""

from __future__ import annotations

import secrets
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect

from gateway import stream as streaming
from gateway.commands import CommandLedger, CommandResult, KernelClient, Outcome, submit
from servicekit import logging as svclog
from servicekit.app import create_service_app
from servicekit.probes import probe_http
from servicekit.runtime import peer_url, serve
from servicekit.status import Dependency

log = svclog.get_logger("gateway")

SERVICE = "gateway"

#: Installed by the launcher. None means nothing composed this app, which is a
#: misconfiguration rather than a topology.
_kernel: KernelClient | None = None
_ledger = CommandLedger()


def use_kernel(client: KernelClient) -> None:
    """Install a kernel client. Called by the launcher (R16), in every topology."""
    global _kernel
    _kernel = client


def kernel() -> KernelClient:
    if _kernel is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "no kernel client is configured. This app is served by "
                "`backend/single_process.py`, which installs one; running "
                "`python -m gateway.main` directly starts the routes without a kernel behind "
                "them, which is useful for reading /status and for nothing else."
            ),
        )
    return _kernel


def _probe_kernel() -> tuple[bool, str]:
    if _kernel is not None:
        return True, "in-process kernel"
    # No client installed. The peer probe is what a bare `python -m gateway.main` reports, and
    # it is honest there: it says nothing is answering rather than claiming a healthy kernel.
    return probe_http(peer_url("kernel"))


app = create_service_app(
    SERVICE,
    dependencies=[
        Dependency(
            name="kernel",
            probe=_probe_kernel,
            required=False,
            note="reported, not required: the gateway stays up to explain a kernel outage",
        )
    ],
)


# =========================================================================
# Runs
# =========================================================================


@app.post("/runs")
async def post_run(body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create a run and start its clock.

    **Its own route rather than a command kind.** `POST /runs/{id}/commands` answers not-found for
    an unknown run deliberately — a command must never bring a simulation into being, or a typo'd
    id in a client would silently start one. So creation is a different verb on a different path,
    and the run id is its own idempotency key: creating an id that already exists returns that run
    rather than a second one or an error, which is what a client retrying a request whose response
    it never saw needs.

    **`async` on purpose.** Starting the run's clock creates an asyncio task, which needs a running
    loop — the sibling command route is synchronous so FastAPI's threadpool can absorb a blocking
    kernel call, and this one cannot be.

    The seed is echoed back because it is the run's identity for reproduction: two fresh runs from
    one seed produce identical logs (R11), and a caller that let the server mint one would otherwise
    have no way to ask for that run again.
    """
    payload = body or {}
    client = kernel()

    run_id = str(payload.get("run_id") or "").strip()
    if run_id == "":
        run_id = f"run-{uuid.uuid4().hex[:12]}"

    existing = client.run_status(run_id)
    if existing is not None:
        # Idempotent on the id. Reporting `created: false` rather than 409 keeps a retry from
        # looking like a failure while still telling an honest client it did not make this run.
        return {**existing, "created": False}

    seed = payload.get("run_seed")
    if seed is None:
        # 63 bits, so it stays a positive value that survives every integer path it crosses.
        seed = secrets.randbits(63)

    horizon = payload.get("horizon_tick")

    try:
        created = client.create_run(
            run_id,
            int(seed),
            horizon_tick=None if horizon is None else int(horizon),
        )
    except (TypeError, ValueError) as bad:
        raise HTTPException(status_code=400, detail=f"could not create the run: {bad}") from bad

    log.info("run created", extra={"run": run_id, "seed": int(seed)})
    return {**created, "created": True}


# =========================================================================
# Commands
# =========================================================================


@app.post("/runs/{run_id}/commands")
def post_command(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Apply a command at a tick boundary and report the outcome.

    Synchronous on purpose: FastAPI runs it in its threadpool, so awaiting the kernel does not
    stall the event loop that the WebSocket senders share.

    The response reports the outcome of *applying*, not of accepting. A rejection is a 200 with
    a reason rather than an error status — the request was well-formed and the answer is "no",
    which is information the client renders rather than an exception it handles.
    """
    kind = body.get("kind", "")
    if not kind:
        raise HTTPException(status_code=400, detail="a command needs a kind")

    # Gateway-minted, and carried onto every event this command produces. The causation trace
    # that makes one identifier greppable across seven service streams.
    command_id = body.get("command_id") or f"cmd-{uuid.uuid4().hex[:12]}"

    result = submit(
        kernel=kernel(),
        ledger=_ledger,
        run_id=run_id,
        kind=kind,
        payload=body.get("payload", {}),
        idempotency_key=body.get("idempotency_key", ""),
        command_id=command_id,
    )

    if result.status == Outcome.RUN_NOT_FOUND:
        raise HTTPException(status_code=404, detail=result.reason)

    return {**result.to_dict(), "command_id": command_id}


@app.get("/runs/{run_id}/commands/{idempotency_key}")
def get_command_outcome(run_id: str, idempotency_key: str) -> dict[str, Any]:
    """R30: a reconnecting client resolves an in-flight command instead of retrying blind."""
    recorded = _ledger.recorded(run_id, idempotency_key)
    if recorded is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no command recorded under {idempotency_key!r} for run {run_id}. It was never "
                "applied, so submitting it again is safe."
            ),
        )
    return recorded.to_dict()


# =========================================================================
# The event stream
# =========================================================================


@app.websocket("/ws/{run_id}")
async def events(socket: WebSocket, run_id: str, after_seq: int = Query(0)) -> None:
    """Stream events, resuming from `after_seq`.

    Resume is keyed on run *and* sequence: a fork shares sequence values with its parent, so a
    bare sequence names two different events once one exists.
    """
    await socket.accept()

    if _kernel is None:
        await socket.send_json({"kind": "ERROR", "detail": "no kernel client configured"})
        await socket.close()
        return

    connection = streaming.Connection(run_id)

    try:
        plan = streaming.plan_resume(_kernel, run_id, after_seq)
    except streaming.ResyncRequired as mismatch:
        await socket.send_json({"kind": "RESYNC_REQUIRED", "detail": str(mismatch)})
        await socket.close()
        return

    if plan.resync:
        # Too far behind to catch up by replay. A snapshot plus its sequence, and the client
        # hard-resets — replaying a month of history it cannot use would be slower and would
        # leave it behind again by the time it finished.
        await socket.send_json(
            {
                "kind": "RESYNC",
                "head_seq": str(plan.head_seq),
                "state": _kernel.snapshot(run_id),
            }
        )
    else:
        for envelope in plan.backlog:
            await socket.send_json(streaming.envelope_frame(envelope))
        connection.last_sent_seq = plan.head_seq

    import asyncio

    # One sender task. The tick loop pushes unsolicited frames while this handler awaits
    # receive; one coroutine doing both would deadlock the two directions.
    connection.sender = asyncio.create_task(
        connection.run_sender(socket.send_json), name=f"ws-sender-{run_id}"
    )
    subscription = _kernel.subscribe(run_id, connection)

    try:
        while not connection.closed:
            # The receive side exists to notice the client going away. Anything it sends is
            # ignored: commands go over REST, where they can be answered.
            await socket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _kernel.unsubscribe(run_id, subscription)
        await connection.close()


# =========================================================================
# Reads
# =========================================================================


@app.get("/runs/{run_id}/state")
def get_state(run_id: str) -> dict[str, Any]:
    status = kernel().run_status(run_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"no run {run_id}")
    return status


if __name__ == "__main__":
    serve(SERVICE)
