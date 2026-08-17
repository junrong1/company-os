"""The work: every item runs into a point only the CEO can settle.

Ported from script section 3 of `company-os.html` (`:1279`).

Two representational changes, both forced by R6.

**Checkpoint thresholds are integer percent, not fractions.** The prototype writes
`at: 0.5`, `at: 0.45`, `at: 0.3`. Every one of those converts exactly to percent, and
the comparison is done by cross-multiplication (`done * 100 >= at_percent * total`)
rather than by dividing — so there is no truncation and no float anywhere on the path
that decides when someone stops and waits for you.

**Effort is counted in sim-seconds, not hours.** The prototype adds
`hours * (director ? 0.8 : 1)` to a float accumulator. Here one tick of work is 60
units and a director's tick is `60 * 4/5 = 48` — both exact integers. Sim-seconds are
fine enough that U7's further multipliers still land on whole numbers in the cases
that matter, and coarse enough that the numbers stay readable in a log.

**The work graph itself is no longer here.** `ITEMS` and `SEEDED_ASSIGNMENTS` were module
constants until U6; a company is a file now, and the catalog is reached through
`state.scenario`. What remains is the *shape* of an item and the arithmetic over it —
thresholds, effort units, the copy generated from a draw figure — which is rules rather than
content, and which every scenario is measured by rather than authoring for itself.

The shipped company carries nine checkpoints across eight items. That is a run's decision
supply, and it is what bounds a useful horizon — baseline load regenerates forever, but a
horizon longer than the decisions on offer is burn with nothing to decide. It is read off the
loaded scenario (`Scenario.total_checkpoints`), so a second company gets its own bound.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from simcore.scenario import Scenario

#: One sim-hour of effort. A tick is one sim-minute, so a tick of work is 60 units.
EFFORT_UNITS_PER_SIM_HOUR = 3600
EFFORT_UNITS_PER_TICK = EFFORT_UNITS_PER_SIM_HOUR // 60  # 60

#: The prototype's `progress > 0.2` gate on the cross-department meeting.
VISIT_MEETING_THRESHOLD_PERCENT = 20

#: The prototype's `p.metHours >= 2`.
MEETING_SIM_HOURS = 2


@dataclass(frozen=True, slots=True)
class Option:
    """One way to settle a checkpoint, and what it costs.

    `effect` is integer deltas keyed by metric. `note` is what the deliverable's
    provenance records — the sentence that survives the run.
    """

    label: str
    detail: str
    effect: dict[str, int]
    note: str


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """A point where work stops until the CEO decides.

    `tacit` is the line that only surfaces if the decision is taken in person. It is
    the reason walking over beats clearing the tray, so it is content, not flavour.
    """

    at_percent: int
    kind: str  # "info" | "approval" | "decision"
    label: str
    prompt: str
    options: tuple[Option, ...]
    tacit: str


@dataclass(frozen=True, slots=True)
class Requires:
    visibility: int | None = None
    items: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ItemSpec:
    id: str
    title: str
    brief: str
    dept: str
    want: str
    effort_hours: int
    friction: str
    checkpoints: tuple[Checkpoint, ...]
    output_title: str
    output_kind: str
    effect: dict[str, int] = field(default_factory=dict)
    requires: Requires = Requires()
    unlocks: tuple[str, ...] = ()
    visit_meeting: bool = False
    final: bool = False

    @property
    def effort_units(self) -> int:
        return self.effort_hours * EFFORT_UNITS_PER_SIM_HOUR


# =========================================================================
# What the company was already doing (M6)
# =========================================================================


@dataclass(frozen=True, slots=True)
class SeededAssignment:
    """Work already in flight when the CEO walked in.

    Without this the floor is idle until the CEO assigns something, and the first thing on
    screen is an empty office with nothing asking for a decision — which is the opposite of
    what the product is claiming to be about. M6 asks for the reverse: a person waiting.

    **Progress is authored in percent of the item's effort, not in units and not in hours.**
    Percent is the unit checkpoints are already authored in (`Checkpoint.at_percent`), so
    authoring a seed *at* a checkpoint's own percent is what makes the opening stop
    structural rather than arithmetic that happens to land. Hours would not: the shipped seed's
    checkpoint sits at 45% of twenty-two hours, which is 9.9, so an hours figure would have to
    be rounded by the author — and rounding it the wrong way is a scenario that silently opens
    on an idle floor.
    """

    item_id: str
    person_id: str
    #: Percent of the item's total effort already burned.
    done_percent: int

    def done_units(self, item: ItemSpec) -> int:
        """The seeded progress in effort units, rounded *up*.

        Up rather than down, so `checkpoint_reached` holds at a seed authored at the
        checkpoint's own percent whatever the effort figure is. `checkpoint_reached`
        cross-multiplies (`done * 100 >= at * total`), so a floor division would leave a seed
        one unit short of its own threshold for every item whose effort does not divide by a
        hundred — and the symptom would be an opening that works for some scenarios and
        quietly idles for others.
        """
        return (self.done_percent * item.effort_units + 99) // 100


# =========================================================================
# Recurring-draw effects, and the copy generated from them (R59)
# =========================================================================
#
# Every authored option effect that used to move `manualHours` now names a recurring-draw
# reduction under the `"draw"` key, in hours per month — the unit the metric displays. The
# metric itself is derived from the sum of department draws (R49), so the two cannot drift.
#
# The *department* is derived from the item rather than authored a second time. Each item
# names the person it wants, and that person's reporting line is the department whose draw
# changes — unambiguous for all fourteen effects, and impossible to leave inconsistent with
# the item it belongs to.
#
# The copy is generated from the same number. Two authored strings quote an hours figure
# verbatim, and if they were authored separately the report's decision consequences would
# stop reconciling with its own `manualHours` trajectory the first time either was edited.

_NUMBER_WORDS = {
    6: "six",
    8: "eight",
    10: "ten",
    12: "twelve",
    14: "fourteen",
    15: "fifteen",
    18: "eighteen",
    20: "twenty",
    25: "twenty-five",
    26: "twenty-six",
    30: "thirty",
    40: "forty",
}


def spell(figure: int) -> str:
    """Spell a figure the way the authored copy reads, or fall back to digits."""
    return _NUMBER_WORDS.get(figure, str(figure))


def draw_delta(option: Option) -> int | None:
    """The recurring-draw change this option produces, in hours per month."""
    return option.effect.get("draw")


def director_for(scenario: Scenario, item: ItemSpec) -> str:
    """Whose department's draw this item's decisions move.

    Derived from the person the item wants, via their reporting line — so it is the same
    department the work is actually done in, and it cannot be authored inconsistently.

    Takes the scenario rather than reading a global. One process ticks many runs, so a global
    would answer for whichever company was loaded last: two runs on two scenarios would price
    the same decision against each other's departments.
    """
    return scenario.reporting_line_of(item.want)


def headline_draw_figure(item: ItemSpec, cp_index: int = 0) -> int:
    """The figure the copy quotes: the largest reduction on offer at that checkpoint.

    The largest rather than the first, because that is what the authored copy quoted — the
    prompt describes the best case and the options then price it down.
    """
    reductions = [
        -delta
        for option in item.checkpoints[cp_index].options
        if (delta := draw_delta(option)) is not None and delta < 0
    ]
    return max(reductions) if reductions else 0


def render_copy(template: str, figure: int) -> str:
    """Fill a copy template from a structured figure.

    `{hours}` for digits, `{hours_word}` spelled out, `{Hours_word}` capitalised.
    """
    word = spell(figure)
    return (
        template.replace("{hours}", str(figure))
        .replace("{hours_word}", word)
        .replace("{Hours_word}", word[:1].upper() + word[1:])
    )


def rendered_brief(item: ItemSpec) -> str:
    return render_copy(item.brief, headline_draw_figure(item))


def rendered_prompt(item: ItemSpec, cp_index: int) -> str:
    return render_copy(item.checkpoints[cp_index].prompt, headline_draw_figure(item, cp_index))


def checkpoint_reached(done_units: int, total_units: int, at_percent: int) -> bool:
    """Has work passed a checkpoint threshold?

    Cross-multiplied rather than divided: `done / total >= at / 100` would truncate,
    and the truncation would decide whether someone stops for you or not.
    """
    return done_units * 100 >= at_percent * total_units


def visit_meeting_due(done_units: int, total_units: int) -> bool:
    """The prototype's `progress > 0.2`, strict, cross-multiplied."""
    return done_units * 100 > VISIT_MEETING_THRESHOLD_PERCENT * total_units


# =========================================================================
# What the client is told at genesis
# =========================================================================


def catalog_to_state(scenario: Scenario) -> list[dict[str, Any]]:
    """The authored work graph, as the genesis event carries it to every consumer.

    The DAG (U14) assigns layers by longest-path depth and orders within a layer by item
    id, so that unlocking an item reshuffles nothing. Both need the *whole* graph before
    anything unlocks — a client that learned about an item only when it became available
    would re-layer the graph on every unlock, which is the one thing the layout exists to
    prevent. So the dependency edges have to be on the wire from the first event.

    It rides on genesis rather than on a read endpoint for two reasons. An exported run
    stays self-contained (R32): the graph replays from the log alone, without the kernel's
    Python constants in the process. And it is immutable and inherited by forks, which is
    exactly what genesis is for — the same argument that already puts `floor` here rather
    than letting a viewport re-derive geometry.

    **One thing is deliberately withheld.** `Checkpoint.tacit` is the line only an
    in-person resolution surfaces; shipping it at genesis would hand the client the whole
    mechanic the product is built on, and the art direction is explicit that inspecting
    shows less than conversation. It reaches the client only through a resolution that
    earned it.

    **`Option.effect` used to be withheld too, and no longer is (R35).** The stated reason
    was that the client showing the deltas would let the CEO optimise against arithmetic
    instead of judgement. What reverses it is the authored-tuning marking (R27, R36): a
    figure that travels with a persistent glyph saying it was invented is visible *and*
    labelled as invented, which is a better trade than a figure hidden and then imagined.
    A CEO who cannot see what an option costs is not exercising judgement; they are
    guessing, and the guess is against numbers they assume rather than numbers they read.

    The recurring-draw key is split out rather than shipped inside `effect`, because it is
    not a metric: `manualHours` is derived from the sum of department draws (R49), so a
    client rendering `draw` as a metric delta would show a movement no metric makes. The
    department it moves is the entry's own `director`, already on the wire.

    Copy arrives rendered. `brief` and `prompt` are templates over a figure derived from
    the options, and a client filling them itself would be a second implementation of
    authored copy in a second language — the duplicated-logic hazard the plan's Risks
    section names, with no golden vector cheap enough to guard it.
    """
    from simcore import effects

    def option_to_state(option: Option) -> dict[str, Any]:
        metric_effect, draw = effects.split_draw(option.effect)
        return {
            "label": option.label,
            "detail": option.detail,
            # Metric deltas only, in the same integer units the metric tiles render.
            "effect": metric_effect,
            # Hours per month off (or onto) the owning department's recurring draw. Zero
            # rather than absent, so a client never has to ask whether the key is missing
            # because the option does not move the draw or because the payload is old.
            "draw_delta": draw,
            "note": option.note,
        }

    return [
        {
            "id": item.id,
            "title": item.title,
            "brief": rendered_brief(item),
            "dept": item.dept,
            "want": item.want,
            # The department whose load this item's work sits in, derived from the person
            # it wants. The DAG's load tint reads this, so it cannot be authored apart
            # from the reporting line it describes.
            "director": director_for(scenario, item),
            "effort_hours": item.effort_hours,
            "effort_units": item.effort_units,
            "friction": item.friction,
            "requires": {
                "visibility": item.requires.visibility,
                "items": list(item.requires.items),
            },
            "unlocks": list(item.unlocks),
            "visit_meeting": item.visit_meeting,
            "final": item.final,
            "output_title": item.output_title,
            "output_kind": item.output_kind,
            "checkpoints": [
                {
                    "at_percent": checkpoint.at_percent,
                    "kind": checkpoint.kind,
                    "label": checkpoint.label,
                    "prompt": rendered_prompt(item, cp_index),
                    "options": [option_to_state(option) for option in checkpoint.options],
                }
                for cp_index, checkpoint in enumerate(item.checkpoints)
            ],
        }
        for item in scenario.items
    ]
