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

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Query
from fastapi.responses import HTMLResponse

from report import export as exporting
from report import fold as reporting
from servicekit import logging as svclog
from servicekit.app import create_service_app
from servicekit.probes import ENV_REPORT_STORE_URL, probe_store, store_url
from servicekit.runtime import serve
from servicekit.status import Dependency

if TYPE_CHECKING:  # pragma: no cover - the runtime import is deliberately lazy, below
    from report import universe as universes

SERVICE = "report"

log = svclog.get_logger(SERVICE)

#: Writes the prose over the report's automation proposals, or is absent (U21, M58).
#:
#: Takes a run id and the packets `report.proposals` built, and answers one reply per packet.
#: A type alias rather than a client, for the reason the gateway's spend reader is one: the
#: producer lives in the agents service, this service may not import it (R4), and the launcher
#: hands the callable across (R28). This service therefore cannot ask a provider for anything
#: the report did not first compute and hand it.
Prescriber = Callable[[str, list[dict[str, Any]]], Sequence[Mapping[str, Any]]]

#: Absent by default, and absent is a working state rather than a fault. With no prescriber the
#: proposals render with their figures and say their prose is absent — which is also exactly
#: what a keyless run shows for its whole life (M20's rule, applied to the report).
_prescriber: Prescriber | None = None


def use_prescriber(writer: Prescriber | None) -> None:
    """Install the producer that writes prose over the proposals. Optional."""
    global _prescriber
    _prescriber = writer


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

    from logschema import runs

    engine = create_engine(reader_url(), future=True)
    try:
        with engine.connect() as connection:
            events = _events_of(connection, run_id)
            run = connection.execute(
                select(runs.c.current_tick).where(runs.c.run_id == run_id)
            ).first()

        return events, int(run.current_tick) if run else 0
    finally:
        engine.dispose()


def _events_of(connection: Any, run_id: str) -> list:
    """One run's log, in sequence order, validated on the way out.

    Validated on read as well as before append: a non-canonical payload that somehow reached
    the store is caught here rather than folded.

    Split out of `_read_log` so the diff can read two logs on one connection. Three separate
    engines for one answer would be three connections and — on Postgres — three chances to read
    two timelines from two different instants.
    """
    from sqlalchemy import select

    from contracts.envelope import Envelope
    from logschema import event_log

    rows = (
        connection.execute(
            select(event_log).where(event_log.c.run_id == run_id).order_by(event_log.c.seq)
        )
        .mappings()
        .all()
    )
    return [Envelope.from_dict(dict(row)) for row in rows]


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


@app.get("/runs/{run_id}/universe")
def universe_report(run_id: str) -> dict[str, Any]:
    """One report over the whole tree of timelines this run belongs to (M53–M56, M59)."""
    return _universe(run_id).to_dict()


@app.get("/runs/{run_id}/universe.html", response_class=HTMLResponse)
def universe_export(run_id: str) -> HTMLResponse:
    """The same report as one standalone HTML file (M54, M60, M61).

    **The same fold, rendered rather than serialised.** It is the export route's whole design
    that it is not a second reading of the log: `_universe` is the one that both this and the
    JSON route call, so a figure on the page a player is looking at and a figure in the file
    they mailed cannot disagree about a company.

    **Served from here rather than proxied into existence.** The launcher mounts this app at
    `/report` in the one process (R28), so the client's link is `/api/report/...` through the
    same nginx prefix every other call goes through — one anchor, no second origin, and no
    gateway import of a fold that R4 forbids.

    `Content-Disposition: inline` rather than `attachment`: reaching the report from the client
    should *show* it, and the filename is there for the Save As that follows. The name is
    derived from a validated identifier because a run id is whatever `POST /runs` accepted, and
    an unfiltered one would be a header a caller writes.

    The policy travels twice — as this response's header, which governs it while it is served,
    and inside the document, which is what governs it once it is a file on somebody's disk.
    """
    report = _universe(run_id)
    document = exporting.render(report.to_dict())
    log.info(
        "universe exported",
        extra={
            "run": run_id,
            "root": report.root_run_id,
            "timelines": len(report.timelines),
            "bytes": len(document.encode("utf-8")),
        },
    )
    return HTMLResponse(
        content=document,
        headers={
            "content-disposition": (
                f'inline; filename="{exporting.filename_for(report.root_run_id)}"'
            ),
            "content-security-policy": exporting.csp(),
            "referrer-policy": "no-referrer",
            "x-content-type-options": "nosniff",
        },
    )


def _universe(run_id: str) -> universes.Universe:
    """The Universe this run belongs to, folded once for whoever asked (M53–M56, M59).

    **Named by any timeline, identified by the root.** The caller passes whichever timeline they
    are standing in and gets the Universe that contains it, keyed by `root_run_id` — so two
    players in two branches of one lineage export the same artifact, which is what makes it a
    document about a company rather than about a camera position.

    Synchronous, like the run report and for the same reason: a fold over a whole lineage is real
    work, and FastAPI runs a synchronous endpoint in its threadpool so the blocking happens off
    the event loop.

    **Every timeline's log is read on one connection**, as the diff's two are. On Postgres,
    N engines would be N chances to read N timelines from N different instants, and a report
    whose sections disagreed about where the Universe was would be worse than a slow one.
    """
    from sqlalchemy import create_engine

    from logschema import lineage
    from report import universe as universes
    from simcore import time as simtime

    engine = create_engine(reader_url(), future=True)
    try:
        tree = lineage.tree(engine, run_id, simtime.TICKS_PER_SIM_DAY)
        if tree is None:
            raise HTTPException(status_code=404, detail=f"no run {run_id}")

        with engine.connect() as connection:
            logs = {
                node.run_id: _timeline_log(connection, node) for node in tree.nodes
            }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced as a 503, not a stack trace
        log.warning("could not read the lineage", extra={"run": run_id, "error": str(exc)})
        raise HTTPException(status_code=503, detail=f"the log is unreadable: {exc}") from exc
    finally:
        engine.dispose()

    if not logs.get(run_id) or not logs[run_id].events:
        raise HTTPException(status_code=404, detail=f"no run {run_id}")

    report = universes.build(tree, logs)

    # After the engine is disposed, and that ordering is the point rather than the sequence it
    # happens to fall in: with a bench configured this makes a provider call per proposal, and
    # holding a store connection open across a 30-second network timeout would be one of this
    # process's connections spent waiting on somebody else's server.
    _write_the_prose(report)
    log.info(
        "universe report built",
        extra={
            "run": run_id,
            "root": report.root_run_id,
            "timelines": len(report.timelines),
            "claims": len(report.claims),
            "proposals": [proposal.id for proposal in report.proposals],
            "refused": [entry.run_id for entry in report.timelines if entry.refusal],
        },
    )
    return report


def _write_the_prose(report: Any) -> None:
    """Ask the installed producer for prose over the proposals, and guard what comes back.

    **After the figures, never before, and never instead of them.** The document is complete at
    the point this is called: every proposal already carries its evidence and its payback, and
    what this adds is sentences over them. So every failure here costs prose and nothing else —
    no prescriber installed, a provider that could not be reached, a reply this repository will
    not show — and the report is the same document minus a paragraph.

    Keyed by the *root*, because the artifact is identified by its lineage root: two players in
    two branches export the same document, and prose addressed by where somebody was standing
    would make that false the moment it was cached.
    """
    from report import proposals as prescribing

    if _prescriber is None or not report.proposals:
        return

    packets = [proposal.to_packet() for proposal in report.proposals]
    try:
        written = _prescriber(report.root_run_id, packets)
    except Exception as exc:  # noqa: BLE001 - the prose is optional; the figures are not
        log.warning(
            "the proposals' prose could not be produced; their figures stand alone",
            extra={"run": report.root_run_id, "error": str(exc)},
        )
        return

    prescribing.attach(report.proposals, written)
    log.info(
        "the prescription's prose was read back",
        extra={
            "run": report.root_run_id,
            "written": [
                proposal.id
                for proposal in report.proposals
                if proposal.note.status == prescribing.WRITTEN
            ],
            "refused": [
                f"{proposal.id}: {proposal.note.reason}"
                for proposal in report.proposals
                if proposal.note.status == prescribing.REFUSED
            ],
        },
    )


@app.get("/runs/{run_id}/diff/{against_run_id}")
def timeline_diff(
    run_id: str,
    against_run_id: str,
    day: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    """Two timelines at one sim-day, and the decision that separated them (M50, M52).

    **Served here rather than on the gateway, and that is settled rather than convenient.** The
    diff is a fold across two logs; the fold lives in this service; the gateway may not import it
    (R4) and the launcher mounts this app into the one process instead (R28). So the client
    reaches it at `/api/report/...` through the same proxy that carries every other call.

    **The day is the server's to default.** The client sends none on the first open and takes
    `max_day` back, because the bound depends on how far each timeline got and this route is the
    side that reads the rows. A client that guessed would sometimes ask for a day one side has
    not reached and get a refusal it could have avoided.

    Three answers, and they are deliberately different shapes. A run this store has never heard
    of is a 404, because there is no Universe to say anything about. A log that cannot be read
    is a 503, like the report's. Everything else — a timeline against itself, a run from another
    Universe, a day one side has not reached — is a *refusal on the payload* with a 200, which
    is the same shape a rejected fork and a refused switch already take: the request was
    well-formed and the answer is no, and the surface renders the reason where the figures would
    have been.
    """
    from sqlalchemy import create_engine

    from logschema import lineage
    from simcore import time as simtime

    engine = create_engine(reader_url(), future=True)
    try:
        tree = lineage.tree(engine, run_id, simtime.TICKS_PER_SIM_DAY)
        if tree is None:
            raise HTTPException(status_code=404, detail=f"no run {run_id}")

        nodes = {node.run_id: node for node in tree.nodes}
        if against_run_id not in nodes:
            # True whether that run is in another Universe or in none at all, and the
            # distinction is not one this answer needs: either way there is no shared history
            # to fold to a common day. Naming the root is what makes the sentence actionable.
            return reporting.Diff(
                day=0,
                at_tick=0,
                max_day=0,
                refusal=(
                    f"{against_run_id} is not a timeline of this Universe, whose root is "
                    f"{tree.root_run_id}. Two timelines can only be diffed against the "
                    "Genesis they share."
                ),
            ).to_dict()

        separation = lineage.separating_decision(tree, run_id, against_run_id)

        with engine.connect() as connection:
            left = _timeline_log(connection, nodes[run_id])
            right = _timeline_log(connection, nodes[against_run_id])
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced as a 503, not a stack trace
        log.warning(
            "could not read the lineage",
            extra={"run": run_id, "against": against_run_id, "error": str(exc)},
        )
        raise HTTPException(status_code=503, detail=f"the log is unreadable: {exc}") from exc
    finally:
        engine.dispose()

    if not left.events:
        raise HTTPException(status_code=404, detail=f"no run {run_id}")
    if not right.events:
        raise HTTPException(status_code=404, detail=f"no run {against_run_id}")

    answer = reporting.diff(
        left,
        right,
        day=day,
        separation=None if separation is None else separation.to_payload(),
    )
    log.info(
        "diff built",
        extra={
            "run": run_id,
            "against": against_run_id,
            "day": answer.day,
            "rows": len(answer.rows),
            "refusal": answer.refusal,
        },
    )
    return answer.to_dict()


def _timeline_log(connection: Any, node: Any) -> reporting.TimelineLog:
    """One side of a diff: its log, and the row's reading of where its clock got to.

    The tick comes from the node rather than from a second query, so both sides are described by
    the one select the tree already did. The row's `rate` and `terminal_reason` are deliberately
    *not* carried: `TimelineLog` says why, with the measurement.
    """
    return reporting.TimelineLog(
        run_id=node.run_id,
        events=_events_of(connection, node.run_id),
        current_tick=node.tick,
    )


if __name__ == "__main__":
    serve(SERVICE)
