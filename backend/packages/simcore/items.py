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

The eight items carry nine checkpoints between them. That is the run's decision
supply, and it is what bounds a useful horizon — baseline load regenerates forever,
but a horizon longer than nine decisions is burn with nothing to decide.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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


ITEMS: tuple[ItemSpec, ...] = (
    ItemSpec(
        id="wi_ap_map",
        title="Map how accounts payable actually works",
        brief="Trace the real steps from invoice arrival to payment. Right now nobody in the company can explain who decides what.",
        dept="accounting",
        want="stf_ap",
        effort_hours=16,
        friction="I am stuck reconciling paper against PDFs.",
        checkpoints=(
            Checkpoint(
                at_percent=50,
                kind="info",
                label="Information",
                prompt="Invoices arrive twice — once on paper, once as a PDF. Which one is the record? I cannot make that call.",
                options=(
                    Option(
                        "PDF is the record",
                        "Paper becomes reference only. Everything gets searchable, but thirty suppliers need telling.",
                        {"visibility": 6, "leadTime": -1, "morale": -1},
                        "PDF is the system of record",
                    ),
                    Option(
                        "Paper is the record",
                        "Nothing changes on the floor. We can map it, but the waste stays.",
                        {"visibility": 3},
                        "Paper stays the system of record",
                    ),
                    Option(
                        "Let the team decide",
                        "Priya picks. Morale goes up, the rule drifts.",
                        {"morale": 3, "visibility": 1},
                        "Record of truth delegated to the team",
                    ),
                ),
                tacit="...for the last three days of every month we release payment before the paper even arrives. If we waited we would miss the supplier close. Nobody upstairs knows that.",
            ),
        ),
        output_title="AP process map (12 steps)",
        output_kind="Process map",
        effect={"visibility": 10},
        unlocks=("wi_ap_auto",),
    ),
    ItemSpec(
        id="wi_ap_auto",
        title="Automate invoice matching",
        brief="Four of the twelve mapped steps eat 60% of the time. Find out whether a machine can take them.",
        dept="accounting",
        want="stf_ap",
        effort_hours=24,
        friction="Running OCR accuracy tests. The quote came back.",
        requires=Requires(items=("wi_ap_map",)),
        checkpoints=(
            Checkpoint(
                at_percent=60,
                kind="approval",
                label="Approval",
                prompt="The OCR matching quote is in: $120K up front, $8K a month, and {hours_word} hours of manual work disappear every month. I need your approval.",
                options=(
                    Option(
                        "Approve it",
                        "Roll out to every invoice. The investment pays back and the human check stays.",
                        {"cash": -120, "draw": -40, "leadTime": -1, "morale": 3},
                        "OCR matching approved for all invoices",
                    ),
                    Option(
                        "Approve a limited rollout",
                        "Top ten suppliers only. A third of the cost, a third of the benefit.",
                        {"cash": -40, "draw": -15, "morale": 1},
                        "Limited rollout to the top ten suppliers",
                    ),
                    Option(
                        "Reject it",
                        "No spend this year. The manual work stays.",
                        {"morale": -3},
                        "Automation rejected",
                    ),
                ),
                tacit="...70% of the matching is the same three suppliers. You do not need all of them to get the benefit. That is not in the quote.",
            ),
        ),
        output_title="Invoice matching automation plan",
        output_kind="Automation candidate",
        effect={"visibility": 4},
    ),
    ItemSpec(
        id="wi_dup_entry",
        title="Kill the duplicate order entry",
        brief="The same figures get typed into a sales spreadsheet and the order system. {Hours_word} hours a month. Nobody has decided to stop it.",
        dept="sales",
        want="stf_order",
        effort_hours=20,
        friction="Sales and the order desk disagree on which number is real.",
        visit_meeting=True,
        checkpoints=(
            Checkpoint(
                at_percent=45,
                kind="decision",
                label="Decision",
                prompt="We enter the same figures twice — the sales spreadsheet and the order system. Which one do we kill?",
                options=(
                    Option(
                        "Kill the spreadsheet",
                        "One record. The hours vanish, but sales can no longer quote dates at the customer table.",
                        {"draw": -25, "leadTime": -1, "morale": -2},
                        "Spreadsheet retired, system is the record",
                    ),
                    Option(
                        "Change the system to fit sales",
                        "$60K of development. Sales keeps working the way it works; only the double entry goes.",
                        {"cash": -60, "draw": -18, "morale": 2},
                        "System modified to match how sales works",
                    ),
                    Option(
                        "Leave it alone",
                        "Do not touch it. The double entry stays.",
                        {"morale": -1},
                        "Duplicate entry continues",
                    ),
                ),
                tacit="...the spreadsheet holds the delivery date we actually promised. The system has no field for it. Kill the spreadsheet and sales starts lying to customers.",
            ),
        ),
        output_title="Duplicate order entry: options",
        output_kind="Automation candidate",
        effect={"visibility": 8},
    ),
    ItemSpec(
        id="wi_faq",
        title="Cut first-line support with an FAQ",
        brief="Six of ten first-line tickets are questions we have answered before. Classify them and the queue stops needing a person.",
        dept="support",
        want="stf_cs",
        effort_hours=18,
        friction="Classifying past tickets. Forty-eight so far.",
        checkpoints=(
            Checkpoint(
                at_percent=55,
                kind="approval",
                label="Approval",
                prompt="Publishing the FAQ should cut tickets 25%. The trade-off is that answer quality gets uneven. Do we publish?",
                options=(
                    Option(
                        "Publish it externally",
                        "Goes to customers. Largest effect, and our mistakes go public too.",
                        {"draw": -30, "leadTime": -2, "morale": 1},
                        "FAQ published externally",
                    ),
                    Option(
                        "Keep it internal",
                        "Answer templates for the team. Half the effect, none of the exposure.",
                        {"draw": -12},
                        "FAQ kept as internal templates",
                    ),
                    Option(
                        "Hold off",
                        "Take no quality risk.",
                        {"morale": -2},
                        "FAQ publication deferred",
                    ),
                ),
                tacit="...five tickets a day are the ones I genuinely cannot judge. Those five generate 80% of the complaints. An FAQ will not touch them.",
            ),
        ),
        output_title="Ticket taxonomy and FAQ v1 (48 entries)",
        output_kind="Process map",
        effect={"visibility": 7},
    ),
    ItemSpec(
        id="wi_quotes",
        title="Revisit the three-quote purchasing rule",
        brief="Every purchase needs three competing quotes regardless of value. Each order parks 2.5 days waiting for them.",
        dept="admin",
        want="stf_buyer",
        effort_hours=14,
        friction="Sorting two years of purchase orders by value.",
        checkpoints=(
            Checkpoint(
                at_percent=50,
                kind="decision",
                label="Decision",
                prompt="Three quotes on everything costs us 2.5 days per order. Do we change the rule?",
                options=(
                    Option(
                        "One quote under $50K",
                        "80% of orders go single-quote. Controls stay where the money is.",
                        {"leadTime": -2, "draw": -14, "morale": 2},
                        "Single quote permitted under $50K",
                    ),
                    Option(
                        "One quote on everything",
                        "Fastest possible. We also lose the ability to defend a price.",
                        {"leadTime": -3, "draw": -20, "morale": -4},
                        "Single quote on all purchases",
                    ),
                    Option(
                        "Keep all three",
                        "No change. Audit-proof and slow.",
                        {"morale": -1},
                        "Three-quote rule retained",
                    ),
                ),
                tacit="...the three-quote rule came from an audit finding five years ago. That finding was closed long ago. Nobody has decided to remove the rule.",
            ),
        ),
        output_title="Purchasing policy revision (value thresholds)",
        output_kind="Policy change",
        effect={"visibility": 6},
    ),
    ItemSpec(
        id="wi_hiring",
        title="Shorten hiring lead time",
        brief="Two weeks to agree requirements, six emails to schedule one interview. Candidates accept elsewhere while we are still talking.",
        dept="hr",
        want="stf_rec",
        effort_hours=22,
        friction="What the hiring manager wants and what the job post says do not match.",
        checkpoints=(
            Checkpoint(
                at_percent=45,
                kind="info",
                label="Information",
                prompt="The job post does not match what the hiring manager actually wants. Which one is right?",
                options=(
                    Option(
                        "The hiring manager version",
                        "Rewrite the post. The listing goes dark for two weeks.",
                        {"leadTime": -3, "visibility": 4, "morale": 1},
                        "Hiring manager requirements take precedence",
                    ),
                    Option(
                        "The job post",
                        "The listing keeps running. We absorb the gap at interview stage.",
                        {"morale": -2},
                        "Existing job post stands",
                    ),
                    Option(
                        "Put them in a room weekly",
                        "More hours, but it stops recurring.",
                        {"draw": 6, "leadTime": -4, "visibility": 6, "morale": 2},
                        "Weekly requirements alignment introduced",
                    ),
                ),
                tacit="...referrals skip the requirements entirely, and they are the ones who stay. We have never measured that.",
            ),
        ),
        output_title="Hiring requirements alignment process",
        output_kind="Process map",
        effect={"visibility": 6},
    ),
    ItemSpec(
        id="wi_close",
        title="Close the month in two days instead of five",
        brief="Spans accounting and procurement. Two causes: waiting on paper invoices, and approvals sitting idle. Only a director can move both.",
        dept="admin",
        want="dir_admin",
        effort_hours=32,
        friction="Interviewing both accounting and procurement.",
        visit_meeting=True,
        requires=Requires(visibility=24),
        checkpoints=(
            Checkpoint(
                at_percent=30,
                kind="decision",
                label="Decision",
                prompt="Two things stall the close. Which do we fix first? The floor cannot absorb both at once.",
                options=(
                    Option(
                        "Paper invoices first",
                        "Needs supplier cooperation. Worth two days.",
                        {"leadTime": -2, "visibility": 3},
                        "Paper invoice wait tackled first",
                    ),
                    Option(
                        "Idle approvals first",
                        "Entirely internal, so it moves fast. Worth a day and a half.",
                        {"leadTime": -1, "visibility": 3, "morale": 1},
                        "Approval delays tackled first",
                    ),
                    Option(
                        "Both at once",
                        "Fastest, and it spikes the team workload.",
                        {"leadTime": -3, "draw": 10, "morale": -3},
                        "Both causes tackled simultaneously",
                    ),
                ),
                tacit="...when an approver travels, everything parks for two days. We have a delegate mechanism, but it is not in the authority policy, so nobody uses it.",
            ),
            Checkpoint(
                at_percent=75,
                kind="approval",
                label="Approval",
                prompt="We can automate the manager approval above $10K with a value threshold. Where do you set it? This changes the authority policy, so it is your call.",
                options=(
                    Option(
                        "Raise it to $30K",
                        "Approvals drop 70%. Controls stay in place.",
                        {"leadTime": -1, "draw": -18, "morale": 2},
                        "Approval threshold raised to $30K",
                    ),
                    Option(
                        "Leave it at $10K",
                        "No policy change. The delay stays.",
                        {},
                        "Approval threshold unchanged",
                    ),
                    Option(
                        "Remove manager approval",
                        "Fastest. Fraud detection becomes after-the-fact.",
                        {"leadTime": -2, "draw": -26, "morale": -5},
                        "Manager approval removed",
                    ),
                ),
                tacit="...somebody set $10K five years ago and it has never been revisited. In today prices that is about $3K.",
            ),
        ),
        output_title="Month-end close roadmap",
        output_kind="Improvement plan",
        effect={"visibility": 8, "leadTime": -1},
    ),
    ItemSpec(
        id="wi_ai_rank",
        title="Rank what AI can take over",
        brief="Line up every mapped process, decide what a machine can do, and put them in order. Everything produced so far feeds into this.",
        dept="sales",
        want="dir_sales",
        effort_hours=30,
        friction="Reconciling the deliverables from every department.",
        requires=Requires(visibility=48),
        checkpoints=(
            Checkpoint(
                at_percent=60,
                kind="decision",
                label="Decision",
                prompt="Pick the ranking criterion. Change the criterion and the order changes.",
                options=(
                    Option(
                        "Rank by hours saved",
                        "Easy to defend as payback. The painful work waits.",
                        {"draw": -20, "visibility": 4},
                        "Ranked by hours saved",
                    ),
                    Option(
                        "Rank by lead time",
                        "Customers feel it. The hours stay.",
                        {"leadTime": -3, "visibility": 4},
                        "Ranked by lead time",
                    ),
                    Option(
                        "Rank by where it hurts",
                        "It sticks, and it is harder to defend with numbers.",
                        {"morale": 6, "visibility": 6, "draw": -8},
                        "Ranked by where the pain is",
                    ),
                ),
                tacit="...ask the four directors separately and each names a different worst pain. Pick one company-wide winner and three departments will not accept it.",
            ),
        ),
        output_title="AI opportunity ranking (12 processes)",
        output_kind="Improvement plan",
        effect={"visibility": 10},
        final=True,
    ),
)

ITEMS_BY_ID: dict[str, ItemSpec] = {item.id: item for item in ITEMS}

#: Nine, across eight items. The run's decision supply.
TOTAL_CHECKPOINTS = sum(len(item.checkpoints) for item in ITEMS)


def spec(item_id: str) -> ItemSpec:
    try:
        return ITEMS_BY_ID[item_id]
    except KeyError:
        raise KeyError(f"no work item {item_id!r}") from None


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


def director_for(item: ItemSpec) -> str:
    """Whose department's draw this item's decisions move.

    Derived from the person the item wants, via their reporting line — so it is the same
    department the work is actually done in, and it cannot be authored inconsistently.
    """
    from simcore import people as roster

    return roster.reporting_line_of(item.want)


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


def catalog_to_state() -> list[dict[str, Any]]:
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

    **Two things are deliberately withheld.** `Checkpoint.tacit` is the line only an
    in-person resolution surfaces; shipping it at genesis would hand the client the whole
    mechanic the product is built on, and the art direction is explicit that inspecting
    shows less than conversation. It reaches the client only through a resolution that
    earned it. And `Option.effect` is withheld because the prototype's tray prices an
    option in prose, not in numbers: the client showing the deltas would let the CEO
    optimise against arithmetic instead of judgement, and the kernel is the authority on
    what an option actually did anyway.

    Copy arrives rendered. `brief` and `prompt` are templates over a figure derived from
    the options, and a client filling them itself would be a second implementation of
    authored copy in a second language — the duplicated-logic hazard the plan's Risks
    section names, with no golden vector cheap enough to guard it.
    """
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
            "director": director_for(item),
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
                    "options": [
                        {"label": option.label, "detail": option.detail}
                        for option in checkpoint.options
                    ],
                }
                for cp_index, checkpoint in enumerate(item.checkpoints)
            ],
        }
        for item in ITEMS
    ]
