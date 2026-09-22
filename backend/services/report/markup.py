"""Escaping by construction, for a document that is mailed to somebody (R25).

**The threat is the corpus, not the reader.** Everything the export interpolates is content
this repository did not write: a scenario's `title` and `summary` arrived by pull request, a
proposal's prose came out of a model, a run id came off a URL, and a director's name is a
string in a TOML file. The artifact is then deliberately sent to somebody else and opened from
a filesystem — where, in every browser, a script still reaches the network. So a single missed
`html.escape` in one of a hundred interpolations is a cross-site scripting hole with an
exfiltration path, and "we escaped carefully" is the kind of claim that is true on the day it
is written.

**So raw markup is a type here, and there are exactly two values of it.** `esc` and `tag` are
the only ways to build a fragment, and both escape everything that is not already `Html`. The
only way to get unescaped bytes into a document is to call `Html(...)` directly, which happens
twice in `export.py` — the checked-in QR asset and the stylesheet, both repository-authored
constants — and `test_report.py` asserts that the count stays two. A third call site is a
review conversation rather than a silent hole.

This is deliberately *not* a template engine. A template is a string with holes in it, which is
the shape that makes the missed hole possible; a tree of calls has no holes.
"""

from __future__ import annotations

import html
from collections.abc import Iterable

#: Elements written without a closing tag. Only the ones this document uses: an export with an
#: `<img>` in it would be an export that makes a network request, and there is no list here for
#: it to be on.
VOID = frozenset({"meta", "br", "hr"})


class Html(str):
    """A fragment that is already escaped and may be written out as it stands.

    A `str` subclass rather than a wrapper so a fragment can be joined, sliced and measured like
    the string it is. What the type buys is the opposite of convenience: `tag` escapes every
    child that is *not* one of these, so an ordinary `str` — which is what every value off the
    payload is — cannot reach the document without passing through `html.escape` first.
    """

    __slots__ = ()


#: A fragment that renders nothing. A named constant rather than `Html("")` at each site, so
#: the sweep over `Html(` call sites stays a sweep over the trusted surface rather than over
#: every branch that had nothing to draw.
NOTHING = Html("")


def esc(value: object) -> Html:
    """One value, escaped. The only door text comes through."""
    return Html(html.escape(str(value), quote=True))


def raw(markup: str) -> Html:
    """Markup this repository wrote, taken as it stands.

    **The whole trusted surface of the export, and it is meant to be countable.** Every other
    string reaching the document came from a scenario, a model, a store or a URL; these did not.
    `test_report.py` asserts that `raw` has exactly two call sites — the checked-in QR asset and
    the stylesheet — so a third is a review conversation rather than a line that looks like
    every other line.

    Never call it on anything derived from a payload, however safe the value looks today. The
    value that looks safe today is a scenario field somebody widens tomorrow.
    """
    return Html(markup)


def join(fragments: Iterable[object], separator: str = "") -> Html:
    """Several fragments, in order. Anything not already `Html` is escaped on the way in."""
    return Html(separator.join(_escaped(fragment) for fragment in fragments))


def tag(name: str, /, *children: object, **attrs: object) -> Html:
    """One element.

    The element name is positional-only, because `name` is also an HTML attribute and a
    `tag("meta", name="viewport")` that collided with it would be a `TypeError` at the one call
    site that needed it.

    Attribute names are written Python-side with underscores — `class_`, `data_authored_tuning`
    — and land as `class` and `data-authored-tuning`. A trailing underscore is stripped, which
    is what makes the keywords (`class`, `for`) expressible; every other underscore becomes a
    hyphen.

    An attribute whose value is `True` is written bare; one whose value is `False` or `None` is
    omitted entirely, so a conditional attribute is an expression rather than a branch around
    two copies of the element. A child that is `None` or `False` is skipped for the same reason.
    """
    rendered = "".join(
        _escaped(child) for child in children if child is not None and child is not False
    )
    written = "".join(_attribute(key, value) for key, value in attrs.items())
    if name in VOID:
        assert not rendered, f"{name} is a void element and was given children"
        return Html(f"<{name}{written}>")
    return Html(f"<{name}{written}>{rendered}</{name}>")


def _escaped(value: object) -> str:
    """A child, ready to write. `Html` passes; everything else is escaped."""
    return value if isinstance(value, Html) else html.escape(str(value), quote=True)


def _attribute(key: str, value: object) -> str:
    """One attribute, name normalised and value escaped — including the quotes.

    `quote=True` is what closes the attribute-context hole: a value carrying `"` would otherwise
    end the attribute and start an element, and the payload's strings include a scenario summary
    an author typed.
    """
    if value is None or value is False:
        return ""
    name = key.rstrip("_").replace("_", "-")
    if value is True:
        return f" {name}"
    return f' {name}="{html.escape(str(value), quote=True)}"'
