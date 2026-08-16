"""The scenario format and its loader: what a company file may say, and what it may not.

This suite is where the shipped company's data is pinned. `backend/scenarios/default.toml` was
extracted mechanically from the module constants it replaces — `people.PEOPLE`, `people.VOICE`,
`people.DEFLECTIONS`, `items.ITEMS`, `items.SEEDED_ASSIGNMENTS`, `capacity.DEPARTMENT_DRAWS` —
and the digests below are the evidence that the extraction was faithful. They were taken from
those constants *before* the move, so a test that reproduces them is asserting that the file and
the constants describe one company. The extraction script is not kept: a one-shot generator that
imports constants this change removes would not run again, and a script that cannot run reads as
a second source of truth rather than as evidence. The digests are the evidence, and they run
every time.

Three properties get most of the attention here, because each of them is a way for a scenario to
be quietly wrong rather than loudly wrong.

**A refusal has to be total** (M12, R9). Half a company is worse than none, because the half that
loaded is the half nobody looked at. `test_nothing_is_constructed_when_a_scenario_is_refused`
proves it the only way that is not a restatement: it replaces the constructor and asserts the
constructor is never reached.

**The content hash has to cover exactly what a scenario *is*** (R7). Reordering two people has to
move it, because roster order decides who wins a contested desk; reformatting the file must not,
because a reformat changes no company and a hash that moved with it would invalidate every run
for a whitespace change.

**Every authored string has to be bounded and printable** (R9, R19). All of it reaches a director's
prompt eventually. The cap and the character rule live in the loader so that U11 can assemble a
prompt from scenario text without re-deriving either.
"""

from __future__ import annotations

import ast
import copy
import re
import shutil
import sys
from pathlib import Path

import pytest

from simcore import hashing
from simcore import people as roster
from simcore import scenario as sc
from simcore import step as sim
from simcore.world import DEFAULT_COLS, DEFAULT_ROWS, plan_floor

BACKEND = Path(__file__).resolve().parent.parent
SHIPPED_PATH = BACKEND / "scenarios" / "default.toml"
SHIPPED = SHIPPED_PATH.read_text()

#: Digests taken from the module constants before the data moved into `default.toml`. See the
#: module docstring: these are the evidence that the move was a move.
#:
#: `CATALOG_DIGEST` is `items.catalog_to_state()`'s output and `ROSTER_DIGEST` is
#: `people.roster_to_state(seats)`'s, both at the default grid. The roster projection is the
#: pre-M15 shape — name, initials, title, dept, mgr, rank, seat — because the four fields M15
#: adds are new content rather than moved content, and pinning the old shape is what makes
#: "nothing else moved" checkable.
PRE_MOVE_SEATS_DIGEST = "151ec291b306b19a9e91ff7101ec7232"
PRE_MOVE_ROSTER_DIGEST = "58cb6ef110b92928a06de1491f4b602f"
PRE_MOVE_CATALOG_DIGEST = "c91fc31f15dcb7ba6f397072aefda264"
PRE_MOVE_VOICE_DIGEST = "d529e8e2b72aa99e375c900f5985e5ad"


@pytest.fixture
def directory(tmp_path: Path) -> Path:
    """A scenarios directory holding a copy of the shipped file, free to be edited."""
    shutil.copy(SHIPPED_PATH, tmp_path / "default.toml")
    return tmp_path


def variant(*replacements: tuple[str, str], text: str = SHIPPED) -> str:
    """The shipped file with edits applied, each asserted to have landed.

    Asserted rather than hoped for: a replacement that silently matched nothing would leave the
    test asserting that the *unmodified* file is refused, which would fail for the right reason
    and the wrong cause.
    """
    for old, new in replacements:
        assert text.count(old) >= 1, f"nothing to replace: {old!r}"
        text = text.replace(old, new, 1)
    return text


def refusal(text: str, *, name: str = "default") -> str:
    """Load a variant and return the refusal. Fails if the variant is accepted."""
    with pytest.raises(sc.ScenarioInvalid) as caught:
        sc.parse(text.encode(), name=name, origin="scenarios/default.toml")
    return str(caught.value)


def line_of(reason: str) -> int:
    """The line number a refusal names, so a test can check it points at the right entry."""
    match = re.search(r"scenarios/default\.toml:(\d+):", reason)
    assert match, f"refusal names no line: {reason}"
    return int(match.group(1))


def offending_line(reason: str, text: str) -> str:
    """The line the refusal points at, read out of the file the refusal was about.

    Read out of the *variant*, never out of `SHIPPED`: a test that located the line in the
    unedited file would pass or fail on whether the edit happened to shift the line count.
    """
    return text.split("\n")[line_of(reason) - 1].strip()


# =========================================================================
# The shipped company loads, and it is the one the port asserted against
# =========================================================================


def test_the_default_scenario_loads_from_the_scenarios_directory() -> None:
    company = sc.load_default()

    assert company.scenario_id == "default"
    assert company.schema_version == sc.SCENARIO_SCHEMA_VERSION
    assert company.hash_version == sc.SCENARIO_HASH_VERSION
    assert company.content_hash
    assert company.title


def test_the_default_scenario_is_the_only_one_the_directory_offers_yet() -> None:
    """A second file is U7's, and this is what would notice one arriving unannounced."""
    assert sc.available() == ("default",)


def test_the_roster_is_the_one_the_ported_parity_suite_asserts_against() -> None:
    """The pre-move digest, reproduced from the file (M10).

    Compared over the projection the genesis payload used *before* this unit, so the four fields
    M15 adds do not mask a change to the seven that moved.
    """
    company = sc.load_default()
    seats = _seats_of(company)

    assert hashing.digest({pid: list(seat) for pid, seat in seats.items()}) == (
        PRE_MOVE_SEATS_DIGEST
    )
    assert (
        hashing.digest(
            {
                person.id: {
                    "name": person.name,
                    "initials": person.initials,
                    "title": person.title,
                    "dept": person.dept,
                    "mgr": person.mgr,
                    "rank": person.rank,
                    "seat": list(seats[person.id]),
                }
                for person in company.people
            }
        )
        == PRE_MOVE_ROSTER_DIGEST
    )


def test_the_catalog_is_byte_for_byte_the_work_graph_that_moved() -> None:
    """The pre-move catalog digest, reproduced from the file.

    `items.catalog_to_state` is not called here on purpose: it still reads the module constant
    in this pass, so calling it would compare the constant with itself. The projection is
    rebuilt from the loaded scenario instead, in the same shape and order.
    """
    from simcore import effects
    from simcore import items as work

    company = sc.load_default()

    rebuilt = [
        {
            "id": item.id,
            "title": item.title,
            "brief": work.rendered_brief(item),
            "dept": item.dept,
            "want": item.want,
            "director": company.reporting_line_of(item.want),
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
                    "prompt": work.rendered_prompt(item, index),
                    "options": [
                        {
                            "label": option.label,
                            "detail": option.detail,
                            "effect": effects.split_draw(option.effect)[0],
                            "draw_delta": effects.split_draw(option.effect)[1],
                            "note": option.note,
                        }
                        for option in checkpoint.options
                    ],
                }
                for index, checkpoint in enumerate(item.checkpoints)
            ],
        }
        for item in company.items
    ]

    assert hashing.digest(rebuilt) == PRE_MOVE_CATALOG_DIGEST


def test_every_scripted_line_survived_the_move() -> None:
    company = sc.load_default()

    assert (
        hashing.digest(
            {
                "voice": {person.id: dict(person.voice) for person in company.people},
                "deflections": {person.id: person.deflection for person in company.people},
            }
        )
        == PRE_MOVE_VOICE_DIGEST
    )


def test_the_reporting_lines_and_draws_are_the_ones_capacity_was_built_from() -> None:
    company = sc.load_default()

    assert company.directors == ("dir_sales", "dir_admin", "dir_cs", "dir_hr")
    assert company.lines == {
        "dir_sales": ("dir_sales", "stf_order", "stf_field"),
        "dir_admin": ("dir_admin", "stf_ap", "stf_buyer"),
        "dir_cs": ("dir_cs", "stf_cs"),
        "dir_hr": ("dir_hr", "stf_rec"),
    }
    # Declaration order, not sorted: `state.capacity` is built by walking this, and the
    # attrition loop walks `state.capacity` — so two departments shedding someone on one day
    # would emit their events in this order.
    assert list(company.draws) == ["dir_admin", "dir_sales", "dir_cs", "dir_hr"]
    assert company.draws == {"dir_admin": 120, "dir_sales": 100, "dir_cs": 60, "dir_hr": 60}
    assert company.initial_manual_hours == 340


def test_priyas_desk_and_her_reporting_line_still_disagree() -> None:
    """The deliberate mismatch, which is why seating and reporting are two questions.

    If a tidy-up ever collapses them, the format loses the thing it exists to be able to
    express, and it would do so silently — the file would still load.
    """
    company = sc.load_default()
    priya = company.person("stf_ap")

    assert priya.dept == "accounting"
    assert company.reporting_line_of("stf_ap") == "dir_admin"
    assert company.person("dir_admin").dept == "admin"
    assert priya.dept != company.person(priya.mgr).dept


def test_the_seeded_assignment_arrived_as_data_rather_than_as_a_rewrite() -> None:
    """U2's seed, relocated. Percent of effort, rounding up, unchanged (M6)."""
    company = sc.load_default()

    assert len(company.seeded) == 1
    seeded = company.seeded[0]
    assert (seeded.item_id, seeded.person_id, seeded.done_percent) == ("wi_hiring", "dir_hr", 45)

    item = company.item("wi_hiring")
    # Authored at the checkpoint's own percent, which is what makes the opening stop structural.
    assert seeded.done_percent == item.checkpoints[0].at_percent
    # Rounded up, so cross-multiplication holds at the threshold for any effort figure. 45% of
    # 22 hours is 35 640 units exactly here, but a floor division on an item whose effort did
    # not divide by 100 would leave the seed one unit short and the floor idle.
    assert seeded.done_units(item) == -(-seeded.done_percent * item.effort_units // 100)
    from simcore import items as work

    assert work.checkpoint_reached(
        seeded.done_units(item), item.effort_units, item.checkpoints[0].at_percent
    )


def test_every_person_carries_a_responsibility_and_a_tool_list(  # noqa: D103 - see below
) -> None:
    """M15: authored for all ten, whether or not a model ever answers for them."""
    company = sc.load_default()

    assert len(company.people) == 10
    for person in company.people:
        assert person.responsibility, person.id
        assert person.tools, person.id
        assert person.mcp_servers, person.id
        assert person.skills, person.id
        assert person.title and person.dept and person.rank
        assert set(person.voice) == set(sc.VOICE_SLOTS), person.id
        assert person.deflection, person.id


def test_the_nine_checkpoints_are_still_the_run_s_decision_supply() -> None:
    company = sc.load_default()

    assert len(company.items) == 8
    assert company.total_checkpoints == 9


MINIMAL = """
schema = 1
id = "minimal"
title = "Two Desks Ltd"

[[department]]
id = "sales"
director = "dir_one"
draw_hours_per_month = 40

[[department]]
id = "admin"
director = "dir_two"
draw_hours_per_month = 40

[[department]]
id = "support"
director = "dir_three"
draw_hours_per_month = 40

[[department]]
id = "hr"
director = "dir_four"
draw_hours_per_month = 40

[[person]]
id = "dir_one"
name = "One"
initials = "O1"
title = "Head of Sales"
room = "sales"
rank = "director"
seat_slot = 0
responsibility = "Sells."
deflection = "No idea."
[person.voice]
why = "Because."
exception = "Sometimes."
axis = "Mine."
bottleneck = "Waiting."

[[person]]
id = "dir_two"
name = "Two"
initials = "O2"
title = "Head of Administration"
room = "admin"
rank = "director"
seat_slot = 0
responsibility = "Administers."
deflection = "No idea."
[person.voice]
why = "Because."
exception = "Sometimes."
axis = "Mine."
bottleneck = "Waiting."

[[person]]
id = "dir_three"
name = "Three"
initials = "O3"
title = "Head of Support"
room = "support"
rank = "director"
seat_slot = 0
responsibility = "Supports."
deflection = "No idea."
[person.voice]
why = "Because."
exception = "Sometimes."
axis = "Mine."
bottleneck = "Waiting."

[[person]]
id = "dir_four"
name = "Four"
initials = "O4"
title = "Head of People"
room = "hr"
rank = "director"
seat_slot = 0
responsibility = "Hires."
deflection = "No idea."
[person.voice]
why = "Because."
exception = "Sometimes."
axis = "Mine."
bottleneck = "Waiting."

[[item]]
id = "wi_only"
title = "Do the one thing"
brief = "The only work there is."
room = "sales"
want = "dir_one"
effort_hours = 4
friction = "Nothing in the way."
output_title = "The one thing, done"
output_kind = "Process map"

[[item.checkpoint]]
at_percent = 50
kind = "decision"
label = "Decision"
prompt = "Which way?"
tacit = "...the obvious answer is the wrong one."

[[item.checkpoint.option]]
label = "This way"
detail = "Costs cash."
note = "Went this way"
effect = { cash = -10 }

[[item.checkpoint.option]]
label = "That way"
detail = "Costs morale."
note = "Went that way"
effect = { morale = -2 }
"""


def test_a_company_can_be_written_from_scratch_against_this_format() -> None:
    """The floor allows a four-person company, and the format allows authoring one (M13).

    Not merely a smaller default: this is the shape a second scenario takes, and it exercises
    every default the format fills in — no `manager`, no `tools`, no `unlocks`, no `requires`,
    no `visit_meeting`, no `final`, no seeded work.
    """
    company = sc.parse(MINIMAL.encode(), name="minimal")

    assert company.scenario_id == "minimal"
    assert [person.id for person in company.people] == [
        "dir_one",
        "dir_two",
        "dir_three",
        "dir_four",
    ]
    assert company.directors == tuple(person.id for person in company.people)
    assert company.lines == {
        "dir_one": ("dir_one",),
        "dir_two": ("dir_two",),
        "dir_three": ("dir_three",),
        "dir_four": ("dir_four",),
    }
    assert company.seeded == ()
    assert company.initial_manual_hours == 160

    only = company.item("wi_only")
    assert only.requires.items == () and only.requires.visibility is None
    assert only.unlocks == () and not only.visit_meeting and not only.final
    assert only.effect == {}
    assert company.person("dir_one").tools == ()


def test_a_defaulted_field_is_a_scenario_field_rather_than_a_loader_constant() -> None:
    """R7: the hash covers the fully-defaulted structure, so writing a default is a no-op.

    Which is the point of hashing the defaulted form: a scenario that spells out `unlocks = []`
    and one that omits it are the same company, and a later change to what the default *is*
    would move the hash of every file that omitted it — announcing itself rather than silently
    changing what a recorded run meant.
    """
    omitted = sc.parse(MINIMAL.encode(), name="minimal")
    spelled_out = sc.parse(
        variant(
            (
                'friction = "Nothing in the way."',
                'friction = "Nothing in the way."\n'
                "unlocks = []\n"
                "visit_meeting = false\n"
                "final = false\n"
                "effect = {}\n"
                "requires = { items = [] }",
            ),
            (
                'room = "sales"\nrank = "director"',
                'room = "sales"\nmanager = ""\nrank = "director"',
            ),
            (
                'responsibility = "Sells."',
                'responsibility = "Sells."\ntools = []\nmcp_servers = []\nskills = []',
            ),
            text=MINIMAL,
        ).encode(),
        name="minimal",
    )

    assert spelled_out.content_hash == omitted.content_hash


# =========================================================================
# M12: refused whole, with a reason that names the line
# =========================================================================


def test_a_scenario_naming_an_unknown_department_is_refused() -> None:
    reason = refusal(
        variant(
            (
                "# --- the roster",
                '[[department]]\nid = "legal"\ndirector = "dir_admin"\n'
                "draw_hours_per_month = 10\n\n# --- the roster",
            )
        )
    )

    assert "reporting lines" in reason
    assert "sales, admin, support, hr" in reason
    assert "cannot add a fifth" in reason


def test_a_department_id_outside_the_fixed_four_is_refused_by_name() -> None:
    text = variant(('id = "support"', 'id = "legal"'))
    reason = refusal(text)

    assert "'legal'" in reason
    assert "not one of the four reporting lines" in reason
    assert offending_line(reason, text) == 'id = "legal"'


def test_a_scenario_naming_an_unknown_manager_is_refused() -> None:
    text = variant(('manager = "dir_admin"', 'manager = "dir_nobody"'))
    reason = refusal(text)

    assert "'stf_ap'" in reason
    assert "'dir_nobody'" in reason
    assert "does not head one of the four reporting lines" in reason
    assert offending_line(reason, text) == 'manager = "dir_nobody"'


def test_a_scenario_with_an_unresolvable_prerequisite_is_refused() -> None:
    text = variant(
        ('requires = { items = ["wi_ap_map"] }', 'requires = { items = ["wi_ghost"] }')
    )
    reason = refusal(text)

    assert "'wi_ap_auto'" in reason
    assert "'wi_ghost'" in reason
    assert "a gate that never opens" in reason
    assert offending_line(reason, text) == 'requires = { items = ["wi_ghost"] }'


def test_a_scenario_carrying_an_unknown_key_is_refused() -> None:
    """The closed set. This is the check that keeps a provider setting out of a company file."""
    text = variant(('id = "wi_faq"', 'id = "wi_faq"\nbase_url = "https://elsewhere"'))
    reason = refusal(text)

    assert "'base_url' is not a key this format has" in reason
    assert "The set is closed" in reason
    assert offending_line(reason, text) == 'base_url = "https://elsewhere"'


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        pytest.param(('schema = 1', 'schema = 1\napi_key = "sk-live-nope"'), "api_key", id="top"),
        pytest.param(
            ('director = "dir_admin"', 'director = "dir_admin"\nbudget = 4'),
            "budget",
            id="department",
        ),
        pytest.param(
            ('initials = "PR"', 'initials = "PR"\nemail = "priya@example.com"'),
            "email",
            id="person",
        ),
        pytest.param(
            ('bottleneck = "Matching', 'shortcut = "x"\nbottleneck = "Matching'),
            "shortcut",
            id="voice",
        ),
        pytest.param(
            ('at_percent = 50', 'at_percent = 50\nwebhook = "https://elsewhere"'),
            "webhook",
            id="checkpoint",
        ),
        pytest.param(
            (
                'note = "PDF is the system of record"',
                'note = "PDF is the system of record"\nrun = "rm -rf /"',
            ),
            "run",
            id="option",
        ),
        pytest.param(
            ('done_percent = 45', 'done_percent = 45\nat_tick = 3'),
            "at_tick",
            id="seeded",
        ),
    ],
)
def test_the_key_set_is_closed_at_every_level(edit: tuple[str, str], expected: str) -> None:
    reason = refusal(variant(edit))

    assert f"{expected!r} is not a key this format has" in reason


def test_nothing_is_constructed_when_a_scenario_is_refused() -> None:
    """M12's partial-load prohibition, proven rather than asserted.

    Every other refusal test shows that a bad file raises. None of them shows that nothing was
    built on the way to raising — a loader that constructed nine people and then refused would
    pass all of them. Replacing the constructor and finding it unreached is the difference.
    """
    called: list[str] = []
    original = sc._build

    def spy(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        called.append("built")
        return original(*args, **kwargs)

    sc._build = spy  # noqa: SLF001
    try:
        # Broken in the *last* entry in the file, so every earlier entry is one a
        # construct-as-you-go loader would already have built.
        with pytest.raises(sc.ScenarioInvalid):
            sc.parse(
                variant(('person = "dir_hr"', 'person = "nobody_at_all"')).encode(),
                name="default",
            )
        assert called == []

        # And the spy really would have noticed, which is what stops this from passing
        # vacuously if `_build` is ever moved.
        sc.parse(SHIPPED.encode(), name="default")
        assert called == ["built"]
    finally:
        sc._build = original  # noqa: SLF001


def test_one_pass_reports_every_problem_it_can_find() -> None:
    """Collected, not raised one at a time.

    A loader that stopped at the first problem would make fixing a file an N-run loop, and each
    run would tell the author one thing. It is also the observable difference between "validate,
    then build" and "build, and validate as you go".
    """
    reason = refusal(
        variant(
            ('initials = "MW"', 'initials = "MARCUS"'),
            ('kind = "approval"', 'kind = "maybe"'),
            ('at_percent = 45\nkind = "info"', 'at_percent = 0\nkind = "info"'),
        )
    )

    assert "initials is 6 characters" in reason
    assert "kind is 'maybe'" in reason
    assert "at_percent is 0" in reason


def test_a_refusal_says_when_the_cross_reference_checks_have_not_run_yet() -> None:
    """So an author who fixes everything the first refusal listed is not surprised by a second.

    The two phases exist because a cross-reference check reads ids the field checks validate:
    running "is this room on the floor" against a room key that failed its own type check would
    report an invented failure about the real one.
    """
    shape_only = refusal(variant(('initials = "MW"', 'initials = "MARCUS"')))
    cross_only = refusal(variant(('manager = "dir_admin"', 'manager = "dir_nobody"')))

    assert "checked once the above are fixed" in shape_only
    assert "checked once the above are fixed" not in cross_only


def test_the_cross_reference_phase_also_reports_everything_at_once() -> None:
    reason = refusal(
        variant(
            ('room = "accounting"\nmanager', 'room = "cellar"\nmanager'),
            ('requires = { items = ["wi_ap_map"] }', 'requires = { items = ["wi_ghost"] }'),
            ('person = "dir_hr"', 'person = "nobody_at_all"'),
        )
    )

    assert "'cellar'" in reason
    assert "'wi_ghost'" in reason
    assert "'nobody_at_all'" in reason


# =========================================================================
# M11: the floor, the eight rooms and the four lines are fixed
# =========================================================================


def test_a_person_in_a_room_that_does_not_exist_is_refused() -> None:
    reason = refusal(variant(('room = "accounting"\nmanager', 'room = "server_room"\nmanager')))

    assert "'server_room'" in reason
    assert "not a room on this floor" in reason
    assert "A scenario cannot add one" in reason


def test_an_item_in_a_room_that_does_not_exist_is_refused() -> None:
    reason = refusal(variant(('room = "accounting"\nwant', 'room = "server_room"\nwant')))

    assert "'wi_ap_map'" in reason
    assert "The eight rooms are fixed" in reason


def test_a_scenario_declaring_a_new_room_is_refused_by_the_closed_key_set() -> None:
    """There is no `[[room]]` key, so the floor cannot be extended by a data file at all."""
    reason = refusal(SHIPPED + '\n[[room]]\nid = "server_room"\nseats = 4\n')

    assert "'room' is not a key this format has" in reason


def test_a_room_cannot_hold_more_people_than_it_has_desks() -> None:
    """Seats against floor capacity.

    `assign_seats` has fallbacks for a room that is too small, and they exist for a hire
    arriving mid-run. Reaching them from an authored roster would mean somebody sitting on a
    floor tile or on the spawn point, which is the floor stopping to mean anything.
    """
    reason = refusal(
        variant(
            (
                'room = "accounting"\nmanager = "dir_admin"\nrank = "staff"\nseat_slot = 0',
                'room = "sales"\nmanager = "dir_admin"\nrank = "staff"\nseat_slot = 0',
            )
        )
    )

    assert "'sales' seats 3 and 4 people are authored into it" in reason
    assert "the roster has to fit it" in reason


def test_a_seat_slot_beyond_the_rooms_desks_is_refused() -> None:
    reason = refusal(
        variant(
            (
                'rank = "staff"\nseat_slot = 0\nresponsibility = "Reconciles',
                'rank = "staff"\nseat_slot = 2\nresponsibility = "Reconciles',
            )
        )
    )

    assert "seat_slot is 2 and 'accounting' has 1 desks" in reason


def test_rank_and_the_department_list_have_to_agree() -> None:
    reason = refusal(
        variant(
            (
                'rank = "staff"\nseat_slot = 0\nresponsibility = "Collects competing quotes',
                'rank = "director"\nseat_slot = 0\nresponsibility = "Collects competing quotes',
            )
        )
    )

    assert "'stf_buyer'" in reason
    assert "no department names them as its director" in reason


def test_a_directors_room_has_to_be_their_own_lines_room() -> None:
    """A line is named by its director's room, because a hire into the line is seated there."""
    reason = refusal(variant(('room = "support"\nmanager = ""', 'room = "lounge"\nmanager = ""')))

    assert "its director sits in 'lounge'" in reason


# =========================================================================
# R7: what the content hash covers, and what it does not
# =========================================================================


def reformatted(text: str) -> str:
    """The same company, written differently.

    Three reformattings a real contributor performs, in one pass: comments and blank lines
    dropped, whitespace around `=` closed up, and the keys inside each run of assignments
    reversed. None of them changes a single value, so none of them may change the hash.

    Reversing only within a contiguous run of assignments, never across a `[table]` header:
    TOML binds a key to whichever table header last opened, so moving a key past one would
    change the document rather than reformat it.
    """
    out: list[str] = []
    run: list[str] = []

    def flush() -> None:
        out.extend(reversed(run))
        run.clear()

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            flush()
            continue
        if stripped.startswith("["):
            flush()
            out.append(line)
            continue
        run.append(re.sub(r"^(\S+?)\s*=\s*", r"\1=", stripped))
    flush()

    return "\n".join(out) + "\n"


def test_reformatting_a_scenario_changes_neither_its_hash_nor_its_meaning() -> None:
    original = sc.parse(SHIPPED.encode(), name="default")
    rewritten = reformatted(SHIPPED)

    assert rewritten.encode() != SHIPPED.encode()

    again = sc.parse(rewritten.encode(), name="default")

    assert again.content_hash == original.content_hash
    assert [person.id for person in again.people] == [person.id for person in original.people]
    assert again.people == original.people
    assert again.items == original.items


def test_reordering_two_people_changes_the_content_hash() -> None:
    """Roster order is part of what a scenario is, so it is inside the identity.

    It has to be, because it is observable: two people may claim one desk, and the earlier one
    in the file gets it. A hash keyed on people by id would have sorted the ordering out while
    seating still followed it — a file that seated two people differently would have hashed the
    same as the one it replaced.
    """
    original = sc.parse(SHIPPED.encode(), name="default")
    swapped = sc.parse(with_two_people_swapped(SHIPPED).encode(), name="default")

    assert [person.id for person in swapped.people] != [
        person.id for person in original.people
    ]
    assert sorted(person.id for person in swapped.people) == sorted(
        person.id for person in original.people
    )
    assert swapped.content_hash != original.content_hash


def test_two_people_may_contend_for_one_desk_so_that_order_stays_observable() -> None:
    """Deliberately not validated as unique.

    `assign_seats` gives a contested slot to whoever comes first in the roster and puts the
    other on the next free desk. Refusing a shared `seat_slot` would be tidier and would make
    roster order unobservable, which would make covering it in the hash pointless.
    """
    contested = sc.parse(
        variant(
            (
                'rank = "staff"\nseat_slot = 1\nresponsibility = "Carries',
                'rank = "staff"\nseat_slot = 0\nresponsibility = "Carries',
            )
        ).encode(),
        name="default",
    )

    sales = [person for person in contested.people if person.dept == "sales"]
    assert [person.slot for person in sales] == [2, 0, 0]


def with_two_people_swapped(text: str) -> str:
    """The shipped file with the second and third `[[person]]` blocks exchanged.

    Both are in Sales, which is where a contested desk would actually move somebody.
    """
    head, _, rest = text.partition("[[person]]")
    body, marker, tail = rest.partition("# --- the work")
    blocks = ("[[person]]" + body).split("[[person]]")
    people = ["[[person]]" + block for block in blocks[1:]]
    people[1], people[2] = people[2], people[1]
    return head + "".join(people) + marker + tail


# =========================================================================
# R7: the guard, and the three sites that run it
# =========================================================================


def test_the_guard_accepts_a_run_whose_scenario_has_not_changed(directory: Path) -> None:
    company = sc.load("default", directory=directory)

    for site in ("the fold from zero", "a snapshot restore", "a fold resumed from a snapshot"):
        assert (
            sc.load_recorded(company.identity(), at=site, directory=directory).content_hash
            == company.content_hash
        )
    assert sc.verify_unchanged(company, at="a fold resumed from a snapshot", directory=directory)


def test_editing_a_scenario_refuses_a_re_fold_and_names_both_sides(directory: Path) -> None:
    company = sc.load("default", directory=directory)
    (directory / "default.toml").write_text(
        variant(('title = "Accounts Payable"', 'title = "Senior Accounts Payable"'))
    )

    with pytest.raises(sc.ScenarioMismatch) as caught:
        sc.load_recorded(company.identity(), at="the fold from zero", directory=directory)

    reason = str(caught.value)
    edited = sc.load("default", directory=directory)

    # Both sides, named.
    assert company.content_hash in reason
    assert edited.content_hash in reason
    assert company.content_hash != edited.content_hash
    # The site, so three guards do not produce one indistinguishable message.
    assert "Refused at the fold from zero" in reason
    # And the remedy, which is the thing a contributor surprised mid-session needs.
    assert "Remedy" in reason
    assert str(directory / "default.toml") in reason
    assert "start a new run" in reason
    assert "documented wipe rather than a migration" in reason


def test_the_refusal_says_which_person_moved(directory: Path) -> None:
    """Structurally compared, so the refusal is actionable rather than two digests."""
    company = sc.load("default", directory=directory)
    (directory / "default.toml").write_text(
        variant(('title = "Accounts Payable"', 'title = "Senior Accounts Payable"'))
    )

    with pytest.raises(sc.ScenarioMismatch) as caught:
        sc.verify_unchanged(company, at="a fold resumed from a snapshot", directory=directory)

    assert "stf_ap changed: title 'Accounts Payable' -> 'Senior Accounts Payable'" in str(
        caught.value
    )


def test_the_refusal_says_which_item_moved(directory: Path) -> None:
    company = sc.load("default", directory=directory)
    (directory / "default.toml").write_text(variant(("effort_hours = 24", "effort_hours = 26")))

    with pytest.raises(sc.ScenarioMismatch) as caught:
        sc.verify_unchanged(company, at="a snapshot restore", directory=directory)

    assert "wi_ap_auto changed: effort_hours 24 -> 26" in str(caught.value)


def test_the_refusal_says_when_the_roster_order_moved(directory: Path) -> None:
    """Which a hash comparison alone cannot: two digests differ and nothing says why."""
    company = sc.load("default", directory=directory)
    (directory / "default.toml").write_text(with_two_people_swapped(SHIPPED))

    with pytest.raises(sc.ScenarioMismatch) as caught:
        sc.verify_unchanged(company, at="a fold resumed from a snapshot", directory=directory)

    reason = str(caught.value)
    assert "the roster is in a different order" in reason
    assert "decides who wins a contested desk" in reason


def test_the_refusal_names_a_person_who_appeared_or_vanished(directory: Path) -> None:
    company = sc.load("default", directory=directory)
    (directory / "default.toml").write_text(variant(('id = "stf_field"', 'id = "stf_road"')))

    with pytest.raises(sc.ScenarioMismatch) as caught:
        sc.verify_unchanged(company, at="the fold from zero", directory=directory)

    reason = str(caught.value)
    assert "the roster no longer has stf_field" in reason
    assert "the roster has gained stf_road" in reason


def test_the_from_zero_guard_can_name_a_person_from_the_genesis_payload(
    directory: Path,
) -> None:
    """The fold has no previous `Scenario`, only what genesis recorded — and that is enough.

    Genesis already carries the roster and the catalog, so the guard is handed those and reports
    a moved person rather than a moved digest. This is the shape `log._apply_genesis` calls.
    """
    company = sc.load("default", directory=directory)
    recorded_roster = {
        person.id: {
            "name": person.name,
            "initials": person.initials,
            "title": person.title,
            "dept": person.dept,
            "mgr": person.mgr,
            "rank": person.rank,
        }
        for person in company.people
    }
    (directory / "default.toml").write_text(variant(('initials = "PR"', 'initials = "PJR"')))

    with pytest.raises(sc.ScenarioMismatch) as caught:
        sc.load_recorded(
            company.identity(),
            at="the fold from zero",
            directory=directory,
            recorded_roster=recorded_roster,
        )

    assert "stf_ap changed: initials 'PR' -> 'PJR'" in str(caught.value)


def test_a_hash_version_move_is_distinguishable_from_an_edited_file(directory: Path) -> None:
    """Which is why the version rides along with the hash.

    Without it, improving the canonicalisation would read as every existing run's scenario
    having been edited — and the remedy for that is the opposite of the remedy for this.
    """
    company = sc.load("default", directory=directory)
    stale = {**company.identity(), "hash_ver": company.hash_version - 1}

    with pytest.raises(sc.ScenarioMismatch) as caught:
        sc.load_recorded(stale, at="a snapshot restore", directory=directory)

    reason = str(caught.value)
    assert "the way a scenario is hashed changed" in reason
    assert "not the same thing as the file having been edited" in reason


def test_a_run_recording_no_scenario_identity_is_refused_with_the_remedy() -> None:
    """A log from a build that predates scenario files. There is nothing to check, so refuse."""
    with pytest.raises(sc.ScenarioMismatch) as caught:
        sc.load_recorded({}, at="the fold from zero")

    reason = str(caught.value)
    assert "records no scenario identity" in reason
    assert "start a new run" in reason


def test_the_scenario_a_state_carries_is_free_to_clone() -> None:
    """`log._clone` deep-copies state on a resume, and this keeps that from copying the company.

    A `Scenario` is an immutable fold-time input, so a copy defends against nothing. Returning
    itself is also what makes identity comparison meaningful for a caller holding two states.
    """
    company = sc.load_default()

    assert copy.deepcopy(company) is company
    assert copy.deepcopy({"state": company})["state"] is company


# =========================================================================
# R9: bounds, characters, and the name rule
# =========================================================================


def test_a_string_that_reaches_a_prompt_is_length_capped() -> None:
    reason = refusal(
        variant(
            (
                'friction = "I am stuck reconciling paper against PDFs."',
                f'friction = "{"x" * (sc.MAX_SHORT_PROSE_CHARS + 1)}"',
            )
        )
    )

    assert f"the limit is {sc.MAX_SHORT_PROSE_CHARS}" in reason
    assert "reaches a director's prompt" in reason


def test_the_longest_authored_string_has_headroom_under_its_own_cap() -> None:
    """A cap tight against the shipped content is a cap the next author trips over."""
    company = sc.load_default()
    longest = max(
        len(text)
        for person in company.people
        for text in (person.deflection, *person.voice.values())
    )

    assert longest < sc.MAX_PROSE_CHARS // 2


@pytest.mark.parametrize(
    ("codepoint", "why"),
    [
        pytest.param(0x00, "NUL", id="nul"),
        pytest.param(0x09, "tab", id="tab"),
        pytest.param(0x0A, "newline", id="newline"),
        pytest.param(0x1B, "escape", id="escape"),
        pytest.param(0x7F, "delete", id="delete"),
        pytest.param(0x9B, "C1 control sequence introducer", id="c1"),
        pytest.param(0x202E, "right-to-left override", id="bidi-override"),
        pytest.param(0x200B, "zero-width space", id="zero-width"),
        pytest.param(0x2060, "word joiner", id="word-joiner"),
        pytest.param(0xFEFF, "byte-order mark", id="bom"),
    ],
)
def test_a_string_that_reaches_a_prompt_rejects_control_characters(
    codepoint: int, why: str
) -> None:
    """R9, and the first half of R19.

    Format characters are refused alongside control characters, and that is the interesting
    half: a right-to-left override or a zero-width joiner is printable-looking text that changes
    what a reviewer sees without changing what a model reads. A scenario arrives by pull
    request, so text that reads one way to the reviewer and another to the prompt is exactly the
    carrier worth closing.
    """
    escaped = f"\\u{codepoint:04X}"
    reason = refusal(
        variant(('title = "Accounts Payable"', f'title = "Accounts{escaped}Payable"'))
    )

    assert f"U+{codepoint:04X}" in reason, why
    assert "control and format characters are refused" in reason


def test_a_newline_inside_authored_text_is_refused() -> None:
    """Authored copy is one line. A multi-line prompt is a scenario asking for the prompt's own
    structure, which R19 reserves to the assembler."""
    reason = refusal(
        variant(('note = "Automation rejected"', 'note = """Automation\nrejected"""'))
    )

    assert "U+000A" in reason


@pytest.mark.parametrize(
    "name",
    [
        "../default",
        "../../etc/passwd",
        "sub/default",
        "sub\\default",
        ".",
        "..",
        "",
        "default.toml",
        "Default",
        "défaut",
        "default\x00",
        "/absolute",
        "~/default",
    ],
)
def test_a_scenario_name_is_refused_before_the_filesystem_is_touched(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R9's path rule, proven by making every filesystem call explode.

    Checking the name and *then* comparing resolved parents is the usual shape and it is the one
    with the interesting failure modes — a symlink, a case-insensitive mount, an error message
    that reveals whether a path outside the directory exists. Refusing on the name means none of
    those questions arises.
    """

    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("the filesystem was touched before the name was checked")

    for attribute in ("read_bytes", "read_text", "open", "exists", "is_file", "is_dir", "glob"):
        monkeypatch.setattr(Path, attribute, explode)

    with pytest.raises(sc.ScenarioNotFound):
        sc.resolve(name)
    with pytest.raises(sc.ScenarioNotFound):
        sc.load(name)


def test_an_accepted_name_resolves_inside_the_scenarios_directory() -> None:
    assert sc.resolve("default") == sc.SCENARIO_DIR / "default.toml"
    assert sc.resolve("acme", directory=Path("/tmp/x")) == Path("/tmp/x/acme.toml")


def test_an_unknown_but_well_formed_name_is_refused_and_lists_what_there_is() -> None:
    with pytest.raises(sc.ScenarioNotFound) as caught:
        sc.load("acme")

    reason = str(caught.value)
    assert "no scenario named 'acme'" in reason
    assert "default" in reason


def test_a_file_larger_than_the_bound_is_refused_before_it_is_parsed() -> None:
    with pytest.raises(sc.ScenarioInvalid) as caught:
        sc.parse(b"#" * (sc.MAX_FILE_BYTES + 1), name="default")

    assert "Refused before parsing" in str(caught.value)


def test_an_id_that_disagrees_with_the_filename_is_refused() -> None:
    """The id is what a run records and reloads by, so it cannot be a second name."""
    reason = refusal(variant(('id = "default"', 'id = "elsewhere"')))

    assert "loaded as 'default'" in reason


def test_a_file_declaring_another_schema_version_is_refused() -> None:
    reason = refusal(variant(("schema = 1", "schema = 2")))

    assert f"this build reads scenario schema {sc.SCENARIO_SCHEMA_VERSION}" in reason


def test_a_file_that_is_not_toml_is_refused_with_the_parser_s_position() -> None:
    with pytest.raises(sc.ScenarioInvalid) as caught:
        sc.parse(b'schema = 1\nid = "default"\n[[person\n', name="default")

    reason = str(caught.value)
    assert "not valid TOML" in reason
    assert "line 3" in reason


# =========================================================================
# The rest of what the format will not let a scenario say
# =========================================================================


def test_a_checkpoint_offering_more_options_than_a_comparison_runs_is_refused() -> None:
    extra = "".join(
        f'[[item.checkpoint.option]]\nlabel = "Extra {n}"\ndetail = "d"\nnote = "n"\n\n'
        for n in range(sc.MAX_OPTIONS_PER_CHECKPOINT)
    )
    reason = refusal(
        variant(
            (
                '[[item.checkpoint.option]]\nlabel = "Let the team decide"',
                extra + '[[item.checkpoint.option]]\nlabel = "Let the team decide"',
            )
        )
    )

    assert f"between {sc.MIN_OPTIONS_PER_CHECKPOINT} and {sc.MAX_OPTIONS_PER_CHECKPOINT}" in reason


def test_the_option_bound_is_the_comparison_s_branch_bound() -> None:
    """Two constants that have to be one number.

    A comparison runs one branch per option and refuses a checkpoint offering more than
    `MAX_BRANCHES_PER_COMPARISON`. If the loader allowed a wider one, the scenario would ship a
    decision the comparison surface has to refuse to open — a rejection the player meets rather
    than an authoring error the loader catches.
    """
    assert sc.MAX_OPTIONS_PER_CHECKPOINT == sim.MAX_BRANCHES_PER_COMPARISON


def test_the_format_asks_for_exactly_the_four_questions_the_kernel_matches() -> None:
    assert sc.VOICE_SLOTS == roster.ASK_SLOTS


#: One extra item, appended rather than spliced in: array-of-tables entries may appear anywhere
#: in a TOML document, so adding one at the end leaves every existing entry untouched.
ONE_OPTION_ITEM = """
[[item]]
id = "wi_spare"
title = "The one with nothing to weigh"
brief = "A checkpoint that offers no alternative."
room = "sales"
want = "dir_sales"
effort_hours = 2
friction = "Nothing."
output_title = "Nothing much"
output_kind = "Process map"

[[item.checkpoint]]
at_percent = 50
kind = "info"
label = "Information"
prompt = "Do the only thing?"
tacit = "...there was never a choice."

[[item.checkpoint.option]]
label = "The only thing"
detail = "There is no other."
note = "Did the only thing"
"""


def test_a_checkpoint_with_one_option_is_refused() -> None:
    reason = refusal(SHIPPED + ONE_OPTION_ITEM)

    assert "1 options" in reason
    assert "a notification rather than a decision" in reason


def test_two_options_sharing_a_label_are_refused() -> None:
    reason = refusal(variant(('label = "Paper is the record"', 'label = "PDF is the record"')))

    assert "two options share a label" in reason
    assert "names the option by its label" in reason


def test_checkpoints_that_do_not_ascend_are_refused() -> None:
    reason = refusal(variant(("at_percent = 75", "at_percent = 30")))

    assert "they have to ascend and cannot repeat" in reason


def test_a_prerequisite_cycle_is_refused() -> None:
    reason = refusal(
        variant(
            (
                "unlocks = []\nvisit_meeting = false\nfinal = false\n"
                "effect = { visibility = 4 }",
                'unlocks = ["wi_ap_map"]\nvisit_meeting = false\nfinal = false\n'
                "effect = { visibility = 4 }",
            ),
            ('requires = { items = [] }', 'requires = { items = ["wi_ap_auto"] }'),
        )
    )

    assert "form a cycle" in reason


def test_an_unlocks_edge_with_no_matching_requires_is_refused() -> None:
    """The kernel gates on `requires`; the client's graph draws `unlocks`. One edge, two views."""
    reason = refusal(variant(('unlocks = ["wi_ap_auto"]', 'unlocks = ["wi_faq"]')))

    assert "does not require" in reason
    assert "have to agree" in reason


def test_a_draw_change_on_an_item_completion_effect_is_refused() -> None:
    """It would load and then raise, hours into a run, the first time that item finished."""
    reason = refusal(
        variant(("effect = { visibility = 10 }", "effect = { visibility = 10, draw = -5 }"))
    )

    assert "belongs on an option" in reason


def test_an_effect_naming_something_that_is_not_a_metric_is_refused() -> None:
    reason = refusal(variant(("effect = { visibility = 3 }", "effect = { headcount = 3 }")))

    assert "'headcount', which is not a metric" in reason


def test_seeding_a_gated_item_is_refused() -> None:
    """Seeded work is work the company was already doing, so it has to be startable."""
    reason = refusal(variant(('item = "wi_hiring"', 'item = "wi_close"')))

    assert "cannot already be in flight at genesis" in reason


def test_two_people_sharing_an_id_are_refused() -> None:
    reason = refusal(variant(('id = "stf_field"', 'id = "stf_order"')))

    assert "already declared at person[1]" in reason


def test_a_scenario_with_a_missing_voice_line_is_refused() -> None:
    reason = refusal(
        variant(
            (
                'axis = "Credit and discounts are mine. Everything else sits with the '
                'account owner."\n',
                "",
            )
        )
    )

    assert "voice has no 'axis' line" in reason
    assert "get nothing back" in reason


# =========================================================================
# R8: the loader adds no dependency
# =========================================================================


def test_the_loader_imports_nothing_outside_the_standard_library() -> None:
    """R8, as a property of the module rather than as a claim about a lockfile diff.

    TOML was chosen over YAML for exactly this: `tomllib` is in the 3.12 standard library, so
    the format decision is what discharges the requirement. A parser arriving as a dependency
    would be caught here rather than in a review of `uv.lock`.
    """
    tree = ast.parse((BACKEND / "packages" / "simcore" / "scenario.py").read_text())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])

    first_party = {"simcore", "contracts"}
    stdlib = set(sys.stdlib_module_names)

    assert roots - first_party - stdlib == set()
    assert "tomllib" in roots


# =========================================================================
# M16: what a scenario names is description, and there is nothing to run it
# =========================================================================


def test_a_persons_tools_are_text_and_a_person_is_data() -> None:
    """The description carries no behaviour for a name to be dispatched through.

    `Person` is a frozen record with one derived property and no methods. There is no
    `invoke`, no handler map and no resolver, so "descriptive" is a property of the type rather
    than a convention the next unit has to remember.
    """
    company = sc.load_default()

    for person in company.people:
        for named in (*person.tools, *person.mcp_servers, *person.skills):
            assert isinstance(named, str) and named
            assert len(named) <= sc.MAX_TAG_CHARS

    behaviour = [
        name
        for name in vars(sc.Person)
        if not name.startswith("__") and callable(getattr(sc.Person, name, None))
    ]
    assert behaviour == [], f"Person has grown behaviour: {behaviour}"


def test_nothing_in_the_kernel_library_can_execute_what_a_scenario_names() -> None:
    """M16, as the absence of a mechanism rather than the absence of a call.

    `simcore` is the only package that holds a loaded scenario, and the guarantee worth having
    is that there is nothing in it a tool name could be dispatched *through*. The import-boundary
    suite already keeps transport and the model gateway out; this keeps the general-purpose
    escape hatches out too, so "descriptive" is structural.

    Displaying a tool on a conversation surface is the client's job and costs nothing here.
    """
    forbidden_modules = {
        "subprocess",
        "importlib",
        "runpy",
        "socket",
        "pty",
        "multiprocessing",
        "ctypes",
    }
    # Split by how they are spelled, because `re.compile` is not `compile`: a builtin escape
    # hatch is a bare name, while the `os` ones are always reached through the module.
    forbidden_builtins = {"eval", "exec", "compile", "__import__"}
    forbidden_attributes = {"system", "popen", "spawn", "spawnv", "execv", "fork"}

    offences: list[str] = []
    for path in sorted((BACKEND / "packages" / "simcore").glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in forbidden_modules:
                        offences.append(f"{path.name} imports {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.split(".")[0] in forbidden_modules:
                    offences.append(f"{path.name} imports from {node.module}")
            elif isinstance(node, ast.Call):
                target = node.func
                if isinstance(target, ast.Name) and target.id in forbidden_builtins:
                    offences.append(f"{path.name}:{node.lineno} calls {target.id}")
                elif isinstance(target, ast.Attribute) and target.attr in forbidden_attributes:
                    offences.append(f"{path.name}:{node.lineno} calls .{target.attr}")

    assert not offences, (
        "M16: a tool, MCP server or skill named in a scenario is description, and nothing in "
        "the kernel library may be able to execute one. Found: " + "; ".join(offences)
    )


# =========================================================================
# Loading twice, and loading two companies at once
# =========================================================================


def test_a_scenario_edited_between_two_loads_is_read_again(directory: Path) -> None:
    """The cache is keyed on content, not on name.

    Which is what makes the R7 guard work at all: a name-keyed cache would hand back the
    pre-edit company and report a match, and the run would fold against a file nobody was
    running.
    """
    before = sc.load("default", directory=directory)
    (directory / "default.toml").write_text(
        variant(('title = "Accounts Payable"', 'title = "Accounts Payable II"'))
    )
    after = sc.load("default", directory=directory)

    assert after.content_hash != before.content_hash
    assert after.person("stf_ap").title == "Accounts Payable II"


def test_two_scenarios_load_side_by_side_without_sharing_anything(directory: Path) -> None:
    """One process holds many companies, so none of them is *the* company.

    This is the loader half of the reason the scenario is carried on `State` rather than in a
    module global: a global would be one company shared across runs of different ones, and the
    symptom would be a run seating people from somebody else's roster.
    """
    (directory / "minimal.toml").write_text(MINIMAL)

    shipped = sc.load("default", directory=directory)
    small = sc.load("minimal", directory=directory)

    assert shipped.content_hash != small.content_hash
    assert shipped.people_by_id.keys() != small.people_by_id.keys()
    assert shipped.items_by_id.keys() != small.items_by_id.keys()
    assert sc.load("default", directory=directory).content_hash == shipped.content_hash
    assert sc.load("minimal", directory=directory).content_hash == small.content_hash
    assert sorted(sc.available(directory)) == ["default", "minimal"]


def _seats_of(company: sc.Scenario) -> dict[str, tuple[int, int]]:
    """Seat resolution, walking the scenario's roster in order.

    A local copy of `people.assign_seats`'s traversal rather than a call to it: that function
    still reads the module-level roster in this pass, so calling it would seat the constant
    rather than the file. Both walk the roster in order and take the first free slot at or after
    each person's own, which is the property this file's ordering carries.
    """
    floor = plan_floor(DEFAULT_COLS, DEFAULT_ROWS)
    taken: set[tuple[int, int]] = set()
    seats: dict[str, tuple[int, int]] = {}

    for person in company.people:
        room = next((candidate for candidate in floor.rooms if candidate.id == person.dept), None)
        slots = room.slots if room else []
        spot = next(
            (
                slot
                for index, slot in enumerate(slots)
                if index >= person.slot and slot not in taken
            ),
            None,
        ) or next((slot for slot in slots if slot not in taken), None)
        assert spot is not None, person.id
        taken.add(spot)
        seats[person.id] = spot

    return seats
