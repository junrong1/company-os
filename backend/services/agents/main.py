"""Agents service entrypoint, and the run's model-spend counter.

Like the domain service, it declares no dependencies: the kernel opens the stream
(R17), so this service is reachable-and-serving or it is not, and it has no
opinion about the kernel's state.

**It reads and writes the store with its own engine.** The counter has to survive a
restart, so it lives in the store; and no service may import another service's internals
(R4), so this reaches the `model_spend` table through `logschema` — the shared table
definitions — exactly as the report service reaches the log. Importing `kernel.store` for
its engine is the thing the import-boundary test exists to catch, and it caught the
report service reaching for `LogStore` for the same reason.

**The store stays an undeclared, lazily-opened dependency.** This service must be
answerable when the store is down: a bench that cannot read the counter cannot enforce
the ceiling, so it falls back to scripted replies — which is a working product, not an
unhealthy service. Declaring the store on the status payload would say the opposite, and
would give away the claim that the answer-side services declare nothing.

**The counter is not in the log, and cannot be.** What a call cost depends on which
provider answered and what it counted, so an event carrying it would be an output the
fold cannot reproduce, and strict replay would fail on every run that used the bench. The
log carries the *fallback* and the closed-enum condition that fired; the counter is
bookkeeping beside it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Engine, create_engine, func, insert, select, update

import modelgw
from logschema import model_spend, runs
from modelgw.ceiling import BoundedGateway, Ceiling, Spend, announce
from servicekit import logging as svclog
from servicekit.app import create_service_app
from servicekit.probes import store_url
from servicekit.runtime import serve

SERVICE = "agents"

log = svclog.get_logger(SERVICE)

#: Read once, at import. Resolving it is silent; *saying* what it is happens at startup.
#: The shipped default is finite, an absent setting means that default, and on a
#: bring-your-own-key tool the failure mode of a deferred number is `None` meaning
#: unbounded — so an explicitly unlimited setting is announced at WARNING.
CEILING = Ceiling.from_environment()


def _on_start(_app: Any) -> Any:
    """Announce the ceiling, and hand back the teardown that closes the counter's engine.

    In `on_start` rather than at import, and the distinction is not cosmetic: a module that
    logs when it is imported writes to the stdout of everything that merely imports it —
    which `tests/test_contracts_generated.py` caught by asserting on a probe's output. A
    service says what it is configured with when it *starts*.
    """
    announce(CEILING, log)
    return lambda: ledger().dispose()


app = create_service_app(SERVICE, on_start=_on_start)


def _utc_now_iso() -> str:
    """Explicit ISO-8601 UTC text, in the same shape every other timestamp column holds.

    Written here rather than imported from `kernel.lease`, which has the identical
    function: that module is the kernel service's internals, and R4 forbids reaching into
    it. One line duplicated is the whole price of the boundary, and the boundary is what
    keeps one service's refactor out of another service's runtime.
    """
    return datetime.now(UTC).isoformat(timespec="milliseconds")


# =========================================================================
# The counter, in the store
# =========================================================================


class StoreSpendLedger:
    """The per-run spend counter, kept in `model_spend`.

    Satisfies `modelgw.ceiling.SpendLedger`, and it is the implementation that makes the
    counter survive a restart. The in-memory ledger is not a stub — it is what a bench
    with no reachable store falls back to — but it is not what M28 promises.

    **An increment is arithmetic in the statement, never a read followed by a write.**
    `SET calls = calls + :n` is atomic on both dialects; the read-modify-write would lose
    an increment whenever two directors were briefed at once, and the number it would lose
    is the one the ceiling is checked against.

    **The engine is built on first use, not in `__init__`.** The same call
    `ModelGateway._http` makes, for the same reason: this module is imported in every
    agents process whether or not there is a store to reach, and `store_url()` at import
    time would make an unconfigured process fail to start rather than fail to count.
    """

    __slots__ = ("_engine", "_url")

    def __init__(self, url: str | None = None) -> None:
        self._url = url
        self._engine: Engine | None = None

    def read(self, run_id: str) -> Spend:
        with self._engine_for().connect() as connection:
            row = (
                connection.execute(select(model_spend).where(model_spend.c.run_id == run_id))
                .mappings()
                .first()
            )
        # A run that has never called anything has no row, and that is not an absence to
        # repair. Zero is the honest reading, and writing a row on first *read* would make
        # a status request indistinguishable from activity.
        return _spend_from(row) if row else Spend()

    def add(self, run_id: str, delta: Spend) -> Spend:
        now = _utc_now_iso()
        with self._engine_for().begin() as connection:
            changed = connection.execute(
                update(model_spend)
                .where(model_spend.c.run_id == run_id)
                .values(
                    calls=model_spend.c.calls + delta.calls,
                    input_tokens=model_spend.c.input_tokens + delta.input_tokens,
                    output_tokens=model_spend.c.output_tokens + delta.output_tokens,
                    cache_hits=model_spend.c.cache_hits + delta.cache_hits,
                    updated_at=now,
                )
            ).rowcount
            if not changed:
                # Update-then-insert rather than insert-then-update: the row exists for
                # every call after the first, so the common path is one statement. An
                # ON CONFLICT upsert would be two dialect-specific spellings for the same
                # thing.
                connection.execute(
                    insert(model_spend).values(
                        run_id=run_id,
                        calls=delta.calls,
                        input_tokens=delta.input_tokens,
                        output_tokens=delta.output_tokens,
                        cache_hits=delta.cache_hits,
                        updated_at=now,
                    )
                )
            row = (
                connection.execute(select(model_spend).where(model_spend.c.run_id == run_id))
                .mappings()
                .first()
            )
        return _spend_from(row) if row else delta

    def lineage(self, run_id: str) -> Spend:
        """Every run sharing this run's lineage root, summed.

        A join to `runs.lineage_root_id` rather than a recursive walk of `parent_run_id`: a
        deleted mid-lineage row would silently split one lineage in two, and the player
        would read a session total that quietly under-reported. For a run with no forks the
        root is the run itself, so this equals `read` — which is what makes the aggregate
        testable today and interesting once U16 lands the fork half of R21.
        """
        with self._engine_for().connect() as connection:
            root = connection.execute(
                select(runs.c.lineage_root_id).where(runs.c.run_id == run_id)
            ).scalar_one_or_none()
            if root is None:
                # No run row at all. Zero rather than an error: the counter is a display,
                # and an unknown run has spent nothing by every reading of the question.
                return Spend()

            row = connection.execute(
                select(
                    func.coalesce(func.sum(model_spend.c.calls), 0),
                    func.coalesce(func.sum(model_spend.c.input_tokens), 0),
                    func.coalesce(func.sum(model_spend.c.output_tokens), 0),
                    func.coalesce(func.sum(model_spend.c.cache_hits), 0),
                )
                .select_from(model_spend.join(runs, runs.c.run_id == model_spend.c.run_id))
                .where(runs.c.lineage_root_id == root)
            ).one()

        return Spend(
            calls=int(row[0]),
            input_tokens=int(row[1]),
            output_tokens=int(row[2]),
            cache_hits=int(row[3]),
        )

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    def _engine_for(self) -> Engine:
        if self._engine is None:
            self._engine = create_engine(self._url or store_url(), future=True)
        return self._engine


def _spend_from(row: Any) -> Spend:
    return Spend(
        calls=int(row["calls"]),
        input_tokens=int(row["input_tokens"]),
        output_tokens=int(row["output_tokens"]),
        cache_hits=int(row["cache_hits"]),
    )


#: One ledger per process, holding one engine. Built on first use rather than at import so
#: that a process with no store configured still imports, and reset by `importlib.reload`,
#: which is how the suite gets a fresh one.
_LEDGER: StoreSpendLedger | None = None


def ledger() -> StoreSpendLedger:
    global _LEDGER
    if _LEDGER is None:
        _LEDGER = StoreSpendLedger()
    return _LEDGER


def bench(spend_ledger: Any = None) -> BoundedGateway:
    """The bench this service calls through: one gateway, one ceiling, one counter.

    U11 calls this. U12 reports a cache hit through `note_cache_hit` on the same object,
    which is what keeps a hit from counting against the call ceiling — and what keeps a
    scripted fallback from being recorded as a hit, since that method takes a `Completion`.
    """
    return BoundedGateway(
        modelgw.from_environment(), CEILING, spend_ledger if spend_ledger is not None else ledger()
    )


# =========================================================================
# What the surface reads
# =========================================================================


@app.get("/runs/{run_id}/spend")
def run_spend(run_id: str) -> dict[str, Any]:
    """This run's calls and tokens against this run's ceiling, with the lineage beside it.

    M28's figure at its source. A run that has never called anything answers zeros with
    the bench marked absent — not a 404 and not a 503 — because "no key configured" is a
    supported mode, and a counter that errored would read as a broken bench rather than as
    an absent one.
    """
    gateway = bench()
    try:
        return gateway.reading(run_id).to_payload()
    except Exception as exc:  # noqa: BLE001 - surfaced as a 503, not a stack trace
        log.warning("could not read the model spend", extra={"run": run_id, "error": str(exc)})
        raise HTTPException(
            status_code=503, detail=f"the spend counter is unreadable: {exc}"
        ) from exc


if __name__ == "__main__":
    serve(SERVICE)
