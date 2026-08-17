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

#: How often the spend counter is re-read for a connected client. Wall-clock, not ticks:
#: what a run has spent is not sim state and does not advance with the clock, so a paused
#: run whose last provider call is still in flight still has a figure that moves.
#:
#: Two seconds because the figure is a bookkeeping display rather than a control: fast
#: enough that a briefing's cost lands while the player is still looking at the briefing,
#: slow enough that one store query per client per interval is not worth thinking about.
#:
#: Passed in by the route rather than read as a default inside `publish_spend`, so the
#: cadence is resolved when a connection opens instead of when this module was imported.
SPEND_POLL_SECONDS = 2.0


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


async def publish_spend(connection: Connection, run_id: str, read, interval: float) -> None:
    """Publish `MODEL_SPEND` for one connection, whenever the figures move (M28).

    **A control frame, never an event, and that is not a stylistic choice.** What a call
    cost depends on which provider answered and what it counted, so an event carrying it
    would be an output the fold cannot reproduce — strict replay would then fail on every
    run that used the bench. The counter is bookkeeping beside the log, so it travels on
    the same channel `POSITION_ECHO` uses for derived state the log knows nothing about,
    and it carries no sequence.

    **Polled rather than pushed, deliberately.** The counter is written by the agents
    surface on a worker thread while a provider call completes; a push would mean that
    thread reaching into a per-connection queue on the event loop, which is the hazard
    `_echo_position` already has and which nothing here needs to acquire a second time. A
    read on an interval also self-heals: a dropped frame is corrected two seconds later
    rather than leaving the tile permanently behind, which is what the client's
    "read, never accumulated" reducer is written against.

    **Sent only when it changed.** A keyless run's figures never move, so after the first
    frame — which the tile does need, because it carries the ceiling and the bench-absent
    state — this goes quiet rather than putting an identical frame on the wire every two
    seconds for the length of the run.
    """
    last: dict[str, Any] | None = None

    while not connection.closed:
        try:
            # Off the event loop: the reader is a store query, and the tick loop's
            # publishes and every socket's sends share this loop.
            payload = await asyncio.to_thread(read, run_id)
        except Exception as exc:  # noqa: BLE001 - an unreadable counter is not a dead stream
            # Logged once per occurrence and then retried. The run is unaffected: nothing
            # in this loop can stop a simulation, and a tile that stops moving is a better
            # failure than a stream that closes because a display could not be read.
            log.warning(
                "could not read the model spend for a subscriber",
                extra={"run": run_id, "error": str(exc)},
            )
            payload = None

        if payload is not None and payload != last:
            last = payload
            if not connection.offer({"kind": "MODEL_SPEND", **payload}):
                await connection.close()
                return

        await asyncio.sleep(interval)


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
