"""The timeline diff: two futures at one sim-day, and the decision that separated them (U18).

M50 and M52, and R12 under both. The claims worth pinning here are the ones a screenshot cannot
show:

* **one tick, both sides.** "At the same sim-day" is a day's *first* tick, which either timeline
  has reached or has not. A diff that folded one side forward through ticks it never ran would
  put an invention beside a history, and nothing on the surface would say which was which;
* **the figures are the kernel's fold, not a second reading of the log** (R12). Asserted against
  the state hash the kernel itself wrote into the log at that boundary, which is the strongest
  available form of "these are the same numbers";
* **the separating decision is named where the two actually parted.** Two cousins share the
  lineage root, but the root is not where they diverged — naming its fork would name a decision
  neither of them took;
* **a diff that would mislead is refused with a reason** rather than rendered. A timeline against
  itself, a run from another Universe, and a day one side has not reached are three ways to ask
  for a diff that cannot be honest, and each gets its own sentence.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from contracts import canonical
from contracts.envelope import Envelope, EventKind, build as build_envelope
from logschema import lineage
from report import fold as reporting
from simcore import hashing
from simcore import log as folder
from simcore import step as sim
from simcore import time as simtime
from simcore import verify as verifier
from simcore.rates import RULES_VERSION

SEED = 0xC0FFEE
RUN = "run-diff"


# =========================================================================
# A run, without a store
# =========================================================================


class Recorder:
    """One timeline, driven in-process and recorded exactly as the kernel records it.

    The day checkpoint is emitted here for the same reason the kernel's loop emits it — the log
    is what the diff folds, and a log with no boundary hashes could not be used to check that the
    diff folded to the state the kernel was in.
    """

    def __init__(self, run_id: str, horizon_tick: int | None = None) -> None:
        self.run_id = run_id
        self.state, genesis = sim.new_run(run_seed=SEED, horizon_tick=horizon_tick)
        self.log: list[Envelope] = []
        self._seq = 0
        self.record(genesis)

    def record(self, emitted: list[sim.Emitted]) -> None:
        for item in emitted:
            self._seq += 1
            self.log.append(
                build_envelope(
                    seq=self._seq,
                    tick=int(item.payload.get("tick", self.state.tick)),
                    kind=item.kind,
                    rules_ver=RULES_VERSION,
                    payload=item.payload,
                    run_id=self.run_id,
                    request_id=item.request_id,
                )
            )

    def advance(self, ticks: int) -> None:
        for _ in range(ticks):
            emitted = sim.step(self.state)
            if simtime.is_day_boundary(self.state.tick) and self.state.tick > 0:
                emitted.append(
                    sim.Emitted(
                        kind=EventKind.DAY_CHECKPOINT,
                        payload=verifier.build_checkpoint_payload(self.state),
                    )
                )
            self.record(emitted)

    def advance_until(self, predicate, limit: int = 60_000) -> None:
        for _ in range(limit):
            if predicate(self.state):
                return
            self.advance(1)
        raise AssertionError(f"condition never held within {limit} ticks")

    def settle(self, option_index: int) -> None:
        """Drive to the first checkpoint of the first authored item and settle it."""
        self.record(sim.assign_direct(self.state, "wi_ap_map", "stf_ap"))
        self.advance_until(lambda s: s.items["wi_ap_map"].status == "blocked")
        self.record(
            sim.resolve_checkpoint(self.state, "wi_ap_map", 0, option_index, in_person=True)
        )

    def timeline(self) -> reporting.TimelineLog:
        return reporting.TimelineLog(
            run_id=self.run_id, events=self.log, current_tick=self.state.tick
        )


def _played(run_id: str, option_index: int, days: int) -> Recorder:
    recorder = Recorder(run_id)
    recorder.settle(option_index)
    recorder.advance_until(lambda s: s.tick >= simtime.tick_of_day_start(days))
    return recorder


def _two_timelines(days: int = 4) -> tuple[reporting.TimelineLog, reporting.TimelineLog]:
    """One decision, taken two ways, played the same distance forward.

    Two runs of one seed rather than a real fork, because nothing in this section needs a store:
    a fork's child *is* a run whose log begins with its parent's prefix, so two runs settling one
    checkpoint differently are the same two logs the diff would be handed.
    """
    return (
        _played(f"{RUN}-left", 0, days).timeline(),
        _played(f"{RUN}-right", 1, days).timeline(),
    )


# =========================================================================
# Which decision separated two timelines
# =========================================================================


def _node(run_id: str, parent: str = "", **overrides) -> lineage.Node:
    fields: dict = {
        "run_id": run_id,
        "parent_run_id": parent,
        "forked_at_seq": 0,
        "tick": 0,
        "day": 1,
        "rate": 0,
        "head_seq": 0,
        "terminal_reason": "",
        "created_at": "2026-08-27T00:00:00.000+00:00",
    }
    fields.update(overrides)
    return lineage.Node(**fields)


def _fork_of(
    run_id: str, parent: str, at_seq: int, item: str, mine: int, theirs: int
) -> lineage.Node:
    return _node(
        run_id,
        parent,
        forked_at_seq=at_seq,
        item=item,
        cp_index=0,
        option_index=mine,
        parent_option_index=theirs,
        choice=f"option {mine}",
        parent_choice=f"option {theirs}",
    )


def _tree(*nodes: lineage.Node) -> lineage.Tree:
    return lineage.Tree(root_run_id=nodes[0].run_id, asked_about=nodes[0].run_id, nodes=nodes)


def test_a_child_and_its_parent_are_separated_by_one_decision() -> None:
    """The plain case, and the one the demo shot is of: one decision, two options."""
    tree = _tree(_node("root"), _fork_of("child", "root", 7, "wi_ap_map", 1, 0))

    separation = lineage.separating_decision(tree, "root", "child")

    assert separation is not None
    assert separation.at_run_id == "root", "they parted in the parent, not somewhere above it"
    assert separation.shared, "one decision, taken two ways"
    (parting,) = separation.partings
    assert parting.side == "right", "the child is the side that turned"
    assert parting.item == "wi_ap_map"
    assert parting.at_seq == 8, "one past the last sequence the two shared"
    assert (parting.choice, parting.parent_choice) == ("option 1", "option 0")


def test_the_side_a_parting_belongs_to_follows_the_order_asked_in() -> None:
    """`left` and `right` are the caller's words, so they cannot be positional by accident."""
    tree = _tree(_node("root"), _fork_of("child", "root", 7, "wi_ap_map", 1, 0))

    (parting,) = lineage.separating_decision(tree, "child", "root").partings

    assert parting.side == "left"


def test_two_forks_of_one_decision_are_named_once_with_both_options() -> None:
    """The mechanic U16's id fix exists for: one checkpoint, two alternatives, two timelines."""
    tree = _tree(
        _node("root"),
        _fork_of("a", "root", 7, "wi_ap_map", 1, 0),
        _fork_of("b", "root", 7, "wi_ap_map", 2, 0),
    )

    separation = lineage.separating_decision(tree, "a", "b")

    assert separation.at_run_id == "root"
    assert separation.shared, "both turned at the same checkpoint, so it is one decision"
    assert [parting.choice for parting in separation.partings] == ["option 1", "option 2"]
    assert {parting.parent_choice for parting in separation.partings} == {"option 0"}, (
        "the timeline they both left took a third option, and the surface can say so"
    )


def test_two_cousins_are_named_by_the_decisions_at_their_nearest_common_ancestor() -> None:
    """Covers the case a single name would get wrong.

    A grandchild of one fork and a child of another share only the root. There is no one decision
    either of them took, so naming one would name a decision that is not what separated them.
    """
    tree = _tree(
        _node("root"),
        _fork_of("a", "root", 7, "wi_ap_map", 1, 0),
        _fork_of("b", "root", 40, "wi_fin_close", 1, 0),
        _fork_of("a2", "a", 60, "wi_ops_rota", 1, 0),
    )

    separation = lineage.separating_decision(tree, "a2", "b")

    assert separation.at_run_id == "root", "the nearest common ancestor, not either parent"
    assert not separation.shared, "two decisions, and neither side took the other's"
    named = {parting.side: parting.item for parting in separation.partings}
    assert named == {"left": "wi_ap_map", "right": "wi_fin_close"}, (
        "each side is named by the decision it left the ancestor at, not by its own last fork"
    )


def test_a_deeper_common_ancestor_wins_over_the_root() -> None:
    """Two forks of one child part in that child, not in the Genesis above it."""
    tree = _tree(
        _node("root"),
        _fork_of("a", "root", 7, "wi_ap_map", 1, 0),
        _fork_of("a1", "a", 40, "wi_fin_close", 1, 0),
        _fork_of("a2", "a", 60, "wi_ops_rota", 1, 0),
    )

    separation = lineage.separating_decision(tree, "a1", "a2")

    assert separation.at_run_id == "a"
    assert not separation.shared
    assert {parting.item for parting in separation.partings} == {"wi_fin_close", "wi_ops_rota"}


def test_a_timeline_has_nothing_separating_it_from_itself() -> None:
    tree = _tree(_node("root"), _fork_of("child", "root", 7, "wi_ap_map", 1, 0))

    assert lineage.separating_decision(tree, "child", "child") is None


def test_a_run_this_tree_does_not_hold_cannot_be_separated_from_one_it_does() -> None:
    tree = _tree(_node("root"), _fork_of("child", "root", 7, "wi_ap_map", 1, 0))

    assert lineage.separating_decision(tree, "root", "somewhere-else") is None


def test_a_trimmed_divergence_names_nothing_rather_than_raising() -> None:
    """A store this build did not write. The tree still draws; the diff simply cannot name it."""
    tree = _tree(_node("root"), _node("child", "root", forked_at_seq=7))

    assert lineage.separating_decision(tree, "root", "child") is None


def test_an_ancestry_walk_survives_a_parent_that_is_not_in_the_tree() -> None:
    """The same guard the drawn tree's depth walk has, for the same malformed store."""
    nodes = {"child": _node("child", "gone")}

    assert lineage.ancestry(nodes, "child") == ["child"]


# =========================================================================
# Folding two timelines to one day
# =========================================================================


def test_both_sides_are_folded_to_the_same_tick() -> None:
    """"At the same sim-day" is one tick, not two. Everything else here rests on it."""
    left, right = _two_timelines()

    answer = reporting.diff(left, right, day=3)

    assert answer.at_tick == simtime.tick_of_day_start(3) == 1080
    assert answer.left is not None and answer.right is not None
    for side, timeline in ((answer.left, left), (answer.right, right)):
        state, _ = reporting.state_at_day(timeline.events, 3)
        assert state.tick == answer.at_tick


def test_the_day_defaults_to_the_furthest_both_sides_have_reached() -> None:
    """The newest point the two can honestly be compared at, and the bound on the control."""
    left = _played(f"{RUN}-long", 0, 5).timeline()
    right = _played(f"{RUN}-short", 1, 3).timeline()

    answer = reporting.diff(left, right)

    assert right.current_day < left.current_day
    assert answer.max_day == right.current_day
    assert answer.day == answer.max_day
    assert answer.refusal == ""


def test_the_figures_are_the_state_the_kernel_hashed_at_that_boundary() -> None:
    """Covers R12. The strongest available form of "the diff folds what the kernel folded".

    The kernel writes a state hash into the log at every day boundary. Folding the same log to
    the same tick and hashing the result reproduces it — so a second reconstruction anywhere in
    this path would have to reproduce the kernel's hash to pass, which is another way of saying
    it would have to be the same fold.
    """
    recorder = _played(f"{RUN}-hashed", 0, 4)
    at_tick = simtime.tick_of_day_start(3)

    recorded = {
        int(envelope.decoded_payload()["tick"]): envelope.decoded_payload()["state_hash"]
        for envelope in recorder.log
        if envelope.kind is EventKind.DAY_CHECKPOINT
    }
    assert at_tick in recorded, "the run reached the boundary being compared at"

    state, _ = reporting.state_at_day(recorder.log, 3)

    assert hashing.state_hash(sim.snapshot(state)).overall == recorded[at_tick]


def test_the_diff_carries_the_hash_it_folded_to_on_each_side() -> None:
    """So a reader can check the claim above against the log rather than take it on trust."""
    left, right = _two_timelines()

    answer = reporting.diff(left, right, day=3)

    assert answer.left.state_hash and answer.right.state_hash
    assert answer.left.state_hash != answer.right.state_hash, (
        "the two timelines settled one checkpoint differently; their states cannot be equal"
    )


def test_before_they_diverged_the_two_timelines_are_the_same_state() -> None:
    """Day 1 is Genesis on both sides, so the diff over it is all zeroes and two equal hashes.

    Worth pinning because it is the one case where "no difference" is the correct answer, and a
    diff that manufactured one — by folding the two to different ticks, say — would be caught
    here and nowhere else.
    """
    left, right = _two_timelines()

    answer = reporting.diff(left, right, day=1)

    assert answer.at_tick == 0
    assert answer.left.state_hash == answer.right.state_hash
    assert {row.delta for row in answer.rows} == {0}


def test_a_straddling_ceo_input_folds_to_the_kernels_own_hash_either_way() -> None:
    """The case U18 sidestepped and U19 closed, now asserted from the other side.

    A `CEO_INPUT` names the tick it *applies* at, a few ticks ahead of the one it was submitted
    on — so a player holding a direction across a day boundary leaves an event naming a tick past
    it. U18 measured a sequence cut being refused here, and filtered the event out to get past it;
    the filter then dropped an input the kernel's own state held, so this fold's hash at such a
    boundary was not the kernel's.

    Both halves are asserted, because only together do they say the defect is gone rather than
    moved: the sequence cut folds, and the day fold reproduces the byte the log's own
    `DAY_CHECKPOINT` carries.
    """
    boundary = simtime.TICKS_PER_SIM_DAY
    recorder = Recorder(f"{RUN}-walking")
    recorder.advance_until(lambda s: s.tick >= boundary - 4)
    recorder.record(sim.submit_ceo_input(recorder.state, at_tick=boundary + 2, bitmask=1))
    recorder.advance(14)

    recorded = _checkpoint_at(recorder.log, boundary)
    assert recorded, "the recorder writes a checkpoint at the boundary this test is about"

    cut = max(envelope.seq for envelope in recorder.log if envelope.tick <= boundary)
    by_cut = folder.fold(
        [envelope for envelope in recorder.log if envelope.seq <= cut],
        at_live_head=False,
        through_tick=boundary,
    )
    assert by_cut.state.tick == boundary

    state, _ = reporting.state_at_day(recorder.log, 2)
    assert state.tick == boundary
    assert hashing.state_hash(sim.snapshot(state)).overall == recorded
    assert hashing.state_hash(sim.snapshot(by_cut.state)).overall == recorded


def _checkpoint_at(log: list, tick: int) -> str:
    """The state hash the kernel wrote at that boundary, or "" if it wrote none."""
    for envelope in log:
        if envelope.kind is EventKind.DAY_CHECKPOINT:
            payload = envelope.decoded_payload()
            if int(payload["tick"]) == tick:
                return str(payload["state_hash"])
    return ""


# =========================================================================
# What the diff says
# =========================================================================


def test_the_diff_reports_every_metric_the_run_defines_and_the_runway() -> None:
    """Covers M50's "metric by metric", and the execution decision that fixed the column set.

    The set is the comparison surface's rather than a second one invented here, which means it is
    the kernel's metric table plus the runway a branch column already states.
    """
    from simcore import effects

    left, right = _two_timelines()

    answer = reporting.diff(left, right)

    keys = [row.key for row in answer.rows]
    assert keys == [str(m["key"]) for m in effects.metric_defs_to_state()] + ["runway"]


def test_every_figure_in_the_diff_is_marked_as_authored_tuning() -> None:
    """Covers M52 on the wire. The client's own sweep covers it on the surface."""
    left, right = _two_timelines()

    answer = reporting.diff(left, right).to_dict()

    assert answer["every_number_is"] == reporting.AUTHORED
    assert answer["rows"], "an unmarked figure cannot hide in an empty list"
    for row in answer["rows"]:
        assert row["basis"] == reporting.AUTHORED, row


def test_a_metric_carries_the_direction_that_counts_as_an_improvement() -> None:
    """Cash rising and manual hours falling are both wins, and the surface must not guess."""
    left, right = _two_timelines()

    rows = {row.key: row for row in reporting.diff(left, right).rows}

    assert rows["cash"].good == 1
    assert rows["manualHours"].good == -1


def test_the_runway_states_that_it_has_no_favourable_direction() -> None:
    """The comparison surface renders runway neutral because the wire gives it no `good`.

    A diff that decided rising runway were favourable would be a second answer to a question the
    kernel deliberately does not answer, so it says "none" rather than choosing.
    """
    left, right = _two_timelines()

    rows = {row.key: row for row in reporting.diff(left, right).rows}

    assert rows["runway"].good == reporting.NO_DIRECTION == 0


def test_the_settled_option_moves_at_least_one_figure_apart() -> None:
    """Otherwise every assertion above would hold over two identical columns."""
    left, right = _two_timelines()

    answer = reporting.diff(left, right)

    assert any(row.delta not in (0, None) for row in answer.rows), (
        f"the two timelines are indistinguishable: {[row.to_dict() for row in answer.rows]}"
    )


def test_a_timeline_that_had_ended_by_that_day_says_so_from_its_own_fold() -> None:
    """The defect this closes was live, and neither the row nor the log could have caught it.

    Measured on the compose path: two timelines that had both reached their horizon carried
    `terminal_reason` NULL and rates of 1 and 3 in `runs`, and **no terminal event in either
    log** — the fact existed only in the kernel's folded state, and the Universe tree showed it
    only because the kernel overlays its own fold on the rows. The report cannot do that (R4), so
    the column would have read "running" over two histories that were over.

    Reading it from the diff's own fold is exact, free, and answers the better question: whether
    this timeline had ended *by the day being compared at*.
    """
    horizon = simtime.TICKS_PER_SIM_DAY * 3
    ended = Recorder(f"{RUN}-ended", horizon_tick=horizon)
    ended.advance_until(lambda s: s.terminal_reason != "")
    assert ended.state.terminal_reason == "horizon"

    # The row the report would otherwise have believed: still running, nothing terminal on it.
    row_says_running = reporting.TimelineLog(
        run_id=ended.run_id, events=ended.log, current_tick=ended.state.tick
    )
    other = _played(f"{RUN}-alongside", 1, 4).timeline()

    answer = reporting.diff(row_says_running, other)

    assert answer.left is not None
    assert answer.left.terminal_reason == "horizon"
    assert answer.right.terminal_reason == "", "the timeline still going says nothing terminal"


def test_a_timeline_is_known_to_have_reached_what_its_own_log_proves() -> None:
    """A run row lags its clock, and the log is a floor under it.

    U17 measured a run at tick 58 whose row said tick 1. The bound on the day control comes from
    these readings, so a bound taken from the row alone would refuse days both timelines plainly
    reached — and the log is evidence rather than a guess: an event stamped at tick T is proof
    the run got to T.
    """
    played = _played(f"{RUN}-lagging", 0, 4)
    lagging = reporting.TimelineLog(run_id=played.run_id, events=played.log, current_tick=1)

    assert lagging.reached_tick == max(event.tick for event in played.log)
    assert lagging.current_day == simtime.day_of(lagging.reached_tick) > 1


# =========================================================================
# The three ways a diff is refused
# =========================================================================


def test_a_timeline_against_itself_is_refused_with_a_reason() -> None:
    left, _ = _two_timelines()

    answer = reporting.diff(left, left)

    assert left.run_id in answer.refusal
    assert answer.rows == [] and answer.left is None and answer.right is None


def test_a_day_one_side_has_not_reached_is_refused_rather_than_extrapolated() -> None:
    """The whole reason the comparison point is a day's first tick.

    Folding the shorter timeline to a day it never reached would run its simulation forward
    through ticks that are not in its history and report the result beside the other side's
    facts. So the answer is the reason, and it names which timeline and how far it got.
    """
    left = _played(f"{RUN}-ahead", 0, 6).timeline()
    right = _played(f"{RUN}-behind", 1, 2).timeline()

    answer = reporting.diff(left, right, day=5)

    assert answer.rows == []
    assert right.run_id in answer.refusal
    assert str(right.current_day) in answer.refusal
    assert answer.max_day == right.current_day, "and the day the control should go back to"


def test_a_day_before_the_first_is_refused() -> None:
    left, right = _two_timelines()

    answer = reporting.diff(left, right, day=0)

    assert "numbered from 1" in answer.refusal
    assert answer.rows == []


def test_a_refusal_still_names_the_decision_that_separated_them() -> None:
    """The reason the diff cannot be drawn does not make the two timelines unrelated."""
    left, right = _two_timelines()
    separation = {"at_run_id": "root", "shared": True, "partings": []}

    answer = reporting.diff(left, right, day=99, separation=separation)

    assert answer.refusal
    assert answer.separation == separation


# =========================================================================
# The published surface
# =========================================================================
#
# Driven through `single_process.compose()`, which is the composition the one command ships. The
# diff answers on the report app the launcher mounts, so the path the client uses — `/report/...`
# behind the client's own `/api` proxy — is the path these exercise.


@pytest.fixture
def composed(tmp_path, monkeypatch):
    """A kernel, a gateway and the mounted report in one process, with a run ready to drive."""
    monkeypatch.setenv("COMPANY_OS_STORE_URL", f"sqlite:///{tmp_path}/diff.sqlite3")

    from agents import main as agents_main
    from gateway.commands import CommandLedger

    import single_process

    # Per-process handles built on first use, and both are built inside `compose()`. Left over
    # from another test they would be pointed at that test's store — see `test_gateway`, which
    # states the same clearing and why forgetting it is worse than merely wrong.
    agents_main._LEDGER = None
    agents_main._dispose_log_engine()

    runtime, _ = single_process.compose()
    runtime.create_run(RUN, SEED, horizon_tick=simtime.TICKS_PER_SIM_DAY * 30)

    from gateway import main as gateway_main

    gateway_main._ledger = CommandLedger()

    yield runtime
    runtime.writer.stop()
    agents_main.ledger().dispose()


@pytest.fixture
def api(composed):
    from gateway import main as gateway_main

    with TestClient(gateway_main.app) as client:
        yield client


def _kind(name: str) -> int:
    from contracts.grpc import kernel_pb2

    return getattr(kernel_pb2, name)


def _a_settled_decision(runtime, api) -> int:
    """Drive the run to a checkpoint, settle it, and answer with the resolution's sequence."""
    run = runtime.runs[RUN]
    runtime.apply_command(
        RUN,
        _kind("ASSIGN_WORK"),
        canonical.encode({"item": "wi_ap_map", "person": "stf_ap", "via_manager": False}),
    )
    while run.state.items["wi_ap_map"].status != "blocked":
        runtime._advance(run, 1)

    applied = api.post(
        f"/runs/{RUN}/commands",
        json={
            "kind": "resolve_checkpoint",
            "payload": {
                "item": "wi_ap_map",
                "cp_index": 0,
                "option_index": 0,
                "in_person": True,
            },
            "idempotency_key": "settle-it",
        },
    ).json()
    assert applied["status"] == "applied", applied
    return applied["produced_seq"][0]


def _a_fork(runtime, api, days: int = 2) -> str:
    """A second timeline that took the other option, with both sides played some days on."""
    decision_seq = _a_settled_decision(runtime, api)
    forked = api.post(
        f"/runs/{RUN}/fork",
        json={"at_seq": decision_seq, "option_index": 1, "idempotency_key": "u18-a"},
    ).json()
    assert not forked.get("refusal"), forked
    child = forked["child_run_id"]

    for run_id in (RUN, child):
        run = runtime.runs[run_id]
        while run.state.tick < simtime.tick_of_day_start(days):
            runtime._advance(run, 1)
    return child


def test_the_diff_answers_on_the_report_surface_the_launcher_mounts(composed, api) -> None:
    """Covers M50 through the published surface, which is where a player meets it.

    On `/report` rather than on the gateway's own namespace: the diff is a fold, the fold lives
    in the report service, and the gateway may not import it (R4, R28).
    """
    child = _a_fork(composed, api)

    answered = api.get(f"/report/runs/{RUN}/diff/{child}")

    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert body["refusal"] == ""
    assert body["left"]["run_id"] == RUN and body["right"]["run_id"] == child
    assert body["day"] == body["max_day"] >= 2
    assert body["at_tick"] == simtime.tick_of_day_start(body["day"])
    assert [row["key"] for row in body["rows"]][-1] == "runway"

    assert api.get(f"/runs/{RUN}/diff/{child}").status_code == 404, (
        "the diff answered on the client's own namespace, which the gateway owns"
    )


def test_the_published_diff_names_the_decision_with_both_option_labels(composed, api) -> None:
    """M50's second half. No catalog lookup on the surface: the labels arrive with the answer."""
    child = _a_fork(composed, api)

    separation = api.get(f"/report/runs/{RUN}/diff/{child}").json()["separation"]

    assert separation["at_run_id"] == RUN
    assert separation["shared"] is True
    (parting,) = separation["partings"]
    assert parting["side"] == "right" and parting["run_id"] == child
    assert parting["item"] == "wi_ap_map"
    assert parting["choice"] and parting["parent_choice"]
    assert parting["choice"] != parting["parent_choice"]


def test_every_published_figure_carries_its_marking_and_its_direction(composed, api) -> None:
    """Covers M52 on the wire."""
    child = _a_fork(composed, api)

    body = api.get(f"/report/runs/{RUN}/diff/{child}").json()

    assert body["every_number_is"] == reporting.AUTHORED
    for row in body["rows"]:
        assert row["basis"] == reporting.AUTHORED, row
        assert set(row) >= {"key", "label", "unit", "good", "left", "right", "delta"}, row


def test_the_two_sides_name_the_sequence_each_figure_was_folded_through(composed, api) -> None:
    """Covers R12 on the wire: a figure resolves to a run and a position in its log."""
    child = _a_fork(composed, api)

    body = api.get(f"/report/runs/{RUN}/diff/{child}").json()

    for side in (body["left"], body["right"]):
        assert side["through_seq"] > 0
        assert side["state_hash"]
        assert side["current_day"] >= body["day"], "a side folded past where its clock got to"


def test_a_diff_of_a_timeline_against_itself_is_refused_with_a_sentence(composed, api) -> None:
    """A 200 and a reason, like a refused switch: the request was well-formed, the answer is no."""
    refused = api.get(f"/report/runs/{RUN}/diff/{RUN}")

    assert refused.status_code == 200
    body = refused.json()
    assert RUN in body["refusal"]
    assert body["rows"] == []


def test_a_diff_across_two_lineages_is_refused_and_names_the_root(composed, api) -> None:
    """Two Universes have no Genesis in common, so there is no day they both reached."""
    composed.create_run("run-elsewhere", SEED + 1)

    body = api.get(f"/report/runs/{RUN}/diff/run-elsewhere").json()

    assert "run-elsewhere" in body["refusal"]
    assert RUN in body["refusal"], "the Universe the caller is in, so the sentence is actionable"
    assert body["rows"] == []


def test_a_diff_of_an_unknown_run_is_not_found(composed, api) -> None:
    """Not a refusal: there is no Universe to say anything about."""
    assert api.get(f"/report/runs/run-nowhere/diff/{RUN}").status_code == 404


def test_a_day_past_the_shorter_timeline_is_refused_through_the_route(composed, api) -> None:
    child = _a_fork(composed, api)

    body = api.get(f"/report/runs/{RUN}/diff/{child}?day=99").json()

    assert body["rows"] == []
    assert body["refusal"]
    assert body["max_day"] >= 1, "and the answer says where the control should go back to"


def test_a_day_below_one_is_the_callers_mistake_rather_than_a_refusal(composed, api) -> None:
    """Nothing on the surface can produce it, so it is a malformed request rather than an answer."""
    child = _a_fork(composed, api)

    assert api.get(f"/report/runs/{RUN}/diff/{child}?day=0").status_code == 422
