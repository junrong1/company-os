"""The log store, on both dialects.

Every test here runs twice, once against SQLite and once against Postgres, because the
whole point of the portability work in `schema.py` is that the two behave the same. A
suite that only ran on SQLite would pass while the JSONB key ordering, the sequence
semantics and the transaction control differences waited for the first Postgres run.

Postgres has to be published to the host for that half to run:

    docker compose -f docker-compose.yml -f docker-compose.test.yml up -d postgres

When it is not, the Postgres half **skips with an explanatory message** rather than
silently passing. Skipped is not passed, and U6's verification requires both.
"""

from __future__ import annotations

import os
import threading

import pytest
from sqlalchemy import delete, insert, select, text, update

from contracts.envelope import EventKind
from kernel import lease as lease_module
from kernel.lease import LeaseHeld, utc_now_iso
from logschema import (
    APPEND_ONLY_TABLES,
    DDL_VERSION,
    MUTABLE_TABLES,
    event_log,
    metadata,
    model_cache,
    model_spend,
    runs,
    snapshots,
    store_version,
    writer_lease,
)
from kernel.store import (
    DdlVersionMismatch,
    DuplicateAnswer,
    FencedOut,
    LogStore,
    RunAlreadyTerminated,
    SequenceCollision,
    StoreWriter,
    is_append_only_refusal,
    make_engine,
)
from simcore.rates import RULES_VERSION
from simcore.step import Emitted

#: A separate database from the application's, provisioned by
#: infra/postgres/init/20-test-database.sql. This suite creates and drops the schema and
#: takes the writer lease; from U9 a running kernel does the same things in `companyos`.
#: Sharing one database would have the suite dropping tables from under a live kernel.
DEFAULT_POSTGRES_URL = "postgresql+psycopg://companyos:companyos@127.0.0.1:55432/companyos_test"
POSTGRES_URL = os.environ.get("COMPANY_OS_TEST_POSTGRES_URL", DEFAULT_POSTGRES_URL)

RUN = "run-aaaa"
OTHER_RUN = "run-bbbb"


def _postgres_reachable() -> tuple[bool, str]:
    try:
        engine = make_engine(POSTGRES_URL)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
        return True, ""
    except Exception as exc:  # noqa: BLE001 - any failure means "not available"
        return False, f"{type(exc).__name__}: {exc}"


_POSTGRES_OK, _POSTGRES_WHY = _postgres_reachable()


@pytest.fixture(params=["sqlite", "postgresql"])
def store(request, tmp_path):
    """A fresh, empty store on each dialect."""
    if request.param == "sqlite":
        engine = make_engine(f"sqlite:///{tmp_path}/log.sqlite3")
    else:
        if not _POSTGRES_OK:
            pytest.skip(
                f"Postgres not reachable at {POSTGRES_URL} ({_POSTGRES_WHY}). Start it with "
                "`docker compose -f docker-compose.yml -f docker-compose.test.yml up -d "
                "postgres`. This half of the suite is required by U6's verification, so a "
                "skip here is an incomplete run, not a pass."
            )
        engine = make_engine(POSTGRES_URL)
        # A shared database, so start from nothing. Dropping the tables takes the
        # append-only triggers with them.
        metadata.drop_all(engine)

    log_store = LogStore(engine)
    log_store.create_all()
    log_store.create_run(
        run_id=RUN,
        run_seed=0xC0FFEE,
        rules_ver=RULES_VERSION,
        quantum_sim_seconds=60,
        grid=(31, 18),
    )

    yield log_store

    if request.param == "postgresql":
        metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def handle(store):
    with store.engine.begin() as connection:
        return lease_module.acquire(connection, owner="test-kernel")


def events(count: int = 1, **overrides) -> list[Emitted]:
    return [
        Emitted(
            kind=EventKind.WORK_ASSIGNED,
            payload={"item": f"wi_{index}", "person": "stf_ap", **overrides.pop("payload", {})},
            **overrides,
        )
        for index in range(count)
    ]


def append(store, handle, count: int = 1, tick: int = 1, run_id: str = RUN, **kwargs):
    return store.append_tick(
        run_id=run_id,
        emitted=events(count),
        lease_handle=handle,
        rules_ver=RULES_VERSION,
        tick=tick,
        **kwargs,
    )


# =========================================================================
# Append-only enforcement
# =========================================================================


def test_the_log_refuses_update(store, handle) -> None:
    append(store, handle)

    with pytest.raises(Exception) as excinfo:  # noqa: PT011 - dialect-specific type
        with store.engine.begin() as connection:
            connection.execute(update(event_log).where(event_log.c.seq == 1).values(tick=99))

    assert is_append_only_refusal(excinfo.value), excinfo.value


def test_the_log_refuses_delete(store, handle) -> None:
    append(store, handle)

    with pytest.raises(Exception) as excinfo:  # noqa: PT011
        with store.engine.begin() as connection:
            connection.execute(delete(event_log).where(event_log.c.seq == 1))

    assert is_append_only_refusal(excinfo.value), excinfo.value


def test_the_log_still_accepts_appends_after_a_refused_mutation(store, handle) -> None:
    """A refused mutation must not poison the connection or the cursor."""
    append(store, handle)

    with pytest.raises(Exception):  # noqa: B017, PT011
        with store.engine.begin() as connection:
            connection.execute(delete(event_log).where(event_log.c.seq == 1))

    result = append(store, handle, tick=2)
    assert result.head_seq == 2


def test_every_table_is_registered_as_append_only_or_as_mutable() -> None:
    """The coverage claim the two tuples make, asserted rather than assumed.

    A table added to `metadata` and left out of both lists is not *failed* by the suite
    below — it is skipped by it, silently, because that suite parametrizes over the list.
    So the list has to be provably complete. This is what makes U9's `model_spend` and
    U12's cache table impossible to add without deciding which kind they are.
    """
    registered = set(APPEND_ONLY_TABLES) | set(MUTABLE_TABLES)
    declared = set(metadata.tables)

    assert declared == registered, (
        "these tables are in the schema but registered as neither append-only nor mutable, "
        f"so the append-only suite skips them: {sorted(declared - registered)}"
    )
    assert not (set(APPEND_ONLY_TABLES) & set(MUTABLE_TABLES)), "a table cannot be both"


@pytest.mark.parametrize("table_name", MUTABLE_TABLES)
def test_the_other_tables_stay_mutable(store, handle, table_name: str) -> None:
    """The recovery ladder depends on it.

    A snapshot invalidated by a tuning change is dropped and re-folded; the lease is
    updated on every heartbeat; the spend counter is incremented on every model call.
    Append-only on any of these would break one of them.

    Parametrized over `MUTABLE_TABLES` rather than over a written-out list, so a table
    registered as mutable with no statement here raises a `KeyError` instead of quietly
    going unchecked.
    """
    tables = {
        "runs": (update(runs).where(runs.c.run_id == RUN).values(rate=3), None),
        "snapshots": (
            insert(snapshots).values(
                run_id=RUN,
                through_seq=1,
                tick=1,
                rules_ver=RULES_VERSION,
                state_shape_ver=1,
                state_hash="abc",
                state=b"{}",
                created_at=utc_now_iso(),
            ),
            delete(snapshots).where(snapshots.c.run_id == RUN),
        ),
        "writer_lease": (
            update(writer_lease).where(writer_lease.c.id == 1).values(heartbeat_at=utc_now_iso()),
            None,
        ),
        "store_version": (
            update(store_version).where(store_version.c.id == 1).values(created_at=utc_now_iso()),
            None,
        ),
        "model_spend": (
            insert(model_spend).values(
                run_id=RUN,
                calls=1,
                input_tokens=41,
                output_tokens=7,
                cache_hits=0,
                updated_at=utc_now_iso(),
            ),
            # Removed with its run, like a snapshot. The in-place increment that is the
            # actual reason this table cannot be append-only is asserted by the ledger's
            # own test in `test_modelgw.py`.
            delete(model_spend).where(model_spend.c.run_id == RUN),
        ),
        "model_cache": (
            insert(model_cache).values(
                cache_key="0" * 64,
                lineage_root_id=RUN,
                purpose="director_statement",
                rules_ver=RULES_VERSION,
                response_text="BRIEFING: ...",
                model_identity="a-model",
                created_at=utc_now_iso(),
            ),
            # Deletable, and that is the requirement rather than a convenience: the startup
            # sweep removes entries written under a rules version no longer running, and a
            # lineage's entries go with the lineage. An append-only cache would be a table
            # that can only grow and can never be corrected.
            delete(model_cache).where(model_cache.c.lineage_root_id == RUN),
        ),
    }
    statement, cleanup = tables[table_name]

    with store.engine.begin() as connection:
        connection.execute(statement)
        if cleanup is not None:
            # Deleting a snapshot has to work too — that is how invalidation happens.
            connection.execute(cleanup)


# =========================================================================
# Sequences (R37)
# =========================================================================


def test_sequences_are_dense_and_start_at_one(store, handle) -> None:
    append(store, handle, count=3, tick=1)
    append(store, handle, count=2, tick=2)

    seqs = [envelope.seq for envelope in store.read_events(RUN)]
    assert seqs == [1, 2, 3, 4, 5]


def test_a_duplicate_run_and_sequence_is_rejected_by_the_store(store, handle) -> None:
    append(store, handle)

    row = store.read_events(RUN)[0].to_dict()
    row["ingested_at"] = utc_now_iso()

    with pytest.raises(Exception) as excinfo:  # noqa: PT011
        with store.engine.begin() as connection:
            connection.execute(insert(event_log).values(**row))

    assert not is_append_only_refusal(excinfo.value)


def test_a_stale_cursor_halts_the_kernel_rather_than_advancing(store, handle, monkeypatch) -> None:
    """The two-writer case, forced through the real append path.

    A writer holding a stale cursor is exactly what a second kernel looks like. The
    sequence it picks collides, and the store must refuse — the kernel halts rather than
    advancing over an event that already exists.
    """
    append(store, handle, count=2, tick=1)

    # Pretend this writer never saw the first tick.
    monkeypatch.setattr(store, "_committed_head", staticmethod(lambda _connection, _run: 0))

    with pytest.raises(SequenceCollision) as excinfo:
        append(store, handle, tick=2)

    assert "two writers" in str(excinfo.value)


def test_an_aborted_tick_leaves_the_cursor_untouched_and_the_sequence_reusable(
    store, handle
) -> None:
    """R37's reason for reading the maximum inside the transaction.

    If an aborted tick advanced the cursor, the gap it left would be read by the
    corrupt-tail detector as damage.
    """
    append(store, handle, count=2, tick=1)
    assert store.head_seq(RUN) == 2

    with pytest.raises(Exception):  # noqa: B017, PT011
        store.append_tick(
            run_id=RUN,
            emitted=events(3),
            lease_handle=handle,
            rules_ver=RULES_VERSION,
            tick=2,
            fail_between_events=True,
        )

    assert store.head_seq(RUN) == 2, "an aborted tick advanced the committed maximum"

    # The next successful append reuses the sequence the aborted one would have taken.
    result = append(store, handle, count=1, tick=2)
    assert result.envelopes[0].seq == 3

    count, maximum = store.sequence_density(RUN)
    assert count == maximum == 3, "the log has a gap"


# =========================================================================
# One transaction per tick (R21)
# =========================================================================


def test_a_failure_between_two_events_of_one_tick_leaves_neither(store, handle) -> None:
    """A partial tick folds to a state no pure step could produce."""
    with pytest.raises(Exception):  # noqa: B017, PT011
        store.append_tick(
            run_id=RUN,
            emitted=events(3),
            lease_handle=handle,
            rules_ver=RULES_VERSION,
            tick=1,
            fail_between_events=True,
        )

    assert store.read_events(RUN) == []
    assert store.head_seq(RUN) == 0


def test_a_whole_tick_commits_together(store, handle) -> None:
    result = append(store, handle, count=4, tick=7)

    stored = store.read_events(RUN)
    assert len(stored) == 4
    assert {envelope.tick for envelope in stored} == {7}
    assert result.head_seq == 4


# =========================================================================
# One writer, across more than one run (R35)
# =========================================================================


def test_two_active_runs_append_through_one_writer_with_contiguous_sequences(
    store, handle
) -> None:
    """Sole-writer at the transaction level, not merely at the process level.

    Concurrent appends could otherwise make a later sequence visible before an earlier
    one, and a tailer treating the highest row as the head would skip an event
    permanently.
    """
    store.create_run(
        run_id=OTHER_RUN,
        run_seed=1,
        rules_ver=RULES_VERSION,
        quantum_sim_seconds=60,
        grid=(31, 18),
    )

    writer = StoreWriter(store)
    writer.start()
    try:
        errors: list[BaseException] = []

        def hammer(run_id: str) -> None:
            try:
                for tick in range(1, 16):
                    writer.submit(
                        run_id=run_id,
                        emitted=events(2),
                        lease_handle=handle,
                        rules_ver=RULES_VERSION,
                        tick=tick,
                    )
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [
            threading.Thread(target=hammer, args=(RUN,)),
            threading.Thread(target=hammer, args=(OTHER_RUN,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        assert not errors, errors
    finally:
        writer.stop()

    for run_id in (RUN, OTHER_RUN):
        seqs = [envelope.seq for envelope in store.read_events(run_id)]
        assert seqs == list(range(1, 31)), f"{run_id} sequence is not contiguous"

        # No interleaved partial ticks: each tick contributed exactly its two events,
        # and both carry adjacent sequences.
        by_tick: dict[int, list[int]] = {}
        for envelope in store.read_events(run_id):
            by_tick.setdefault(envelope.tick, []).append(envelope.seq)
        for tick, tick_seqs in by_tick.items():
            assert len(tick_seqs) == 2, f"tick {tick} committed partially"
            assert tick_seqs[1] == tick_seqs[0] + 1, f"tick {tick} was split by another append"


def test_the_writer_refuses_work_before_it_is_started(store, handle) -> None:
    writer = StoreWriter(store)
    with pytest.raises(Exception, match="not running"):
        writer.submit(
            run_id=RUN,
            emitted=events(1),
            lease_handle=handle,
            rules_ver=RULES_VERSION,
            tick=1,
        )


# =========================================================================
# The writer lease (R26)
# =========================================================================


def test_a_second_kernel_cannot_acquire_the_lease_and_is_told_who_holds_it(store) -> None:
    with store.engine.begin() as connection:
        first = lease_module.acquire(connection, owner="kernel-one")

    with pytest.raises(LeaseHeld) as excinfo:
        with store.engine.begin() as connection:
            lease_module.acquire(connection, owner="kernel-two")

    assert excinfo.value.owner == "kernel-one"
    assert "kernel-one" in str(excinfo.value), "the refusal must name the holder"
    assert first.token == 1


def test_a_kernel_whose_lease_was_taken_over_cannot_append(store) -> None:
    """The fencing token doing the work a timestamp cannot.

    A process paused past its TTL and then resumed still believes it holds the lease. Its
    appends are refused because the token it presents is stale.
    """
    with store.engine.begin() as connection:
        zombie = lease_module.acquire(connection, owner="kernel-zombie")

    # The zombie stops heartbeating and its lease expires.
    _expire_lease(store)

    with store.engine.begin() as connection:
        successor = lease_module.acquire(connection, owner="kernel-successor")

    assert successor.token == zombie.token + 1

    with pytest.raises(FencedOut) as excinfo:
        append(store, zombie)

    assert str(zombie.token) in str(excinfo.value)

    # And the successor can append.
    assert append(store, successor).head_seq == 1


def test_a_lease_held_by_a_killed_process_is_reclaimable_after_the_interval(store) -> None:
    with store.engine.begin() as connection:
        lost = lease_module.acquire(connection, owner="kernel-killed")

    # Before the interval elapses, it is still held.
    with pytest.raises(LeaseHeld):
        with store.engine.begin() as connection:
            lease_module.acquire(connection, owner="kernel-next")

    _expire_lease(store)

    with store.engine.begin() as connection:
        reclaimed = lease_module.acquire(connection, owner="kernel-next")

    assert reclaimed.owner == "kernel-next"
    assert reclaimed.token > lost.token


def test_a_heartbeat_keeps_the_lease_and_reports_a_takeover(store) -> None:
    with store.engine.begin() as connection:
        holder = lease_module.acquire(connection, owner="kernel-one")
        assert lease_module.heartbeat(connection, holder) is True

    _expire_lease(store)
    with store.engine.begin() as connection:
        lease_module.acquire(connection, owner="kernel-two")

    with store.engine.begin() as connection:
        assert lease_module.heartbeat(connection, holder) is False, (
            "a fenced-out kernel must learn it lost the lease"
        )


def test_releasing_the_lease_lets_a_successor_start_without_waiting(store) -> None:
    with store.engine.begin() as connection:
        first = lease_module.acquire(connection, owner="kernel-one")
        assert lease_module.release(connection, first) is True

    with store.engine.begin() as connection:
        second = lease_module.acquire(connection, owner="kernel-two")

    assert second.token > first.token


def _expire_lease(store) -> None:
    """Backdate the heartbeat past the TTL, as a crashed holder would leave it."""
    from datetime import UTC, datetime, timedelta

    stale = (
        datetime.now(UTC) - timedelta(seconds=lease_module.LEASE_TTL_SECONDS * 2)
    ).isoformat(timespec="milliseconds")

    with store.engine.begin() as connection:
        connection.execute(
            update(writer_lease).where(writer_lease.c.id == 1).values(heartbeat_at=stale)
        )


# =========================================================================
# Idempotency and terminal runs (R25)
# =========================================================================


def test_a_duplicate_answer_for_one_request_id_is_rejected_structurally(store, handle) -> None:
    request_id = "11111111-1111-5111-8111-111111111111"

    store.append_tick(
        run_id=RUN,
        emitted=[Emitted(kind=EventKind.INPUT_RECEIVED, payload={"v": 1}, request_id=request_id)],
        lease_handle=handle,
        rules_ver=RULES_VERSION,
        tick=1,
    )

    with pytest.raises(DuplicateAnswer):
        store.append_tick(
            run_id=RUN,
            emitted=[
                Emitted(kind=EventKind.INPUT_RECEIVED, payload={"v": 2}, request_id=request_id)
            ],
            lease_handle=handle,
            rules_ver=RULES_VERSION,
            tick=2,
        )

    assert len(store.read_events(RUN)) == 1


def test_events_without_a_request_id_do_not_collide(store, handle) -> None:
    """The uniqueness index is partial, or every ordinary event would collide."""
    append(store, handle, count=5, tick=1)
    assert len(store.read_events(RUN)) == 5


def test_nothing_appends_after_a_terminal_event(store, handle) -> None:
    append(store, handle, count=2, tick=1)
    store.terminate_run(RUN, terminal_seq=2, reason="horizon")

    with pytest.raises(RunAlreadyTerminated):
        append(store, handle, tick=2)

    assert store.head_seq(RUN) == 2


# =========================================================================
# The DDL version guard (R28)
# =========================================================================


def test_a_matching_ddl_version_passes(store) -> None:
    assert store.check_ddl_version() == DDL_VERSION


def test_a_ddl_version_mismatch_refuses_and_names_both_versions_and_the_remedy(store) -> None:
    with store.engine.begin() as connection:
        connection.execute(
            update(store_version).where(store_version.c.id == 1).values(ddl_version=DDL_VERSION + 7)
        )

    with pytest.raises(DdlVersionMismatch) as excinfo:
        store.check_ddl_version()

    message = str(excinfo.value)
    assert str(DDL_VERSION + 7) in message, "the store's version must be named"
    assert str(DDL_VERSION) in message, "the kernel's version must be named"
    assert "Remedy" in message


def test_a_store_with_no_version_row_refuses(store) -> None:
    with store.engine.begin() as connection:
        connection.execute(delete(store_version))

    with pytest.raises(DdlVersionMismatch, match="no DDL version row"):
        store.check_ddl_version()


def test_a_store_written_before_the_cache_table_existed_gains_it_without_a_wipe(store) -> None:
    """The claim `DDL_VERSION`'s comment makes, held as a test rather than as prose.

    U12 adds a table and does not move the version, and that is the rule rather than an exception:
    the version exists to refuse a schema this build cannot *read*, and `create_all` is
    check-first and runs at every startup, so a store written before the table existed grows it on
    its next boot with nothing to migrate. DDL 3 could not do this because the same bump added a
    column to `runs`, which is the case that is still a documented wipe.

    Simulated by dropping the table from a provisioned store, which is what such a store looks
    like from this build's side, and then doing what a restart does.
    """
    from sqlalchemy import inspect

    model_cache.drop(store.engine)
    assert "model_cache" not in set(inspect(store.engine).get_table_names())

    # A restart: the pre-flight check first, against a store this process has not touched...
    assert store.check_ddl_version() == DDL_VERSION, "an older store is not a mismatch"
    # ...and then the creation step, which is where the table arrives.
    store.create_all()

    assert "model_cache" in set(inspect(store.engine).get_table_names())
    with store.engine.begin() as connection:
        connection.execute(
            insert(model_cache).values(
                cache_key="1" * 64,
                lineage_root_id=RUN,
                purpose="director_statement",
                rules_ver=RULES_VERSION,
                response_text="BRIEFING: ...",
                model_identity="a-model",
                created_at=utc_now_iso(),
            )
        )
    assert store.check_ddl_version() == DDL_VERSION, "and the version row was not touched"


# =========================================================================
# Reading back
# =========================================================================


def test_events_round_trip_through_the_store(store, handle) -> None:
    original = append(store, handle, count=3, tick=5).envelopes
    restored = store.read_events(RUN)

    for before, after in zip(original, restored, strict=True):
        assert after.hashed_projection() == before.hashed_projection()
        assert after.decoded_payload() == before.decoded_payload()


def test_the_payload_survives_as_canonical_bytes_on_both_dialects(store, handle) -> None:
    """The reason payload is LargeBinary and never a JSON column.

    Postgres JSONB reorders object keys where SQLite does not. If the payload went through
    a JSON column, the bytes the state hash covers would differ per dialect — and the
    failure would look like a determinism bug, not a schema choice.
    """
    store.append_tick(
        run_id=RUN,
        emitted=[Emitted(kind=EventKind.METRICS_APPLIED, payload={"z": 1, "a": 2, "m": 3})],
        lease_handle=handle,
        rules_ver=RULES_VERSION,
        tick=1,
    )

    stored = store.read_events(RUN)[0]
    assert stored.payload == b'{"a":2,"m":3,"z":1}', "keys are not in canonical order"


def test_reading_is_bounded_and_ordered(store, handle) -> None:
    append(store, handle, count=10, tick=1)

    assert [e.seq for e in store.read_events(RUN, after_seq=4)] == [5, 6, 7, 8, 9, 10]
    assert [e.seq for e in store.read_events(RUN, through_seq=3)] == [1, 2, 3]
    assert [e.seq for e in store.read_events(RUN, limit=2)] == [1, 2]


def test_sequence_density_reports_count_and_maximum(store, handle) -> None:
    append(store, handle, count=4, tick=1)
    assert store.sequence_density(RUN) == (4, 4)


def test_a_run_row_carries_the_head_and_current_tick(store, handle) -> None:
    append(store, handle, count=3, tick=9)

    row = store.run_row(RUN)
    assert row is not None
    assert row["current_tick"] == 9
    assert row["head_seq"] == 3
    # There is no tick event per quantum, so the run row is where current tick lives.
    assert row["rules_ver"] == RULES_VERSION


# =========================================================================
# Fork (U15), which lives in the store because it must be lease-fenced
# =========================================================================


def test_a_fork_copies_the_prefix_and_inherits_the_parents_genesis(store, handle) -> None:
    """A child inherits seed, quantum, grid and horizon.

    The horizon especially: it is chosen at genesis and immutable, so a fork cannot outlive
    the bound its parent was created with.
    """
    with store.engine.begin() as connection:
        connection.execute(update(runs).where(runs.c.run_id == RUN).values(horizon_tick=5400))

    append(store, handle, count=3, tick=1)
    append(store, handle, count=2, tick=2)

    result = store.fork_run(
        parent_run_id=RUN, at_seq=4, child_run_id=OTHER_RUN, lease_handle=handle
    )

    assert result.forked
    assert result.copied_through_seq == 4

    child = store.run_row(OTHER_RUN)
    parent = store.run_row(RUN)
    assert child is not None and parent is not None
    assert child["run_seed"] == parent["run_seed"]
    assert child["quantum_sim_seconds"] == parent["quantum_sim_seconds"]
    assert (child["grid_cols"], child["grid_rows"]) == (parent["grid_cols"], parent["grid_rows"])
    assert child["horizon_tick"] == 5400
    assert child["parent_run_id"] == RUN
    assert child["forked_at_seq"] == 4

    # The prefix, and only the prefix.
    assert [e.seq for e in store.read_events(OTHER_RUN)] == [1, 2, 3, 4]
    assert [e.seq for e in store.read_events(RUN)] == [1, 2, 3, 4, 5]


def test_a_forked_child_shares_sequence_values_with_its_parent(store, handle) -> None:
    """Which is why stream resume is keyed on (run, seq) and not on seq alone."""
    append(store, handle, count=3, tick=1)
    store.fork_run(parent_run_id=RUN, at_seq=2, child_run_id=OTHER_RUN, lease_handle=handle)

    parent_seqs = {e.seq for e in store.read_events(RUN)}
    child_seqs = {e.seq for e in store.read_events(OTHER_RUN)}
    assert child_seqs & parent_seqs == child_seqs


def test_a_fork_above_the_prefix_bound_is_refused_with_a_reason(store, handle) -> None:
    """Rather than holding the single writer in one long transaction.

    That would stall every other run's ticks and show up in the lag metric as a store
    outage that is not happening.
    """
    append(store, handle, count=5, tick=1)

    result = store.fork_run(
        parent_run_id=RUN,
        at_seq=5,
        child_run_id=OTHER_RUN,
        lease_handle=handle,
        prefix_bound=2,
    )

    assert not result.forked
    assert "above the bound" in result.refusal
    assert "single writer" in result.refusal
    assert store.run_row(OTHER_RUN) is None, "a refused fork must create nothing"


def test_a_fenced_out_kernel_cannot_fork(store) -> None:
    """A fork is a write, so it presents the same token every append does."""
    with store.engine.begin() as connection:
        zombie = lease_module.acquire(connection, owner="kernel-zombie")

    append(store, zombie, count=2, tick=1)
    _expire_lease(store)

    with store.engine.begin() as connection:
        lease_module.acquire(connection, owner="kernel-successor")

    with pytest.raises(FencedOut):
        store.fork_run(
            parent_run_id=RUN, at_seq=2, child_run_id=OTHER_RUN, lease_handle=zombie
        )

    assert store.run_row(OTHER_RUN) is None


def test_forking_an_unknown_run_is_refused(store, handle) -> None:
    with pytest.raises(Exception, match="no such run"):
        store.fork_run(
            parent_run_id="run-nope", at_seq=1, child_run_id=OTHER_RUN, lease_handle=handle
        )


# =========================================================================
# A multi-day run survives the store, on both dialects (U15's verification)
# =========================================================================


def _play_and_persist(store, handle, sim_days: int = 3):
    """Run the simulation for real, appending each tick's events through the store."""
    from simcore import step as sim
    from simcore import time as simtime
    from simcore import verify as verifier

    state, genesis = sim.new_run(run_seed=0xC0FFEE, cols=31, rows=18)
    store.append_tick(
        run_id=RUN, emitted=genesis, lease_handle=handle, rules_ver=RULES_VERSION, tick=0
    )

    def persist(emitted, tick):
        if emitted:
            store.append_tick(
                run_id=RUN,
                emitted=emitted,
                lease_handle=handle,
                rules_ver=RULES_VERSION,
                tick=tick,
            )

    persist(sim.assign_via_manager(state, "wi_ap_map"), state.tick)

    resolved = False
    for _ in range(sim_days * simtime.TICKS_PER_SIM_DAY):
        persist(sim.step(state), state.tick)

        if not resolved and state.items["wi_ap_map"].status == sim.STATUS_BLOCKED:
            persist(
                sim.resolve_checkpoint(state, "wi_ap_map", 0, 0, in_person=True), state.tick
            )
            resolved = True

        if simtime.is_day_boundary(state.tick):
            persist(
                [
                    sim.Emitted(
                        kind=EventKind.DAY_CHECKPOINT,
                        payload=verifier.build_checkpoint_payload(state),
                    )
                ],
                state.tick,
            )

    with store.engine.begin() as connection:
        connection.execute(
            update(runs).where(runs.c.run_id == RUN).values(current_tick=state.tick)
        )

    return state


def test_a_multi_day_run_refolds_from_the_store_to_the_same_hash(store, handle) -> None:
    """U15's verification, on whichever dialect this parametrisation is running."""
    from simcore import hashing
    from simcore import log as folder
    from simcore import step as sim

    live = _play_and_persist(store, handle)
    live_hash = hashing.state_hash(sim.snapshot(live)).overall

    row = store.run_row(RUN)
    assert row is not None

    refolded = folder.fold(
        store.read_events(RUN),
        at_live_head=False,
        strict=True,
        through_tick=row["current_tick"],
    )

    assert hashing.state_hash(sim.snapshot(refolded.state)).overall == live_hash
    assert refolded.state.tick == live.tick


def test_a_multi_day_run_verifies_clean_from_the_store(store, handle) -> None:
    from simcore import verify as verifier

    _play_and_persist(store, handle)
    report = verifier.verify(store.read_events(RUN))

    assert report.healthy, report.summary()
    assert report.last_good_tick > 0, "no day-boundary checkpoint was reached"


def test_a_run_resumes_at_the_same_sim_time_with_actors_mid_path(store, handle) -> None:
    """A restart must not teleport anyone or lose sim-time."""
    from simcore import log as folder
    from simcore import step as sim

    state, genesis = sim.new_run(run_seed=0xC0FFEE, cols=31, rows=18)
    store.append_tick(
        run_id=RUN, emitted=genesis, lease_handle=handle, rules_ver=RULES_VERSION, tick=0
    )
    emitted = sim.assign_via_manager(state, "wi_ap_map")
    store.append_tick(
        run_id=RUN, emitted=emitted, lease_handle=handle, rules_ver=RULES_VERSION, tick=state.tick
    )
    for _ in range(80):  # the hand-off walk takes 168 ticks, so this is mid-path
        produced = sim.step(state)
        if produced:
            store.append_tick(
                run_id=RUN,
                emitted=produced,
                lease_handle=handle,
                rules_ver=RULES_VERSION,
                tick=state.tick,
            )

    with store.engine.begin() as connection:
        connection.execute(
            update(runs).where(runs.c.run_id == RUN).values(current_tick=state.tick)
        )

    row = store.run_row(RUN)
    assert row is not None
    resumed = folder.fold(
        store.read_events(RUN), at_live_head=False, through_tick=row["current_tick"]
    ).state

    assert resumed.tick == state.tick
    walker = resumed.people["dir_admin"]
    assert walker.state == sim.STATE_WALKING
    assert walker.pos == state.people["dir_admin"].pos
    assert walker.path == state.people["dir_admin"].path


def test_an_exported_run_from_the_store_reproduces_its_hash(store, handle) -> None:
    from simcore import export as exporter
    from simcore import hashing
    from simcore import step as sim

    live = _play_and_persist(store, handle, sim_days=2)
    live_hash = hashing.state_hash(sim.snapshot(live)).overall

    artifact = exporter.export_run(
        run_id=RUN,
        events=store.read_events(RUN),
        through_tick=live.tick,
        state_hash=live_hash,
    )

    imported, meta = exporter.import_and_replay(artifact)
    assert hashing.state_hash(sim.snapshot(imported)).overall == live_hash
    assert meta.through_tick == live.tick


# =========================================================================
# Both dialects actually ran
# =========================================================================


def test_postgres_is_reachable_for_this_suite() -> None:
    """U6's verification names both dialects, so a Postgres-less run is incomplete.

    This is a separate test rather than a skip inside the fixture so the omission is
    visible in the summary line rather than buried in skip counts.
    """
    if not _POSTGRES_OK:
        pytest.skip(
            f"Postgres not reachable at {POSTGRES_URL} ({_POSTGRES_WHY}). "
            "U6 is only verified when this passes."
        )
    assert _POSTGRES_OK
