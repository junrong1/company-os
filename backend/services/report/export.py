"""One HTML file that opens anywhere (U22 — M54, M60, M61, R25).

**What makes this different from every other surface in the tree is that it leaves.** The
client renders the same figures, but it renders them into a page served by the process that
computed them, to somebody sitting in front of it. This file is written to be attached to a
message and opened from a stranger's filesystem, which changes three things and only three:

* **it carries its own data, styles and image, and reaches for nothing** (M54). No stylesheet
  link, no font, no image element, no `url(` in the CSS — because the first thing an offline
  file does when it cannot reach a server is look broken, and the second is tell whoever hosts
  the missing resource that it was opened;
* **it contains no script and says so in a policy the document carries** (R25). A file opened
  from `file://` still reaches the network in every browser, and the strings interpolated into
  it include a scenario that arrived by pull request and prose that came out of a model. So
  escaping is a type rather than a discipline — see `markup` — and the content-security policy
  is a second answer to the same question, addressed to the browser rather than to the author;
* **it declares what it contains** (`manifest`). `render` refuses a payload carrying a field no
  content class claims, so a new field on the Universe report is a decision about what this
  artifact mails to somebody rather than a diff nobody read.

**Nothing here is time-stamped, and that is deliberate.** The report is identified by its
lineage root (U20), so two exports of one Universe are the same document — and the suite asserts
byte-equality rather than trusting it. A "generated at" line would have made that false on
every export, for a fact the covering message already carries.
"""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from report import manifest as declaring
from report.fold import AUTHORED
from report.markup import NOTHING, Html, join, raw, tag

#: Where the artifact says it came from (M60). The link and the QR code are the same string.
REPOSITORY = "https://github.com/junrong1/company-os"

#: The authored-tuning marking, as every other surface renders it: a glyph and a label, never a
#: hue. The client's copy is `design/tokens.ts`; this is the first one outside it, and the two
#: are a pair rather than a source and a copy, because nothing crosses the language boundary.
GLYPH = "≈"
MARKING = "authored tuning"
MARKING_SAYS = "This figure is authored tuning, not a measurement."

#: The directory holding what the document embeds. One file, and it ships in the image because
#: the Dockerfile copies `services/` whole.
ASSETS = Path(__file__).parent / "assets"


class UndeclaredContent(ValueError):
    """A payload carrying a field the manifest does not declare.

    Raised rather than rendered around. The export is the artifact that leaves the machine, and
    the failure mode this guards is a field that quietly starts travelling — so the answer is a
    red suite, not a silently narrower document.
    """


# =========================================================================
# The look of it
# =========================================================================
#
# One stylesheet, inline, and no second theme. The product's visual system is daylight — ivory
# ground, cool hairlines, colour as meaning and never as decoration — and a dark variant here
# would be a second palette to keep in step with `design/tokens.ts` for a document people read
# once. The values below are that file's, transcribed, and the comment beside each is its name.

STYLE = """
:root {
  --ground: #fffef8;     /* 象牙白 — the product ground */
  --card: #f7fbf9;       /* 月白, lifted */
  --band: #fbf2e3;       /* 粉白 — warm quiet bands */
  --rule: #c3d5de;       /* 远天蓝 — hairlines */
  --text: #1c2938;       /* 鸽蓝 */
  --muted: #475164;      /* 鲸鱼灰 */
  --faint: #5c677a;      /* 鲸鱼灰, lifted */
  --accent: #1677b3;     /* 天蓝 */
  --good: #12775e;       /* 竹绿, deepened */
  --bad: #c8402a;        /* 朱红, deepened */
  --over: #8a4028;       /* 赭石 — past the ceiling */
  --tacit: #6b5bb5;      /* 紫棠 — knowledge only a conversation yields */
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0;
  padding: 0 16px 96px;
  background: var(--ground);
  color: var(--text);
  font: 16px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
main { max-width: 68rem; margin: 0 auto; }
h1, h2, h3, h4 { line-height: 1.2; font-weight: 650; }
h1 { font-size: 1.9rem; margin: 2rem 0 0.25rem; letter-spacing: -0.01em; }
h2 { font-size: 1.3rem; margin: 2.75rem 0 0.75rem; padding-bottom: 0.35rem; border-bottom: 2px solid var(--rule); }
h3 { font-size: 1.05rem; margin: 1.5rem 0 0.4rem; }
h4 { font-size: 0.95rem; margin: 1rem 0 0.3rem; color: var(--muted); }
p { margin: 0.5rem 0; }
a { color: var(--accent); }
.sub { color: var(--muted); margin: 0 0 1.25rem; }
.note {
  background: var(--band);
  border: 1px solid var(--rule);
  border-radius: 6px;
  padding: 0.85rem 1rem;
  margin: 1rem 0;
}
.note--plain { background: var(--card); }
.masthead { display: flex; flex-wrap: wrap; gap: 1.5rem; align-items: flex-start; justify-content: space-between; }
.masthead__id { min-width: 18rem; flex: 1 1 24rem; }
.origin { text-align: center; font-size: 0.8rem; color: var(--muted); flex: 0 0 auto; }
.origin svg { display: block; margin: 0 auto 0.35rem; border: 1px solid var(--rule); border-radius: 4px; }
.origin a { word-break: break-all; }
dl.facts { display: grid; grid-template-columns: max-content 1fr; gap: 0.15rem 1rem; margin: 0.75rem 0; }
dl.facts dt { color: var(--faint); font-size: 0.85rem; }
dl.facts dd { margin: 0; }
table { border-collapse: collapse; width: 100%; margin: 0.75rem 0; font-size: 0.9rem; }
caption { text-align: left; color: var(--faint); font-size: 0.85rem; padding-bottom: 0.3rem; }
th, td { text-align: left; padding: 0.3rem 0.6rem 0.3rem 0; border-bottom: 1px solid var(--rule); vertical-align: top; }
th { color: var(--faint); font-weight: 600; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.04em; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
tbody tr:last-child td { border-bottom: none; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.85em; }
.figure { white-space: nowrap; font-variant-numeric: tabular-nums; }
.mark { color: var(--faint); margin-left: 0.25em; font-size: 0.85em; }
.at { color: var(--faint); font-size: 0.8rem; white-space: nowrap; }
.timeline { border-left: 3px solid var(--rule); padding-left: 1rem; margin: 1.5rem 0 2.5rem; }
.timeline--root { border-left-color: var(--accent); }
.tag { display: inline-block; border: 1px solid var(--rule); border-radius: 999px; padding: 0 0.55em; font-size: 0.75rem; color: var(--muted); background: var(--card); }
.over { color: var(--over); font-weight: 650; }
.good { color: var(--good); }
.bad { color: var(--bad); }
.tacit { color: var(--tacit); }
.heat th.day { text-align: right; font-size: 0.7rem; }
.heat td { padding: 0.15rem 0.35rem 0.15rem 0; }
.absent { color: var(--faint); font-style: italic; }
.prose { border-left: 3px solid var(--tacit); padding-left: 0.85rem; margin: 0.75rem 0; }
.prose p { margin: 0.35rem 0; }
.cites { color: var(--faint); font-size: 0.8rem; }
ul.plain { margin: 0.35rem 0; padding-left: 1.1rem; }
footer { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--rule); color: var(--faint); font-size: 0.85rem; }
@media print {
  body { padding: 0; }
  h2 { break-after: avoid; }
  .timeline { break-inside: avoid; }
}
"""


@lru_cache(maxsize=1)
def qr_svg() -> Html:
    """The repository's QR code, as a checked-in inline SVG (M60).

    **Checked in rather than encoded at runtime**, which execution decision §5 settled: the URL
    is fixed at authoring time, the export may contain no script, and a QR encoder in the tree
    would be a few hundred lines of Reed-Solomon doing at runtime what a constant already does.
    `tests/qrread.py` decodes this file's own path data back to a string and asserts it is
    `REPOSITORY`, so the asset cannot drift away from the link beside it.

    One of the two places raw markup is constructed. It is repository-authored, ASCII, and
    contains no script; the suite asserts all three of those every run.
    """
    return raw((ASSETS / "repository-qr.svg").read_text(encoding="utf-8").strip())


def csp() -> str:
    """The policy the document carries, as a string both the meta and the header use.

    `default-src 'none'` is the whole of it: a document that loads nothing needs no allowances,
    and every other directive here exists to close a hole `default-src` does not cover —
    `base-uri` because a `<base>` would retarget the repository link, `form-action` because
    `default-src` does not govern submissions.

    **The stylesheet is admitted by hash rather than by `'unsafe-inline'`**, so the policy
    permits exactly the bytes this module wrote and not a second `<style>` somebody's content
    smuggled in. A browser too old to understand a style hash drops the stylesheet, and the
    document is semantic HTML that reads perfectly well without one — which is the right way
    round for the failure.
    """
    digest = base64.b64encode(hashlib.sha256(STYLE.encode("utf-8")).digest()).decode("ascii")
    return (
        "default-src 'none'; "
        f"style-src 'sha256-{digest}'; "
        "base-uri 'none'; "
        "form-action 'none'"
    )


# =========================================================================
# Figures, addresses and other small things every section uses
# =========================================================================


def number(value: object) -> str:
    """A figure as the document prints it. Grouped, and never reinterpreted.

    A `bool` is checked before `int` because it is one, and "True" is what a reader of a
    `clears_the_ceiling` cell wants rather than "1".
    """
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def figure(value: object, of: str, basis: str = AUTHORED) -> Html:
    """One number, wearing its marking (M59).

    The marking travels with the figure rather than sitting once at the top of the page, for the
    reason `Marking.tsx` gives: a completeness claim survives only if there is one thing to
    render, and a page-level caption is a claim nobody can check per number.

    A basis this surface has no glyph for is *printed* rather than dropped. Every figure in the
    tree is authored tuning today; the day one is not, this document says which one.

    **An absent value is a stated absence, never a marked one.** `None` used to render as the
    word "None" with the marking beside it, which claims a figure where the report has none —
    the same rule the HUD follows when it will not draw a zero for something it does not know.
    Defence in depth rather than the fix: the section that produced those Nones no longer runs
    at all, and this is what stops the next one.
    """
    if value is None:
        return absent("not stated")
    if basis == AUTHORED:
        mark = tag("span", GLYPH, class_="mark", title=MARKING_SAYS, aria_label=f"{of}: {MARKING}")
    else:
        mark = tag("span", basis, class_="mark", aria_label=f"{of}: {basis}")
    return tag(
        "span",
        number(value),
        mark,
        class_="figure",
        **{"data_authored_tuning" if basis == AUTHORED else "data_basis": GLYPH if basis == AUTHORED else basis},
    )


def marked(entry: Mapping[str, Any] | None, of: str) -> Html:
    """A `{value, basis}` pair off the payload, or a stated absence."""
    if entry is None:
        return absent("not stated")
    return figure(entry.get("value"), of, str(entry.get("basis", AUTHORED)))


def absent(why: str) -> Html:
    """A stated absence. Never a zero, and never an empty cell."""
    return tag("span", why, class_="absent")


def address(
    run_id: object, at_seq: object, at_tick: object = None, at_day: object = None
) -> Html:
    """Where a figure came from: the run and the sequence, which together are the address.

    A bare sequence stopped being one when forks landed — a child copies its parent's rows, so
    sequence 42 exists in every timeline of a lineage. U20 put the pair on every claim; this
    prints the pair.
    """
    parts = [f"{run_id} · seq {number(at_seq)}"]
    if at_day is not None:
        parts.append(f"day {number(at_day)}")
    if at_tick is not None:
        parts.append(f"tick {number(at_tick)}")
    return tag("span", " · ".join(parts), class_="at mono")


def table(
    headings: Sequence[tuple[str, bool]],
    rows: Iterable[Sequence[object]],
    *,
    caption: str = "",
    class_: str = "",
) -> Html:
    """A table, or nothing at all.

    `headings` pairs each column's label with whether it is numeric, so the alignment is
    declared once beside the label rather than repeated on every cell.
    """
    body = [
        tag(
            "tr",
            join(
                tag("td", cell, class_="num" if numeric else None)
                for cell, (_, numeric) in zip(row, headings, strict=True)
            ),
        )
        for row in rows
    ]
    if not body:
        return NOTHING
    return tag(
        "table",
        tag("caption", caption) if caption else None,
        tag(
            "thead",
            tag(
                "tr",
                join(tag("th", label, class_="num" if numeric else None) for label, numeric in headings),
            ),
        ),
        tag("tbody", join(body)),
        class_=class_ or None,
    )


def facts(pairs: Iterable[tuple[str, object]]) -> Html:
    """A short definition list. The shape every "here is what this is" block takes.

    The two elements go into `join` as two fragments rather than as one concatenation, because
    `Html + Html` is a plain `str` — `str.__add__` knows nothing about the subclass — and `join`
    would then escape the pair as text. That is the type doing its job rather than a wart: the
    alternative is an `__add__` that keeps the subclass, which would also keep it across
    `Html + str` and hand back a fragment carrying unescaped content.
    """
    return tag(
        "dl",
        join(
            fragment
            for label, value in pairs
            for fragment in (tag("dt", label), tag("dd", value))
        ),
        class_="facts",
    )


# =========================================================================
# The document
# =========================================================================


def render(payload: Mapping[str, Any]) -> str:
    """The whole file, as a string.

    Takes the payload rather than the `Universe` dataclass on purpose: what is exported is the
    wire shape, so the manifest is a statement about the thing that actually travels, and this
    module holds no opinion about how the fold assembled it.
    """
    smuggled = declaring.undeclared(payload)
    if smuggled:
        raise UndeclaredContent(
            "the export refuses a payload carrying fields no content class declares: "
            + ", ".join(smuggled)
            + ". Add them to report/manifest.py, deciding as you do whether this artifact "
            "should be mailing them to somebody."
        )

    company = payload.get("company") or {}
    title = f"{company.get('title') or 'Company OS'} — the Universe report"

    document = tag(
        "html",
        tag(
            "head",
            tag("meta", charset="utf-8"),
            tag("meta", http_equiv="Content-Security-Policy", content=csp()),
            tag("meta", name="viewport", content="width=device-width, initial-scale=1"),
            tag("meta", name="referrer", content="no-referrer"),
            tag("title", title),
            tag("style", raw(STYLE)),
        ),
        tag(
            "body",
            tag(
                "main",
                _masthead(payload, title),
                _contains(),
                _company(payload),
                _timelines(payload),
                _overload(payload),
                _prescription(payload),
                _claims(payload),
                _footer(payload),
            ),
        ),
        lang="en",
    )
    return "<!doctype html>\n" + document + "\n"


def _masthead(payload: Mapping[str, Any], title: str) -> Html:
    """The first screenful: what this is, that the company is invented, and where it came from."""
    timelines = payload.get("timelines") or []
    return tag(
        "header",
        tag(
            "div",
            tag(
                "div",
                tag("h1", title),
                tag(
                    "p",
                    f"One Genesis, {number(len(timelines))} "
                    + ("timeline" if len(timelines) == 1 else "timelines")
                    + ", and every figure addressed back to the event that produced it.",
                    class_="sub",
                ),
                facts(
                    [
                        ("Lineage root", mono(payload.get("root_run_id"))),
                        ("Asked from", mono(payload.get("asked_about"))),
                    ]
                ),
                class_="masthead__id",
            ),
            _origin(),
            class_="masthead",
        ),
        tag("div", tag("p", payload.get("invented_company", "")), class_="note"),
        tag(
            "div",
            tag(
                "p",
                f"Every figure in this document is {payload.get('every_number_is', AUTHORED)}: "
                "a shape somebody chose rather than a measurement anything took. The marking ",
                tag("span", GLYPH, class_="mark", title=MARKING_SAYS),
                " travels beside each number rather than sitting once at the top, so there is "
                "no figure here you have to take this paragraph's word for.",
            ),
            class_="note note--plain",
        ),
    )


def _origin() -> Html:
    """The link and the QR code, which are the same URL twice (M60)."""
    return tag(
        "div",
        qr_svg(),
        tag("a", REPOSITORY, href=REPOSITORY, rel="noreferrer noopener"),
        tag("p", "The simulator this report came out of."),
        class_="origin",
    )


def _contains() -> Html:
    """What the sender is sending, from the declaration the renderer is bound by."""
    rows = [
        (
            tag("strong", content.name),
            content.says,
            number(len(content.carries)),
            number(len(content.withholds)) if content.withholds else "—",
        )
        for content in declaring.MANIFEST
    ]
    withheld = [path for content in declaring.MANIFEST for path in content.withholds]
    return tag(
        "section",
        tag("h2", "What this file contains"),
        tag(
            "p",
            "This document is complete and offline: it holds its own data and styling, loads "
            "nothing, and runs nothing. The classes below are declared in the code that wrote "
            "it, and a field belonging to none of them refuses the export rather than riding "
            "along in it.",
        ),
        table(
            [("Content", False), ("What that means", False), ("Fields", True), ("Withheld", True)],
            rows,
        ),
        tag(
            "p",
            "Deliberately not carried: "
            + ", ".join(path.rsplit(".", 1)[-1] for path in withheld)
            + " — the model and provider that wrote the prose below. Which model spoke is the "
            "operator's business, and this file travels.",
            class_="cites",
        )
        if withheld
        else None,
    )


def mono(value: object, why: str = "not stated") -> Html:
    """An identifier, or a stated absence where the report does not have one.

    A Universe whose every timeline refused knows no company, no rules version and no state
    shape: nothing folded, so nothing said. An empty `<span class="mono">` renders as a blank
    where an identifier should be, which reads as a missing *value* rather than as a report that
    correctly has none.
    """
    return tag("span", value, class_="mono") if value else absent(why)


def _company(payload: Mapping[str, Any]) -> Html:
    company = payload.get("company") or {}
    scale = payload.get("load_scale") or {}
    return tag(
        "section",
        tag("h2", "The company"),
        tag(
            "p",
            company.get("summary")
            or "No timeline in this Universe could be folded, so it names no company.",
            class_=None if company.get("summary") else "absent",
        ),
        facts(
            [
                ("Scenario", mono(company.get("id"))),
                ("Content hash", mono(company.get("content_hash"))),
                ("Rules version", mono(payload.get("rules_ver"))),
                ("State shape", mono(payload.get("state_shape_ver"))),
                (
                    "Load ceiling",
                    join(
                        [
                            "a line is over its ceiling above ",
                            figure(scale.get("ceiling_permille"), "the ceiling", str(scale.get("basis", AUTHORED))),
                            ", on a scale where ",
                            figure(scale.get("scale_permille"), "the scale", str(scale.get("basis", AUTHORED))),
                            " per mille is the whole of what it can carry.",
                        ]
                    ),
                ),
            ]
        ),
    )


# =========================================================================
# The timelines
# =========================================================================


def _timelines(payload: Mapping[str, Any]) -> Html:
    entries = payload.get("timelines") or []
    root = payload.get("root_run_id", "")
    return tag(
        "section",
        tag("h2", "The timelines"),
        join(_timeline(entry, root) for entry in entries),
    )


def _timeline(entry: Mapping[str, Any], root: str) -> Html:
    """One timeline's section, or the reason there is nothing to put in one.

    **A timeline that would not fold stops here, and that is a fix rather than a shortcut.** It
    used to fall through to the same blocks every other timeline gets, against an empty report —
    which rendered `None ≈ taken of None ≈ offered`, a `Shipped` of `None ≈`, a last event at
    `seq None`, and the sentence "No decision was settled in this timeline" about a log nobody
    could read. Four fabrications wearing the authored-tuning marking, on the one timeline whose
    whole point is that it has no figures. Found by rendering a lineage whose only member had
    been written under other rules; no suite could see it, because every assertion about a
    refused timeline was about the payload rather than about the page.

    The refusal and the integrity notes still print: a reader is owed the reason, and a hole in
    the sequences is knowable without folding anything.
    """
    run_id = entry.get("run_id", "")
    is_root = run_id == root
    report = entry.get("report")
    heading = tag(
        "h3",
        tag("span", run_id, class_="mono"),
        " ",
        tag("span", "the Genesis" if is_root else "a fork", class_="tag"),
    )
    class_ = "timeline timeline--root" if is_root else "timeline"

    if report is None or entry.get("refusal"):
        return tag(
            "article",
            heading,
            _separation(entry.get("separation") or {}),
            _integrity(entry),
            tag(
                "p",
                entry.get("refusal")
                or "This timeline could not be folded, so this report states nothing about it.",
                class_="absent",
            ),
            class_=class_,
        )

    return tag(
        "article",
        heading,
        _separation(entry.get("separation") or {}),
        _integrity(entry),
        _outcome(entry, report),
        _trajectories(report),
        _decisions(report),
        _deliverables(report),
        _authorizations(report),
        _spans(entry),
        _heat(entry),
        _load_events(report),
        _attrition(report),
        class_=class_,
    )


def _separation(separation: Mapping[str, Any]) -> Html:
    if not separation:
        return tag("p", "Nothing separated this timeline: it is the Genesis every other one forked from.")
    return tag(
        "p",
        "Forked from ",
        tag("span", separation.get("from_run_id", ""), class_="mono"),
        f" at “{separation.get('item', '')}”, taking “{separation.get('choice', '')}” where the "
        f"parent took “{separation.get('parent_choice', '')}”. ",
        address(separation.get("from_run_id", ""), separation.get("at_seq")),
    )


def _integrity(entry: Mapping[str, Any]) -> Html:
    """Whether this timeline's figures can be trusted, stated beside them rather than instead."""
    missing = entry.get("missing_seqs") or []
    diverged = entry.get("diverged_days") or []
    if not missing and not diverged:
        return NOTHING
    lines = []
    if missing:
        lines.append(
            tag(
                "p",
                f"{number(len(missing))} sequence(s) the log should hold and does not: ",
                tag("span", ", ".join(str(seq) for seq in missing), class_="mono"),
            )
        )
    for day in diverged:
        lines.append(
            tag(
                "p",
                f"Day {number(day.get('day'))} did not fold to the hash the kernel wrote there. "
                f"Diverging subsystems: {', '.join(str(name) for name in day.get('subsystems') or [])}. ",
                address(entry.get("run_id", ""), day.get("at_seq"), day.get("at_tick")),
            )
        )
    return tag("div", tag("h4", "This timeline does not check out"), join(lines), class_="note")


def _outcome(entry: Mapping[str, Any], report: Mapping[str, Any]) -> Html:
    outcome = report.get("outcome") or {}
    return facts(
        [
            ("Outcome", f"{outcome.get('reason', 'unknown')} — {outcome.get('detail', '')}"),
            ("Reached", f"day {number(entry.get('current_day'))}, tick {number(entry.get('reached_tick'))}"),
            (
                "Decisions",
                join(
                    [
                        figure(outcome.get("decisions_taken"), "decisions taken"),
                        " taken of ",
                        figure(outcome.get("decision_supply"), "decisions offered"),
                        " offered",
                    ]
                ),
            ),
            ("Shipped", figure(outcome.get("deliverables"), "deliverables")),
            ("Last event", address(entry.get("run_id", ""), outcome.get("at_seq"), outcome.get("at_tick"), outcome.get("at_day"))),
        ]
    )


def _trajectories(report: Mapping[str, Any]) -> Html:
    """Each metric's first and last reading, and how many readings there were between them.

    The whole series is on the payload and deliberately not printed as a column of numbers: a
    thirty-day run is five metrics × thirty rows of a shape a reader cannot see anything in. The
    endpoints and the direction are what a reader takes from a trajectory, and every intermediate
    reading is still addressable through the claims index, which is the promise M55 makes.
    """
    trajectories = report.get("trajectories") or {}
    rows = []
    for metric, series in trajectories.items():
        if not series:
            continue
        first, last = series[0], series[-1]
        delta = _delta(first.get("value"), last.get("value"))
        rows.append(
            (
                metric,
                figure(first.get("value"), f"{metric} at the start", str(first.get("basis", AUTHORED))),
                figure(last.get("value"), f"{metric} at the end", str(last.get("basis", AUTHORED))),
                delta,
                number(len(series)),
                address(report.get("run_id", ""), last.get("at_seq"), last.get("at_tick"), last.get("at_day")),
            )
        )
    return table(
        [
            ("Metric", False),
            ("At genesis", True),
            ("Latest", True),
            ("Change", True),
            ("Readings", True),
            ("Latest reading", False),
        ],
        rows,
        caption="Where each metric went. Every intermediate reading is in the claims index.",
    )


def _delta(first: object, last: object) -> Html:
    """The change between two readings, signed and deliberately uncoloured.

    **The client colours a delta by whether it is favourable, and this surface cannot.** Rising
    cash is good and rising manual hours is not; the kernel states a favourable direction per
    metric and puts it on the *diff's* wire as `good`, but a trajectory carries no such field.
    Colouring by sign here would put a green number on a company getting worse, in the product's
    own "favourable delta" hue, on the one surface a reader cannot cross-check against the app.
    So the sign says which way and nothing claims whether that is good news.
    """
    if not isinstance(first, int) or not isinstance(last, int) or isinstance(first, bool):
        return absent("—")
    change = last - first
    return tag("span", "0" if change == 0 else f"{change:+,}")


def _decisions(report: Mapping[str, Any]) -> Html:
    rows = []
    for decision in report.get("decisions") or []:
        uninformed = decision.get("uninformed_directors") or []
        rows.append(
            (
                decision.get("item", ""),
                decision.get("choice", ""),
                decision.get("path", ""),
                tag("span", decision.get("tacit"), class_="tacit")
                if decision.get("tacit_surfaced")
                else absent("none surfaced"),
                ", ".join(str(name) for name in uninformed) if uninformed else "—",
                address(decision.get("run_id", ""), decision.get("at_seq"), decision.get("at_tick"), decision.get("at_day")),
            )
        )
    if not rows:
        return tag("p", "No decision was settled in this timeline.", class_="absent")
    return table(
        [
            ("Item", False),
            ("Taken", False),
            ("How", False),
            ("Tacit knowledge", False),
            ("Left uninformed", False),
            ("Event", False),
        ],
        rows,
        caption="Every settled decision, and whether the CEO walked over or cleared it from the tray.",
    )


def _deliverables(report: Mapping[str, Any]) -> Html:
    produced = report.get("deliverables") or []
    if not produced:
        return NOTHING
    return join(
        tag(
            "div",
            tag("h4", f"{output.get('title', '')} ({output.get('kind', '')})"),
            tag("ul", join(tag("li", line) for line in output.get("provenance") or []), class_="plain"),
            tag(
                "ul",
                join(tag("li", line) for line in output.get("tacit") or []),
                class_="plain tacit",
            )
            if output.get("tacit")
            else None,
            address(report.get("run_id", ""), output.get("at_seq"), None, output.get("at_day")),
        )
        for output in produced
    )


def _authorizations(report: Mapping[str, Any]) -> Html:
    rows = []
    for record in report.get("authorizations") or []:
        rows.append(
            (
                record.get("asking", ""),
                record.get("needs", ""),
                record.get("item", ""),
                number(record.get("asks")),
                record.get("outcome", ""),
                figure(record.get("stalled_hours"), "hours the work stood still", str(record.get("basis", AUTHORED))),
                "yes" if record.get("delivered") else "no",
                address(report.get("run_id", ""), record.get("raised_at_seq"), record.get("raised_at_tick"), record.get("raised_at_day")),
            )
        )
    return table(
        [
            ("Asked by", False),
            ("To read", False),
            ("On", False),
            ("Ask", True),
            ("Verdict", False),
            ("Hours stalled", True),
            ("Delivered", False),
            ("Raised at", False),
        ],
        rows,
        caption="Every time one line asked to read another's, and what the answer cost.",
    )


def _spans(entry: Mapping[str, Any]) -> Html:
    rows = []
    for span in entry.get("spans") or []:
        rows.append(
            (
                span.get("line", ""),
                span.get("director", ""),
                f"day {number(span.get('from_day'))}–{number(span.get('to_day'))}",
                figure(span.get("days"), "days over the ceiling", str(span.get("basis", AUTHORED))),
                figure(span.get("peak_permille"), "the worst reading", str(span.get("basis", AUTHORED))),
                number(span.get("peak_day")),
                address(entry.get("run_id", ""), span.get("opened_at_seq")),
            )
        )
    return table(
        [
            ("Line", False),
            ("Director", False),
            ("Span", False),
            ("Days over", True),
            ("Worst (per mille)", True),
            ("On day", True),
            ("Opened at", False),
        ],
        rows,
        caption="Where this timeline sat over its ceiling, and for how long.",
    )


def _heat(entry: Mapping[str, Any]) -> Html:
    """Every line's load at every day boundary, as a grid rather than a column.

    The readings are the bulk of the payload — 1344 of them at the sixteen-timeline fork cap —
    and a thousand-row table is a thousand rows nobody reads. One row per line and one cell per
    day is the same data in a shape where an overloaded line is visible at a glance, and the
    cells over the ceiling are marked in the text rather than only in the colour.
    """
    readings = entry.get("load") or []
    if not readings:
        return NOTHING

    days = sorted({int(reading["day"]) for reading in readings})
    lines: dict[str, dict[int, Mapping[str, Any]]] = {}
    for reading in readings:
        lines.setdefault(str(reading.get("line", "")), {})[int(reading["day"])] = reading

    rows = []
    for line, by_day in lines.items():
        cells: list[object] = [line]
        for day in days:
            reading = by_day.get(day)
            if reading is None:
                cells.append(absent("—"))
                continue
            value = figure(
                reading.get("load_permille"),
                f"{line} on day {day}",
                str(reading.get("basis", AUTHORED)),
            )
            cells.append(tag("span", value, class_="over") if reading.get("over") else value)
        rows.append(cells)

    return table(
        [("Line", False), *((f"d{day}", True) for day in days)],
        rows,
        caption=(
            "Load per mille at each day boundary the log can address. A reading over the "
            "ceiling is marked; the ceiling itself is in “The company”, above."
        ),
        class_="heat",
    )


def _load_events(report: Mapping[str, Any]) -> Html:
    """What the log said about load, as distinct from what the fold measured.

    Kept separate from the grid above rather than merged into it, because they are two different
    claims: `LOAD_CHANGED` fires only on a day that produced morale counters, so the log is
    silent through most of an overload and the grid is the fold's own reading at every boundary.
    Merging them would present a measurement as an event the log carries.
    """
    events = report.get("load_events") or []
    if not events:
        return NOTHING
    rows = []
    for event in events:
        load = event.get("load") or {}
        below = event.get("days_below_threshold") or {}
        rows.append(
            (
                number(event.get("at_day")),
                ", ".join(f"{line} {number(value)}" for line, value in sorted(load.items())),
                ", ".join(f"{line} {number(value)}" for line, value in sorted(below.items())) or "—",
                address(report.get("run_id", ""), event.get("at_seq"), None, event.get("at_day")),
            )
        )
    return table(
        [("Day", True), ("Load the event carried", False), ("Days below threshold", False), ("Event", False)],
        rows,
        caption="The days the log itself spoke about load. The grid above is the fold, at every boundary.",
    )


def _attrition(report: Mapping[str, Any]) -> Html:
    rows = [
        (
            record.get("person", ""),
            record.get("director", ""),
            record.get("returned_item") or "—",
            number(record.get("at_day")),
            address(report.get("run_id", ""), record.get("at_seq"), None, record.get("at_day")),
        )
        for record in report.get("attrition") or []
    ]
    return table(
        [("Person", False), ("Line", False), ("Work returned", False), ("Day", True), ("Event", False)],
        rows,
        caption="Who left, and what came back unowned when they did.",
    )


# =========================================================================
# Across the tree
# =========================================================================


def _overload(payload: Mapping[str, Any]) -> Html:
    """Where the company was overloaded, by line, across every timeline (M56).

    Never a sum of days over a tree: timelines share a prefix, so a parent's overloaded days are
    also its children's and adding them would report a company two and three times as overloaded
    as it was. What is safe across a tree is how many of its timelines a line went over in, which
    is what this states.
    """
    lines = payload.get("overload") or []
    rows = []
    for line in lines:
        first = line.get("first_over")
        peak = line.get("peak")
        rows.append(
            (
                line.get("line", ""),
                line.get("director_name", ""),
                join(
                    [
                        figure(line.get("timelines_over"), "timelines over the ceiling"),
                        " of ",
                        figure(line.get("timelines"), "timelines folded"),
                    ]
                ),
                "yes" if line.get("everywhere") else "no",
                figure(peak["load_permille"], "the worst reading anywhere", str(peak.get("basis", AUTHORED)))
                if peak
                else absent("never over"),
                address(first["run_id"], first["at_seq"], first["at_tick"], first["day"])
                if first
                else absent("never over"),
            )
        )
    return tag(
        "section",
        tag("h2", "Where the company was overloaded"),
        tag(
            "p",
            "One row per reporting line, over the whole tree. Days are counted under each "
            "timeline rather than added across them: a fork copies its parent's history, so a "
            "tree-wide total would count the same overloaded day once per timeline that "
            "inherited it.",
        ),
        table(
            [
                ("Line", False),
                ("Director", False),
                ("Timelines over", True),
                ("In every one", False),
                ("Worst (per mille)", True),
                ("First went over", False),
            ],
            rows,
        )
        or tag("p", "No line went over its ceiling in any timeline.", class_="absent"),
    )


def _prescription(payload: Mapping[str, Any]) -> Html:
    """What is worth automating, and why this document is willing to say so (M57, M58)."""
    proposals = payload.get("proposals") or []
    rule = payload.get("prescription_rule") or {}
    return tag(
        "section",
        tag("h2", "What is worth automating"),
        tag("p", rule.get("says", ""), " ", figure(rule.get("min_overload_days"), "the threshold", str(rule.get("basis", AUTHORED)))),
        tag(
            "p",
            "Nothing was proposed. A line has to sit over its ceiling for long enough to make "
            "the case, and none did — which is a report with nothing to prescribe rather than "
            "one that failed to prescribe.",
            class_="absent",
        )
        if not proposals
        else join(_proposal(proposal) for proposal in proposals),
    )


def _proposal(proposal: Mapping[str, Any]) -> Html:
    return tag(
        "article",
        tag("h3", proposal.get("title", "")),
        tag(
            "p",
            tag("span", proposal.get("line", ""), class_="tag"),
            " ",
            f"{proposal.get('director_name', '')}'s line. ",
            proposal.get("detail", ""),
        ),
        facts(
            [
                (
                    "Draw it removes",
                    join([marked(proposal.get("removes_draw_hours_per_month"), "hours removed"), " hours a month"]),
                ),
                (
                    "Timelines this held in",
                    join(
                        [
                            figure(proposal.get("timelines_over"), "timelines over the ceiling"),
                            " of ",
                            figure(proposal.get("timelines"), "timelines folded"),
                            " — in every one" if proposal.get("everywhere") else "",
                        ]
                    ),
                ),
            ]
        ),
        _evidence(proposal),
        _payback(proposal),
        _note(proposal.get("note") or {}),
    )


def _evidence(proposal: Mapping[str, Any]) -> Html:
    rows = [
        (citation.get("note", ""), address(citation.get("run_id", ""), citation.get("at_seq"), citation.get("at_tick"), citation.get("at_day")))
        for citation in proposal.get("evidence") or []
    ]
    return table([("What the fold found", False), ("Event", False)], rows, caption="The evidence for it.")


def _payback(proposal: Mapping[str, Any]) -> Html:
    """What it gives back, recomputed from the company's own arithmetic at a cited boundary.

    Both halves are printed — money and load — because the evidence that motivates a proposal is
    an overloaded line, and a payback stated only in days of runway would answer a question
    nobody asked.
    """
    rows = []
    for entry in proposal.get("payback") or []:
        rows.append(
            (
                number(entry.get("day")),
                marked(entry.get("days_over"), "days over"),
                join([marked(entry.get("daily_burn_before"), "burn before"), " → ", marked(entry.get("daily_burn_after"), "burn after")]),
                marked(entry.get("daily_saving"), "daily saving"),
                join([marked(entry.get("runway_days_before"), "runway before"), " → ", marked(entry.get("runway_days_after"), "runway after")]),
                marked(entry.get("runway_days_gained"), "runway gained"),
                join([marked(entry.get("load_permille_before"), "load before"), " → ", marked(entry.get("load_permille_after"), "load after")]),
                "yes" if entry.get("clears_the_ceiling") else "no",
                address(entry.get("run_id", ""), entry.get("at_seq"), entry.get("at_tick"), entry.get("day")),
            )
        )
    return table(
        [
            ("At day", True),
            ("Days over", True),
            ("Daily burn", True),
            ("Saved a day", True),
            ("Runway", True),
            ("Days gained", True),
            ("Load (per mille)", True),
            ("Clears the ceiling", False),
            ("Boundary", False),
        ],
        rows,
        caption="What it gives back, recomputed at the day boundary each row cites.",
    )


def _note(note: Mapping[str, Any]) -> Html:
    """The one part of a proposal a model wrote, said outright (M58).

    `model_identity` is on the payload and is deliberately not printed: the manifest declares it
    withheld and the suite asserts the value never reaches these bytes. Which provider an
    operator ran is theirs, and this file travels.
    """
    sentences = note.get("sentences") or []
    if not sentences:
        return tag(
            "p",
            note.get("reason") or "No prose was written over this proposal; its figures stand alone.",
            class_="absent",
        )
    return tag(
        "div",
        join(
            tag(
                "p",
                sentence.get("text", ""),
                " ",
                tag("span", "[" + ", ".join(str(seq) for seq in sentence.get("citations") or []) + "]", class_="cites mono"),
            )
            for sentence in sentences
        ),
        tag("p", note.get("written_by", ""), class_="cites"),
        class_="prose",
    )


def _claims(payload: Mapping[str, Any]) -> Html:
    """Every figure, and the event it came from (M55).

    Grouped by timeline, because the address is the pair and a flat list would make a reader
    scan a run id column to find the one they hold a log for.
    """
    claims = payload.get("claims") or []
    by_run: dict[str, list[Mapping[str, Any]]] = {}
    for claim in claims:
        by_run.setdefault(str(claim.get("run_id", "")), []).append(claim)

    sections = []
    for run_id, held in by_run.items():
        rows = [
            (
                claim.get("label", ""),
                figure(claim.get("value"), str(claim.get("label", "")), str(claim.get("basis", AUTHORED))),
                number(claim.get("at_seq")),
                number(claim.get("at_tick")),
                number(claim.get("at_day")),
            )
            for claim in held
        ]
        sections.append(
            tag(
                "div",
                tag("h3", tag("span", run_id, class_="mono")),
                table(
                    [("Figure", False), ("Value", True), ("Sequence", True), ("Tick", True), ("Day", True)],
                    rows,
                ),
            )
        )

    return tag(
        "section",
        tag("h2", "Every figure, and the event it came from"),
        tag(
            "p",
            f"{number(len(claims))} figures, each addressed by the run and the sequence "
            "together. A sequence alone is not an address in a tree of timelines: a fork copies "
            "its parent's rows, so the same sequence exists in the parent and in every child.",
        )
        if claims
        else tag(
            "p",
            "No figure in this document resolves to an event, because no timeline in this "
            "Universe could be folded. The refusals are under “The timelines”, above.",
            class_="absent",
        ),
        join(sections),
    )


def _footer(payload: Mapping[str, Any]) -> Html:
    return tag(
        "footer",
        tag(
            "p",
            "Company OS — a company simulator. This report describes an invented company and "
            "was produced by folding its event log. ",
            tag("a", REPOSITORY, href=REPOSITORY, rel="noreferrer noopener"),
        ),
        tag(
            "p",
            "Lineage root ",
            mono(payload.get("root_run_id")),
            ", rules ",
            mono(payload.get("rules_ver")),
            ", state shape ",
            mono(payload.get("state_shape_ver")),
            ".",
        ),
    )


# =========================================================================
# The name it is saved under
# =========================================================================

#: What a run id may contribute to a filename. Everything else is replaced.
#:
#: Narrow on purpose. A run id is whatever `POST /runs` accepted — the kernel refuses only
#: control characters, so a quote, a semicolon, a slash or a right-to-left run of Arabic are all
#: reachable — and this string lands in a `Content-Disposition` header and in the name a
#: browser writes to disk.
_USABLE = re.compile(r"[A-Za-z0-9._-]+")

#: How much of an id the name keeps. Long enough for every id this product mints — `run-` plus
#: twelve hex — and short enough to leave room on filesystems that bound a name at 255 bytes.
_MAX_SLUG = 60


def filename_for(root_run_id: str) -> str:
    """What the exported file is called, derived from a validated identifier.

    **Validated rather than trusted, and derived rather than refused.** An id the pattern does
    not accept whole is not a reason to withhold a perfectly good report, so the unusable parts
    are dropped and an id that survives as nothing at all falls back to a digest of itself. The
    document always prints the root id in full, so nothing is lost by the name being tamer than
    the id.
    """
    kept = "-".join(_USABLE.findall(root_run_id))[:_MAX_SLUG].strip("-.")
    if not kept:
        kept = hashlib.blake2s(root_run_id.encode("utf-8"), digest_size=6).hexdigest()
    return f"company-os-report-{kept}.html"
