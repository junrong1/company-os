"""Agents service entrypoint, the run's model-spend counter, and its response cache.

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

**Neither is the cache, and for a stronger reason.** `StoreResponseCache` keeps what a
provider said so that the same situation reached again in the same lineage costs nothing,
and it is authoritative for nothing at all: a fork copies its parent's event rows, so
pre-divergence statements come out of the log byte for byte and the cache is never
consulted for them. Emptying the table between a fork and a re-fold changes no state hash,
which is the property that says where a statement actually lives.

**This module is also the answering side of the statement seam** (U10). The kernel raises a
statement request inside `step()` and hands it here through a callable the launcher
installs — the same shape as the kernel client and the spend reader, and for the same
reason: neither service may import the other, so something outside both composes them.
The direction is what the proto describes and what survived the compose collapse: the
kernel opens the stream, and nothing here holds a kernel handle or reaches for a runtime.

**The store read lives here and the scope does not.** `_read_run` is this service's own
engine over `logschema`, following the report's `_read_log` rather than importing
`kernel.store`. What it reads is unscoped by necessity — it is the whole log — which is
exactly why it is *here* and not in `bench/`: `bench.context.retrieve` cannot be called
without an authorized scope, and the scope arrives on the request rather than being
computed, so no bench module can widen it (R23). `tests/test_pending_input.py` reads the
`bench/` files to keep that door single.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Engine, create_engine, delete, func, insert, select, update

import modelgw
from contracts.envelope import Envelope, EventKind
from logschema import event_log, model_cache, model_spend, runs
from modelgw import Completion
from modelgw.cache import CachedResponse, CacheKey, entry_from
from modelgw.ceiling import BoundedGateway, Ceiling, Spend, announce
from servicekit import logging as svclog
from servicekit.app import create_service_app
from servicekit.probes import store_url
from servicekit.runtime import serve
from simcore.rates import RULES_VERSION

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
    _sweep_the_cache()

    def teardown() -> None:
        ledger().dispose()
        response_cache().dispose()
        _dispose_log_engine()

    return teardown


def _sweep_the_cache() -> None:
    """Drop cached responses written under a rules version this build is not running.

    At startup rather than on a timer, and swallowing its own failure: the cache is an
    optimisation, so a store that is not there yet — which is the normal state of a service that
    starts before its database — must not stop this service answering. `get` filters on the rules
    version too, so nothing stale can be served in the meantime; this only reclaims the rows.
    """
    try:
        removed = response_cache().evict_other_rules_versions()
    except Exception as exc:  # noqa: BLE001 - housekeeping never blocks a startup
        log.info(
            "the response cache could not be swept at startup; entries under another rules "
            "version are refused by every lookup regardless",
            extra={"error": str(exc)},
        )
        return

    if removed:
        log.info(
            "evicted cached responses written under another rules version",
            extra={"evicted": removed, "rules_ver": RULES_VERSION},
        )


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


class StoreResponseCache:
    """The provider responses this lineage has already paid for, kept in `model_cache`.

    Satisfies `modelgw.cache.ResponseCache`. The content address is derived in `modelgw.cache`,
    which is stdlib-only because `test_modelgw.py` requires that package to import nothing but
    `httpx`; everything here is the half that needs an engine, and it sits beside
    `StoreSpendLedger` for the reason U9 put the ledger here — the service that has a store is
    where the store-backed half of a `modelgw` protocol lives.

    **An entry is addressed by the situation and scoped by the lineage.** The lookup joins to
    `runs.lineage_root_id` rather than taking a root from the caller, exactly as the spend
    aggregate does: one place records a lineage, and a caller cannot widen what it is served by
    passing a different root. Until U16 copies a parent's root at fork, a child is its own root
    and so hits nothing of its parent's — the fork half of M33 is unreachable until then, and the
    test that pins this behaviour sets the column by hand to say so.

    **A store this cannot reach is a miss, never an exception.** Both methods run on the worker
    thread that produces a briefing, where an exception is a lost statement; a miss is one
    provider call. The exception to that is `put` handed something that is not a `Completion`,
    which is this repository calling it wrong rather than a database being down.

    **Its own engine, like the ledger's and the log reader's.** Three handles on one DSN in one
    process is the price of three concerns whose lifetimes and failure modes are separate — the
    counter must keep counting when the cache is unreadable, and neither may be taken down by the
    other's `dispose`.
    """

    __slots__ = ("_engine", "_rules_ver", "_url")

    def __init__(self, url: str | None = None, rules_version: str = RULES_VERSION) -> None:
        self._url = url
        self._rules_ver = rules_version
        self._engine: Engine | None = None

    def get(self, key: CacheKey) -> Completion | None:
        """The answer this lineage already has to this situation, or nothing.

        Filtered on the rules version as well as swept for it at startup. Two defences rather
        than one, because a sweep alone would leave a window between a tuning change and the next
        restart in which advice assembled under the old numbers is served as if it were current —
        and the sweep would then be the thing correctness rested on rather than housekeeping.
        """
        try:
            with self._engine_for().connect() as connection:
                row = connection.execute(
                    select(model_cache.c.response_text, model_cache.c.model_identity)
                    .select_from(
                        model_cache.join(
                            runs, runs.c.lineage_root_id == model_cache.c.lineage_root_id
                        )
                    )
                    .where(
                        runs.c.run_id == key.run_id,
                        model_cache.c.cache_key == key.digest,
                        model_cache.c.rules_ver == self._rules_ver,
                    )
                ).first()
        except Exception as exc:  # noqa: BLE001 - an unreadable cache is a miss
            log.warning(
                "could not read the response cache; treating it as a miss",
                extra={"run": key.run_id, "error": str(exc)},
            )
            return None

        if row is None:
            return None
        return CachedResponse(text=row[0], model=row[1]).as_completion()

    def put(self, key: CacheKey, served: Completion) -> None:
        """Keep one provider answer for the next time this lineage reaches this situation.

        `entry_from` runs first and outside the `try`: a `Failure` here is a fallback being
        written as though it were a briefing, which is the one thing this cache must never hold,
        and it is a defect in the caller rather than a store that went away.

        A write that loses a race with another director's identical situation is a lost write, not
        an error — the row that won says the same thing.
        """
        entry = entry_from(served)
        try:
            with self._engine_for().begin() as connection:
                root = connection.execute(
                    select(runs.c.lineage_root_id).where(runs.c.run_id == key.run_id)
                ).scalar_one_or_none()
                if root is None:
                    # No run row to scope the entry to. Nothing to repair: an entry keyed to a
                    # lineage that does not exist could never be matched and never be removed.
                    return

                already = connection.execute(
                    select(model_cache.c.cache_key).where(
                        model_cache.c.cache_key == key.digest,
                        model_cache.c.lineage_root_id == root,
                    )
                ).first()
                if already is not None:
                    # Left as it was rather than overwritten. The digest is the situation, so the
                    # two answers are answers to the same question, and keeping the first means a
                    # replayed situation reads the same on the tenth visit as on the second.
                    return

                connection.execute(
                    insert(model_cache).values(
                        cache_key=key.digest,
                        lineage_root_id=root,
                        purpose=str(key.purpose),
                        rules_ver=self._rules_ver,
                        response_text=entry.text,
                        model_identity=entry.model,
                        created_at=_utc_now_iso(),
                    )
                )
        except Exception as exc:  # noqa: BLE001 - a failed write costs a provider call, nothing more
            log.warning(
                "could not write the response cache; the next identical situation will call out",
                extra={"run": key.run_id, "error": str(exc)},
            )

    def evict_other_rules_versions(self) -> int:
        """Remove every entry written under a rules version this build is not running.

        Housekeeping, run once at startup. It reclaims rows that `get` already refuses to serve,
        which is the correct order of those two: a sweep that failed leaves the cache larger and
        never wrong.
        """
        with self._engine_for().begin() as connection:
            return connection.execute(
                delete(model_cache).where(model_cache.c.rules_ver != self._rules_ver)
            ).rowcount

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    def _engine_for(self) -> Engine:
        if self._engine is None:
            self._engine = create_engine(self._url or store_url(), future=True)
        return self._engine


#: One ledger per process, holding one engine. Built on first use rather than at import so
#: that a process with no store configured still imports, and reset by `importlib.reload`,
#: which is how the suite gets a fresh one.
_LEDGER: StoreSpendLedger | None = None

#: The response cache, on the same terms as the ledger above.
_CACHE: StoreResponseCache | None = None


def ledger() -> StoreSpendLedger:
    global _LEDGER
    if _LEDGER is None:
        _LEDGER = StoreSpendLedger()
    return _LEDGER


def response_cache() -> StoreResponseCache:
    global _CACHE
    if _CACHE is None:
        _CACHE = StoreResponseCache()
    return _CACHE


def bench(spend_ledger: Any = None, cache: Any = None) -> BoundedGateway:
    """The bench this service calls through: one gateway, one ceiling, one counter, one cache.

    U11 calls this. A cache hit is reported through `note_cache_hit` on the same object, which is
    what keeps a hit from counting against the call ceiling — and what keeps a scripted fallback
    from being recorded as a hit, since that method takes a `Completion`.

    Both collaborators are arguments with defaults rather than constructed unconditionally, so a
    test can supply an in-memory pair and a caller that wants the ceiling alone can pass a cache
    that holds nothing. The shipped call passes neither and gets the store.
    """
    return BoundedGateway(
        modelgw.from_environment(),
        CEILING,
        spend_ledger if spend_ledger is not None else ledger(),
        cache if cache is not None else response_cache(),
    )


# =========================================================================
# The statement leg
# =========================================================================


#: The engine the statement leg reads the log with. One per process, built on first use.
#:
#: Its own rather than the ledger's, and the reason is the boundary the ledger's docstring already
#: draws: the counter is this service's *own* table and the log is the kernel's, so reading them
#: through one handle would make a future change to either reach into the other. Built lazily for
#: the same reason the ledger's is — this module is imported in every agents process whether or not
#: there is a store to reach.
_LOG_ENGINE: Engine | None = None


def _log_engine() -> Engine:
    global _LOG_ENGINE
    if _LOG_ENGINE is None:
        _LOG_ENGINE = create_engine(store_url(), future=True)
    return _LOG_ENGINE


def _dispose_log_engine() -> None:
    global _LOG_ENGINE
    if _LOG_ENGINE is not None:
        _LOG_ENGINE.dispose()
        _LOG_ENGINE = None


def _read_run(run_id: str) -> list[Envelope]:
    """A run's events, in sequence order.

    Reads rows described by `logschema`, the shared table definitions, with this service's own
    engine. It deliberately does *not* import `kernel.store`: that module owns the write path
    and R4 forbids a service from importing another service's internals — the same reasoning,
    and the same shape, as `report.main._read_log`.

    Nothing is imported lazily here, unlike in the report, and the difference is the module rather
    than the rule: this one already holds `sqlalchemy` and `logschema` at the top for the spend
    counter, so a local import would buy nothing and imply a constraint that is not there. What has
    to stay lazy is the *engine*, and it is — see `_log_engine`.
    """
    with _log_engine().connect() as connection:
        rows = (
            connection.execute(
                select(event_log)
                .where(event_log.c.run_id == run_id)
                .order_by(event_log.c.seq)
            )
            .mappings()
            .all()
        )

    # Validated on read as well as before append: a non-canonical payload that somehow reached
    # the store is caught here rather than handed to a director as context.
    return [Envelope.from_dict(dict(row)) for row in rows]


def produce_statement(request: Any) -> dict[str, Any] | None:
    """Answer one statement request, or decline.

    **Declining means one thing only now: there is no bench.** With no provider configured this
    returns `None`, the request reaches its sim-tick deadline, and the conversation is the Phase 2
    conversation exactly (M20). Every other outcome answers — with a briefing, or with that turn's
    scripted reply naming the condition that fired (M21). A run that has a bench and gets silence
    would leave the player watching a pending block expire with nothing said about why.

    What is assembled here and nowhere else is the *situation*: the log is read once, and the persona,
    the checkpoint, the guards' view of the offer and the line-scoped retrieval all come out of that
    one read. Two reads would be two moments, and the second one would include events the first did
    not — which is the latency-dependent context R2 forbids, arriving through a second door.

    **The guard runs here as well as in the kernel, and it is the same function** (execution
    decision §1). This side is the cheap rejection: a statement refused here never crosses the wire.
    The kernel's copy at answer-application time is the auditable one, because its verdict is an
    output event the fold regenerates. Filtering the citations instead — quietly dropping the ones
    that resolve to nothing — was the first shape of this and it is the wrong one: a repair on one
    side of a rule and a refusal on the other is exactly the drift §1 says two copies would cause,
    and the CEO would be shown a briefing whose figures had been silently edited.

    **A refusal here becomes a fallback, not silence.** That is the change U11 makes to this shape: a
    statement that ranked the options is replaced by the scripted reply, which is what R5's single
    exit means — the player learns that the bench answered and was not usable, rather than that
    nothing happened.

    A store this cannot read is a declined statement, not a raised exception. The kernel dispatches
    this off the tick thread and outside the run lock, but an exception crossing back would still be
    an exception on a path whose whole job is to be optional.
    """
    from simcore import statement as stmt

    from agents.bench import guards

    situation = _situation_for(request)
    if situation is None:
        return None

    gateway = bench()
    prose = compose_statement(situation, gateway)
    if prose is None:
        log.info(
            "no bench configured; the statement request is left for its deadline",
            extra={"run": request.run_id, "person": request.person},
        )
        return None

    answer = _answer_from(situation, prose)
    refusal = guards.refusal_of(answer, situation)
    if not refusal:
        # Only now is the reply worth keeping. `complete` staged it and this is the acceptance:
        # a briefing that ranked the options is a real provider response and must still write
        # nothing, or the next visit to this situation would serve the refusal back rather than
        # ask again — which is "guard rejections write nothing" failing through a side door,
        # with switching to a better model as the remedy that does not work.
        gateway.keep()
        return answer

    if answer[stmt.KEY_PRODUCER_KIND] == stmt.PRODUCER_SCRIPTED:
        # The scripted reply itself is refused, and there is no third thing to substitute. Detected
        # by what was *refused* rather than by refusing twice, which matters: rebuilding a refused
        # fallback as a `guard_refused` one would relabel a timeout as this repository's decision and
        # lose the condition an operator needs. So the request is left to its deadline and the reason
        # is logged loudly — a defect in this repository's own content or in the run's shape, not in
        # anything a provider said. Not `raise`: this runs on a worker thread the kernel dispatched,
        # where an exception is a lost briefing at best.
        log.error(
            "the scripted reply was itself refused; the request is left for its deadline",
            extra={
                "run": request.run_id,
                "person": request.person,
                "reason": refusal,
                "condition": answer.get(stmt.KEY_FALLBACK, ""),
            },
        )
        return None

    log.info(
        "a statement was refused before it crossed the wire; the scripted reply stands",
        extra={"run": request.run_id, "person": request.person, "reason": refusal},
    )
    return _answer_from(situation, guards.fallback_prose(stmt.FALLBACK_GUARD_REFUSED))


def compose_statement(
    situation: Any, gateway: Any
) -> tuple[str, str, tuple[int, ...], str, str, str] | None:
    """The prose half: what the director says, and whether a provider said it.

    Returns `(briefing, objection, citations, model_identity, producer_kind, fallback)`, or `None`
    when there is no provider configured at all. It stays a named seam rather than an inlined call so
    that a test can substitute prose and still run the shipped retrieval, the shipped guard and the
    shipped transport around it — which is what `test_a_briefing_crosses_the_whole_leg_and_lands_in_the_log`
    does, and the property it proves is worth more than the line it costs.

    The gateway is built per statement rather than held, and it is now built by the *caller* rather
    than here. `guards.completed` explains the per-statement half: the call runs on a worker thread
    with its own event loop, and an `httpx.AsyncClient` created in the launcher's loop and awaited in
    this one is a cross-loop bug this shape cannot have. What changed is who holds it, and the reason
    is the cache — the answer's entry is written only once the guards have accepted it, so the object
    that staged the write has to outlive this call.
    """
    from agents.bench import guards

    return guards.prose_from_provider(situation, gateway)


def _situation_for(request: Any) -> Any:
    """Everything one statement needs, out of one read of the log, or `None`.

    Every `None` here is a declined statement rather than an exception, and each has a distinct
    reason worth logging separately: a log this process cannot read, a roster that does not describe
    the person the request names, a person who is not a director, and an item that is not in the
    authored catalog. Only the first is a fault; the others are all states a run can legitimately be
    in, and the last two are M14 — a specialist stays scripted, and only a director briefs.
    """
    from simcore import statement as stmt

    from agents.bench import context as retrieval
    from agents.bench import guards, personas

    try:
        events = _read_run(request.run_id)
    except Exception as exc:  # noqa: BLE001 - an unreadable log is a declined statement
        log.warning(
            "could not read the run's log for a briefing",
            extra={"run": request.run_id, "person": request.person, "error": str(exc)},
        )
        return None

    genesis = next(
        (envelope for envelope in events if envelope.kind is EventKind.GENESIS), None
    )
    if genesis is None:
        log.warning("a run with no genesis event cannot be briefed", extra={"run": request.run_id})
        return None
    payload = genesis.decoded_payload()

    persona = personas.persona_for(payload, request.person)
    if persona is None:
        log.warning(
            "the roster does not describe the person this request names",
            extra={"run": request.run_id, "person": request.person},
        )
        return None
    if not persona.is_director:
        # M14, on this side as well as in the kernel's guard. Four directors are model-backed and
        # specialists stay scripted, so a request naming one is declined here rather than answered
        # with a persona the scenario did not author.
        log.info(
            "only a director produces a statement; this request names a specialist",
            extra={"run": request.run_id, "person": request.person},
        )
        return None

    entry = _catalog_entry(payload, request.owning_item)
    if entry is None:
        log.info(
            "the item this request is about is not in the authored catalog",
            extra={"run": request.run_id, "item": request.owning_item},
        )
        return None

    checkpoints = entry.get("checkpoints", [])
    if not isinstance(checkpoints, list) or not 0 <= request.cp_index < len(checkpoints):
        log.warning(
            "the request names a checkpoint the catalog does not hold",
            extra={"run": request.run_id, "item": request.owning_item, "cp": request.cp_index},
        )
        return None

    try:
        retrieved = retrieval.retrieve(
            events,
            authorized=request.authorized,
            owning_item=request.owning_item,
            at_tick=request.raised_at_tick,
        )
    except Exception as exc:  # noqa: BLE001 - an unreadable log is a declined statement
        log.warning(
            "could not assemble a director's context",
            extra={"run": request.run_id, "person": request.person, "error": str(exc)},
        )
        return None

    return guards.Situation(
        request=request,
        persona=persona,
        checkpoint=checkpoints[request.cp_index],
        offered=stmt.Offered.from_catalog(entry, request.cp_index),
        retrieved=retrieved,
    )


def _catalog_entry(genesis_payload: dict[str, Any], item_id: str) -> dict[str, Any] | None:
    """One item's authored entry, out of the genesis catalog."""
    catalog = genesis_payload.get("catalog", [])
    if not isinstance(catalog, list):
        return None
    for entry in catalog:
        if isinstance(entry, dict) and entry.get("id") == item_id:
            return entry
    return None


def _answer_from(situation: Any, prose: tuple[str, str, tuple[int, ...], str, str, str]) -> dict[str, Any]:
    """One statement's answer payload, with the context it was drawn from attached here.

    Attached by the caller rather than returned by the prose half, deliberately: M32 records the world
    a briefing was produced from, and a producer that chose its own evidence could record a context
    its prose was never checked against.
    """
    from simcore import statement as stmt

    briefing, objection, citations, model_identity, producer_kind, fallback = prose
    return stmt.Statement(
        briefing=briefing,
        objection=objection,
        citations=tuple(citations),
        producer=situation.request.person,
        producer_kind=producer_kind,
        model_identity=model_identity,
        context=situation.retrieved.to_payload(),
        fallback=fallback,
    ).to_answer()


# =========================================================================
# The memory read (U14)
# =========================================================================


def read_memory(
    run_id: str, *, authorized: Any, as_of_tick: int, with_summary: bool = True
) -> dict[str, Any] | None:
    """One director's memory as the CEO reads it, or `None` if the log is unreadable.

    **It cannot be called without a scope, and this service cannot make one.** `authorized` is a
    `simcore.statement.Authorized` derived in the kernel from folded state — `step.remembered_scope`
    — and handed here by the launcher, exactly as a statement request's scope is. That is why there
    is no route for this on the agents surface: a mounted endpoint taking a director id would have
    to derive its own scope, and R23's whole shape is that the reader cannot. The one door onto the
    log is `_read_run`, and everything past it is filtered by a scope somebody else computed.

    **The selection is derived and only the prose is generated** (M38). With no provider configured
    this still answers — the events that mattered, the counts, the two day stamps — and the summary
    says it is absent. A store this cannot read is `None` rather than an exception, because the
    caller is a read route and a stack trace is a worse answer than a sentence.

    **`with_summary=False` is what lets the panel open at once.** The derived half is a read of the
    log and is fast; the prose is a provider call and is not. So the client asks twice — once for
    the selection, which it renders immediately, and once for the summary, which replaces a stated
    pending line when it lands. That is not a narrower *scope*, it is less work: the same events
    come back either way, and a caller that omits the prose learns nothing extra by doing so.

    The pending status is only ever reported when a bench is actually present. On a keyless run the
    first read already says `absent`, so the client makes no second call and the panel never shows a
    pending line that cannot resolve — which would be M38 failing in the one place it is most
    visible.

    A roster that does not describe the director is a summary that is absent rather than refused.
    From the CEO's side "there is no bench" and "there is nobody to write it" are the same absence,
    the derived selection renders either way, and the distinction that matters — whether a bench
    answered and was not usable — is the one `REFUSED` keeps.
    """
    from agents.bench import memory, personas

    try:
        events = _read_run(run_id)
    except Exception as exc:  # noqa: BLE001 - an unreadable log is a read that failed, not a crash
        log.warning(
            "could not read the run's log for a director's memory",
            extra={"run": run_id, "director": authorized.director, "error": str(exc)},
        )
        return None

    selection = memory.select(events, authorized=authorized, as_of_tick=as_of_tick)

    if not with_summary:
        gateway = bench()
        waiting = memory.Summary.pending() if gateway.present else memory.Summary.absent()
        return memory.Memory(selection=selection, summary=waiting).to_payload()

    genesis = next(
        (envelope for envelope in events if envelope.kind is EventKind.GENESIS), None
    )
    persona = (
        personas.persona_for(genesis.decoded_payload(), authorized.director)
        if genesis is not None
        else None
    )
    if persona is None:
        log.info(
            "no persona for this director, so the memory renders without prose",
            extra={"run": run_id, "director": authorized.director},
        )
        return memory.Memory(selection=selection, summary=memory.Summary.absent()).to_payload()

    gateway = bench()
    try:
        summary = memory.summarise(
            selection, persona=persona, gateway=gateway, run_id=run_id
        )
    except Exception as exc:  # noqa: BLE001 - the prose is optional; the selection is not
        # The selection is derived from the log and is already in hand, so a failure in the half
        # that needs a provider must not cost the half that does not. `guards.completed` answers
        # with a typed failure rather than raising, so this is for this module's own mistakes.
        log.warning(
            "the memory summary could not be produced; the derived selection stands alone",
            extra={"run": run_id, "director": authorized.director, "error": str(exc)},
        )
        summary = memory.Summary.refused(str(modelgw.FailureKind.GATEWAY_FAULT))

    return memory.Memory(selection=selection, summary=summary).to_payload()


# =========================================================================
# The report's prose (U21)
# =========================================================================


def write_prescription(run_id: str, packets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prose over the report's automation proposals, one reply per packet (M58).

    **It cannot invent a proposal, and this signature is why.** `packets` come from
    `report.proposals`, which selected them from the scenario's authored catalog over its own
    fold; this service is handed the finished figures and asked for sentences. There is no route
    for it on the agents surface and no reader of the store here that could assemble one — the
    launcher hands the report this callable, exactly as it hands the gateway a spend reader and
    the kernel a statement producer, and neither service learns the other exists.

    One call per proposal rather than one for all of them, and the reason is the guard: a reply
    is refused against *that proposal's* citable and resolvable sets, so a single answer covering
    four proposals would either be accepted whole with one paragraph's figures checked against
    another's, or refused whole because one sentence was wrong.

    A reply is always returned, one per packet, so the report can tell "there is no bench" from
    "the bench answered and was not usable" — the same distinction `Scripted reply` keeps for a
    briefing. An exception here would cost the whole prescription section, so each packet is
    caught on its own: prose is the optional half of a proposal and the figures are not.
    """
    from agents.bench import prescription

    if not bench().present:
        # Asked once for the whole batch: with no provider configured there is nothing to ask,
        # and building a gateway per packet to be told so four times is work for an answer that
        # cannot vary.
        return [{"proposal": str(packet.get("proposal", ""))} for packet in packets]

    written: list[dict[str, Any]] = []
    for packet in packets:
        proposal = str(packet.get("proposal", ""))
        try:
            # A *fresh* gateway per call, which is `guards.completed`'s rule rather than a
            # habit: its `httpx.AsyncClient` is created and closed inside one `asyncio.run`, so
            # a gateway held across two calls is a client awaited in a loop that has gone.
            written.append(prescription.write(packet, gateway=bench(), run_id=run_id))
        except Exception as exc:  # noqa: BLE001 - the prose is optional; the figures are not
            log.warning(
                "a proposal's prose could not be produced; its figures stand alone",
                extra={"run": run_id, "proposal": proposal, "error": str(exc)},
            )
            written.append(
                {"proposal": proposal, "refusal": str(modelgw.FailureKind.GATEWAY_FAULT)}
            )
    return written


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
