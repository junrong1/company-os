"""What the export may carry, declared field by field (U22).

**The export is the one artifact that leaves the machine.** Everything else this repository
produces is read by somebody sitting in front of the process that produced it; this file is
written to be attached to a message. So the question "what am I actually sending?" has to have
an answer the sender can read before they send it, and an answer that cannot quietly grow.

Two things follow from that, and they are the whole of this module.

**The classes are rendered into the document.** `§ What this file contains` is this table, so
the sender reads the same declaration the code is bound by rather than a paragraph somebody
wrote once.

**The declaration is closed, and the renderer enforces it.** `undeclared` walks every leaf of
the payload and returns the paths no class claims; `export.render` refuses a payload with any.
A field added to the Universe report therefore fails the report suite until somebody decides
which class it belongs to — which is the moment to notice that, say, a director's retrieved
context has just become something the artifact mails to a stranger.

**One field is declared and deliberately withheld.** `note.model_identity` names the provider
and model that wrote a proposal's prose. It is on the wire because the live surface is entitled
to say which model spoke; it is not in the export, because the export travels and the operator's
provider is theirs. `withheld` is checked the other way round from `carries` — the suite asserts
the value never appears in the rendered bytes.

The path grammar is the payload's own shape: `.` descends a mapping, `[]` enters a list, and
`{}` matches any one key of a mapping that is keyed by content rather than by field — a
trajectory's metric name, a load event's line. `{}` appears four times and each one is a
mapping whose *keys* are data; every field of every record is spelled out.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContentClass:
    """One kind of thing the export carries, and the payload paths that are it."""

    name: str
    #: What the sender is told they are sending. Rendered into the document verbatim.
    says: str
    carries: tuple[str, ...] = ()
    #: Paths this class covers and the document deliberately does not print.
    withholds: tuple[str, ...] = ()

    @property
    def declared(self) -> tuple[str, ...]:
        return self.carries + self.withholds


def _marked(prefix: str) -> tuple[str, ...]:
    """A figure carrying its own marking, as `proposals._figure` builds one.

    A mirror of that shape rather than a wildcard: if it grows a third key, this does not, and
    the completeness test is what says so.
    """
    return (f"{prefix}.value", f"{prefix}.basis")


def _reading(prefix: str) -> tuple[str, ...]:
    """One line's load on one day, as `universe.LoadReading.to_dict` writes it."""
    return (
        f"{prefix}.run_id",
        f"{prefix}.director",
        f"{prefix}.line",
        f"{prefix}.day",
        f"{prefix}.at_tick",
        f"{prefix}.at_seq",
        f"{prefix}.load_permille",
        f"{prefix}.over",
        f"{prefix}.basis",
    )


_TIMELINE = "timelines[]"
_REPORT = f"{_TIMELINE}.report"
_PROPOSAL = "proposals[]"
_PAYBACK = f"{_PROPOSAL}.payback[]"


MANIFEST: tuple[ContentClass, ...] = (
    ContentClass(
        name="Identity and provenance",
        says=(
            "which Genesis this is a report of, the rules and state-shape versions it is only "
            "interpretable against, and the statement that the company is invented"
        ),
        carries=(
            "root_run_id",
            "asked_about",
            "rules_ver",
            "state_shape_ver",
            "every_number_is",
            "invented_company",
            f"{_REPORT}.run_id",
            f"{_REPORT}.rules_ver",
            f"{_REPORT}.state_shape_ver",
            f"{_REPORT}.every_number_is",
        ),
    ),
    ContentClass(
        name="The company, as its scenario authors it",
        says=(
            "the invented company's id, name and summary, the hash of the file that describes "
            "it, and the load scale every reading below is against"
        ),
        carries=(
            "company.id",
            "company.title",
            "company.summary",
            "company.content_hash",
            "load_scale.scale_permille",
            "load_scale.ceiling_permille",
            "load_scale.basis",
        ),
    ),
    ContentClass(
        name="The timelines and how they parted",
        says=(
            "every timeline this Genesis produced, how far each one's clock got, and the "
            "decision that separated each from its parent"
        ),
        carries=(
            f"{_TIMELINE}.run_id",
            f"{_TIMELINE}.parent_run_id",
            f"{_TIMELINE}.forked_at_seq",
            f"{_TIMELINE}.reached_tick",
            f"{_TIMELINE}.current_day",
            f"{_TIMELINE}.separation.from_run_id",
            f"{_TIMELINE}.separation.item",
            f"{_TIMELINE}.separation.cp_index",
            f"{_TIMELINE}.separation.at_seq",
            f"{_TIMELINE}.separation.option_index",
            f"{_TIMELINE}.separation.choice",
            f"{_TIMELINE}.separation.parent_option_index",
            f"{_TIMELINE}.separation.parent_choice",
        ),
    ),
    ContentClass(
        name="How each timeline ended, and where its metrics went",
        says=(
            "the outcome and its reason, the decisions the run offered against the ones taken, "
            "and every metric's whole trajectory"
        ),
        carries=(
            f"{_REPORT}.outcome.reason",
            f"{_REPORT}.outcome.detail",
            f"{_REPORT}.outcome.at_seq",
            f"{_REPORT}.outcome.at_tick",
            f"{_REPORT}.outcome.at_day",
            f"{_REPORT}.outcome.decisions_taken",
            f"{_REPORT}.outcome.decision_supply",
            f"{_REPORT}.outcome.deliverables",
            f"{_REPORT}.trajectories.{{}}[].value",
            f"{_REPORT}.trajectories.{{}}[].at_seq",
            f"{_REPORT}.trajectories.{{}}[].at_tick",
            f"{_REPORT}.trajectories.{{}}[].at_day",
            f"{_REPORT}.trajectories.{{}}[].kind",
            f"{_REPORT}.trajectories.{{}}[].basis",
        ),
    ),
    ContentClass(
        name="Decisions, and the knowledge each one surfaced",
        says=(
            "every settled decision, whether the CEO took it in person or from the tray, the "
            "tacit knowledge that surfaced if they went, and who was left uninformed"
        ),
        carries=(
            f"{_REPORT}.decisions[].run_id",
            f"{_REPORT}.decisions[].item",
            f"{_REPORT}.decisions[].choice",
            f"{_REPORT}.decisions[].note",
            f"{_REPORT}.decisions[].path",
            f"{_REPORT}.decisions[].tacit_surfaced",
            f"{_REPORT}.decisions[].tacit",
            f"{_REPORT}.decisions[].uninformed_directors[]",
            f"{_REPORT}.decisions[].at_seq",
            f"{_REPORT}.decisions[].at_tick",
            f"{_REPORT}.decisions[].at_day",
        ),
    ),
    ContentClass(
        name="What the company produced",
        says="every deliverable that shipped, what went into it, and the tacit knowledge it carries",
        carries=(
            f"{_REPORT}.deliverables[].item",
            f"{_REPORT}.deliverables[].title",
            f"{_REPORT}.deliverables[].kind",
            f"{_REPORT}.deliverables[].provenance[]",
            f"{_REPORT}.deliverables[].tacit[]",
            f"{_REPORT}.deliverables[].at_seq",
            f"{_REPORT}.deliverables[].at_day",
        ),
    ),
    ContentClass(
        name="Authorizations, and what refusing one cost",
        says=(
            "every time a director asked to read another line, the CEO's verdict, and how long "
            "the work stood still waiting for it"
        ),
        carries=(
            f"{_REPORT}.authorizations[].item",
            f"{_REPORT}.authorizations[].asking",
            f"{_REPORT}.authorizations[].needs",
            f"{_REPORT}.authorizations[].asks",
            f"{_REPORT}.authorizations[].outcome",
            f"{_REPORT}.authorizations[].raised_at_seq",
            f"{_REPORT}.authorizations[].raised_at_tick",
            f"{_REPORT}.authorizations[].raised_at_day",
            f"{_REPORT}.authorizations[].decided_at_seq",
            f"{_REPORT}.authorizations[].decided_at_tick",
            f"{_REPORT}.authorizations[].decided_at_day",
            f"{_REPORT}.authorizations[].stalled_ticks",
            f"{_REPORT}.authorizations[].stalled_hours",
            f"{_REPORT}.authorizations[].open",
            f"{_REPORT}.authorizations[].delivered",
            f"{_REPORT}.authorizations[].basis",
        ),
    ),
    ContentClass(
        name="Load, by line and by day",
        says=(
            "every line's load at every day boundary the log can address, the spans where it "
            "sat over the ceiling, and where each line's worst day was across the whole tree"
        ),
        carries=(
            *_reading(f"{_TIMELINE}.load[]"),
            f"{_TIMELINE}.spans[].run_id",
            f"{_TIMELINE}.spans[].director",
            f"{_TIMELINE}.spans[].line",
            f"{_TIMELINE}.spans[].from_day",
            f"{_TIMELINE}.spans[].to_day",
            f"{_TIMELINE}.spans[].days",
            f"{_TIMELINE}.spans[].opened_at_seq",
            f"{_TIMELINE}.spans[].peak_permille",
            f"{_TIMELINE}.spans[].peak_day",
            f"{_TIMELINE}.spans[].peak_at_seq",
            f"{_TIMELINE}.spans[].basis",
            f"{_REPORT}.load_events[].load.{{}}",
            f"{_REPORT}.load_events[].days_below_threshold.{{}}",
            f"{_REPORT}.load_events[].at_seq",
            f"{_REPORT}.load_events[].at_day",
            f"{_REPORT}.load_events[].basis",
            "overload[].director",
            "overload[].line",
            "overload[].director_name",
            "overload[].timelines",
            "overload[].timelines_over",
            "overload[].everywhere",
            # Declared twice over, as the record and as the `null` a line nobody overloaded
            # leaves behind. A null is a field carrying nothing rather than a field that is not
            # there, and `leaf_paths` reports it as one — which is the honest reading, and it is
            # what stops "declared" quietly meaning "declared in the shape the fixture happened
            # to produce".
            "overload[].first_over",
            "overload[].peak",
            *_reading("overload[].first_over"),
            *_reading("overload[].peak"),
            "overload[].basis",
        ),
    ),
    ContentClass(
        name="Who left",
        says="every person who walked out, whose line they were on, and the work that came back",
        carries=(
            f"{_REPORT}.attrition[].person",
            f"{_REPORT}.attrition[].director",
            f"{_REPORT}.attrition[].returned_item",
            f"{_REPORT}.attrition[].at_seq",
            f"{_REPORT}.attrition[].at_day",
            f"{_REPORT}.attrition[].basis",
        ),
    ),
    ContentClass(
        name="What is worth automating",
        says=(
            "the authored candidates this run is the argument for, the events that motivate "
            "each one, and the payback recomputed from the company's own arithmetic"
        ),
        carries=(
            f"{_PROPOSAL}.id",
            f"{_PROPOSAL}.line",
            f"{_PROPOSAL}.director",
            f"{_PROPOSAL}.director_name",
            f"{_PROPOSAL}.title",
            f"{_PROPOSAL}.detail",
            *_marked(f"{_PROPOSAL}.removes_draw_hours_per_month"),
            f"{_PROPOSAL}.timelines",
            f"{_PROPOSAL}.timelines_over",
            f"{_PROPOSAL}.everywhere",
            f"{_PROPOSAL}.evidence[].run_id",
            f"{_PROPOSAL}.evidence[].at_seq",
            f"{_PROPOSAL}.evidence[].at_tick",
            f"{_PROPOSAL}.evidence[].at_day",
            f"{_PROPOSAL}.evidence[].note",
            f"{_PAYBACK}.run_id",
            f"{_PAYBACK}.day",
            f"{_PAYBACK}.at_tick",
            f"{_PAYBACK}.at_seq",
            *_marked(f"{_PAYBACK}.days_over"),
            *_marked(f"{_PAYBACK}.removes_draw_hours_per_month"),
            *_marked(f"{_PAYBACK}.manual_hours_before"),
            *_marked(f"{_PAYBACK}.daily_burn_before"),
            *_marked(f"{_PAYBACK}.daily_burn_after"),
            *_marked(f"{_PAYBACK}.daily_saving"),
            *_marked(f"{_PAYBACK}.runway_days_before"),
            *_marked(f"{_PAYBACK}.runway_days_after"),
            *_marked(f"{_PAYBACK}.runway_days_gained"),
            *_marked(f"{_PAYBACK}.load_permille_before"),
            *_marked(f"{_PAYBACK}.load_permille_after"),
            *_marked(f"{_PAYBACK}.load_permille_removed"),
            f"{_PAYBACK}.clears_the_ceiling",
            f"{_PAYBACK}.basis",
            f"{_PROPOSAL}.basis",
            "prescription_rule.min_overload_days",
            "prescription_rule.says",
            "prescription_rule.basis",
        ),
    ),
    ContentClass(
        name="Prose a model wrote",
        says=(
            "sentences a language model wrote over figures it did not compute, each carrying "
            "the sequences it was written about — and the stated reason where there are none"
        ),
        carries=(
            f"{_PROPOSAL}.note.status",
            f"{_PROPOSAL}.note.sentences[].text",
            f"{_PROPOSAL}.note.sentences[].citations[]",
            f"{_PROPOSAL}.note.reason",
            f"{_PROPOSAL}.note.written_by",
        ),
        withholds=(f"{_PROPOSAL}.note.model_identity",),
    ),
    ContentClass(
        name="Every figure's address",
        says=(
            "the run and the sequence behind every number in this document, so a reader with "
            "the log can check any of them"
        ),
        carries=(
            "claims[].run_id",
            "claims[].label",
            "claims[].value",
            "claims[].at_seq",
            "claims[].at_tick",
            "claims[].at_day",
            "claims[].basis",
            f"{_REPORT}.claims[].run_id",
            f"{_REPORT}.claims[].label",
            f"{_REPORT}.claims[].value",
            f"{_REPORT}.claims[].at_seq",
            f"{_REPORT}.claims[].at_tick",
            f"{_REPORT}.claims[].at_day",
            f"{_REPORT}.claims[].basis",
        ),
    ),
    ContentClass(
        name="Whether each timeline can be trusted",
        says=(
            "sequences a timeline's log should hold and does not, day boundaries whose fold did "
            "not reproduce the hash the kernel wrote there, and any timeline that would not fold"
        ),
        carries=(
            # The `null` a timeline that would not fold leaves in place of its whole report.
            f"{_TIMELINE}.report",
            f"{_TIMELINE}.missing_seqs[]",
            f"{_TIMELINE}.diverged_days[].day",
            f"{_TIMELINE}.diverged_days[].at_tick",
            f"{_TIMELINE}.diverged_days[].at_seq",
            f"{_TIMELINE}.diverged_days[].subsystems[]",
            f"{_TIMELINE}.trustworthy",
            f"{_TIMELINE}.refusal",
        ),
    ),
)


#: Every path any class claims, flattened once at import.
DECLARED: frozenset[str] = frozenset(
    path for content in MANIFEST for path in content.declared
)

#: Every path declared and deliberately not printed.
WITHHELD: frozenset[str] = frozenset(
    path for content in MANIFEST for path in content.withholds
)


def leaf_paths(value: object, prefix: str = "") -> Iterator[str]:
    """Every leaf of a payload, addressed in the manifest's grammar.

    An empty list and an empty mapping yield nothing, which is the honest reading: a field
    carrying no values carries nothing to declare. It also means the completeness test is only
    as good as the fixture it runs on, which is why `test_report.py` runs it over a lineage
    driven far enough to fill the lists rather than over a one-day run.
    """
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from leaf_paths(item, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, list):
        for item in value:
            yield from leaf_paths(item, f"{prefix}[]")
    elif prefix:
        yield prefix


def _matches(path: str, pattern: str) -> bool:
    """One concrete path against one declared pattern, segment by segment.

    A wildcard segment is `{}` followed by whatever list brackets the declaration wrote — so
    `trajectories.{}[]` matches `trajectories.cash[]` and not `trajectories.cash`.
    """
    theirs = pattern.split(".")
    mine = path.split(".")
    if len(theirs) != len(mine):
        return False
    return all(_segment(want, have) for want, have in zip(theirs, mine, strict=True))


def _segment(want: str, have: str) -> bool:
    if want == have:
        return True
    if not want.startswith("{}"):
        return False
    brackets = want[2:]
    return have.endswith(brackets) and len(have) > len(brackets)


def undeclared(payload: object) -> list[str]:
    """The payload's paths that no content class claims, deduplicated and sorted.

    Empty is the only acceptable answer for a payload about to be rendered, and `export.render`
    is what turns that into a refusal rather than a convention.
    """
    found: set[str] = set()
    for path in leaf_paths(payload):
        if path in DECLARED:
            continue
        if any(_matches(path, pattern) for pattern in DECLARED):
            continue
        found.add(path)
    return sorted(found)
