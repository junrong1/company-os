"""Report service entrypoint.

The store is required: a report service that cannot read the log has nothing to say. It
connects with the read-only role that compose provisions, because a credential outlives every
future code path a test did not anticipate.

It reads and never writes. Two things enforce that rather than one: the Postgres role has
SELECT only, and `tests/test_lifecycle.py` asserts that no module in this service mentions a
write operation. The role is the one that holds when someone later hands this service a
session that could write.

**The role is now the only one of the two that is structural.** This app is mounted by the
launcher into the process that also holds the writer's engine (R28), so "the report is a
different container" has stopped being part of the argument. What is left is the credential:
a SQLAlchemy engine is per-DSN rather than per-process, so a connection opened against
`COMPANY_OS_REPORT_STORE_URL` cannot append however the code around it changes. That is why
this module resolves its own name rather than calling `store_url()` bare — which, in one
process, would have handed the fold the owner's connection and quietly retired the boundary.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from report import fold as reporting
from servicekit import logging as svclog
from servicekit.app import create_service_app
from servicekit.probes import ENV_REPORT_STORE_URL, probe_store, store_url
from servicekit.runtime import serve
from servicekit.status import Dependency

SERVICE = "report"

log = svclog.get_logger(SERVICE)


def reader_url() -> str:
    """The read-only DSN, falling back to the writer's where there are no roles.

    Compose sets both. A laptop run sets neither and gets a SQLite file, where a
    read-only role is not a thing that exists — so the fallback is what keeps the
    contributor path working rather than a second variable to remember.
    """
    return store_url(ENV_REPORT_STORE_URL)


def _probe_reader() -> tuple[bool, str]:
    """Probe the credential this service actually uses, not the writer's."""
    return probe_store(reader_url())


app = create_service_app(
    SERVICE,
    dependencies=[
        Dependency(
            name="store",
            probe=_probe_reader,
            required=True,
            note="read-only; the report folds the log and never appends to it",
        )
    ],
    reports_store=True,
    store_env_var=ENV_REPORT_STORE_URL,
)


def _read_log(run_id: str) -> tuple[list, int]:
    """Read a run's events and its current tick.

    Reads rows described by `logschema`, the shared table definitions, with the read-only
    credential compose provisions. It deliberately does *not* import `kernel.store`: that
    module owns the write path, and R4 forbids a service from importing another service's
    internals. The import-boundary test caught exactly that when this function first reached
    for `LogStore`.

    Imported lazily so the module stays importable — and the status endpoint stays answerable
    — when the store is unreachable. A report service that could not start because the store
    was down would be unable to say that the store was down.
    """
    from sqlalchemy import create_engine, select

    from contracts.envelope import Envelope
    from logschema import event_log, runs

    engine = create_engine(reader_url(), future=True)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                select(event_log)
                .where(event_log.c.run_id == run_id)
                .order_by(event_log.c.seq)
            ).mappings().all()
            run = connection.execute(
                select(runs.c.current_tick).where(runs.c.run_id == run_id)
            ).first()

        # Validated on read as well as before append: a non-canonical payload that somehow
        # reached the store is caught here rather than folded.
        events = [Envelope.from_dict(dict(row)) for row in rows]
        return events, int(run.current_tick) if run else 0
    finally:
        engine.dispose()


@app.get("/runs/{run_id}/report")
def run_report(run_id: str) -> dict[str, Any]:
    """The run report: what happened, and which event says so.

    Synchronous on purpose. A fold over a whole run is real work, and FastAPI runs a
    synchronous endpoint in its threadpool — so the blocking happens off the event loop
    rather than stalling every other request for the duration.
    """
    try:
        events, through_tick = _read_log(run_id)
    except Exception as exc:  # noqa: BLE001 - surfaced as a 503, not a stack trace
        log.warning("could not read the log", extra={"run": run_id, "error": str(exc)})
        raise HTTPException(status_code=503, detail=f"the log is unreadable: {exc}") from exc

    if not events:
        raise HTTPException(status_code=404, detail=f"no run {run_id}")

    report = reporting.build(run_id, events, through_tick=through_tick)
    log.info(
        "report built",
        extra={"run": run_id, "claims": len(report.claims), "outcome": report.outcome["reason"]},
    )
    return report.to_dict()


if __name__ == "__main__":
    serve(SERVICE)
