"""Canonical encoding for anything that crosses the log or gets hashed.

Pure Python, no gRPC, no transport. `simcore` imports this module, so it must stay
that way — R5 forbids the kernel library from acquiring a transport dependency, and
the import-boundary suite enforces it.

Three properties, each of which exists because its absence produces a determinism
bug that surfaces far from its cause:

*Sorted keys.* Two structurally equal states built in different insertion orders
must produce one hash, or a refactor that merely reorders assignments reads as a
determinism regression.

*No floats, ever.* R6. A float that accumulates diverges across platforms and
across the two languages this project implements movement in. Rejecting at the
encoder means the failure lands on the line that produced the float, not at a
day-boundary hash mismatch hours later.

*No sets.* A `set` in kernel state makes iteration order part of the run. Python's
set ordering depends on hash randomisation and insertion history, so the same seed
would produce different logs in different processes.

Deliberately *not* protobuf. Protobuf's wire format is not canonical for maps, so
hashing proto bytes would make replay identity depend on a serializer's field
ordering. Payloads are opaque `bytes` in the envelope for exactly this reason.
"""

from __future__ import annotations

import json
import math
from typing import Any


class NotCanonical(ValueError):
    """A value cannot cross the log. The message names the path and the reason."""


def _describe(path: tuple[str, ...]) -> str:
    return "payload" + "".join(f"[{part}]" for part in path) if path else "payload"


def validate(value: Any, path: tuple[str, ...] = ()) -> None:
    """Raise NotCanonical unless every leaf is an integer, string, bool or None."""
    where = _describe(path)

    # bool before int: bool is a subclass of int in Python, and it is allowed —
    # JSON encodes it as true/false, which is stable. Checking it first keeps the
    # error messages honest about which type was found.
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return

    if isinstance(value, str):
        return

    if isinstance(value, float):
        detail = "NaN" if math.isnan(value) else "inf" if math.isinf(value) else repr(value)
        raise NotCanonical(
            f"{where} is a float ({detail}). R6: no float value accumulates in kernel "
            "state or crosses the log. Express it as an integer in its smallest unit."
        )

    if isinstance(value, (set, frozenset)):
        raise NotCanonical(
            f"{where} is a {type(value).__name__}. Set iteration order depends on hash "
            "randomisation, which would make the same seed produce different logs in "
            "different processes. Use a sorted list."
        )

    if isinstance(value, (bytes, bytearray)):
        raise NotCanonical(f"{where} is {type(value).__name__}; encode it as a string first.")

    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise NotCanonical(
                    f"{where} has a non-string key {key!r} ({type(key).__name__}). JSON "
                    "coerces keys to strings, so 1 and \"1\" would collide silently."
                )
            validate(item, (*path, key))
        return

    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            validate(item, (*path, str(index)))
        return

    raise NotCanonical(f"{where} is {type(value).__name__}, which has no canonical form.")


def encode(value: Any) -> bytes:
    """Canonical bytes for a value. Validates first, so the error names the cause.

    A note for anything that carries these integers to the browser: Python encodes
    integers at arbitrary precision, and `JSON.parse` silently loses precision above
    2^53. Sequence numbers and tick indices are `uint64`. Anything that can exceed
    2^53 must reach the client as a string or a BigInt, which is why the plan lists
    a golden vector above 2^53 as a U12 test scenario.
    """
    validate(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def decode(raw: bytes) -> Any:
    """Decode canonical bytes, validating on read as well as before append.

    Validating twice is the point: a malformed value that reached the store becomes
    a permanent logged fact that every future fold reproduces, and the report would
    then present it with a resolvable event behind it — which makes it look more
    credible, not less.
    """
    try:
        value = json.loads(raw.decode("utf-8"), parse_float=_reject_float)
    except NotCanonical:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NotCanonical(f"payload is not canonical JSON: {exc}") from exc

    validate(value)
    return value


def _reject_float(literal: str) -> Any:
    raise NotCanonical(
        f"payload contains the float literal {literal!r}. R6: no float crosses the log."
    )


def is_canonical(raw: bytes) -> bool:
    """Round-trip check: does re-encoding the decoded value reproduce these bytes?

    Catches bytes that decode fine but were not produced canonically — unsorted
    keys, or added whitespace — which would compare unequal under R11 while
    describing the same state.
    """
    try:
        return encode(decode(raw)) == raw
    except NotCanonical:
        return False
