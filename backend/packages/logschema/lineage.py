"""The tree of timelines one Genesis produced, as a query rather than a table (M49).

**No new table, and no recursive walk.** `runs` already carries the three columns a Universe needs:
`lineage_root_id` is flat across a whole tree, `parent_run_id` is the chain, and `forked_at_seq` is
where a child left its parent. So the tree is one select, and the only thing added here is the
reading of it — which is why the plan's own line is that this is a query the shape of the store
already admits.

**It lives in `logschema` because `simcore` may not import SQLAlchemy.** The sim is the fold and
knows nothing about rows; the tables are a contract shared by the kernel and the read side. That
boundary is enforced statically and by an import sweep in `tests/test_import_boundaries.py`, so this
module is where a store-shaped question about a lineage belongs and `simcore` is where it cannot go.

**The decision that separated two timelines is read from the log, one row per node.** A child's
divergence sits at `forked_at_seq + 1` in the child, and the decision it reconsidered sits at the
same sequence in the parent — U16's deliberate shape, and the reason both are addressable without a
join through anything mutable. One batched select covers every node in the tree, so a nine-timeline
lineage is two round trips rather than eighteen.

**Nothing here decides what a node looks like.** A row's rate, tick and terminal reason travel as
they are, and "active at day 4" or "ended: insolvent" is composed on the surface that draws it. A
query that returned a rendered state would be the second place the three states were defined, and
the first divergence would be a node the tree and the HUD described differently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contracts import canonical
from contracts.envelope import EventKind
from logschema.tables import event_log, runs
from sqlalchemy import Engine, func, select

#: How many timelines one lineage may hold, and therefore how many forks it will accept.
#:
#: **U16's review found there was no cap at all** — 25 sequential forks produced 25 children, 26
#: rows and 26 resident `RunLoop`s, every call answering 200 — and left it to this unit, as the
#: first one with a surface that can show a lineage's size. Sixteen is chosen for the surface rather
#: than for the store: it is what a tree can be drawn at and still be read, and the failure it
#: prevents is not memory but a Universe nobody can navigate. Two other costs come with it, and both
#: are why the number is small: `resume_all` folds every non-terminated run at startup, so N children
#: make a restart O(N x prefix), and each one holds a folded `State` for the life of the process.
#:
#: A cap this side of the fork means a refusal with a sentence rather than a process that slows down
#: and then stops. What it deliberately does *not* do is delete anything: eviction and deletion are
#: their own decisions about a player's history, and refusing to make more is the honest half that
#: does not need one.
MAX_TIMELINES_PER_LINEAGE = 16


@dataclass(frozen=True, slots=True)
class Node:
    """One timeline in the tree, as the store describes it.

    Every field is a row value or a figure read out of one logged event. Nothing is derived, and
    nothing is rendered: `rate` and `terminal_reason` are what a surface composes the three node
    states from, and `day` is the one convenience — the same `day_of` division the HUD does, done
    here so the tree and the HUD cannot disagree about which day a tick is in.
    """

    run_id: str
    parent_run_id: str
    #: The last sequence this child shares with its parent. Zero for the lineage root.
    forked_at_seq: int
    #: Where the clock is, and the sim-day it falls in.
    tick: int
    day: int
    rate: int
    head_seq: int
    terminal_reason: str
    created_at: str
    #: The decision that separated this timeline from its parent: the item, the checkpoint, the
    #: option this one took and the option its parent took. Empty for the root, which was not
    #: separated from anything.
    item: str = ""
    cp_index: int = -1
    option_index: int = -1
    parent_option_index: int = -1
    #: The two option labels, as the authored catalog spells them. Carried rather than left to a
    #: catalog lookup on the surface: a tree spans a lineage, and a client attached to one timeline
    #: naming another timeline's decision from its own genesis would be right only for as long as
    #: every timeline in a lineage is a run of one scenario. It is, today, by construction — and
    #: this way the tree says what separated two nodes without depending on that staying true.
    choice: str = ""
    parent_choice: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "parent_run_id": self.parent_run_id,
            "forked_at_seq": self.forked_at_seq,
            "tick": self.tick,
            "day": self.day,
            "rate": self.rate,
            "head_seq": self.head_seq,
            "terminal_reason": self.terminal_reason,
            "created_at": self.created_at,
            "item": self.item,
            "cp_index": self.cp_index,
            "option_index": self.option_index,
            "parent_option_index": self.parent_option_index,
            "choice": self.choice,
            "parent_choice": self.parent_choice,
        }


@dataclass(frozen=True, slots=True)
class Tree:
    """Every timeline descending from one Genesis, and which one was asked about."""

    root_run_id: str
    #: The run the caller named. On the payload so a client that reloaded into a child can mark
    #: where the player is standing without matching ids itself.
    asked_about: str
    nodes: tuple[Node, ...] = field(default_factory=tuple)

    def to_payload(self) -> dict[str, Any]:
        return {
            "root_run_id": self.root_run_id,
            "asked_about": self.asked_about,
            "cap": MAX_TIMELINES_PER_LINEAGE,
            "nodes": [node.to_payload() for node in self.nodes],
        }


def tree(engine: Engine, run_id: str, ticks_per_day: int) -> Tree | None:
    """The whole lineage the named run belongs to, or `None` if there is no such run.

    `ticks_per_day` is passed rather than imported, because the division is `simcore.time`'s
    convention and this package may not import the sim. Passing it keeps one definition of a
    sim-day in the tree, in the HUD and in the report — a second constant here is exactly the drift
    that would have the Universe say day 4 while the HUD said day 5.

    **A run with no forks is a one-node tree, not an empty one.** That matters for the surface: the
    Universe stage renders from the first run rather than being introduced to the player by their
    first fork, so a lineage of one has to come back as a lineage.

    Ordered by creation, then by run id. Stable under insertion, which is the property the drawn
    tree needs — a new fork must not reorder the nodes already on screen.
    """
    with engine.connect() as connection:
        row = (
            connection.execute(select(runs).where(runs.c.run_id == run_id)).mappings().first()
        )
        if row is None:
            return None

        root_id = str(row["lineage_root_id"])
        rows = (
            connection.execute(
                select(runs)
                .where(runs.c.lineage_root_id == root_id)
                .order_by(runs.c.created_at, runs.c.run_id)
            )
            .mappings()
            .all()
        )

        divergences = _divergences(connection, rows)

    nodes = tuple(
        Node(
            run_id=str(entry["run_id"]),
            parent_run_id=str(entry["parent_run_id"] or ""),
            forked_at_seq=int(entry["forked_at_seq"] or 0),
            tick=int(entry["current_tick"]),
            day=int(entry["current_tick"]) // ticks_per_day + 1,
            rate=int(entry["rate"]),
            head_seq=int(entry["head_seq"]),
            terminal_reason=str(entry["terminal_reason"] or ""),
            created_at=str(entry["created_at"]),
            **divergences.get(str(entry["run_id"]), {}),
        )
        for entry in rows
    )
    return Tree(root_run_id=root_id, asked_about=run_id, nodes=nodes)


def size(engine: Engine, lineage_root_id: str) -> int:
    """How many timelines this lineage already holds.

    Read at the fork rather than tracked on a counter, because the rows are the truth and a counter
    is a second one. It is a count over an indexed equality on a table with one row per timeline, so
    the cheap-enough argument does not need making.
    """
    with engine.connect() as connection:
        return int(
            connection.execute(
                select(func.count(runs.c.run_id)).where(runs.c.lineage_root_id == lineage_root_id)
            ).scalar_one()
        )


def running_in_lineage(engine: Engine, lineage_root_id: str) -> list[str]:
    """The timelines in this lineage whose stored rate is not zero, oldest first.

    What `resume_all` reads to refuse starting a second clock in one lineage after a restart. The
    rate is run state (R18) so a restart is meant to pick it up — but a crash between the two appends
    of a switch can leave two rows claiming to be running, and resuming both would have one lineage
    advancing two timelines with the player watching one of them.
    """
    with engine.connect() as connection:
        rows = connection.execute(
            select(runs.c.run_id)
            .where(
                runs.c.lineage_root_id == lineage_root_id,
                runs.c.rate != 0,
                # Coalesced rather than `IS NULL`, because a terminal reason is written as an empty
                # string on one path and left null on another. A predicate that only knew about one
                # of them would quietly resume an ended timeline's clock.
                func.coalesce(runs.c.terminal_reason, "") == "",
            )
            .order_by(runs.c.created_at, runs.c.run_id)
        ).all()
    return [str(row[0]) for row in rows]


def _divergences(connection: Any, rows: Any) -> dict[str, dict[str, Any]]:
    """The decision that separated each child from its parent, keyed by child run id.

    One select for the whole tree. A child's divergence is at `forked_at_seq + 1` in the child, and
    the decision it reconsidered is at the same sequence in the parent — so both sides come out of
    one `IN` over the same pairs, and a lineage of nine costs one query rather than eighteen.

    A node whose event is missing simply carries no decision. That is not a defect to raise: the
    root has none by construction, and a child whose divergence has been trimmed by a store this
    build did not write is a tree that should still draw.
    """
    wanted: list[tuple[str, int]] = []
    for entry in rows:
        parent = str(entry["parent_run_id"] or "")
        if not parent:
            continue
        at_seq = int(entry["forked_at_seq"] or 0) + 1
        wanted.append((str(entry["run_id"]), at_seq))
        wanted.append((parent, at_seq))

    if not wanted:
        return {}

    found: dict[tuple[str, int], dict[str, Any]] = {}
    resolved = connection.execute(
        select(event_log.c.run_id, event_log.c.seq, event_log.c.payload).where(
            event_log.c.kind == int(EventKind.DECISION_RESOLVED),
            event_log.c.run_id.in_({run_id for run_id, _ in wanted}),
            event_log.c.seq.in_({seq for _, seq in wanted}),
        )
    ).all()
    for run_id, seq, payload in resolved:
        found[(str(run_id), int(seq))] = canonical.decode(payload)

    divergences: dict[str, dict[str, Any]] = {}
    for entry in rows:
        parent = str(entry["parent_run_id"] or "")
        if not parent:
            continue
        child_id = str(entry["run_id"])
        at_seq = int(entry["forked_at_seq"] or 0) + 1
        mine = found.get((child_id, at_seq))
        theirs = found.get((parent, at_seq))
        if mine is None and theirs is None:
            continue
        source = mine or theirs or {}
        divergences[child_id] = {
            "item": str(source.get("item", "")),
            "cp_index": int(source.get("cp_index", -1)),
            "option_index": int((mine or {}).get("option_index", -1)),
            "parent_option_index": int((theirs or {}).get("option_index", -1)),
            "choice": str((mine or {}).get("choice", "")),
            "parent_choice": str((theirs or {}).get("choice", "")),
        }
    return divergences
