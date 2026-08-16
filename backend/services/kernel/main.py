"""Kernel service entrypoint: the clock, the lease, and the only writer.

Startup has two phases, and the split matters.

**An unreachable store is a transient.** The kernel retries and reports itself unhealthy while
it waits. Exiting would make an outage look like a crash, and the capped restart policy would
then hide the outage inside a restart loop.

**A DDL mismatch or a held lease is not.** Once the store *is* reachable, appending to a schema
this kernel does not understand, or appending alongside a second kernel, are both worse than not
running. The service records the fatal reason, reports it on the status endpoint, and — when run
as a service rather than imported by a test — exits non-zero naming the holder (R26).

*The tick task is held by a strong reference and cancelled explicitly.* The event loop keeps only
weak references to tasks, so a dropped handle can be collected mid-execution — and the failure
mode is not an exception, it is the simulation silently stopping.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from typing import Any

from fastapi import FastAPI, HTTPException

from servicekit import logging as svclog
from servicekit.app import create_service_app
from servicekit.probes import probe_store, store_url
from servicekit.runtime import serve
from servicekit.status import Dependency

log = svclog.get_logger("kernel")

_RETRY_SECONDS = 2.0
SERVICE = "kernel"


class StoreWatch:
    """Keeps probing the store, forever, and remembers what it last saw.

    Only transitions are logged. Probing every two seconds and logging each attempt would fill
    the stream with the fact that nothing changed.
    """

    def __init__(self, interval: float = _RETRY_SECONDS) -> None:
        self.interval = interval
        self.attempts = 0
        self.reachable: bool | None = None
        self.detail: str = "not probed yet"

    async def observe(self) -> None:
        reachable, detail = await asyncio.to_thread(probe_store)
        self.attempts += 1

        if reachable != self.reachable:
            if reachable:
                log.info("store reachable", extra={"attempts": self.attempts, "probe": detail})
            else:
                log.warning(
                    "store unreachable; retrying rather than exiting",
                    extra={"probe": detail, "retry_interval_s": self.interval},
                )
        self.reachable = reachable
        self.detail = detail

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            await self.observe()

    def probe(self) -> tuple[bool, str]:
        if self.reachable is None:
            return False, self.detail
        suffix = f" (retry attempts: {self.attempts})" if not self.reachable else ""
        return self.reachable, self.detail + suffix


_watch = StoreWatch()

#: Set when startup hit something that is not a transient. Reported, not swallowed.
_fatal: str = ""

#: The runtime, once the store let us in.
_runtime: Any = None

#: True when the runtime was handed to us by the launcher rather than started here. It
#: changes exactly one thing — who stops it — and getting that wrong is not visible in a
#: status payload, which is why it is a flag rather than an inference.
_adopted: bool = False


def use_runtime(runtime: Any) -> None:
    """Adopt a runtime the launcher already started (R28).

    The mirror of the gateway's `use_kernel`, and it exists for the same reason. In one
    process there is one `KernelRuntime`, because there is one writer lease: this app
    starting a second one would take the lease against itself, fail, and report the
    launcher's own runtime as a fatal `LeaseHeld` — a healthy system describing itself
    as broken.

    Ownership does not transfer with it. The launcher started it and the launcher stops
    it, so `teardown` below leaves an adopted runtime alone; stopping it here would cancel
    the tick loops while the gateway on the same port was still answering commands.
    """
    global _runtime, _adopted
    _runtime, _adopted = runtime, True


def _store_ddl_version() -> dict[str, Any]:
    """What this build expects. What was *found* is reported under `store`."""
    try:
        from logschema import DDL_VERSION
    except Exception:
        return {"store_ddl": None}
    return {"store_ddl": str(DDL_VERSION)}


def _clock_probe() -> tuple[bool, str]:
    """Readiness reflects tick-task liveness (R29).

    A kernel reporting healthy with a stopped clock is worse than one that is down, because
    nothing prompts anyone to look.
    """
    if _fatal:
        return False, _fatal
    if _runtime is None:
        return False, "the runtime has not started; the store was not reachable yet"
    return _runtime.healthy()


async def _try_start_runtime() -> None:
    """Take the lease once the store is reachable. Fatal failures are recorded, not hidden."""
    global _runtime, _fatal

    from kernel.lease import LeaseHeld
    from kernel.loop import KernelRuntime
    from kernel.store import DdlVersionMismatch, LogStore, make_engine

    runtime = KernelRuntime(LogStore(make_engine(store_url())))

    try:
        await asyncio.to_thread(runtime.start)
    except DdlVersionMismatch as mismatch:
        _fatal = str(mismatch)
        log.error("refusing to start", extra={"reason": _fatal})
        return
    except LeaseHeld as held:
        _fatal = str(held)
        log.error("refusing to start", extra={"reason": _fatal, "holder": held.owner})
        return
    except Exception as exc:  # noqa: BLE001 - a transient; the watch keeps retrying
        log.warning("store not ready for the lease yet", extra={"error": str(exc)})
        return

    await runtime.start_background()
    _runtime = runtime
    log.info("kernel runtime started; this process is the sole writer")


async def _on_start(app: FastAPI):
    await _watch.observe()

    # `_runtime is None` rather than an unconditional attempt: under the launcher one is
    # already installed, and starting a second would collide with it at the lease.
    if _runtime is None and _watch.reachable:
        await _try_start_runtime()

    watch_task = asyncio.create_task(_watch.run(), name="kernel-store-watch")
    app.state.store_watch_task = watch_task  # strong reference

    async def _await_store() -> None:
        """Keep trying to take the lease until the store lets us, or a fatal reason stops us."""
        while _runtime is None and not _fatal:
            await asyncio.sleep(_RETRY_SECONDS)
            if _watch.reachable:
                await _try_start_runtime()

    starter = asyncio.create_task(_await_store(), name="kernel-runtime-starter")
    app.state.runtime_starter = starter

    async def teardown() -> None:
        for task in (watch_task, starter):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if _runtime is not None and not _adopted:
            await _runtime.stop()

    return teardown


app = create_service_app(
    SERVICE,
    dependencies=[
        Dependency(
            name="store",
            probe=_watch.probe,
            required=True,
            note="the kernel cannot append without it, so unreachable means unhealthy",
        ),
        Dependency(
            name="clock",
            probe=_clock_probe,
            required=True,
            note="a kernel reporting healthy with a stopped clock is worse than one that is down",
        ),
    ],
    version_extra=_store_ddl_version,
    reports_store=True,
    on_start=_on_start,
)


@app.get("/runs/{run_id}/diagnose")
def diagnose(run_id: str) -> dict[str, Any]:
    """Why did the clock stop? Answered without anyone reading a log.

    Synchronous, so FastAPI runs it in its threadpool and the gathering never touches the
    event loop the tick task shares.
    """
    if _runtime is None:
        raise HTTPException(status_code=503, detail=_fatal or "the runtime has not started")
    if run_id not in _runtime.runs:
        raise HTTPException(status_code=404, detail=f"no active run {run_id}")
    return _runtime.diagnose(run_id).to_dict()


def main() -> None:
    """Run as a service. A fatal startup reason exits non-zero rather than serving."""
    if os.environ.get("COMPANY_OS_KERNEL_EXIT_ON_FATAL", "1") == "1" and _fatal:
        print(_fatal, file=sys.stderr)
        raise SystemExit(1)
    serve(SERVICE)


if __name__ == "__main__":
    main()
