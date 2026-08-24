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

**Loopback is not a boundary against a browser (R26).** Any page the operator has open can
open a WebSocket to `ws://127.0.0.1:8800/ws/<run>`, and the handshake is exempt from every
cross-origin rule the browser applies to `fetch` — no preflight, no `Access-Control-*`, the
socket simply connects. That mattered less when this port carried a tick stream and nothing
else; it now fronts the report, the diagnose call and the domain surface in one process. So the
stream checks the request origin itself, and the app declares an explicit cross-origin policy
for the REST half, which is the other thing R26 asks for.
"""

from __future__ import annotations

import math
import os
import secrets
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from gateway import stream as streaming
from gateway.commands import CommandLedger, CommandResult, KernelClient, Outcome, submit
from servicekit import logging as svclog
from servicekit.app import create_service_app
from servicekit.probes import probe_http
from servicekit.runtime import peer_url, serve
from servicekit.status import Dependency

log = svclog.get_logger("gateway")

SERVICE = "gateway"

#: Origins allowed in addition to the client's own, comma-separated. Empty in every
#: shipped configuration: nginx and the vite dev server both make the client
#: same-origin with this port, so a value here is somebody's deliberate second front
#: end rather than something the product needs.
ENV_ALLOWED_ORIGINS = "COMPANY_OS_ALLOWED_ORIGINS"

#: Installed by the launcher. None means nothing composed this app, which is a
#: misconfiguration rather than a topology.
_kernel: KernelClient | None = None
_ledger = CommandLedger()

#: What this run has spent on model calls, as the HUD's frame reads it. Installed by
#: the launcher for the same reason `_kernel` is: the counter lives in the agents
#: service and R4 forbids the gateway importing it, so the launcher — the one
#: component allowed to see both — hands the stream a reader.
SpendReader = Callable[[str], dict[str, Any]]
_spend: SpendReader | None = None


def use_kernel(client: KernelClient) -> None:
    """Install a kernel client. Called by the launcher (R16), in every topology."""
    global _kernel
    _kernel = client


def use_spend(reader: SpendReader) -> None:
    """Install the spend reader the stream publishes `MODEL_SPEND` from.

    Optional, and absent is a working state rather than a fault: with no reader the
    stream publishes no spend frame and the HUD tile shows its zero-and-absent state,
    which is also what a keyless run shows for its whole life.
    """
    global _spend
    _spend = reader


# =========================================================================
# Request origin (R26)
# =========================================================================


def allowed_origins() -> list[str]:
    raw = os.environ.get(ENV_ALLOWED_ORIGINS, "")
    return [entry.strip() for entry in raw.split(",") if entry.strip()]


def _authority(origin: str) -> str:
    """An origin reduced to the part two origins have to agree on to be one origin."""
    parts = urlsplit(origin)
    return parts.netloc.lower()


def origin_is_our_own(origin: str | None, host_header: str | None) -> bool:
    """Whether a handshake came from the page this port serves.

    **"Its own" is the `Host` header, not a configured hostname.** The client reaches
    this port under three different authorities — `127.0.0.1:8800` directly,
    `127.0.0.1:8790` through nginx, `127.0.0.1:5173` through the dev server — and every
    one of them is legitimately the client's own origin. Pinning a list would mean
    editing the backend to change a published port. Comparing against `Host` needs no
    list, because the browser computes both values from the same address bar: a page at
    `http://127.0.0.1:8790` sends `Origin: http://127.0.0.1:8790` and `Host:
    127.0.0.1:8790`, and `frontend/nginx.conf` forwards the client's `Host` unchanged so
    that stays true through the proxy. That forward is `$http_host` and not `$host`, and
    the distinction is load-bearing rather than stylistic: `$host` is the Host header
    **with the port stripped**, so it would present `127.0.0.1` against an origin of
    `127.0.0.1:8790` and refuse the client this check exists to admit.

    **The authority is compared, not the scheme.** nginx does not send
    `X-Forwarded-Proto`, so this process cannot tell `http` from `https` upstream, and a
    scheme comparison would reject every request behind a TLS terminator. The dev server
    rewrites the origin to its own target, which it spells `ws://` while the same address
    reached directly is spelled `http://`. What the check is for is a *foreign* origin —
    `https://evil.example` reaching loopback — and that differs in the authority every
    time.

    **No `Origin` header at all is allowed.** A browser always sends one on a WebSocket
    handshake, so its absence means the caller is curl, `websocat`, the test client or
    the CLI — none of which a hostile page can drive, and all of which would otherwise
    need a header they have no reason to send. Refusing them would harden nothing and
    would break every non-browser consumer of the stream.
    """
    if not origin:
        return True
    if origin in allowed_origins():
        return True
    return bool(host_header) and _authority(origin) == host_header.lower()


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

# R26's second half: the policy is declared rather than defaulted. Whatever the operator
# named, and nothing else — a JSON `POST /runs/{id}/commands` from another origin is
# preflighted, the preflight goes unanswered, and the browser refuses it. Installed even
# when the list is empty, because the requirement is that the application *states* its
# cross-origin position: a missing middleware and a deny-everything middleware behave the
# same and read very differently.
#
# It carries no same-origin case because it does not need one: a same-origin request has no
# `Origin` header the middleware acts on, and the browser never preflights it. And CORS is
# irrelevant to the WebSocket above — Starlette's middleware passes any non-HTTP scope
# straight through — which is exactly why R26 asks for the handshake check as well.
#
# Read once, here, because middleware is constructed once. The stream's own check reads the
# same variable per handshake, so the two can only disagree inside a test that changes the
# environment after import.
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["content-type"],
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

    **`scenario` is a name and the body is the only way to send one** (M13). There is no
    `?path=`, no upload and no default that reads an environment variable: the kernel resolves
    the name inside the scenarios directory and refuses anything that could leave it before
    touching the filesystem (R9). Omitting it is how you ask for the shipped company, which is
    what every client that predates the choice keeps doing.
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

    # Empty and absent mean the same thing — the shipped company — because a form that posts its
    # untouched field would otherwise ask for a scenario named "" and be told it does not exist.
    scenario = str(payload.get("scenario") or "").strip() or None

    try:
        created = client.create_run(
            run_id,
            int(seed),
            horizon_tick=None if horizon is None else int(horizon),
            scenario=scenario,
        )
    except (TypeError, ValueError) as bad:
        raise HTTPException(status_code=400, detail=f"could not create the run: {bad}") from bad

    log.info(
        "run created",
        extra={"run": run_id, "seed": int(seed), "scenario": created.get("scenario", "")},
    )
    return {**created, "created": True}


#: The widest value the store's sequence and tick columns hold — `BigInteger` on Postgres,
#: `INTEGER` on SQLite, both signed 64-bit. A number above it is refused here rather than
#: reaching a bind parameter, where psycopg and pysqlite each raise their own `OverflowError`
#: from inside the driver and it surfaces as a 500 on a request the client can fix.
SIGNED_64_BIT_MAX = 2**63 - 1


def _whole_number(payload: dict[str, Any], key: str) -> int:
    """One required non-negative integer off a client-supplied body, or a 400 saying why.

    **`int()` alone is three bugs, and two of them are silent.** `int(8.9)` is 8, so a
    non-integral JSON number forks a *different decision* than the caller named with no error at
    all — the worst of the three, because nothing about the response says it happened. `int(1e999)`
    raises `OverflowError` on the infinity JSON decodes that to, and `int(10**40)` succeeds and
    then raises `OverflowError` inside the database driver; neither is a `TypeError` or a
    `ValueError`, so the guard that named those two caught neither and both reached the client as
    a 500 from a route whose docstring promises a 400.

    Modelled on `apply_command`'s `whole()`, which draws the same distinction for the same reason
    one layer down. This one additionally refuses a bool — `isinstance(True, int)` is true in
    Python, and `{"at_seq": true}` is a client mistake worth naming rather than forking sequence 1.
    """
    if key not in payload:
        raise HTTPException(
            status_code=400,
            detail=f"a fork needs {key!r}: which decision, and which option instead",
        )

    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HTTPException(
            status_code=400,
            detail=f"{key!r} is {value!r}, which is not a whole number",
        )
    if isinstance(value, float):
        # Finiteness first, and not merely for tidiness: `1e999` decodes to `inf`, and `int(inf)`
        # raises `OverflowError` — so a wholeness check written as `value != int(value)` raises
        # from inside the guard that exists to prevent exactly that.
        if not math.isfinite(value):
            raise HTTPException(
                status_code=400,
                detail=f"{key!r} is {value!r}, which is not a finite number",
            )
        if value != int(value):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{key!r} is {value!r}, which is not whole. Refusing rather than truncating: "
                    "rounding it would name a different "
                    f"{'decision' if key == 'at_seq' else 'option'} than you asked for, and "
                    "nothing in the answer would say so."
                ),
            )

    whole = int(value)
    if not 0 <= whole <= SIGNED_64_BIT_MAX:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{key!r} is {whole}, outside the range the store holds "
                f"(0 to {SIGNED_64_BIT_MAX})."
            ),
        )
    return whole


@app.post("/runs/{run_id}/fork")
def post_fork(run_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Take a past decision differently, and get a timeline back for it (M44).

    **Its own verb rather than a command kind**, and `post_run` above already argues it: a fork
    creates a run, and a command must never bring a simulation into being. It is also the only
    thing the client can do to a run that has *ended* — going back from a finished timeline is
    the point — and `POST /runs/{id}/commands` refuses a terminated run for a reason that is
    correct about commands and wrong about this.

    **Synchronous, like the command route.** Folding the parent's prefix is bounded pure-Python
    work and the write blocks on the store, so FastAPI's threadpool is where it belongs; the
    creation route above is `async` only because starting a clock needs the loop, and a child
    arrives paused.

    A refusal is a 200 with a `refusal` sentence, for the reason a rejected command is: the
    request was well-formed and the answer is "no", which the client renders. An unknown parent
    is a 404, and a missing or non-integer field is a 400 — those are the caller's mistakes.
    """
    payload = body or {}
    client = kernel()

    at_seq = _whole_number(payload, "at_seq")
    option_index = _whole_number(payload, "option_index")

    try:
        forked = client.fork_run(
            run_id,
            at_seq,
            option_index,
            str(payload.get("idempotency_key", "")),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no run {run_id}") from None

    if forked.get("refusal"):
        log.info(
            "fork refused",
            extra={"run": run_id, "at_seq": at_seq, "reason": forked["refusal"]},
        )
    else:
        log.info(
            "run forked",
            extra={
                "run": run_id,
                "child": forked.get("child_run_id", ""),
                "at_seq": at_seq,
                # `minted` rather than `created`, and the rename is not taste: `created` is a
                # `LogRecord` attribute — the record's own timestamp — and stdlib logging raises
                # `KeyError: Attempt to overwrite 'created' in LogRecord` rather than shadowing
                # it. Inside a route that is a 500 on a fork that already committed.
                "minted": forked.get("created", False),
            },
        )
    return forked


@app.get("/scenarios")
def get_scenarios() -> dict[str, Any]:
    """The companies a run can be created against.

    What makes M13 true from the client rather than only from the loader: a second file in the
    scenarios directory has to be *reachable*, and a picker cannot offer what it cannot list.

    A file that will not load is listed with its refusal rather than dropped, so an author who
    mistyped a key sees the reason here instead of a file that silently vanished.
    """
    return {"scenarios": kernel().scenarios()}


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
    origin = socket.headers.get("origin")
    if not origin_is_our_own(origin, socket.headers.get("host")):
        # Refused before `accept`, so the handshake fails rather than a connected socket
        # being closed: a page that never completes the upgrade cannot read a frame, and
        # the browser reports it as a failed connection instead of a server error.
        log.warning(
            "refusing a stream connection from another origin",
            extra={"run": run_id, "origin": origin, "host": socket.headers.get("host")},
        )
        await socket.close(code=1008, reason="origin not allowed")
        return

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

    # M28: the HUD's one measured figure has to move *during* a run. It cannot ride on an
    # event — what a call cost depends on which provider answered, so an event carrying it
    # would be an output the fold cannot reproduce and strict replay would fail on every run
    # that used the bench — so it is a control frame on this stream, beside `POSITION_ECHO`,
    # which is here for the same reason.
    spender = (
        asyncio.create_task(
            streaming.publish_spend(
                connection, run_id, _spend, streaming.SPEND_POLL_SECONDS
            ),
            name=f"ws-spend-{run_id}",
        )
        if _spend is not None
        else None
    )

    try:
        while not connection.closed:
            # The receive side exists to notice the client going away. Anything it sends is
            # ignored: commands go over REST, where they can be answered.
            await socket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if spender is not None:
            spender.cancel()
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
