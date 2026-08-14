"""The version facts every status endpoint reports.

Three of these are established by later units: the rules version is derived in
U3, the event-schema version arrives with the envelope in U2, and the store DDL
version arrives with the store in U6. They are resolved by lookup with a `None`
fallback rather than hard-coded placeholders, so a later unit landing makes every
status endpoint truthful without anyone editing this file — and until then the
payload says `null`, which is honest, instead of a stale string that reads like a
real version.

`store_ddl` is supplied by the service rather than resolved here: only the kernel
and the report service touch the store, and a shared package reaching into a
service's modules is the coupling R4 exists to prevent.

One distinction worth being precise about, because it is easy to conflate. Everything
under `versions` is **what this build believes** — the rules it computes, the envelope it
writes, the store schema it expects. What was actually *found* in the store is a separate
fact, reported under `store`, and R28's startup check is what compares the two. A single
field claiming to be both would be wrong exactly when it mattered: during a mismatch.
"""

from __future__ import annotations

import os
from typing import Any


def git_sha() -> str:
    """Short commit sha, or "unknown".

    This tree is not under version control, so the value comes from the
    environment (set it at image build time) and otherwise reports "unknown"
    rather than shelling out to a `git` that would fail on every call.
    """
    return os.environ.get("COMPANY_OS_GIT_SHA", "unknown")


def rules_version() -> str | None:
    """Hash over the tuning table and multiplier composition order. Set by U3."""
    try:
        from simcore.rates import RULES_VERSION
    except Exception:
        return None
    return str(RULES_VERSION)


def event_schema_version() -> int | None:
    """Envelope schema version. Set by U2."""
    try:
        from contracts.envelope import EVENT_SCHEMA_VERSION
    except Exception:
        return None
    return int(EVENT_SCHEMA_VERSION)


def resolve_versions(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    versions: dict[str, Any] = {
        "rules": rules_version(),
        "event_schema": event_schema_version(),
        "store_ddl": None,
    }
    if extra:
        versions.update(extra)
    return versions
