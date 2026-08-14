"""The writer lease: single instance, enforced at the store.

A compose replica cap is a setting, not a mechanism. It cannot see a kernel started by
the documented single-process mode and pointed at the compose store — which is a
supported path to two tick loops, two writers, and interleaved sequences that fold to a
state neither process produced. So single-instance is enforced where both processes
necessarily meet: the store.

The lease carries three things, and each covers a failure the others do not:

* **owner identity** — so a second kernel can say *who* holds it rather than only that
  something does. "Another kernel is running" is not an actionable message.
* **a heartbeat** — so a crashed holder's lease expires rather than wedging the store
  until someone deletes a row by hand.
* **a monotonic fencing token** — so a *resurrected* holder is refused. This is the case a
  timestamp alone cannot cover: a process paused long enough for its lease to expire, then
  resumed, still believes it holds the lease and would happily append. Every append
  presents its token, and the store compares it against the current one, so the zombie's
  writes are refused rather than interleaved.
"""

from __future__ import annotations

import os
import socket
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.engine import Connection

from logschema import writer_lease

#: How long a lease survives without a heartbeat. The documented interval after which a
#: killed process's lease is reclaimable — long enough that a slow tick or a GC pause
#: cannot lose it, short enough that a crash does not block a restart for minutes.
LEASE_TTL_SECONDS = 30

#: How often the holder should renew. A third of the TTL, so two consecutive missed
#: heartbeats are survivable.
HEARTBEAT_SECONDS = 10


class LeaseHeld(Exception):
    """Another kernel holds the lease. Carries who, so the message is actionable."""

    def __init__(self, owner: str, heartbeat_at: str, seconds_until_reclaimable: int) -> None:
        self.owner = owner
        self.heartbeat_at = heartbeat_at
        self.seconds_until_reclaimable = seconds_until_reclaimable
        super().__init__(
            f"the writer lease is held by {owner} (last heartbeat {heartbeat_at}). "
            f"Exactly one kernel may run against a store: it owns the clock and is the "
            f"only writer to the log. If that process is gone, the lease becomes "
            f"reclaimable in {seconds_until_reclaimable}s."
        )


@dataclass(frozen=True, slots=True)
class LeaseHandle:
    """Proof of the right to append. Presented on every append."""

    owner: str
    token: int
    acquired_at: str


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_now_iso() -> str:
    """Explicit ISO-8601 UTC text.

    Stored as text rather than as a datetime column because SQLite has no native datetime
    and does not enforce a timezone flag, so `DateTime(timezone=True)` is a promise only
    Postgres keeps.
    """
    return utc_now().isoformat(timespec="milliseconds")


def this_process_identity() -> str:
    """Who this kernel is, in a form a human can act on."""
    return f"{socket.gethostname()}/pid-{os.getpid()}/{uuid.uuid4().hex[:8]}"


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


def acquire(connection: Connection, owner: str | None = None) -> LeaseHandle:
    """Take the lease, or raise LeaseHeld naming the current holder.

    Runs inside the caller's transaction so that read-then-write cannot interleave with a
    second kernel doing the same. On Postgres the row is locked explicitly; on SQLite the
    write transaction already serialises writers.
    """
    owner = owner or this_process_identity()
    now = utc_now()

    query = select(writer_lease)
    if connection.dialect.name == "postgresql":
        query = query.with_for_update()

    current = connection.execute(query).mappings().first()

    if current is None:
        token = 1
        connection.execute(
            writer_lease.insert().values(
                id=1,
                owner=owner,
                token=token,
                acquired_at=now.isoformat(timespec="milliseconds"),
                heartbeat_at=now.isoformat(timespec="milliseconds"),
            )
        )
        return LeaseHandle(owner=owner, token=token, acquired_at=now.isoformat())

    last_heartbeat = _parse(current["heartbeat_at"])
    expires_at = last_heartbeat + timedelta(seconds=LEASE_TTL_SECONDS)

    if now < expires_at:
        raise LeaseHeld(
            owner=current["owner"],
            heartbeat_at=current["heartbeat_at"],
            seconds_until_reclaimable=max(0, int((expires_at - now).total_seconds())),
        )

    # Expired. Take it over, and bump the token so the previous holder is fenced out even
    # if it comes back to life believing it still holds the lease.
    token = int(current["token"]) + 1
    connection.execute(
        update(writer_lease)
        .where(writer_lease.c.id == 1)
        .values(
            owner=owner,
            token=token,
            acquired_at=now.isoformat(timespec="milliseconds"),
            heartbeat_at=now.isoformat(timespec="milliseconds"),
        )
    )
    return LeaseHandle(owner=owner, token=token, acquired_at=now.isoformat())


def heartbeat(connection: Connection, handle: LeaseHandle) -> bool:
    """Renew. Returns False if the lease has been taken over.

    False is the signal to stop appending immediately: something else owns the log.
    """
    result = connection.execute(
        update(writer_lease)
        .where(writer_lease.c.id == 1, writer_lease.c.token == handle.token)
        .values(heartbeat_at=utc_now_iso())
    )
    return result.rowcount == 1


def current_token(connection: Connection) -> int | None:
    return connection.execute(select(writer_lease.c.token)).scalar_one_or_none()


def holder(connection: Connection) -> str | None:
    return connection.execute(select(writer_lease.c.owner)).scalar_one_or_none()


def release(connection: Connection, handle: LeaseHandle) -> bool:
    """Give the lease up cleanly, so a restart does not wait out the TTL.

    The row is kept and its heartbeat backdated rather than deleted, so the token keeps
    increasing across handovers and a stale holder stays fenced.
    """
    backdated = (utc_now() - timedelta(seconds=LEASE_TTL_SECONDS * 2)).isoformat(
        timespec="milliseconds"
    )
    result = connection.execute(
        update(writer_lease)
        .where(writer_lease.c.id == 1, writer_lease.c.token == handle.token)
        .values(heartbeat_at=backdated)
    )
    return result.rowcount == 1
