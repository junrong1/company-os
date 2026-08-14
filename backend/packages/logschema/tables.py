"""The store's shape, and the DDL version that guards it.

Five tables, and exactly one of them is append-only. That distinction is enforced by
per-dialect triggers on the log table alone: snapshots are dropped and re-folded when a
tuning change invalidates them, the lease is updated on every heartbeat, and the version
row is written once at creation. Putting an append-only trigger on those would break the
recovery ladder it is meant to protect.

**Portability is handled up front, not when Postgres breaks something.** Four choices
here exist because SQLite and Postgres disagree, and each disagreement would otherwise
surface as a determinism bug rather than as a database error:

* `seq` is `BigInteger` on Postgres and `Integer` on SQLite. SQLite's type affinity is
  matched on the literal type name, so a column declared `BIGINT` is not an `INTEGER
  PRIMARY KEY` and silently loses the behaviour that comes with it.
* `payload` is `LargeBinary`, never a JSON column. A dialect-scoped JSON type would put
  Postgres's JSONB key reordering underneath the state hash — which would work locally on
  SQLite and break the first time the schema targeted Postgres.
* timestamps are stored as explicit ISO-8601 UTC **text**. SQLite has no native datetime
  and does not enforce a timezone flag, so `DateTime(timezone=True)` is a promise only
  Postgres keeps.
* foreign keys and transaction control are configured per connection in `store.py`,
  because SQLite's defaults differ from Postgres's in ways that affect SAVEPOINT and
  isolation.
"""

from __future__ import annotations

from contracts.envelope import EventKind
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    text,
)

#: The store's DDL version. Recorded at creation and checked at every startup: a
#: mismatch refuses to start and names both versions and the remedy, because a kernel
#: appending to a schema it does not understand is worse than a kernel that will not run.
#:
#: Distinct from both the rules version (tuning and multiplier order) and the event
#: schema version (envelope shape). All three appear on the status endpoint.
DDL_VERSION = 2

#: Emits BIGINT on Postgres and INTEGER on SQLite. See the module docstring.
SeqType = BigInteger().with_variant(Integer, "sqlite")

metadata = MetaData()


#: Append-only. The kernel is the only writer (R1), and every append presents the
#: lease's fencing token.
event_log = Table(
    "event_log",
    metadata,
    # The primary key is (run, sequence) — R37. Not a store autoincrement: the kernel
    # knows the sequence before insert, and a Postgres sequence would leave gaps on
    # out-of-order commit that the corrupt-tail detector would misread as damage.
    Column("run_id", String(64), nullable=False),
    Column("seq", SeqType, nullable=False),
    Column("tick", SeqType, nullable=False),
    Column("kind", Integer, nullable=False),
    Column("schema_ver", Integer, nullable=False),
    Column("rules_ver", String(64), nullable=False),
    Column("payload", LargeBinary, nullable=False),
    # Metadata, excluded from replay identity and the state hash.
    Column("command_id", String(64), nullable=False, server_default=text("''")),
    Column("request_id", String(64), nullable=False, server_default=text("''")),
    Column("ingested_at", String(32), nullable=False),
    # Dense and gapless from 1.
    CheckConstraint("seq > 0", name="ck_event_log_seq_positive"),
    Index("ix_event_log_run_seq", "run_id", "seq", unique=True),
    # R25, structurally rather than by a racing check: one **answer** per request id per run.
    #
    # The predicate names the answer kind rather than merely a non-empty request id, and that
    # distinction is the requirement. One request legitimately produces several events that
    # share its id — the REQUEST_RAISED that asked, and then either an answer or a rejection —
    # so an index on "any event with a request id" would refuse the second one and halt the
    # kernel over correct behaviour. What must be unique is the answer.
    #
    # A rejection deliberately stays outside the index: a request can be answered out of
    # bounds, rejected, and later abandoned, and each of those is a fact worth keeping.
    Index(
        "ux_event_log_answer",
        "run_id",
        "request_id",
        unique=True,
        sqlite_where=text(f"request_id != '' AND kind = {int(EventKind.INPUT_RECEIVED)}"),
        postgresql_where=text(f"request_id != '' AND kind = {int(EventKind.INPUT_RECEIVED)}"),
    ),
)
event_log.append_constraint(
    # Declared after the indexes so the primary key reads as the point it is.
    UniqueConstraint("run_id", "seq", name="pk_event_log")
)


#: Mutable. Current tick lives here and on snapshots; there is no tick event per quantum,
#: because under a fixed quantum one would carry no information.
runs = Table(
    "runs",
    metadata,
    Column("run_id", String(64), primary_key=True),
    Column("run_seed", SeqType, nullable=False),
    Column("current_tick", SeqType, nullable=False, server_default=text("0")),
    Column("head_seq", SeqType, nullable=False, server_default=text("0")),
    # Rate is run state (R18), so a disconnect does not pause the clock. The replay
    # *multiplier* is deliberately not stored: that would make replay speed part of the
    # run.
    Column("rate", Integer, nullable=False, server_default=text("1")),
    Column("rules_ver", String(64), nullable=False),
    Column("quantum_sim_seconds", Integer, nullable=False),
    Column("grid_cols", Integer, nullable=False),
    Column("grid_rows", Integer, nullable=False),
    # Chosen at genesis, immutable, inherited by forks (U8).
    Column("horizon_tick", SeqType, nullable=True),
    # Set when the run ends. Nothing appends after it (R25).
    Column("terminal_seq", SeqType, nullable=True),
    Column("terminal_reason", String(32), nullable=True),
    # Fork lineage. A child shares sequence values with its parent, which is why resume
    # is keyed on (run, seq) rather than on seq alone.
    Column("parent_run_id", String(64), nullable=True),
    Column("forked_at_seq", SeqType, nullable=True),
    Column("created_at", String(32), nullable=False),
)


#: Mutable, and deliberately so: a snapshot invalidated by a tuning change is dropped and
#: re-folded, which loses nothing.
snapshots = Table(
    "snapshots",
    metadata,
    Column("run_id", String(64), ForeignKey("runs.run_id"), primary_key=True),
    Column("through_seq", SeqType, primary_key=True),
    Column("tick", SeqType, nullable=False),
    Column("rules_ver", String(64), nullable=False),
    Column("state_shape_ver", Integer, nullable=False),
    Column("state_hash", String(64), nullable=False),
    Column("state", LargeBinary, nullable=False),
    Column("created_at", String(32), nullable=False),
)


#: Mutable. A single row, holding the one writer's identity and its fencing token.
#:
#: This is the actual single-instance mechanism (R26). A compose replica cap is a setting,
#: not a mechanism — and the documented single-process mode pointed at the compose store
#: is a supported path to two kernels, two loops and interleaved sequences that fold to a
#: state neither process produced.
writer_lease = Table(
    "writer_lease",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("owner", String(128), nullable=False),
    # Monotonic. A resurrected zombie presents a stale token and is fenced out, which a
    # timestamp alone cannot achieve.
    Column("token", SeqType, nullable=False),
    Column("acquired_at", String(32), nullable=False),
    Column("heartbeat_at", String(32), nullable=False),
    CheckConstraint("id = 1", name="ck_writer_lease_singleton"),
)


#: Written once at creation. Checked at every startup.
store_version = Table(
    "store_version",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("ddl_version", Integer, nullable=False),
    Column("created_at", String(32), nullable=False),
    CheckConstraint("id = 1", name="ck_store_version_singleton"),
)


#: The one table that refuses mutation.
APPEND_ONLY_TABLES = ("event_log",)

#: The tables that must stay mutable for the recovery ladder to work.
MUTABLE_TABLES = ("runs", "snapshots", "writer_lease", "store_version")


_REFUSAL = "event_log is append-only"


def append_only_ddl(dialect: str) -> list[str]:
    """Statements that make the log refuse UPDATE and DELETE.

    Triggers rather than permissions, because they hold for every role including the
    owner. SQLite has no roles at all, so the read-only credential described in the
    plan's System-Wide Impact is a Postgres-side *addition* to this, never the mechanism.

    Scoped to the log table only. Snapshots, the lease and the version row are mutable by
    design.
    """
    if dialect == "sqlite":
        return [
            f"""
            CREATE TRIGGER IF NOT EXISTS trg_event_log_no_update
            BEFORE UPDATE ON event_log
            BEGIN SELECT RAISE(ABORT, '{_REFUSAL}'); END
            """,
            f"""
            CREATE TRIGGER IF NOT EXISTS trg_event_log_no_delete
            BEFORE DELETE ON event_log
            BEGIN SELECT RAISE(ABORT, '{_REFUSAL}'); END
            """,
        ]

    if dialect == "postgresql":
        return [
            f"""
            CREATE OR REPLACE FUNCTION event_log_refuse_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '{_REFUSAL}';
            END;
            $$ LANGUAGE plpgsql
            """,
            """
            DROP TRIGGER IF EXISTS trg_event_log_no_update ON event_log
            """,
            """
            CREATE TRIGGER trg_event_log_no_update
            BEFORE UPDATE OR DELETE ON event_log
            FOR EACH ROW EXECUTE FUNCTION event_log_refuse_mutation()
            """,
        ]

    raise ValueError(f"no append-only enforcement defined for dialect {dialect!r}")


def is_append_only_refusal(exc: BaseException) -> bool:
    """Whether an exception is the append-only trigger firing, on either dialect."""
    return _REFUSAL in str(exc)
