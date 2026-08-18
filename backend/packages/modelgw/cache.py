"""The response cache's content address, and the shape of a store that holds one.

M33 asks that the same situation return the same advice without paying for it twice. R3 says
what "the same situation" means: a digest of the assembled prompt, the authorization scope the
context was drawn under, and a purpose namespace. This module is that derivation, the protocol a
store-backed cache satisfies, and an in-memory one for a bench with no store to reach.

**The cache is authoritative for nothing, and that is the property to protect.** A fork copies
the parent's event rows as a prefix, and replay reads the log rather than calling out — so every
pre-divergence statement in a child *is* the parent's statement, byte for byte, and the cache is
never consulted for those ticks. M34 is discharged by the log and the prefix copy. What this
buys is money and latency after a divergence, where the same situation is reached again. Emptying
the table changes no state hash, which is the test that proves where the property lives.

**Keyed on the situation, not on when it happened.** R29 replaced M33's original "tick, person and
request" key with the digest for two reasons that point the same way: keying on tick is redundant
before a divergence, because the prefix copy already makes those statements identical, and wrong
after one, because the same situation reached at two ticks would miss and call the provider.

**A `Failure` cannot become an entry, by signature.** `put` takes a `Completion`. Execution
resolved that a scripted fallback is never cached — a cached wrong answer has a long life,
and the operator's remedy for it, raise the ceiling or configure a key, would silently not
work — and the type is that decision rather than a comment nobody reads.
`ceiling.BoundedGateway.note_cache_hit` is typed the same way for the same reason, and
`BoundedGateway.keep` is the other half: what a provider said is not yet what the caller
accepted, so the write waits to be committed.

**Stdlib only.** `tests/test_modelgw.py` walks this package's AST and requires the third-party
import set to be exactly `{"httpx"}`, which is what keeps `modelgw` a package a keyless run
imports for free. So the store-backed implementation lives in `services/agents/`, next to
`StoreSpendLedger`, and this module holds the derivation, the protocol and the seam between
them — the same split U9 made for the spend counter.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from modelgw import Completion, Usage
from modelgw.config import Prompt

__all__ = [
    "CacheKey",
    "CachedResponse",
    "KEY_VERSION",
    "MemoryResponseCache",
    "Purpose",
    "ResponseCache",
    "digest_of",
    "entry_from",
]

#: The derivation's own version, mixed into every digest.
#:
#: A change to what goes into the key — a prompt field this forgets today, a scope that grows a
#: third dimension — must not let an entry written under the old rule answer a lookup made under
#: the new one. Bumping this makes every existing digest miss, which is the correct and cheap
#: outcome: a miss costs one provider call, and a stale hit costs a briefing drawn from a
#: situation that was never actually the same.
KEY_VERSION = 1


class Purpose(StrEnum):
    """What the call was for. The namespace half of R3.

    Two members, and the second one exists before its caller does deliberately. A CEO summary and
    a director statement can be raised at the same tick about the same person, and if the prompts
    ever converged — a short summary of one checkpoint reads very like a briefing on it — one
    would be served where the other was asked for. That collision is impossible to notice from the
    served text, so the namespace has to be in the key before the second producer is written
    rather than added when somebody sees the wrong block on screen. U14 owns the summary itself.
    """

    DIRECTOR_STATEMENT = "director_statement"
    CEO_SUMMARY = "ceo_summary"


@dataclass(frozen=True, slots=True)
class CacheKey:
    """One entry's address: what was asked, and which run asked it.

    `digest` is the content address and the whole of R3. `run_id` is not part of it — it is
    how an implementation finds the *lineage* the entry is scoped to, because a run knows its root
    and a caller should not have to. The plan is explicit that the lineage root scopes the entry
    and is not part of correctness, and this is that sentence as two fields.
    """

    digest: str
    run_id: str
    #: Already inside `digest`. Carried alongside it so an implementation can record which kind
    #: of call an entry answers without recomputing anything — a table of opaque hashes is
    #: unreadable to whoever is asking why the bench is quiet.
    purpose: Purpose = Purpose.DIRECTOR_STATEMENT

    @classmethod
    def derive(
        cls,
        prompt: Prompt,
        *,
        scope: Mapping[str, Iterable[str]],
        purpose: Purpose,
        run_id: str,
    ) -> CacheKey:
        """The address of the answer to this prompt, asked for this purpose, under this scope.

        `scope` is `simcore.statement.Authorized.to_payload()` — a mapping of sorted people and
        items — passed as a plain mapping rather than as the type, because this package may not
        import `simcore` and does not need to: what the key needs is the *values*, and re-sorting
        them here means the digest cannot depend on the caller having sorted them first.

        The scope is in the key rather than assumed to be implied by the prompt. Two scopes
        frequently assemble the same evidence — most obviously when both retrieved nothing —
        and a statement produced under a granted Authorization must not be served into one that
        never granted one (U15). The prompt is the situation; the scope is what the situation was
        allowed to be drawn from, and they are different facts.
        """
        return cls(
            digest=digest_of(prompt, scope=scope, purpose=purpose),
            run_id=run_id,
            purpose=purpose,
        )


def digest_of(
    prompt: Prompt, *, scope: Mapping[str, Iterable[str]], purpose: Purpose
) -> str:
    """SHA-256 over a canonical rendering of everything that decides the answer.

    JSON with sorted keys rather than `repr` or a hand-rolled join: `repr` of a dataclass is a
    debugging convenience and not a stability promise, and an ad-hoc join needs an escaping scheme
    the moment a prompt contains the delimiter. Every value here is a string, an int or a
    float, so the encoding is total and identical across processes and Python versions.

    Every field of the prompt is included, `max_output_tokens` and `temperature` among them. They
    are not prose, but they change the answer, and a key that ignored them would serve a reply
    produced under settings the caller has since changed.
    """
    payload = {
        "key_version": KEY_VERSION,
        "purpose": str(purpose),
        "scope": {
            str(name): sorted(str(value) for value in values) for name, values in scope.items()
        },
        "prompt": {
            "system": prompt.system,
            "turns": [[turn.role, turn.text] for turn in prompt.turns],
            "max_output_tokens": prompt.max_output_tokens,
            "temperature": prompt.temperature,
        },
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CachedResponse:
    """What was kept of one provider answer: the prose, and which model wrote it.

    **Not the usage, and that omission is the point.** Serving this contacts no provider, so the
    call costs nothing and the ceiling must not move; an entry carrying the original call's token
    counts would invite exactly the double count `note_cache_hit` refuses to make. `as_completion`
    therefore reports zeros, which is the honest reading of what a hit spent.

    **Not the provider either.** The model identity is kept because the statement records it and a
    served hit must not misattribute the prose; the provider is not, because naming one would name
    a call that did not happen — and R6 keeps provider identity out of the log regardless.

    **Not the prompt.** Only its digest is stored, so the table cannot become a second copy of the
    run's world sitting outside the append-only log.
    """

    text: str
    model: str

    def as_completion(self) -> Completion:
        return Completion(text=self.text, usage=Usage(), provider="", model=self.model)


def entry_from(served: Completion) -> CachedResponse:
    """One entry from the completion that produced it, or a `TypeError`.

    The runtime half of the signature: a `Protocol` cannot make a caller pass a `Completion`, so
    every implementation funnels through here and a `Failure` is refused where it would be written
    rather than discovered as canned prose that outlived its condition.
    """
    if not isinstance(served, Completion):
        raise TypeError(
            "only a provider's completion is cached; a Failure is a fallback, and a fallback is "
            "never a cache entry — the operator's remedy for one is to raise the ceiling or "
            "configure a key, and a cached fallback would make that silently not work"
        )
    return CachedResponse(text=served.text, model=served.model)


class ResponseCache(Protocol):
    """Where an answer is kept between two runs of the same situation.

    Two implementations, and the persistent one is the point — the same shape `SpendLedger` has,
    for the same reason: the arithmetic is testable without a database, and a bench whose store is
    unreachable still works.

    **Neither method may raise over a store it could not reach.** A cache is an optimisation on a
    leg whose whole job is to be optional: a lookup that failed is a miss, and a write that failed
    costs one provider call the next time round. Both are answers this system already handles; an
    exception on the worker thread that produces a briefing is not.

    The one exception is deliberate and is not about the store: `put` handed something that is not
    a `Completion` raises, because that is this repository calling it wrong, and swallowing it
    would be the quiet path to a cached fallback.
    """

    def get(self, key: CacheKey) -> Completion | None: ...

    def put(self, key: CacheKey, served: Completion) -> None: ...


class MemoryResponseCache:
    """A cache that forgets when the process does.

    Not a stub, and not only a test double: this is the right cache for a bench with no reachable
    store, where the alternative is calling the provider twice for one situation because the
    counter's database is down.

    `lineage_roots` is the mapping the store gets from `runs.lineage_root_id`, exactly as
    `MemorySpendLedger` takes it. A run absent from it is its own root, which is what R21 says at
    creation and what makes an unforked run's entries correctly its own.

    No rules version here. The store-backed cache carries one because a stored entry outlives the
    build that wrote it; a dictionary in a process cannot, because the rules version is a constant
    of the code that process is running.
    """

    __slots__ = ("by_lineage", "lineage_roots")

    def __init__(self, lineage_roots: Mapping[str, str] | None = None) -> None:
        self.by_lineage: dict[tuple[str, str], CachedResponse] = {}
        self.lineage_roots: dict[str, str] = dict(lineage_roots or {})

    def get(self, key: CacheKey) -> Completion | None:
        entry = self.by_lineage.get(self._address(key))
        return entry.as_completion() if entry is not None else None

    def put(self, key: CacheKey, served: Completion) -> None:
        self.by_lineage[self._address(key)] = entry_from(served)

    def _address(self, key: CacheKey) -> tuple[str, str]:
        return (self.lineage_roots.get(key.run_id, key.run_id), key.digest)
