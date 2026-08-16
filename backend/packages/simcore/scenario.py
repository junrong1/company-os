"""A company is a file: the scenario format, its loader, and its identity.

Before this module the roster and the work graph were module constants — `people.PEOPLE`,
`items.ITEMS` — which meant "add a company" was a code change (M9, M13). They are now
authored in TOML under `backend/scenarios/`, and this is the only thing that reads them.

**TOML through `tomllib`.** In the standard library on 3.12, so R8's no-new-dependency rule
is satisfied by the format choice rather than by a vendored parser. It is also
comment-friendly and diff-friendly, which matters because the authoring flow is a pull
request: a reviewer has to be able to see what changed about a company.

**A scenario is refused whole or loaded whole** (M12, R9). Validation is a pass over plain
parsed data that collects every failure it can find, and no `Scenario`, `Person` or
`ItemSpec` is constructed until that pass reports nothing. The alternative — a
try-per-record loop — leaves a half-built company behind on the first bad line, and the
half that loaded is the half nobody looked at.

**Every key is checked against a closed set.** An unrecognised key is a refusal, not
something ignored. That is what keeps a `base_url` or an `api_key` from ever appearing in a
scenario file: a file arriving by pull request cannot introduce a setting by being ahead of
the loader, and U13's CI reasoning depends on that being mechanical.

**People are an ordered list, not a keyed map**, and the content hash covers that order.
Roster order decides who wins a contested desk (`people.assign_seats` walks it in order),
while `canonical.encode` sorts keys unconditionally — so a map keyed by person id would sort
the ordering out of the hash while seating still moved with it. The same argument applies to
items, whose order is the order the catalog reaches the client in.

**The hash covers the fully-defaulted, validated structure.** A field the author omitted is
in the hash with its shipped value, which makes a default a *scenario* field with a shipped
value rather than a loader constant that could change underneath a recorded run. Reformatting
the file — comments, whitespace, key order inside a table, an inline table spelled out long —
therefore changes nothing; reordering two people changes it.

**Selection is by name, resolved inside the scenarios directory** (R9). The name is checked
against a closed character set *before any filesystem access*, so a caller-supplied
`../../etc/passwd` is refused by the name rule rather than by a path comparison after the
fact.

**Every authored string is length-capped and rejects control characters** (R9), because
every one of them reaches a prompt eventually: a director's brief is assembled from item
titles, checkpoint prompts, option details and the responsibility and tool lists on the
roster. R19 makes that text *data* inside the prompt rather than instruction, and the
delimiting is U11's job — but the cap and the character rule are this loader's, and they are
the guarantee U11 builds on. See `MAX_PROSE_CHARS` and `_check_text`.

Nothing here executes anything a scenario names. Tools, MCP servers and skills are
description (M16): they say what a person is understood to have, they shape what a director
claims it could do and what the report counts, and no code path in this repository turns one
into a call.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from simcore import hashing
from simcore import items as work
from simcore.world import ROOM_PLAN

# =========================================================================
# Versions, and where scenarios live
# =========================================================================

#: The format version a file declares. Bumped when the *format* changes in a way an existing
#: file would not survive; a file declaring a different one is refused rather than guessed at.
SCENARIO_SCHEMA_VERSION = 1

#: The canonicalisation version the content hash is computed under (R7).
#:
#: Separate from the schema version, and the separation is the point: a run records both, so
#: "the file changed" and "the way we hash files changed" are distinguishable at the guard.
#: Without it, improving the canonical form would read as every existing run's scenario having
#: been edited.
SCENARIO_HASH_VERSION = 1

#: Where a name is resolved. `packages/simcore/scenario.py` -> `backend/`, which is also
#: `/app` in the image — so `COPY scenarios/ scenarios/` in the Dockerfile is what makes a
#: containerised run resolve the default.
SCENARIO_DIR = Path(__file__).resolve().parents[2] / "scenarios"

#: The scenario a run gets when nothing names one.
DEFAULT_SCENARIO = "default"

SCENARIO_SUFFIX = ".toml"

# =========================================================================
# What the rules fix, and what a scenario may therefore not move (M11)
# =========================================================================

#: The four reporting lines a scenario staffs. Fixed, and a scenario declaring a fifth is
#: refused.
#:
#: A line is identified by the room its director sits in, rather than by a fresh identifier:
#: `hiring.target_room_for` already seats a new member of a line in their director's room, so
#: a separate line id would be a second name for a thing the floor plan already names — and
#: the two could then disagree.
REPORTING_LINES: tuple[str, ...] = ("sales", "admin", "support", "hr")

#: Every room on the floor, by id. Eight, fixed by the generator (M11). A person or an item
#: naming anything else is refused; a scenario cannot add a room, because `[[room]]` is not in
#: the closed key set.
ROOMS: dict[str, int] = {room.id: room.seats for room in ROOM_PLAN}

#: The four questions a person can be asked, and therefore the four lines of voice every
#: person authors. Kept here rather than imported from `people.ASK_INTENTS` so that the
#: *format* declares what a file must contain; a test asserts the two agree.
VOICE_SLOTS: tuple[str, ...] = ("why", "exception", "axis", "bottleneck")

#: What a checkpoint's `kind` may be. The client renders a different heading per kind.
CHECKPOINT_KINDS: frozenset[str] = frozenset({"info", "approval", "decision"})

RANKS: frozenset[str] = frozenset({"director", "staff"})

# =========================================================================
# Bounds (R9)
# =========================================================================

#: Read before parsing, so a hostile file is refused without being handed to the parser.
MAX_FILE_BYTES = 512 * 1024

MAX_NAME_CHARS = 64
MAX_ID_CHARS = 48
MAX_INITIALS_CHARS = 4
MAX_TITLE_CHARS = 96
MAX_LABEL_CHARS = 48

#: One line of authored prose: a brief, a prompt, a tacit line, an option detail, a scripted
#: answer. Sized at roughly three times the longest line the shipped company holds, so the
#: cap bounds what reaches a prompt without being a limit an author bumps into.
MAX_PROSE_CHARS = 512

#: A phrase rather than a sentence: a responsibility, an option note, a deliverable title.
MAX_SHORT_PROSE_CHARS = 256

#: One entry in a tools, MCP server or skills list, and how many a person may hold. These are
#: names, and a list long enough to fill a prompt on its own is a scenario using the
#: description field as a payload.
MAX_TAG_CHARS = 64
MAX_TAGS = 12

MAX_PEOPLE = 40
MAX_ITEMS = 40
MAX_CHECKPOINTS_PER_ITEM = 8

#: Must equal `step.MAX_BRANCHES_PER_COMPARISON`, and a test asserts it does. A comparison
#: runs one branch per option and refuses a checkpoint offering more, so a scenario authoring
#: a wider checkpoint would ship a decision the comparison surface cannot open. Refusing at
#: load makes that an authoring error rather than a command rejection the player meets later.
#:
#: Not imported from `simcore.step`: `step` loads a scenario, so the import would be a cycle.
MAX_OPTIONS_PER_CHECKPOINT = 6

#: Two, because a checkpoint is a choice. One option is a notification wearing a decision's
#: clothes, and the tray would offer the CEO nothing to weigh.
MIN_OPTIONS_PER_CHECKPOINT = 2

MAX_EFFORT_HOURS = 400
MAX_DRAW_HOURS_PER_MONTH = 2_000

#: Bounds an authored metric delta and an authored draw change. Generous — the largest shipped
#: figure is 120 — and it exists so a single option cannot move a metric by more than the
#: metric's own display range can express.
MAX_ABS_EFFECT = 10_000

#: How many refusals one message carries before it stops listing them. An author fixing a
#: file wants all of them, and a file with two hundred is a file that is not this format.
MAX_REPORTED_FAILURES = 25

#: A scenario name: lowercase, digits, dash and underscore, starting alphanumeric.
#:
#: Uppercase is excluded deliberately rather than incidentally. On a case-insensitive
#: filesystem `Default` and `default` are one file with two names, so the recorded scenario id
#: would stop being a single value — and the genesis guard compares ids.
_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class ScenarioInvalid(Exception):
    """This file is not a scenario, and the message says every way in which it is not.

    Raised before anything is constructed, so a refused scenario leaves nothing half-loaded
    (M12, R9).
    """


class ScenarioMismatch(Exception):
    """A run's recorded scenario is not the one on disk (R7).

    Not a failure to retry. The run's numbers were produced by a company that no longer
    exists in that form, so folding on would reproduce neither. The message names both sides,
    says what moved, and names the remedy.
    """


# =========================================================================
# The records a scenario is made of
# =========================================================================


@dataclass(frozen=True, slots=True)
class Department:
    """One reporting line, and the recurring workload it carries.

    `id` is one of the four fixed lines; `director` is the person who heads it. The draw is in
    hours per month — the same unit the Manual work metric displays, so the sum of the four
    *is* that metric's starting value rather than a second figure that could disagree with it.
    """

    id: str
    director: str
    draw_hours_per_month: int


@dataclass(frozen=True, slots=True)
class Person:
    """One person on the roster, as a scenario authors them.

    `dept` is the *room* they sit in. `mgr` is the reporting line they are in. They disagree
    for Priya on the shipped roster, on purpose: seating comes from the room, while
    assignment, delegation and capacity all follow the reporting line, and a real org chart
    has people whose desk and whose manager are two different answers.

    `responsibility`, `tools`, `mcp_servers` and `skills` are authored for everyone, whether
    or not a model ever answers for them (M15), and every one of them is description rather
    than capability (M16). `voice` is the four scripted answers, `deflection` the line for a
    question that matched none of them.
    """

    id: str
    name: str
    initials: str
    title: str
    dept: str
    mgr: str
    rank: str
    slot: int
    responsibility: str
    tools: tuple[str, ...]
    mcp_servers: tuple[str, ...]
    skills: tuple[str, ...]
    deflection: str
    voice: dict[str, str] = field(default_factory=dict)

    @property
    def is_director(self) -> bool:
        return self.rank == "director"


@dataclass(frozen=True, slots=True)
class Scenario:
    """A whole company, validated, with its identity.

    Immutable, and carried on `State` as a fold-time input that the state hash excludes: the
    genesis event already records `content_hash`, so hashing the same structure a second time
    inside the state would move the state-shape version to carry a value that cannot change
    within a run.

    The index fields are built once by the loader. `spec()`-style lookups happen per person
    per tick, so rebuilding them per call would put an O(roster) scan inside the tick.
    """

    scenario_id: str
    title: str
    summary: str
    content_hash: str
    hash_version: int
    schema_version: int
    departments: tuple[Department, ...]
    people: tuple[Person, ...]
    items: tuple[work.ItemSpec, ...]
    seeded: tuple[work.SeededAssignment, ...]
    #: Person id -> record, in roster order.
    people_by_id: dict[str, Person]
    #: Item id -> spec, in authored order.
    items_by_id: dict[str, work.ItemSpec]
    #: Director id -> every member of that line, the director first, then staff in roster
    #: order. The director is a member, not an overseer of one: a draw allocated across
    #: non-directors only would have nobody to allocate across in a one-person line.
    lines: dict[str, tuple[str, ...]]
    #: Director id -> monthly draw, in department declaration order.
    draws: dict[str, int]

    # --- identity ------------------------------------------------------

    def identity(self) -> dict[str, Any]:
        """What genesis records, and what the guard compares (R7)."""
        return {
            "id": self.scenario_id,
            "content_hash": self.content_hash,
            "hash_ver": self.hash_version,
        }

    def __deepcopy__(self, memo: dict[int, Any]) -> Scenario:
        """Itself. A scenario is immutable, so a copy protects against nothing.

        `log._clone` deep-copies state so a resumed fold cannot mutate the snapshot it came
        from. Without this, every resume and every branch would also copy the whole company —
        ten people, eight items and forty scripted lines — to defend against a mutation the
        frozen records make impossible.
        """
        memo[id(self)] = self
        return self

    # --- the roster ----------------------------------------------------

    def person(self, person_id: str) -> Person:
        try:
            return self.people_by_id[person_id]
        except KeyError:
            raise KeyError(f"no person {person_id!r} on the roster") from None

    def knows(self, person_id: str) -> bool:
        """Whether this person is authored. An arrived hire is not."""
        return person_id in self.people_by_id

    @property
    def directors(self) -> tuple[str, ...]:
        """Director ids in roster order, which is the order `reporting_lines` used."""
        return tuple(person.id for person in self.people if person.is_director)

    def reporting_line_of(self, person_id: str) -> str:
        """The director whose line this person is in. A director is their own line."""
        person = self.person(person_id)
        return person.mgr or person.id

    def direct_reports(self, director_id: str) -> tuple[str, ...]:
        return tuple(person.id for person in self.people if person.mgr == director_id)

    def same_reporting_line(self, a: str, b: str) -> bool:
        """Whether a reassignment between these two stays inside one line."""
        return self.reporting_line_of(a) == self.reporting_line_of(b)

    def room_of_line(self, director_id: str) -> str:
        """Which room a new member of this line sits in: the director's own."""
        return self.person(director_id).dept

    def voice_of(self, person_id: str, slot: str) -> str:
        """This person's scripted reply for one of the four questions."""
        return self.person(person_id).voice[slot]

    def deflection_of(self, person_id: str) -> str:
        """This person's line for a question they cannot answer. Raises for a stranger."""
        return self.person(person_id).deflection

    # --- the work ------------------------------------------------------

    def item(self, item_id: str) -> work.ItemSpec:
        try:
            return self.items_by_id[item_id]
        except KeyError:
            raise KeyError(f"no work item {item_id!r}") from None

    @property
    def total_checkpoints(self) -> int:
        """The run's decision supply, and the real bound on a useful horizon."""
        return sum(len(item.checkpoints) for item in self.items)

    @property
    def initial_manual_hours(self) -> int:
        """The Manual work metric on day one: the sum of the department draws (R49)."""
        return sum(self.draws.values())


# =========================================================================
# Where in the file, and what was wrong with it
# =========================================================================

_HEADER = re.compile(r"^\s*\[\[?([A-Za-z0-9_.]+)\]?\]")


class _Where:
    """Best-effort line numbers for a refusal.

    A scenario arrives by pull request, so the difference between "person[4] is wrong" and
    "default.toml:112 is wrong" is the difference between reading the file and jumping to it.

    `tomllib` reports a position for a *syntax* error and nothing for a semantic one — it
    returns plain dicts and lists with no provenance — so the position is recovered from the
    raw text instead: find the nth occurrence of a table header, then the key inside it.
    Best-effort by construction, and it degrades to naming the entry rather than guessing: a
    miss returns 0 and the message simply carries no line.
    """

    def __init__(self, text: str) -> None:
        self.lines = text.splitlines()
        self.headers = [
            (index, match.group(1))
            for index, line in enumerate(self.lines)
            if (match := _HEADER.match(line))
        ]

    def at(
        self,
        steps: tuple[tuple[str, int], ...],
        key: str | None = None,
        *,
        table: bool = False,
    ) -> int:
        """The 1-based line of `steps`, or of `key` inside it. 0 if it cannot be found.

        `steps` are (dotted header name, ordinal) pairs, the ordinal counted inside the window
        the previous step opened — so `(("item", 6), ("item.checkpoint", 1))` is the second
        checkpoint of the seventh item.

        `table` says the key is spelled as a header rather than as an assignment, which is how
        an unknown `[[room]]` is found: searching for `room =` would otherwise land on the first
        person's `room` key, several hundred lines from the mistake.
        """
        low, high = 0, len(self.lines)

        for name, ordinal in steps:
            found = [
                index
                for index, header in self.headers
                if low <= index < high and header == name
            ]
            if ordinal >= len(found):
                return 0
            low = found[ordinal]
            # The window ends at the next header that is not a child of this one, so a
            # `[person.voice]` subtable stays inside its `[[person]]`.
            high = next(
                (
                    index
                    for index, header in self.headers
                    if index > low and not header.startswith(f"{name}.")
                ),
                len(self.lines),
            )

        if key is None:
            return low + 1

        escaped = re.escape(key)
        as_header = re.compile(rf"^\s*\[\[?{escaped}(\]|\.)")
        as_assignment = re.compile(rf"^\s*{escaped}\s*=")
        # Tried in the order the caller says is likelier, then the other way, so a wrong guess
        # degrades to a slightly-off line rather than to no line at all.
        for pattern in (as_header, as_assignment) if table else (as_assignment, as_header):
            for index in range(low, high):
                if pattern.match(self.lines[index]):
                    return index + 1
        return low + 1


class _Failures:
    """Every reason a file is refused, collected rather than raised one at a time.

    Collecting is what makes the refusal a single pass (M12): the loader reaches the end of
    the file, then decides. Raising on the first would also mean an author fixing a ten-error
    file runs the loader ten times, and each run tells them one thing.

    `refuse()` is called between the two phases rather than only at the end: the
    cross-reference checks read ids the shape checks validated, so running them over data that
    failed its own type check would report invented failures about the real one.
    """

    def __init__(self, where: _Where, origin: str) -> None:
        self._where = where
        self._origin = origin
        self._reasons: list[tuple[int, str]] = []

    def add(
        self,
        reason: str,
        *,
        steps: tuple[tuple[str, int], ...] = (),
        key: str | None = None,
        table: bool = False,
    ) -> None:
        self._reasons.append((self._where.at(steps, key, table=table), reason))

    def refuse(self, name: str, *, more_to_come: bool = False) -> None:
        """Raise if anything was collected. Called before anything is constructed.

        `more_to_come` says the cross-reference phase has not run, because it reads ids this
        phase was meant to validate. Saying so is worth a sentence: without it an author who
        fixes everything the first refusal listed is surprised by a second refusal, and reads it
        as the loader having missed something.
        """
        if not self._reasons:
            return

        shown = self._reasons[:MAX_REPORTED_FAILURES]
        listed = "\n".join(
            f"  {self._origin}:{line}: {reason}" if line else f"  {self._origin}: {reason}"
            for line, reason in shown
        )
        elided = len(self._reasons) - len(shown)
        tail = f"\n  ... and {elided} more" if elided else ""
        if more_to_come:
            tail += (
                "\n  (Rooms, managers, prerequisites and seating are checked once the above are "
                "fixed: those checks read the ids these fields were supposed to carry.)"
            )

        raise ScenarioInvalid(
            f"scenario {name!r} is refused and nothing was loaded — a scenario loads whole or "
            f"not at all, so no partial company exists.\n{listed}{tail}"
        )


def _describe(steps: tuple[tuple[str, int], ...], label: str = "") -> str:
    """`person[4] 'stf_ap'` — the entry a reason is about, for the message's prefix."""
    path = ".".join(f"{name.rsplit('.', 1)[-1]}[{ordinal}]" for name, ordinal in steps)
    path = path or "the scenario"
    return f"{path} {label}" if label else path


# =========================================================================
# Field checks
# =========================================================================


def _control_character(text: str) -> str:
    """The first character that must never reach a prompt, described, or "" if there is none.

    Rejects every Unicode category beginning with C: control characters, *format* characters,
    surrogates, private use and unassigned codepoints. Format characters are the reason the
    rule is not simply "codepoint below 0x20" — a right-to-left override or a zero-width
    joiner is printable-looking text that changes what a reader sees without changing what a
    model reads, which is exactly the shape of a prompt-injection carrier (R19).

    Newlines and tabs are refused with the rest. Authored copy is one line of prose; a scenario
    that needs a line break in a prompt is a scenario asking for the prompt's own structure.
    """
    for character in text:
        if unicodedata.category(character).startswith("C"):
            return f"U+{ord(character):04X} ({unicodedata.category(character)})"
    return ""


def _check_text(
    failures: _Failures,
    value: Any,
    *,
    limit: int,
    steps: tuple[tuple[str, int], ...],
    key: str,
    label: str = "",
    required: bool = True,
) -> str:
    """A string field that reaches a prompt: typed, bounded and printable (R9).

    Returns the value, or "" once it has recorded why it is unusable — so the caller can go on
    collecting failures from the rest of the entry instead of stopping at the first bad field.
    """
    where = _describe(steps, label)

    if not isinstance(value, str):
        failures.add(
            f"{where}: {key} is {type(value).__name__}; it has to be a string",
            steps=steps,
            key=key,
        )
        return ""

    if required and not value.strip():
        failures.add(f"{where}: {key} is empty", steps=steps, key=key)
        return ""

    if len(value) > limit:
        failures.add(
            f"{where}: {key} is {len(value)} characters and the limit is {limit}. Authored "
            "text reaches a director's prompt, so its length is bounded where it is authored "
            "rather than where it is assembled.",
            steps=steps,
            key=key,
        )
        return ""

    found = _control_character(value)
    if found:
        failures.add(
            f"{where}: {key} contains {found}. Authored text is one line of printable prose: "
            "control and format characters are refused because they change what a reviewer "
            "sees without changing what a model reads.",
            steps=steps,
            key=key,
        )
        return ""

    return value


def _check_id(
    failures: _Failures,
    value: Any,
    *,
    steps: tuple[tuple[str, int], ...],
    key: str,
    label: str = "",
    allow_empty: bool = False,
) -> str:
    """An identifier: lowercase, digits and underscore, and short.

    Bounded to a charset rather than merely to a length because ids are interpolated into
    refusal messages, log payloads and — from U11 — prompts. An id is a name the system uses
    on itself, so it has no business carrying punctuation.
    """
    where = _describe(steps, label)

    if not isinstance(value, str):
        failures.add(
            f"{where}: {key} is {type(value).__name__}; an id is a string", steps=steps, key=key
        )
        return ""

    if allow_empty and value == "":
        return ""

    if not re.fullmatch(r"[a-z][a-z0-9_]*", value) or len(value) > MAX_ID_CHARS:
        failures.add(
            f"{where}: {key} is {value!r}, which is not an id. Lowercase letters, digits and "
            f"underscores, starting with a letter, at most {MAX_ID_CHARS} characters.",
            steps=steps,
            key=key,
        )
        return ""

    return value


def _check_int(
    failures: _Failures,
    value: Any,
    *,
    low: int,
    high: int,
    steps: tuple[tuple[str, int], ...],
    key: str,
    label: str = "",
) -> int:
    """An integer in range. `bool` is refused: TOML has a boolean type and it is not this."""
    where = _describe(steps, label)

    if isinstance(value, bool) or not isinstance(value, int):
        failures.add(
            f"{where}: {key} is {type(value).__name__}; it has to be an integer",
            steps=steps,
            key=key,
        )
        return low

    if not low <= value <= high:
        failures.add(
            f"{where}: {key} is {value}, outside {low}..{high}", steps=steps, key=key
        )
        return low

    return value


def _check_bool(
    failures: _Failures,
    value: Any,
    *,
    steps: tuple[tuple[str, int], ...],
    key: str,
    label: str = "",
) -> bool:
    if isinstance(value, bool):
        return value
    failures.add(
        f"{_describe(steps, label)}: {key} is {type(value).__name__}; it has to be true or false",
        steps=steps,
        key=key,
    )
    return False


def _check_tags(
    failures: _Failures,
    value: Any,
    *,
    steps: tuple[tuple[str, int], ...],
    key: str,
    label: str,
) -> tuple[str, ...]:
    """A tools, MCP-server or skills list. Description only (M16); nothing here is invoked."""
    where = _describe(steps, label)

    if not isinstance(value, list):
        failures.add(
            f"{where}: {key} is {type(value).__name__}; it has to be a list of names",
            steps=steps,
            key=key,
        )
        return ()

    if len(value) > MAX_TAGS:
        failures.add(
            f"{where}: {key} names {len(value)} entries and the limit is {MAX_TAGS}. These "
            "are names that reach a prompt, not a payload.",
            steps=steps,
            key=key,
        )
        return ()

    tags: list[str] = []
    for entry in value:
        checked = _check_text(
            failures, entry, limit=MAX_TAG_CHARS, steps=steps, key=key, label=label
        )
        if checked:
            tags.append(checked)
    return tuple(tags)


def _check_keys(
    failures: _Failures,
    table: dict[str, Any],
    allowed: frozenset[str],
    *,
    steps: tuple[tuple[str, int], ...],
    label: str = "",
) -> None:
    """The closed set (R9).

    An unknown key is refused rather than ignored, and that is the whole reason this function
    exists: a scenario file arrives by pull request, and a loader that ignored keys it did not
    recognise would let a `base_url` or an `api_key` sit in one indefinitely — present, review-
    approved, and read by nothing until something started reading it.
    """
    for key in sorted(set(table) - allowed):
        value = table[key]
        spelled_as_header = isinstance(value, dict) or (
            isinstance(value, list) and bool(value) and isinstance(value[0], dict)
        )
        failures.add(
            f"{_describe(steps, label)}: {key!r} is not a key this format has. The set is "
            f"closed; the keys here are {', '.join(sorted(allowed))}.",
            steps=steps,
            key=key,
            table=spelled_as_header,
        )


def _check_missing(
    failures: _Failures,
    table: dict[str, Any],
    required: frozenset[str],
    *,
    steps: tuple[tuple[str, int], ...],
    label: str = "",
) -> bool:
    missing = sorted(required - set(table))
    for key in missing:
        failures.add(f"{_describe(steps, label)}: {key} is missing", steps=steps)
    return not missing


# =========================================================================
# The closed key sets (R9)
# =========================================================================
#
# One frozenset per table. Adding a field to the format means adding it here *and* bumping
# SCENARIO_SCHEMA_VERSION, which is the same discipline `hashing.SHAPE_HISTORY` and
# `envelope.KIND_SCHEMA_VERSIONS` impose on their own additions: a new key changes what a
# scenario means, so it is a deliberate edit rather than a consequence of writing one.

_SCENARIO_KEYS = frozenset(
    {"schema", "id", "title", "summary", "department", "person", "item", "seeded_assignment"}
)
_SCENARIO_REQUIRED = frozenset({"schema", "id", "title", "department", "person", "item"})

_DEPARTMENT_KEYS = frozenset({"id", "director", "draw_hours_per_month"})

_PERSON_KEYS = frozenset(
    {
        "id",
        "name",
        "initials",
        "title",
        "room",
        "manager",
        "rank",
        "seat_slot",
        "responsibility",
        "tools",
        "mcp_servers",
        "skills",
        "deflection",
        "voice",
    }
)
_PERSON_REQUIRED = frozenset(
    {
        "id",
        "name",
        "initials",
        "title",
        "room",
        "rank",
        "seat_slot",
        "responsibility",
        "deflection",
        "voice",
    }
)

_ITEM_KEYS = frozenset(
    {
        "id",
        "title",
        "brief",
        "room",
        "want",
        "effort_hours",
        "friction",
        "output_title",
        "output_kind",
        "unlocks",
        "visit_meeting",
        "final",
        "effect",
        "requires",
        "checkpoint",
    }
)
_ITEM_REQUIRED = frozenset(
    {
        "id",
        "title",
        "brief",
        "room",
        "want",
        "effort_hours",
        "friction",
        "output_title",
        "output_kind",
    }
)

_REQUIRES_KEYS = frozenset({"visibility", "items"})
_CHECKPOINT_KEYS = frozenset({"at_percent", "kind", "label", "prompt", "tacit", "option"})
_OPTION_KEYS = frozenset({"label", "detail", "note", "effect"})
_SEEDED_KEYS = frozenset({"item", "person", "done_percent"})


def _check_effect(
    failures: _Failures,
    value: Any,
    *,
    steps: tuple[tuple[str, int], ...],
    label: str,
    allow_draw: bool,
) -> dict[str, int]:
    """An authored effect: integer deltas keyed by metric, plus `draw` on an option.

    `draw` is permitted on an *option* and refused on an item, and the asymmetry is not
    arbitrary. An option's effect is split before it is applied — the draw goes to the owning
    department and the rest to the metrics (R59) — while an item's completion effect is handed
    straight to `effects.apply_effect`, which refuses a key that is not a metric. So a `draw`
    on an item would be a scenario that loads and then raises the first time that item
    finished, hours into a run.
    """
    from simcore import effects

    where = _describe(steps, label)
    permitted = set(effects.METRIC_KEYS) | ({effects.DRAW_KEY} if allow_draw else set())

    if not isinstance(value, dict):
        failures.add(
            f"{where}: effect is {type(value).__name__}; it is a table of integer deltas",
            steps=steps,
            key="effect",
        )
        return {}

    effect: dict[str, int] = {}
    for key, delta in value.items():
        if key not in permitted:
            hint = (
                f" `{effects.DRAW_KEY}` is an option's recurring-draw change and belongs on an "
                "option, not on an item's completion effect."
                if key == effects.DRAW_KEY
                else ""
            )
            failures.add(
                f"{where}: effect names {key!r}, which is not a metric. The metrics are "
                f"{', '.join(sorted(permitted))}.{hint}",
                steps=steps,
                key="effect",
            )
            continue
        effect[key] = _check_int(
            failures,
            delta,
            low=-MAX_ABS_EFFECT,
            high=MAX_ABS_EFFECT,
            steps=steps,
            key="effect",
            label=label,
        )
    return effect


def _check_id_list(
    failures: _Failures,
    value: Any,
    *,
    steps: tuple[tuple[str, int], ...],
    key: str,
    label: str,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        failures.add(
            f"{_describe(steps, label)}: {key} is {type(value).__name__}; it has to be a list "
            "of item ids",
            steps=steps,
            key=key,
        )
        return ()
    return tuple(
        checked
        for entry in value
        if (checked := _check_id(failures, entry, steps=steps, key=key, label=label))
    )


# =========================================================================
# Phase one: shape. Every entry, every field, no cross-references yet.
# =========================================================================


def _read_departments(failures: _Failures, data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("department", [])
    if not isinstance(raw, list):
        failures.add("department has to be a list of [[department]] tables")
        return []

    if len(raw) != len(REPORTING_LINES):
        failures.add(
            f"the file declares {len(raw)} departments and there are exactly "
            f"{len(REPORTING_LINES)} reporting lines. The floor, the eight rooms and the four "
            f"lines are fixed by the rules (M11): a scenario staffs "
            f"{', '.join(REPORTING_LINES)} and cannot add a fifth or drop one.",
            steps=(("department", min(len(raw), len(REPORTING_LINES))),) if raw else (),
        )

    read: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        steps = (("department", index),)
        if not isinstance(entry, dict):
            failures.add(f"department[{index}] is not a table", steps=steps)
            continue

        _check_keys(failures, entry, _DEPARTMENT_KEYS, steps=steps)
        _check_missing(failures, entry, _DEPARTMENT_KEYS, steps=steps)

        line = _check_id(failures, entry.get("id", ""), steps=steps, key="id")
        label = f"{line!r}" if line else ""
        if line and line not in REPORTING_LINES:
            failures.add(
                f"department[{index}]: id is {line!r}, which is not one of the four reporting "
                f"lines. They are fixed: {', '.join(REPORTING_LINES)}.",
                steps=steps,
                key="id",
            )
        read.append(
            {
                "id": line,
                "director": _check_id(
                    failures, entry.get("director", ""), steps=steps, key="director", label=label
                ),
                "draw_hours_per_month": _check_int(
                    failures,
                    entry.get("draw_hours_per_month", 0),
                    low=0,
                    high=MAX_DRAW_HOURS_PER_MONTH,
                    steps=steps,
                    key="draw_hours_per_month",
                    label=label,
                ),
            }
        )
    return read


def _read_people(failures: _Failures, data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("person", [])
    if not isinstance(raw, list):
        failures.add("person has to be a list of [[person]] tables")
        return []

    if not raw:
        failures.add("the file declares nobody; a company is at least its four directors")
    if len(raw) > MAX_PEOPLE:
        failures.add(f"the file declares {len(raw)} people and the limit is {MAX_PEOPLE}")

    read: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        steps = (("person", index),)
        if not isinstance(entry, dict):
            failures.add(f"person[{index}] is not a table", steps=steps)
            continue

        _check_keys(failures, entry, _PERSON_KEYS, steps=steps)
        _check_missing(failures, entry, _PERSON_REQUIRED, steps=steps)

        person_id = _check_id(failures, entry.get("id", ""), steps=steps, key="id")
        label = f"{person_id!r}" if person_id else ""

        rank = entry.get("rank", "")
        if rank not in RANKS:
            failures.add(
                f"{_describe(steps, label)}: rank is {rank!r}; it is 'director' or 'staff'",
                steps=steps,
                key="rank",
            )
            rank = ""

        voice = entry.get("voice", {})
        lines: dict[str, str] = {}
        if not isinstance(voice, dict):
            failures.add(
                f"{_describe(steps, label)}: voice is {type(voice).__name__}; it is a "
                f"[person.voice] table with one line per question",
                steps=steps,
                key="voice",
            )
        else:
            _check_keys(failures, voice, frozenset(VOICE_SLOTS), steps=steps, label=label)
            for slot in VOICE_SLOTS:
                if slot not in voice:
                    failures.add(
                        f"{_describe(steps, label)}: voice has no {slot!r} line. Every person "
                        f"answers all four questions ({', '.join(VOICE_SLOTS)}); a missing one "
                        "would be a person the CEO can ask something and get nothing back.",
                        steps=steps,
                        key="voice",
                    )
                    continue
                lines[slot] = _check_text(
                    failures,
                    voice[slot],
                    limit=MAX_PROSE_CHARS,
                    steps=steps,
                    key=slot,
                    label=label,
                )

        read.append(
            {
                "id": person_id,
                "name": _check_text(
                    failures,
                    entry.get("name", ""),
                    limit=MAX_NAME_CHARS,
                    steps=steps,
                    key="name",
                    label=label,
                ),
                "initials": _check_text(
                    failures,
                    entry.get("initials", ""),
                    limit=MAX_INITIALS_CHARS,
                    steps=steps,
                    key="initials",
                    label=label,
                ),
                "title": _check_text(
                    failures,
                    entry.get("title", ""),
                    limit=MAX_TITLE_CHARS,
                    steps=steps,
                    key="title",
                    label=label,
                ),
                "room": _check_id(
                    failures, entry.get("room", ""), steps=steps, key="room", label=label
                ),
                # Defaulted, and the default is a shipped value in the hash: a director has no
                # manager, and writing `manager = ""` on all four would be ceremony.
                "manager": _check_id(
                    failures,
                    entry.get("manager", ""),
                    steps=steps,
                    key="manager",
                    label=label,
                    allow_empty=True,
                ),
                "rank": rank,
                "seat_slot": _check_int(
                    failures,
                    entry.get("seat_slot", 0),
                    low=0,
                    high=max(ROOMS.values()) - 1,
                    steps=steps,
                    key="seat_slot",
                    label=label,
                ),
                "responsibility": _check_text(
                    failures,
                    entry.get("responsibility", ""),
                    limit=MAX_SHORT_PROSE_CHARS,
                    steps=steps,
                    key="responsibility",
                    label=label,
                ),
                "tools": _check_tags(
                    failures, entry.get("tools", []), steps=steps, key="tools", label=label
                ),
                "mcp_servers": _check_tags(
                    failures,
                    entry.get("mcp_servers", []),
                    steps=steps,
                    key="mcp_servers",
                    label=label,
                ),
                "skills": _check_tags(
                    failures, entry.get("skills", []), steps=steps, key="skills", label=label
                ),
                "deflection": _check_text(
                    failures,
                    entry.get("deflection", ""),
                    limit=MAX_PROSE_CHARS,
                    steps=steps,
                    key="deflection",
                    label=label,
                ),
                "voice": lines,
            }
        )
    return read


def _read_requires(
    failures: _Failures,
    value: Any,
    *,
    steps: tuple[tuple[str, int], ...],
    label: str,
) -> dict[str, Any]:
    """`requires = { visibility = 24, items = ["wi_ap_map"] }`, both halves optional."""
    if not isinstance(value, dict):
        failures.add(
            f"{_describe(steps, label)}: requires is {type(value).__name__}; it is a table with "
            "an optional visibility gate and an optional list of prerequisite items",
            steps=steps,
            key="requires",
        )
        return {"visibility": None, "items": ()}

    _check_keys(failures, value, _REQUIRES_KEYS, steps=steps, label=label)

    visibility: int | None = None
    if "visibility" in value:
        visibility = _check_int(
            failures,
            value["visibility"],
            low=0,
            high=100,
            steps=steps,
            key="requires",
            label=label,
        )

    return {
        "visibility": visibility,
        "items": _check_id_list(
            failures, value.get("items", []), steps=steps, key="requires", label=label
        ),
    }


def _read_checkpoints(
    failures: _Failures,
    raw: Any,
    *,
    item_steps: tuple[tuple[str, int], ...],
    label: str,
) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        failures.add(
            f"{_describe(item_steps, label)}: checkpoint has to be a list of "
            "[[item.checkpoint]] tables",
            steps=item_steps,
        )
        return []

    if len(raw) > MAX_CHECKPOINTS_PER_ITEM:
        failures.add(
            f"{_describe(item_steps, label)}: {len(raw)} checkpoints, and the limit is "
            f"{MAX_CHECKPOINTS_PER_ITEM}",
            steps=item_steps,
        )

    read: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        steps = (*item_steps, ("item.checkpoint", index))
        if not isinstance(entry, dict):
            failures.add(f"{_describe(steps, label)} is not a table", steps=steps)
            continue

        _check_keys(failures, entry, _CHECKPOINT_KEYS, steps=steps, label=label)
        _check_missing(failures, entry, _CHECKPOINT_KEYS, steps=steps, label=label)

        kind = entry.get("kind", "")
        if kind not in CHECKPOINT_KINDS:
            failures.add(
                f"{_describe(steps, label)}: kind is {kind!r}; it is one of "
                f"{', '.join(sorted(CHECKPOINT_KINDS))}",
                steps=steps,
                key="kind",
            )
            kind = ""

        options = _read_options(failures, entry.get("option", []), cp_steps=steps, label=label)

        read.append(
            {
                # Percent, never a fraction: the comparison that decides whether someone stops
                # and waits for you cross-multiplies, so there is no float on that path.
                "at_percent": _check_int(
                    failures,
                    entry.get("at_percent", 0),
                    low=1,
                    high=100,
                    steps=steps,
                    key="at_percent",
                    label=label,
                ),
                "kind": kind,
                "label": _check_text(
                    failures,
                    entry.get("label", ""),
                    limit=MAX_LABEL_CHARS,
                    steps=steps,
                    key="label",
                    label=label,
                ),
                "prompt": _check_text(
                    failures,
                    entry.get("prompt", ""),
                    limit=MAX_PROSE_CHARS,
                    steps=steps,
                    key="prompt",
                    label=label,
                ),
                "tacit": _check_text(
                    failures,
                    entry.get("tacit", ""),
                    limit=MAX_PROSE_CHARS,
                    steps=steps,
                    key="tacit",
                    label=label,
                ),
                "option": options,
            }
        )

    # Strictly ascending, so the step's "first unresolved checkpoint whose threshold is past"
    # search visits them in the order the author wrote them. Two checkpoints at one percent
    # would both be reached by the same unit of effort and the second would raise the instant
    # the first was settled, which reads as one decision asked twice.
    percents = [checkpoint["at_percent"] for checkpoint in read]
    if percents != sorted(set(percents)):
        failures.add(
            f"{_describe(item_steps, label)}: checkpoint percentages are {percents}; they have "
            "to ascend and cannot repeat.",
            steps=item_steps,
        )

    return read


def _read_options(
    failures: _Failures,
    raw: Any,
    *,
    cp_steps: tuple[tuple[str, int], ...],
    label: str,
) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        failures.add(
            f"{_describe(cp_steps, label)}: option has to be a list of "
            "[[item.checkpoint.option]] tables",
            steps=cp_steps,
        )
        return []

    if not MIN_OPTIONS_PER_CHECKPOINT <= len(raw) <= MAX_OPTIONS_PER_CHECKPOINT:
        failures.add(
            f"{_describe(cp_steps, label)}: {len(raw)} options. A checkpoint offers between "
            f"{MIN_OPTIONS_PER_CHECKPOINT} and {MAX_OPTIONS_PER_CHECKPOINT} — fewer is a "
            "notification rather than a decision, and more than the comparison surface runs "
            "branches for would ship a decision it has to refuse to open.",
            steps=cp_steps,
        )

    read: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        steps = (*cp_steps, ("item.checkpoint.option", index))
        if not isinstance(entry, dict):
            failures.add(f"{_describe(steps, label)} is not a table", steps=steps)
            continue

        _check_keys(failures, entry, _OPTION_KEYS, steps=steps, label=label)
        _check_missing(
            failures, entry, _OPTION_KEYS - {"effect"}, steps=steps, label=label
        )

        read.append(
            {
                "label": _check_text(
                    failures,
                    entry.get("label", ""),
                    limit=MAX_LABEL_CHARS,
                    steps=steps,
                    key="label",
                    label=label,
                ),
                "detail": _check_text(
                    failures,
                    entry.get("detail", ""),
                    limit=MAX_PROSE_CHARS,
                    steps=steps,
                    key="detail",
                    label=label,
                ),
                # What the deliverable's provenance records: the sentence that survives the run.
                "note": _check_text(
                    failures,
                    entry.get("note", ""),
                    limit=MAX_SHORT_PROSE_CHARS,
                    steps=steps,
                    key="note",
                    label=label,
                ),
                "effect": _check_effect(
                    failures, entry.get("effect", {}), steps=steps, label=label, allow_draw=True
                ),
            }
        )

    labels = [option["label"] for option in read if option["label"]]
    if len(set(labels)) != len(labels):
        failures.add(
            f"{_describe(cp_steps, label)}: two options share a label. A resolution arriving "
            "from a director names the option by its label, so duplicates would make one "
            "answer ambiguous.",
            steps=cp_steps,
        )

    return read


def _read_items(failures: _Failures, data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("item", [])
    if not isinstance(raw, list):
        failures.add("item has to be a list of [[item]] tables")
        return []

    if not raw:
        failures.add(
            "the file declares no work. A company with nothing in flight has no decision to "
            "put in front of the CEO, which is the whole of what a run is."
        )
    if len(raw) > MAX_ITEMS:
        failures.add(f"the file declares {len(raw)} items and the limit is {MAX_ITEMS}")

    read: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        steps = (("item", index),)
        if not isinstance(entry, dict):
            failures.add(f"item[{index}] is not a table", steps=steps)
            continue

        _check_keys(failures, entry, _ITEM_KEYS, steps=steps)
        _check_missing(failures, entry, _ITEM_REQUIRED, steps=steps)

        item_id = _check_id(failures, entry.get("id", ""), steps=steps, key="id")
        label = f"{item_id!r}" if item_id else ""

        read.append(
            {
                "id": item_id,
                "title": _check_text(
                    failures,
                    entry.get("title", ""),
                    limit=MAX_TITLE_CHARS,
                    steps=steps,
                    key="title",
                    label=label,
                ),
                "brief": _check_text(
                    failures,
                    entry.get("brief", ""),
                    limit=MAX_PROSE_CHARS,
                    steps=steps,
                    key="brief",
                    label=label,
                ),
                "room": _check_id(
                    failures, entry.get("room", ""), steps=steps, key="room", label=label
                ),
                "want": _check_id(
                    failures, entry.get("want", ""), steps=steps, key="want", label=label
                ),
                "effort_hours": _check_int(
                    failures,
                    entry.get("effort_hours", 0),
                    low=1,
                    high=MAX_EFFORT_HOURS,
                    steps=steps,
                    key="effort_hours",
                    label=label,
                ),
                "friction": _check_text(
                    failures,
                    entry.get("friction", ""),
                    limit=MAX_SHORT_PROSE_CHARS,
                    steps=steps,
                    key="friction",
                    label=label,
                ),
                "output_title": _check_text(
                    failures,
                    entry.get("output_title", ""),
                    limit=MAX_SHORT_PROSE_CHARS,
                    steps=steps,
                    key="output_title",
                    label=label,
                ),
                "output_kind": _check_text(
                    failures,
                    entry.get("output_kind", ""),
                    limit=MAX_LABEL_CHARS,
                    steps=steps,
                    key="output_kind",
                    label=label,
                ),
                "unlocks": _check_id_list(
                    failures, entry.get("unlocks", []), steps=steps, key="unlocks", label=label
                ),
                "visit_meeting": _check_bool(
                    failures,
                    entry.get("visit_meeting", False),
                    steps=steps,
                    key="visit_meeting",
                    label=label,
                ),
                "final": _check_bool(
                    failures, entry.get("final", False), steps=steps, key="final", label=label
                ),
                "effect": _check_effect(
                    failures,
                    entry.get("effect", {}),
                    steps=steps,
                    label=label,
                    allow_draw=False,
                ),
                "requires": _read_requires(
                    failures, entry.get("requires", {}), steps=steps, label=label
                ),
                "checkpoint": _read_checkpoints(
                    failures, entry.get("checkpoint", []), item_steps=steps, label=label
                ),
            }
        )
    return read


def _read_seeded(failures: _Failures, data: dict[str, Any]) -> list[dict[str, Any]]:
    """Work already in flight when the CEO walked in (M6).

    Progress is authored in percent of the item's own effort, which is the unit checkpoints are
    already authored in — so a seed authored *at* a checkpoint's percent makes the opening stop
    structural rather than arithmetic that happens to land.
    """
    raw = data.get("seeded_assignment", [])
    if not isinstance(raw, list):
        failures.add("seeded_assignment has to be a list of [[seeded_assignment]] tables")
        return []

    read: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        steps = (("seeded_assignment", index),)
        if not isinstance(entry, dict):
            failures.add(f"seeded_assignment[{index}] is not a table", steps=steps)
            continue

        _check_keys(failures, entry, _SEEDED_KEYS, steps=steps)
        _check_missing(failures, entry, _SEEDED_KEYS, steps=steps)

        read.append(
            {
                "item": _check_id(failures, entry.get("item", ""), steps=steps, key="item"),
                "person": _check_id(
                    failures, entry.get("person", ""), steps=steps, key="person"
                ),
                "done_percent": _check_int(
                    failures,
                    entry.get("done_percent", 0),
                    low=0,
                    high=100,
                    steps=steps,
                    key="done_percent",
                ),
            }
        )
    return read


# =========================================================================
# Phase two: does it describe one company?
# =========================================================================
#
# Everything here reads ids the shape pass already validated, which is why it runs only after
# that pass reported nothing: a cross-reference check against a field that failed its own type
# check would report a second, invented failure about the first one.


def _cross_check(
    failures: _Failures,
    departments: list[dict[str, Any]],
    people: list[dict[str, Any]],
    items: list[dict[str, Any]],
    seeded: list[dict[str, Any]],
) -> None:
    person_ids = {person["id"] for person in people}
    item_ids = {item["id"] for item in items}
    directors = {department["director"]: department for department in departments}

    _cross_check_departments(failures, departments, people, person_ids)
    _cross_check_people(failures, people, directors)
    _cross_check_items(failures, items, person_ids, item_ids)
    _cross_check_seeded(failures, seeded, person_ids, item_ids, items)


def _cross_check_departments(
    failures: _Failures,
    departments: list[dict[str, Any]],
    people: list[dict[str, Any]],
    person_ids: set[str],
) -> None:
    by_id = {person["id"]: person for person in people}

    seen: dict[str, int] = {}
    for index, department in enumerate(departments):
        steps = (("department", index),)
        line = department["id"]

        if line in seen:
            failures.add(
                f"department[{index}]: the {line!r} line is already declared at "
                f"department[{seen[line]}]. One department per reporting line.",
                steps=steps,
                key="id",
            )
        seen[line] = index

        director_id = department["director"]
        if director_id not in person_ids:
            failures.add(
                f"department[{index}] {line!r}: director is {director_id!r}, who is not on the "
                "roster.",
                steps=steps,
                key="director",
            )
            continue

        director = by_id[director_id]
        if director["rank"] != "director":
            failures.add(
                f"department[{index}] {line!r}: {director_id!r} heads the line but is authored "
                f"as {director['rank']!r}. A person is a director exactly when a department "
                "names them as one.",
                steps=steps,
                key="director",
            )
        if director["manager"]:
            failures.add(
                f"department[{index}] {line!r}: {director_id!r} heads the line and also reports "
                f"to {director['manager']!r}. A line's head is the top of it.",
                steps=steps,
                key="director",
            )
        if director["room"] != line:
            failures.add(
                f"department[{index}] {line!r}: its director sits in {director['room']!r}. A "
                f"line is named by the room its director sits in — a new member of this line is "
                f"seated there, so the two cannot disagree.",
                steps=steps,
                key="director",
            )


def _cross_check_people(
    failures: _Failures, people: list[dict[str, Any]], directors: dict[str, dict[str, Any]]
) -> None:
    seen: dict[str, int] = {}
    occupancy: dict[str, list[str]] = {}

    for index, person in enumerate(people):
        steps = (("person", index),)
        person_id = person["id"]
        label = f"{person_id!r}"

        if person_id in seen:
            failures.add(
                f"person[{index}] {label}: already declared at person[{seen[person_id]}]",
                steps=steps,
                key="id",
            )
        seen[person_id] = index

        room = person["room"]
        if room not in ROOMS:
            failures.add(
                f"person[{index}] {label}: room is {room!r}, which is not a room on this floor. "
                f"The eight rooms are fixed (M11): {', '.join(sorted(ROOMS))}. A scenario "
                "cannot add one.",
                steps=steps,
                key="room",
            )
        else:
            occupancy.setdefault(room, []).append(person_id)
            if person["seat_slot"] >= ROOMS[room]:
                failures.add(
                    f"person[{index}] {label}: seat_slot is {person['seat_slot']} and {room!r} has "
                    f"{ROOMS[room]} desks (slots 0..{ROOMS[room] - 1}).",
                    steps=steps,
                    key="seat_slot",
                )

        is_head = person_id in directors
        if is_head != (person["rank"] == "director"):
            failures.add(
                f"person[{index}] {label}: rank is {person['rank']!r} but "
                + (
                    "no department names them as its director."
                    if person["rank"] == "director"
                    else f"the {directors[person_id]['id']!r} department names them as "
                    "its director."
                ),
                steps=steps,
                key="rank",
            )

        manager = person["manager"]
        if manager and manager not in directors:
            known = ", ".join(sorted(directors)) or "none are declared"
            failures.add(
                f"person[{index}] {label}: manager is {manager!r}, who does not head one of the "
                f"four reporting lines ({known}). Assignment, delegation and capacity all "
                "follow the reporting line, so it has to end at a director.",
                steps=steps,
                key="manager",
            )
        if manager == person_id:
            failures.add(
                f"person[{index}] {label}: reports to themselves", steps=steps, key="manager"
            )

    # Seats against floor capacity. Desks are generated per room from a fixed plan, and the
    # fallbacks in `assign_seats` exist for a hire arriving at runtime — not for an authored
    # roster, where a room holding more people than it has desks is an authoring mistake that
    # would otherwise show up as somebody sitting on the spawn tile.
    #
    # Deliberately *not* a uniqueness check on seat_slot. Two people may claim one desk, and
    # roster order then decides who gets it: that is the property the content hash covers
    # people as an ordered list for, and forbidding it would make the ordering unobservable.
    for room, occupants in sorted(occupancy.items()):
        if len(occupants) > ROOMS[room]:
            failures.add(
                f"{room!r} seats {ROOMS[room]} and {len(occupants)} people are authored into it "
                f"({', '.join(occupants)}). The floor is fixed, so the roster has to fit it.",
                steps=(("person", seen[occupants[ROOMS[room]]]),),
                key="room",
            )


def _cross_check_items(
    failures: _Failures,
    items: list[dict[str, Any]],
    person_ids: set[str],
    item_ids: set[str],
) -> None:
    seen: dict[str, int] = {}
    prerequisites: dict[str, tuple[str, ...]] = {}

    for index, item in enumerate(items):
        steps = (("item", index),)
        item_id = item["id"]
        label = f"{item_id!r}"

        if item_id in seen:
            failures.add(
                f"item[{index}] {label}: already declared at item[{seen[item_id]}]",
                steps=steps,
                key="id",
            )
        seen[item_id] = index
        prerequisites[item_id] = item["requires"]["items"]

        if item["room"] not in ROOMS:
            failures.add(
                f"item[{index}] {label}: room is {item['room']!r}, which is not a room on this "
                f"floor. The eight rooms are fixed: {', '.join(sorted(ROOMS))}.",
                steps=steps,
                key="room",
            )

        if item["want"] not in person_ids:
            failures.add(
                f"item[{index}] {label}: want is {item['want']!r}, who is not on the roster. "
                "The person an item wants is who the work is assigned to, and whose reporting "
                "line carries its load.",
                steps=steps,
                key="want",
            )

        for required in item["requires"]["items"]:
            if required == item_id:
                failures.add(
                    f"item[{index}] {label}: requires itself", steps=steps, key="requires"
                )
            elif required not in item_ids:
                failures.add(
                    f"item[{index}] {label}: requires {required!r}, which is not an item this "
                    "scenario declares. A prerequisite that resolves to nothing is a gate that "
                    "never opens.",
                    steps=steps,
                    key="requires",
                )

        for unlocked in item["unlocks"]:
            if unlocked not in item_ids:
                failures.add(
                    f"item[{index}] {label}: unlocks {unlocked!r}, which is not an item this "
                    "scenario declares.",
                    steps=steps,
                    key="unlocks",
                )

    # `unlocks` and `requires.items` are two views of one edge, and they have to agree. The
    # kernel gates on `requires`; the client's dependency graph draws `unlocks`. An edge in one
    # and not the other is either a line on screen that gates nothing or a gate with no line —
    # and both look like the graph lying about the work.
    declared = {
        (item["id"], unlocked) for item in items for unlocked in item["unlocks"]
    }
    gated = {
        (required, item["id"]) for item in items for required in item["requires"]["items"]
    }
    for source, target in sorted(declared - gated):
        if source in item_ids and target in item_ids:
            failures.add(
                f"item {source!r} unlocks {target!r}, but {target!r} does not require "
                f"{source!r}. The two describe one edge and have to agree.",
                steps=(("item", seen[source]),),
                key="unlocks",
            )
    for source, target in sorted(gated - declared):
        if source in item_ids and target in item_ids:
            failures.add(
                f"item {target!r} requires {source!r}, but {source!r} does not list it under "
                f"unlocks. The two describe one edge and have to agree.",
                steps=(("item", seen[target]),),
                key="requires",
            )

    cycle = _find_cycle(prerequisites)
    if cycle:
        failures.add(
            "the prerequisites form a cycle: " + " -> ".join(cycle) + ". Nothing in it could "
            "ever unlock, and the dependency graph assigns layers by longest-path depth, which "
            "a cycle has no answer for.",
            steps=(("item", seen[cycle[0]]),),
            key="requires",
        )

    finals = [item["id"] for item in items if item["final"]]
    if len(finals) > 1:
        failures.add(
            f"{len(finals)} items are marked final ({', '.join(finals)}). `final` is the piece "
            "of work the run is aimed at, so there is one of it."
        )


def _find_cycle(edges: dict[str, tuple[str, ...]]) -> list[str]:
    """One prerequisite cycle, as a path, or an empty list. Iterative, so depth cannot bite."""
    WHITE, GREY, BLACK = 0, 1, 2
    colour = dict.fromkeys(edges, WHITE)

    for root in edges:
        if colour[root] != WHITE:
            continue
        stack: list[tuple[str, int]] = [(root, 0)]
        path: list[str] = [root]
        colour[root] = GREY

        while stack:
            node, cursor = stack[-1]
            children = edges.get(node, ())
            if cursor >= len(children):
                colour[node] = BLACK
                stack.pop()
                path.pop()
                continue
            stack[-1] = (node, cursor + 1)
            child = children[cursor]
            if colour.get(child, BLACK) == GREY:
                return [*path[path.index(child) :], child]
            if colour.get(child, BLACK) == WHITE:
                colour[child] = GREY
                stack.append((child, 0))
                path.append(child)

    return []


def _cross_check_seeded(
    failures: _Failures,
    seeded: list[dict[str, Any]],
    person_ids: set[str],
    item_ids: set[str],
    items: list[dict[str, Any]],
) -> None:
    by_id = {item["id"]: item for item in items}
    claimed: dict[str, int] = {}

    for index, entry in enumerate(seeded):
        steps = (("seeded_assignment", index),)
        item_id = entry["item"]

        if item_id not in item_ids:
            failures.add(
                f"seeded_assignment[{index}]: item is {item_id!r}, which this scenario does not "
                "declare",
                steps=steps,
                key="item",
            )
        elif item_id in claimed:
            failures.add(
                f"seeded_assignment[{index}]: {item_id!r} is already seeded at "
                f"seeded_assignment[{claimed[item_id]}]. One item is in flight once.",
                steps=steps,
                key="item",
            )
        else:
            claimed[item_id] = index
            gate = by_id[item_id]["requires"]
            if gate["items"] or gate["visibility"]:
                failures.add(
                    f"seeded_assignment[{index}]: {item_id!r} is gated behind "
                    f"{'a prerequisite' if gate['items'] else 'a visibility threshold'} and "
                    "cannot already be in flight at genesis. Seeded work is work the company "
                    "was already doing, so it has to be work the company could have started.",
                    steps=steps,
                    key="item",
                )

        if entry["person"] not in person_ids:
            failures.add(
                f"seeded_assignment[{index}]: person is {entry['person']!r}, who is not on "
                "the roster",
                steps=steps,
                key="person",
            )


# =========================================================================
# The canonical form, and the hash over it (R7)
# =========================================================================


def _canonical_form(header: dict[str, Any], **sections: list[dict[str, Any]]) -> dict[str, Any]:
    """The structure the content hash is taken over.

    Built from the *validated, fully-defaulted* data rather than from the parsed file, and both
    halves of that matter. Fully-defaulted, so a field the author omitted is in the hash with
    its shipped value — which makes a default a scenario field rather than a loader constant
    that could be changed underneath a run that recorded a hash without it. Validated, so the
    hash is only ever computed for a file that would load.

    Lists stay lists. `canonical.encode` sorts dictionary keys unconditionally, so keying people
    by id would have erased roster order from the hash while `assign_seats` still walked it —
    two files that seat people differently would have hashed the same.
    """
    return _plain(
        {
            # Inside the hash, for the same reason `hashing.state_hash` puts the shape version
            # inside the overall digest: two canonicalisations can then never collide.
            "hash_ver": SCENARIO_HASH_VERSION,
            **header,
            **sections,
        }
    )


def _plain(value: Any) -> Any:
    """Tuples to lists, recursively.

    A tuple and a list already encode identically, so this changes no digest — it is here so
    that the hashed structure is made of the types `contracts.canonical` documents, rather than
    of types that happen to survive the encoder.
    """
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _content_hash(form: dict[str, Any]) -> str:
    return hashing.digest(form)


def _build(
    header: dict[str, Any],
    departments: list[dict[str, Any]],
    people: list[dict[str, Any]],
    items: list[dict[str, Any]],
    seeded: list[dict[str, Any]],
    content_hash: str,
) -> Scenario:
    """Turn validated data into the frozen records. Reached only once nothing was refused."""
    roster = tuple(
        Person(
            id=entry["id"],
            name=entry["name"],
            initials=entry["initials"],
            title=entry["title"],
            dept=entry["room"],
            mgr=entry["manager"],
            rank=entry["rank"],
            slot=entry["seat_slot"],
            responsibility=entry["responsibility"],
            tools=entry["tools"],
            mcp_servers=entry["mcp_servers"],
            skills=entry["skills"],
            deflection=entry["deflection"],
            voice=dict(entry["voice"]),
        )
        for entry in people
    )

    catalog = tuple(
        work.ItemSpec(
            id=entry["id"],
            title=entry["title"],
            brief=entry["brief"],
            dept=entry["room"],
            want=entry["want"],
            effort_hours=entry["effort_hours"],
            friction=entry["friction"],
            checkpoints=tuple(
                work.Checkpoint(
                    at_percent=checkpoint["at_percent"],
                    kind=checkpoint["kind"],
                    label=checkpoint["label"],
                    prompt=checkpoint["prompt"],
                    options=tuple(
                        work.Option(
                            label=option["label"],
                            detail=option["detail"],
                            effect=dict(option["effect"]),
                            note=option["note"],
                        )
                        for option in checkpoint["option"]
                    ),
                    tacit=checkpoint["tacit"],
                )
                for checkpoint in entry["checkpoint"]
            ),
            output_title=entry["output_title"],
            output_kind=entry["output_kind"],
            effect=dict(entry["effect"]),
            requires=work.Requires(
                visibility=entry["requires"]["visibility"], items=entry["requires"]["items"]
            ),
            unlocks=entry["unlocks"],
            visit_meeting=entry["visit_meeting"],
            final=entry["final"],
        )
        for entry in items
    )

    # The director first, then their staff in roster order. The director is a member of their
    # own line rather than an overseer of it: two of the shipped lines hold one non-director
    # each, so a draw allocated across non-directors only would have nobody to allocate across
    # after a single attrition event, and the draw would vanish rather than press on somebody.
    lines: dict[str, list[str]] = {
        person.id: [person.id] for person in roster if person.is_director
    }
    for person in roster:
        if person.mgr:
            lines[person.mgr].append(person.id)

    return Scenario(
        scenario_id=header["id"],
        title=header["title"],
        summary=header["summary"],
        content_hash=content_hash,
        hash_version=SCENARIO_HASH_VERSION,
        schema_version=header["schema"],
        departments=tuple(
            Department(
                id=entry["id"],
                director=entry["director"],
                draw_hours_per_month=entry["draw_hours_per_month"],
            )
            for entry in departments
        ),
        people=roster,
        items=catalog,
        seeded=tuple(
            work.SeededAssignment(
                item_id=entry["item"],
                person_id=entry["person"],
                done_percent=entry["done_percent"],
            )
            for entry in seeded
        ),
        people_by_id={person.id: person for person in roster},
        items_by_id={item.id: item for item in catalog},
        lines={director: tuple(members) for director, members in lines.items()},
        draws={entry["director"]: entry["draw_hours_per_month"] for entry in departments},
    )


def _read_header(failures: _Failures, data: dict[str, Any], name: str) -> dict[str, Any]:
    _check_keys(failures, data, _SCENARIO_KEYS, steps=())
    _check_missing(failures, data, _SCENARIO_REQUIRED, steps=())

    scenario_id = _check_id(failures, data.get("id", ""), steps=(), key="id")
    if scenario_id and scenario_id != name:
        # The id is the value a run records at genesis and the guard compares, while the name is
        # how it was resolved. A file whose id disagrees with its filename would be recorded
        # under one name and reloaded under another.
        failures.add(
            f"id is {scenario_id!r} but this file was loaded as {name!r}. A scenario's id is its "
            "filename without the suffix — a run records the id and reloads by it.",
            key="id",
        )

    return {
        "schema": SCENARIO_SCHEMA_VERSION,
        "id": scenario_id,
        "title": _check_text(
            failures, data.get("title", ""), limit=MAX_TITLE_CHARS, steps=(), key="title"
        ),
        "summary": _check_text(
            failures,
            data.get("summary", ""),
            limit=MAX_PROSE_CHARS,
            steps=(),
            key="summary",
            required=False,
        ),
    }


def parse(raw: bytes, *, name: str, origin: str = "") -> Scenario:
    """Validate scenario bytes and build the company, or refuse and build nothing.

    Separated from `load` so the validator can be exercised without a file, and so `load` reads
    each file exactly once — the content hash is over the parsed structure, not over the bytes,
    so there is no second read to keep in step with the first.
    """
    origin = origin or f"{name}{SCENARIO_SUFFIX}"

    if len(raw) > MAX_FILE_BYTES:
        raise ScenarioInvalid(
            f"scenario {name!r} is {len(raw)} bytes and the limit is {MAX_FILE_BYTES}. Refused "
            "before parsing: a bound checked after the parser has already built the document "
            "bounds nothing."
        )

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ScenarioInvalid(
            f"scenario {name!r} is not UTF-8 ({exc}). A scenario is text a reviewer reads."
        ) from None

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ScenarioInvalid(
            f"scenario {name!r} is not valid TOML and nothing was loaded. {origin}: {exc}"
        ) from None

    where = _Where(text)
    failures = _Failures(where, origin)

    # The format version, on its own and before anything else. A file written against another
    # schema would otherwise be refused for forty unknown keys — every one of them a true
    # statement about this build and none of them the reason.
    if data.get("schema") != SCENARIO_SCHEMA_VERSION:
        failures.add(
            f"schema is {data.get('schema')!r}; this build reads scenario schema "
            f"{SCENARIO_SCHEMA_VERSION}. Refused rather than guessed at: a format version is "
            "how a file says which keys it means, so nothing below it can be checked against "
            "the right rules.",
            key="schema",
        )
        failures.refuse(name)

    header = _read_header(failures, data, name)
    departments = _read_departments(failures, data)
    people = _read_people(failures, data)
    items = _read_items(failures, data)
    seeded = _read_seeded(failures, data)

    # Cross-references read ids the pass above validated, so the pass above has to have passed.
    failures.refuse(name, more_to_come=True)
    _cross_check(failures, departments, people, items, seeded)
    failures.refuse(name)

    form = _canonical_form(
        header, departments=departments, people=people, items=items, seeded=seeded
    )
    return _build(header, departments, people, items, seeded, _content_hash(form))


# =========================================================================
# Resolving a name, and reading the file (R9)
# =========================================================================


class ScenarioNotFound(ScenarioInvalid):
    """No scenario of that name. A subclass, so one `except` still covers every refusal."""


def resolve(name: str, *, directory: Path | None = None) -> Path:
    """Turn a name into the file it names, or refuse. Touches the filesystem not at all.

    **The name is checked before any filesystem access**, which is the whole of R9's path rule.
    A caller-supplied `../../etc/passwd` is refused by the character set, so there is no
    `resolve()`-then-compare-parents step that could be got wrong, no symlink to race, and no
    error message that reveals whether a path outside the directory happens to exist.

    A scenario is therefore selected by *name* everywhere: the gateway takes a name, the genesis
    event records a name, and the fold reloads by name. No caller ever supplies a path.
    """
    if not isinstance(name, str) or not name:
        raise ScenarioNotFound(
            "no scenario name was given. A scenario is selected by name, resolved inside the "
            "scenarios directory."
        )

    if len(name) > MAX_NAME_CHARS:
        raise ScenarioNotFound(
            f"a scenario name is at most {MAX_NAME_CHARS} characters; that one is {len(name)}."
        )

    if not _SAFE_NAME.fullmatch(name):
        raise ScenarioNotFound(
            f"{name!r} is not a scenario name. A name is lowercase letters, digits, dashes and "
            "underscores, starting with a letter or a digit — so it cannot contain a path "
            "separator, a dot or a drive letter. Refused before the filesystem is touched: a "
            "scenario is chosen by name inside the scenarios directory, never by path."
        )

    return (directory or SCENARIO_DIR) / f"{name}{SCENARIO_SUFFIX}"


def available(directory: Path | None = None) -> tuple[str, ...]:
    """The names a caller may ask for, sorted. What an unknown-name refusal lists."""
    root = directory or SCENARIO_DIR
    if not root.is_dir():
        return ()
    return tuple(
        sorted(
            path.stem
            for path in root.glob(f"*{SCENARIO_SUFFIX}")
            if path.is_file() and _SAFE_NAME.fullmatch(path.stem)
        )
    )


#: Parsed scenarios, keyed by (resolved path, digest of the bytes that produced them).
#:
#: Content-addressed rather than name-addressed, and that is load-bearing twice over. A file
#: edited between two loads has different bytes, so it is parsed again — which is what lets the
#: R7 guard notice the edit at all; a name-keyed cache would hand back the pre-edit company and
#: report a match. And two runs on different scenarios can never collide, because the key
#: carries the path.
#:
#: One process ticks many runs, so the alternative this replaces — "the scenario" in a module
#: global — would have been a single company shared across runs of different ones.
_CACHE: dict[tuple[str, str], Scenario] = {}

#: Enough for the shipped scenario plus a handful a test or a session touches. A cache that
#: grew without bound would keep every edit of every file alive for the life of the process.
_CACHE_ENTRIES = 32


def load(name: str = DEFAULT_SCENARIO, *, directory: Path | None = None) -> Scenario:
    """The scenario of that name, validated and hashed, or a refusal that says why."""
    path = resolve(name, directory=directory)

    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        known = ", ".join(available(directory)) or "none"
        raise ScenarioNotFound(
            f"there is no scenario named {name!r}. The scenarios in "
            f"{(directory or SCENARIO_DIR)} are: {known}."
        ) from None
    except OSError as exc:
        raise ScenarioNotFound(f"scenario {name!r} could not be read: {exc}") from None

    key = (str(path), hashlib.blake2b(raw, digest_size=16).hexdigest())
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    scenario = parse(raw, name=name, origin=str(path))

    if len(_CACHE) >= _CACHE_ENTRIES:
        _CACHE.clear()
    _CACHE[key] = scenario
    return scenario


def load_default() -> Scenario:
    """The shipped company. What a run gets when nothing names a scenario."""
    return load(DEFAULT_SCENARIO)


# =========================================================================
# The guard: is this still the company the run was created against? (R7)
# =========================================================================
#
# Three sites check it, and they are three rather than one because each is a distinct way of
# obtaining state:
#
#   * the from-zero fold reads the identity off the genesis event it is replaying;
#   * a snapshot restore reads it off the snapshot, which bypasses the fold entirely;
#   * the resume-from-snapshot fold *skips genesis*, so without its own check it would be the
#     one path that carried a state built from one company into a process running another.
#
# `load_recorded` serves the first two — they hold a recorded identity and no structure.
# `verify_unchanged` serves the third, which holds a whole `Scenario` and can therefore say
# more about what moved.

#: The person fields a drift report compares. Deliberately not `seat`: a seat is derived from
#: the grid the run was created with, so comparing it would report a difference for two runs of
#: one scenario at two window sizes.
_PERSON_COMPARED = ("name", "initials", "title", "dept", "mgr", "rank")

#: The item fields a drift report compares. Enough to name what moved, and all of them present
#: in the catalog the genesis event already carries — so the recorded side needs no extra field.
_ITEM_COMPARED = ("title", "brief", "dept", "want", "effort_hours")


def _people_now(scenario: Scenario) -> dict[str, dict[str, Any]]:
    return {
        person.id: {field_name: getattr(person, field_name) for field_name in _PERSON_COMPARED}
        for person in scenario.people
    }


def _items_now(scenario: Scenario) -> dict[str, dict[str, Any]]:
    return {
        item.id: {
            **{field_name: getattr(item, field_name) for field_name in _ITEM_COMPARED},
            "checkpoints": [
                [checkpoint.at_percent, [option.label for option in checkpoint.options]]
                for checkpoint in item.checkpoints
            ],
        }
        for item in scenario.items
    }


def _projected(recorded: Any, fields: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """A recorded roster or catalog, narrowed to the fields a drift report compares.

    Tolerant on purpose. The recorded side comes off a logged payload written by some earlier
    build, so a field it does not carry is simply not compared — a guard that raised while
    trying to explain a mismatch would replace a useful refusal with a stack trace.
    """
    if isinstance(recorded, dict):
        entries = recorded.items()
    elif isinstance(recorded, list):
        entries = [
            (entry.get("id", ""), entry) for entry in recorded if isinstance(entry, dict)
        ]
    else:
        return {}

    projected: dict[str, dict[str, Any]] = {}
    for key, entry in entries:
        if not isinstance(entry, dict):
            continue
        narrowed = {name: entry[name] for name in fields if name in entry}
        if "checkpoints" in entry and isinstance(entry["checkpoints"], list):
            narrowed["checkpoints"] = [
                [
                    checkpoint.get("at_percent"),
                    [
                        option.get("label")
                        for option in checkpoint.get("options", [])
                        if isinstance(option, dict)
                    ],
                ]
                for checkpoint in entry["checkpoints"]
                if isinstance(checkpoint, dict)
            ]
        projected[str(key)] = narrowed
    return projected


def _drift(
    kind: str,
    recorded: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
) -> list[str]:
    """What changed, entry by entry, so a refusal says which person moved.

    Comparing structurally rather than only reporting two digests is the difference between a
    refusal an author can act on and one they have to bisect. Only fields present on both sides
    are compared, so a recorded payload from an earlier build narrows the report rather than
    filling it with false differences.
    """
    if not recorded:
        return []

    lines: list[str] = []

    for gone in sorted(set(recorded) - set(current)):
        lines.append(f"the {kind} no longer has {gone}")
    for fresh in sorted(set(current) - set(recorded)):
        lines.append(f"the {kind} has gained {fresh}")

    for shared in sorted(set(recorded) & set(current)):
        was, now = recorded[shared], current[shared]
        moved = [
            f"{name} {was[name]!r} -> {now[name]!r}"
            for name in was
            if name in now and was[name] != now[name]
        ]
        if moved:
            lines.append(f"{shared} changed: " + "; ".join(moved))

    return lines


def _mismatch(
    *,
    scenario_id: str,
    recorded_hash: str,
    recorded_hash_ver: int,
    current: Scenario,
    at: str,
    directory: Path | None,
    drift: list[str],
) -> ScenarioMismatch:
    path = resolve(scenario_id, directory=directory)

    if recorded_hash_ver != current.hash_version:
        cause = (
            f"the run recorded hash version {recorded_hash_ver} and this build canonicalises "
            f"scenarios at version {current.hash_version}, so the two hashes are not comparable "
            "— the way a scenario is hashed changed, which is not the same thing as the file "
            "having been edited."
        )
    else:
        cause = (
            f"the run recorded content hash {recorded_hash} and {path} now hashes "
            f"{current.content_hash}."
        )

    detail = ""
    if drift:
        detail = "\n" + "\n".join(f"  {line}" for line in drift)
    elif recorded_hash_ver == current.hash_version:
        detail = (
            "\n  No person or item differs by value, so what moved is the order of the file's "
            "people or items, or a field the recorded payload does not carry — a "
            "responsibility, a tool list, a scripted line, a department draw."
        )

    return ScenarioMismatch(
        f"this run was created against scenario {scenario_id!r}, and that scenario has changed. "
        f"{cause} Refused at {at}: the run's numbers were produced by a company that no longer "
        f"exists in that form, so folding on would reproduce neither.{detail}\n"
        f"Remedy: restore {path} to the content this run was created against to keep playing it, "
        "or start a new run against the edited scenario. A scenario edit invalidates every run "
        "written against it — runs are local and disposable, and this is a documented wipe "
        "rather than a migration."
    )


def load_recorded(
    identity: Any,
    *,
    at: str,
    directory: Path | None = None,
    recorded_roster: Any = None,
    recorded_catalog: Any = None,
) -> Scenario:
    """The scenario a run recorded, refusing one whose file has since changed (R7).

    `identity` is the `scenario` object off a genesis payload or a snapshot: `id`,
    `content_hash` and `hash_ver`. `at` names the site, so the refusal says which of the three
    guards fired. `recorded_roster` and `recorded_catalog` are optional and used only to say
    *what* moved — the genesis payload already carries both, so the from-zero fold can pass them
    and get a refusal that names a person instead of two digests.
    """
    if not isinstance(identity, dict) or not identity.get("id"):
        raise ScenarioMismatch(
            "this run records no scenario identity, so there is nothing to check it against. "
            "It was written by a build that predates scenario files. Remedy: start a new run — "
            "runs are local and disposable."
        )

    scenario_id = str(identity["id"])
    recorded_hash = str(identity.get("content_hash", ""))
    recorded_hash_ver = int(identity.get("hash_ver", 0))

    current = load(scenario_id, directory=directory)

    if recorded_hash == current.content_hash and recorded_hash_ver == current.hash_version:
        return current

    raise _mismatch(
        scenario_id=scenario_id,
        recorded_hash=recorded_hash,
        recorded_hash_ver=recorded_hash_ver,
        current=current,
        at=at,
        directory=directory,
        drift=[
            *_drift("roster", _projected(recorded_roster, _PERSON_COMPARED), _people_now(current)),
            *_drift("catalog", _projected(recorded_catalog, _ITEM_COMPARED), _items_now(current)),
        ],
    )


def verify_unchanged(loaded: Scenario, *, at: str, directory: Path | None = None) -> Scenario:
    """The scenario a state was built with, against the file as it stands now (R7).

    This is the resume-from-snapshot guard, and it is the one that would otherwise be skipped:
    a fold given `resume_from` never touches a genesis event, so nothing on that path reads a
    recorded identity. It is also the richest of the three, because both sides are whole
    scenarios — so the report can name a reordering, which a recorded roster keyed by person id
    cannot express.
    """
    current = load(loaded.scenario_id, directory=directory)

    if (
        loaded.content_hash == current.content_hash
        and loaded.hash_version == current.hash_version
    ):
        return current

    drift = [
        *_drift("roster", _people_now(loaded), _people_now(current)),
        *_drift("catalog", _items_now(loaded), _items_now(current)),
    ]

    was_people = [person.id for person in loaded.people]
    now_people = [person.id for person in current.people]
    if was_people != now_people and sorted(was_people) == sorted(now_people):
        drift.append(
            "the roster is in a different order: was "
            f"{', '.join(was_people)}; is {', '.join(now_people)}. Roster order decides who "
            "wins a contested desk, so it is part of what a scenario is."
        )

    was_items = [item.id for item in loaded.items]
    now_items = [item.id for item in current.items]
    if was_items != now_items and sorted(was_items) == sorted(now_items):
        drift.append(
            f"the catalog is in a different order: was {', '.join(was_items)}; is "
            f"{', '.join(now_items)}."
        )

    raise _mismatch(
        scenario_id=loaded.scenario_id,
        recorded_hash=loaded.content_hash,
        recorded_hash_ver=loaded.hash_version,
        current=current,
        at=at,
        directory=directory,
        drift=drift,
    )
