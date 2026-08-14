"""The event stream: one sender task per connection, bounded, and resumable.

**One sender task per connection.** The tick loop pushes unsolicited frames while the handler is
awaiting `receive`, so a single coroutine trying to do both would either block the push or drop
the receive. Splitting them is not an optimisation; without it the two directions deadlock.

**The send side carries its own close guard.** Sending after close raises, and the close can
happen between the queue pop and the send — so the guard has to live where the send is, not at
the top of the handler where it would already be stale.

**The outbound queue is bounded, and overflow closes the socket.** Buffering unboundedly to
protect a client that has stopped reading buys nothing: the recovery path already exists, because
the client resumes from its last sequence. Closing is faster, bounded in memory, and produces the
same end state.

**Resume is keyed on run *and* sequence.** A fork shares sequence values with its parent, so a
bare sequence is ambiguous — it could name two different events in two different runs. Beyond the
window the gateway returns a resync carrying a state snapshot and its sequence, and the client
hard-resets rather than replaying a month of history it cannot use.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any

from servicekit import logging as svclog

log = svclog.get_logger("gateway")

#: How many events a connection may fall behind before it is closed and told to resume.
OUTBOUND_QUEUE_LIMIT = 512

#: How far back a resume may reach. Beyond this the gateway sends a snapshot instead — replaying
#: a month of history to a client that only needs current state is slower than resetting it.
RESUME_WINDOW_EVENTS = 1000


class ResyncRequired(Exception):
    """The client is too far behind to catch up by replay."""


@dataclass(slots=True)
class Resume:
    """What a reconnecting client asked for, and what it is getting."""

    run_id: str
    after_seq: int
    #: True when the gap was too large and a snapshot is being sent instead.
    resync: bool
    head_seq: int
    backlog: list = None  # type: ignore[assignment]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "after_seq": self.after_seq,
            "resync": self.resync,
            "head_seq": self.head_seq,
            "backlog": len(self.backlog or []),
        }


def plan_resume(kernel, run_id: str, after_seq: int) -> Resume:
    """Decide whether this client can be caught up by replay, or needs a reset.

    Keyed on `(run_id, after_seq)`: asking the kernel for "everything after 40" without saying
    which run is a question with two answers once a fork exists.
    """
    head = kernel.head_seq(run_id)

    if after_seq > head:
        # A sequence ahead of the head means this client is talking about a different run —
        # most likely a parent's sequence against a forked child.
        raise ResyncRequired(
            f"sequence {after_seq} is ahead of run {run_id}'s head ({head}). A fork shares "
            "sequence values with its parent, so this resume is for a different run."
        )

    gap = head - after_seq
    if gap > RESUME_WINDOW_EVENTS:
        return Resume(run_id=run_id, after_seq=after_seq, resync=True, head_seq=head, backlog=[])

    backlog = kernel.read_events(run_id, after_seq=after_seq)
    return Resume(
        run_id=run_id, after_seq=after_seq, resync=False, head_seq=head, backlog=backlog
    )


class Connection:
    """One subscriber's outbound side.

    Deliberately not the WebSocket itself: the queue and the drop policy are testable without a
    socket, and the socket-specific part stays in `main.py` where the framework lives.
    """

    def __init__(self, run_id: str, limit: int = OUTBOUND_QUEUE_LIMIT) -> None:
        self.run_id = run_id
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=limit)
        self.closed = False
        self.dropped = False
        self.last_sent_seq = 0
        self.sender: asyncio.Task | None = None

    def offer(self, frame: Any) -> bool:
        """Hand a frame to the outbound queue, or mark this connection for closure.

        Returns False when the client is too far behind. The caller closes rather than growing
        the queue: the client's own resume is the recovery path.
        """
        if self.closed:
            return False
        try:
            self.queue.put_nowait(frame)
            return True
        except asyncio.QueueFull:
            self.dropped = True
            log.warning(
                "subscriber fell behind and is being closed; it can resume from its sequence",
                extra={"run": self.run_id, "last_sent_seq": self.last_sent_seq},
            )
            return False

    async def run_sender(self, send) -> None:
        """The one task that writes to the socket.

        Every send is guarded, because a close can land between the pop and the write. Without
        the guard the sender raises on a closed socket and takes the connection down in a way
        that looks like a server error rather than a client disconnect.
        """
        try:
            while not self.closed:
                frame = await self.queue.get()
                if frame is None:  # sentinel: shut down cleanly
                    return
                if self.closed:
                    return
                try:
                    await send(frame)
                except Exception:  # noqa: BLE001 - a closed socket is not a server error
                    self.closed = True
                    return
                if isinstance(frame, dict) and "seq" in frame:
                    self.last_sent_seq = int(frame["seq"])
        finally:
            self.closed = True

    async def close(self) -> None:
        self.closed = True
        with contextlib.suppress(asyncio.QueueFull):
            self.queue.put_nowait(None)
        if self.sender is not None:
            self.sender.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.sender
            self.sender = None


def envelope_frame(envelope) -> dict[str, Any]:
    """One event, as the client receives it.

    Sequence and tick go out as **strings**. They are `uint64`, and `JSON.parse` silently loses
    precision above 2^53 — the same reason the golden vectors carry their integers as strings.
    Anything the client does integer arithmetic on has to arrive in a form that survives the
    parse.
    """
    return {
        "kind": envelope.kind.name,
        "seq": str(envelope.seq),
        "tick": str(envelope.tick),
        "schema_ver": envelope.schema_ver,
        "rules_ver": envelope.rules_ver,
        "run_id": envelope.run_id,
        "command_id": envelope.command_id,
        "request_id": envelope.request_id,
        "payload": envelope.decoded_payload(),
    }
