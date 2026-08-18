"""The per-run ceiling, and the counter the player reads.

M27 says a run carries a hard ceiling on model calls and on tokens, that reaching it
falls back to scripted replies, and that it never stops the run. R4 adds where the
ceiling is enforced — per run — and where the spend is *shown* — aggregated across the
lineage. Those are different questions with different answers, and this module is where
the difference lives.

**In front of `complete()`, never inside it.** The ceiling has to refuse *before* the
provider is contacted, which a check inside the gateway could not do for the one case
that matters: a single call whose input alone will not fit in what is left. So
`BoundedGateway` wraps a `ModelGateway` and answers with the same union, which is what
keeps U11's fallback path at one branch — `isinstance(answer, Failure)` — instead of
gaining a third arm for "refused locally". `FailureKind.NOT_CONFIGURED` is the
precedent: a refusal that never reaches a wire is still a typed failure.

**The ceiling is per run; only the display is per lineage.** A lineage-wide budget would
leave a child at its parent's exhaustion point, so the bench would go quiet at a
different moment in each timeline and the diff would present *budget* as consequence —
the exact thing M34 forbids. So `read()` is what the ceiling is checked against and
`lineage()` is what the HUD renders beside it. A parent at its ceiling changes nothing
about a child's arithmetic, and the R4 test asserts that rather than trusting it.

**The shipped default is a finite number, and unbounded has to be typed out.** On a
bring-your-own-key tool the failure mode of a deferred number is `None` meaning
unlimited, discovered on an invoice. So an absent setting is the default, an unreadable
setting is the default, and the *word* `unlimited` is the only thing that removes the
bound — announced at startup at WARNING, naming the variable and what it costs. `int |
None` was rejected as the field type for that reason alone: the representation should
not have a spelling for "unbounded" that an absent value can fall into. `math.inf` is
total under comparison and arithmetic, which a sentinel like `-1` is not.

**A call that reached the provider counts, whatever came back.** A 500, a timeout and a
429 each cost an attempt, and not counting them would let a misconfigured run retry
forever against a counter that never moves. The one exception is `NOT_CONFIGURED`, which
contacted nothing — which is also why a keyless run's counter reads zero rather than
climbing (M20).

**A cache hit is not a call, and a fallback is not a hit.** `note_cache_hit` takes the
`Completion` that was served rather than nothing at all, so a `Failure` cannot be
recorded as a hit even by accident. That is the execution decision "a scripted fallback
is never a cache entry" made structural instead of stated; `modelgw.cache` holds the
content address and the protocol, and the lookup runs here because a hit must be served
*before* the ceiling refuses — see `BoundedGateway.complete`. The *write* runs here too
and waits to be told: only the caller knows whether the answer survived the guards, so
`complete` stages an entry and `keep` commits it.

Stdlib only, deliberately. `tests/test_modelgw.py` asserts this package imports nothing
outside the standard library but `httpx`, so the store-backed ledger lives with the
service that has an engine — `services/agents/main.py`, following the report's pattern —
and this module holds the policy, the arithmetic and the seam between them.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from modelgw import Answer, Completion, Failure, FailureKind, ModelGateway
from modelgw.cache import CacheKey, ResponseCache
from modelgw.config import Prompt

__all__ = [
    "BoundedGateway",
    "CHARS_PER_TOKEN",
    "Ceiling",
    "CeilingSource",
    "DEFAULT_MAX_CALLS",
    "DEFAULT_MAX_TOKENS",
    "ENV_MAX_CALLS",
    "ENV_MAX_TOKENS",
    "MemorySpendLedger",
    "Spend",
    "SpendLedger",
    "SpendReading",
    "UNLIMITED_SPELLING",
    "announce",
    "estimated_input_tokens",
]

log = logging.getLogger("modelgw.ceiling")

ENV_MAX_CALLS = "COMPANY_OS_MODEL_MAX_CALLS"
ENV_MAX_TOKENS = "COMPANY_OS_MODEL_MAX_TOKENS"

#: Model calls one run may make. Sized against a full playthrough rather than against a
#: budget: a shipped scenario raises on the order of a dozen checkpoints, a briefing and
#: its objection ride one call, and the specialists never call at all. Two hundred leaves
#: room for a run that revisits, forks and re-opens without a player meeting the ceiling
#: by accident — and it bounds a runaway loop at roughly the cost of a coffee on any
#: provider in `PROVIDERS`, which is the number that matters when the key is the
#: operator's own.
DEFAULT_MAX_CALLS = 200

#: Tokens one run may spend, input and output together. A bench prompt plus its retrieved
#: context runs to a low four figures, so this is the call ceiling expressed in the unit
#: the invoice uses — and it is the half that catches a context assembly that grew
#: without anybody noticing, which a call count cannot see.
DEFAULT_MAX_TOKENS = 600_000

#: The one value that removes a bound. A word rather than a number, because `0` has an
#: obvious and different meaning — no calls at all, which is a legitimate way to turn the
#: bench off — and a negative number is a typo rather than a request.
UNLIMITED_SPELLING = "unlimited"

#: Characters per token. An estimate, not a tokenizer: M26 keeps `tiktoken` out of the
#: tree and a per-provider tokenizer would be eight of them. Four is the usual English
#: approximation, and it is only ever used to refuse a call that cannot fit — the *call*
#: ceiling is the one that always binds, which is why an estimate is enough here. See
#: `WireReply` for the other half of that argument: a server reporting no usage at all
#: counts as zero tokens, so the token ceiling was never the load-bearing one.
CHARS_PER_TOKEN = 4

#: Per-turn envelope overhead: a role name, a delimiter, the wire's own framing. Counted
#: so that a prompt of many short turns is not estimated at nearly nothing.
TOKENS_PER_TURN = 4


class CeilingSource(StrEnum):
    """How one half of the ceiling was decided. What `announce` reads.

    Kept because "it is 200" does not distinguish "you asked for 200" from "we could not
    read what you asked for", and those two want different sentences at startup.
    """

    DEFAULT = "default"
    CONFIGURED = "configured"
    UNLIMITED = "unlimited"
    #: A value that could not be read as a whole number of calls or tokens. The default
    #: stands and the log says so — treating an unreadable number as no bound is the
    #: failure this module exists to prevent.
    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class Ceiling:
    """What one run may spend. Finite unless somebody typed the word out.

    Both bounds are floats so `math.inf` is a value the arithmetic accepts rather than a
    case every comparison special-cases. Every configured bound is a whole number well
    inside exact float representation.
    """

    max_calls: float = DEFAULT_MAX_CALLS
    max_tokens: float = DEFAULT_MAX_TOKENS
    calls_source: CeilingSource = CeilingSource.DEFAULT
    tokens_source: CeilingSource = CeilingSource.DEFAULT

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> Ceiling:
        """Read both bounds. Never raises, and never resolves to unbounded by accident."""
        environ = os.environ if env is None else env
        calls, calls_source = _read_bound(environ, ENV_MAX_CALLS, DEFAULT_MAX_CALLS)
        tokens, tokens_source = _read_bound(environ, ENV_MAX_TOKENS, DEFAULT_MAX_TOKENS)
        return cls(
            max_calls=calls,
            max_tokens=tokens,
            calls_source=calls_source,
            tokens_source=tokens_source,
        )

    def describe(self) -> dict[str, Any]:
        """The ceiling as a payload. `None` is unlimited, and only here.

        On the wire `None` is safe where in configuration it is not: a client reading
        `max_calls: null` renders "no ceiling", whereas an *absent setting* resolving to
        `None` would be a run with no bound that nobody asked for.
        """
        return {
            "max_calls": _wire_bound(self.max_calls),
            "max_tokens": _wire_bound(self.max_tokens),
            "calls_source": str(self.calls_source),
            "tokens_source": str(self.tokens_source),
        }


def _read_bound(
    environ: Mapping[str, str], variable: str, default: int
) -> tuple[float, CeilingSource]:
    raw = (environ.get(variable) or "").strip()
    if not raw:
        return float(default), CeilingSource.DEFAULT
    if raw.lower() == UNLIMITED_SPELLING:
        return math.inf, CeilingSource.UNLIMITED
    try:
        value = int(raw)
    except ValueError:
        return float(default), CeilingSource.UNREADABLE
    if value < 0:
        return float(default), CeilingSource.UNREADABLE
    # Zero is honoured. Turning the bench off with a ceiling of nothing is a legitimate
    # thing to want, and it is the one number that must not be read as "unset" — which is
    # exactly what `if not value` would have done.
    return float(value), CeilingSource.CONFIGURED


def _wire_bound(bound: float) -> int | None:
    return None if math.isinf(bound) else int(bound)


def announce(ceiling: Ceiling, logger: logging.Logger | None = None) -> None:
    """Say what the ceiling is, once, at startup. Loudly when there is none.

    WARNING rather than INFO for the unlimited case, and the sentence names the variable
    and the consequence: an operator who typed the word out on their own key should meet
    it in the same scan as a failed dependency, not filed under normal operation.
    """
    out = logger if logger is not None else log

    for name, variable, bound, source in (
        ("calls", ENV_MAX_CALLS, ceiling.max_calls, ceiling.calls_source),
        ("tokens", ENV_MAX_TOKENS, ceiling.max_tokens, ceiling.tokens_source),
    ):
        if source is CeilingSource.UNLIMITED:
            out.warning(
                f"the model {name} ceiling is UNLIMITED: {variable} is set to "
                f"'{UNLIMITED_SPELLING}', so one run can spend without bound against the "
                f"configured key. Unset {variable} to restore the shipped ceiling.",
                extra={"ceiling": name, "variable": variable, "bound": None},
            )
        elif source is CeilingSource.UNREADABLE:
            out.warning(
                f"{variable} could not be read as a whole number of {name}; the shipped "
                f"ceiling of {int(bound)} stands. An unreadable setting is never treated as "
                f"no ceiling — type '{UNLIMITED_SPELLING}' if that is what you meant.",
                extra={"ceiling": name, "variable": variable, "bound": int(bound)},
            )
        else:
            out.info(
                f"the model {name} ceiling is {int(bound)} per run",
                extra={"ceiling": name, "variable": variable, "bound": int(bound)},
            )


@dataclass(frozen=True)
class Spend:
    """What a run has spent. Counted, not authored — the one figure in the product that is.

    `cache_hits` is a field rather than something derived because "not a call" has to be
    something the counter can *say*. A cache hit that left no trace would be
    indistinguishable from a turn that never happened, and an operator asking why the
    ceiling is not moving would have nothing to read.
    """

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hits: int = 0

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def plus(self, other: Spend) -> Spend:
        return Spend(
            calls=self.calls + other.calls,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_hits=self.cache_hits + other.cache_hits,
        )


#: One attempt that reached the provider and came back with nothing to count.
ONE_CALL = Spend(calls=1)


class SpendLedger(Protocol):
    """Where the count lives. Two implementations, and the persistent one is the point.

    `read` is what the ceiling is checked against — this run, per M27. `lineage` is what
    the HUD renders beside it, and an implementation resolves the lineage root rather
    than walking `parent_run_id`: a deleted mid-lineage row would silently corrupt a
    walk, which is why `runs.lineage_root_id` exists.
    """

    def read(self, run_id: str) -> Spend: ...

    def add(self, run_id: str, delta: Spend) -> Spend: ...

    def lineage(self, run_id: str) -> Spend: ...


class MemorySpendLedger:
    """A ledger that forgets when the process does.

    Not a stub. This is the right ledger for a bench with no store to reach, where the
    alternative is refusing every call because the counter is unreadable — and it is what
    makes the ceiling's arithmetic testable without a database.

    `lineage_roots` is the mapping the store gets from `runs.lineage_root_id`. A run
    absent from it is its own root, which is what R21 says at creation and what makes the
    aggregate correct for every run that has never been forked.
    """

    __slots__ = ("by_run", "lineage_roots")

    def __init__(self, lineage_roots: Mapping[str, str] | None = None) -> None:
        self.by_run: dict[str, Spend] = {}
        self.lineage_roots: dict[str, str] = dict(lineage_roots or {})

    def read(self, run_id: str) -> Spend:
        return self.by_run.get(run_id, Spend())

    def add(self, run_id: str, delta: Spend) -> Spend:
        total = self.read(run_id).plus(delta)
        self.by_run[run_id] = total
        return total

    def lineage(self, run_id: str) -> Spend:
        root = self.lineage_roots.get(run_id, run_id)
        total = Spend()
        for other, spend in self.by_run.items():
            if self.lineage_roots.get(other, other) == root:
                total = total.plus(spend)
        return total


@dataclass(frozen=True)
class SpendReading:
    """This run's spend against this run's ceiling, with the lineage total beside it.

    One object rather than four numbers, because M28 is a single claim about a single
    tile: what this run has spent, out of what it may, and what the session cost
    altogether. Passed separately, a surface could render three of the four and look
    complete.
    """

    run_id: str
    spend: Spend
    lineage_spend: Spend
    ceiling: Ceiling
    #: Whether there is a provider at all. False is a supported state and not a fault:
    #: with no key the counter reads zero and the bench is absent (M20).
    bench_present: bool

    @property
    def calls_exhausted(self) -> bool:
        return self.spend.calls >= self.ceiling.max_calls

    @property
    def tokens_exhausted(self) -> bool:
        return self.spend.tokens >= self.ceiling.max_tokens

    @property
    def quiet(self) -> bool:
        """Whether a ceiling has stopped the bench calling. Not whether the run stopped."""
        return self.calls_exhausted or self.tokens_exhausted

    def to_payload(self) -> dict[str, Any]:
        """What the wire carries. No provider, no model, no key — R6 by omission.

        The provider's identity is irrelevant to this tile, so it is not sent. The
        gateway's `describe()` already reports it where an operator wants it, and a
        figure on the player's screen is not that place.
        """
        return {
            "run_id": self.run_id,
            "calls": self.spend.calls,
            "tokens": self.spend.tokens,
            "input_tokens": self.spend.input_tokens,
            "output_tokens": self.spend.output_tokens,
            "cache_hits": self.spend.cache_hits,
            "lineage_calls": self.lineage_spend.calls,
            "lineage_tokens": self.lineage_spend.tokens,
            "bench_present": self.bench_present,
            "quiet": self.quiet,
            **self.ceiling.describe(),
        }


def estimated_input_tokens(prompt: Prompt) -> int:
    """Roughly how many tokens this prompt's input is, without a tokenizer.

    Used for one decision only: refusing a call whose input alone will not fit in what
    the run has left, before the provider is contacted.
    """
    characters = len(prompt.system) + sum(len(turn.text) for turn in prompt.turns)
    return -(-characters // CHARS_PER_TOKEN) + TOKENS_PER_TURN * (len(prompt.turns) + 1)


class BoundedGateway:
    """A gateway that refuses before it spends, and counts what it spent.

    Presents `complete()` with a run id in front of it and otherwise the same answer
    union `ModelGateway` does. That substitutability is the whole design: U11's fallback
    path branches on `Failure` once, and an exhausted ceiling arrives through the same
    branch as a 429, with no new arm and no exception.
    """

    def __init__(
        self,
        gateway: ModelGateway,
        ceiling: Ceiling,
        ledger: SpendLedger,
        cache: ResponseCache | None = None,
    ) -> None:
        self._gateway = gateway
        self._ceiling = ceiling
        self._ledger = ledger
        self._cache = cache
        self._unkept: tuple[CacheKey, Completion] | None = None

    @property
    def present(self) -> bool:
        return self._gateway.present

    @property
    def ceiling(self) -> Ceiling:
        return self._ceiling

    def reading(self, run_id: str) -> SpendReading:
        """What the HUD renders. The one aggregate that crosses the lineage."""
        return SpendReading(
            run_id=run_id,
            spend=self._ledger.read(run_id),
            lineage_spend=self._ledger.lineage(run_id),
            ceiling=self._ceiling,
            bench_present=self._gateway.present,
        )

    def note_cache_hit(self, run_id: str, served: Completion) -> Spend:
        """Record that a stored provider response answered instead of a call.

        Takes the `Completion` it served rather than nothing, and the type is the point: a
        `Failure` cannot be passed, so a scripted fallback cannot be recorded as a hit.
        Execution resolved that a fallback is never a cache entry — a cached wrong answer
        has a long life, and it would make "raise the ceiling" silently not work — and
        this is that decision expressed as a signature rather than as a comment.

        The served reply's own usage is deliberately not counted. No provider was
        contacted, so no tokens were spent; counting them again would make the ceiling a
        function of how often the cache was consulted.
        """
        if not isinstance(served, Completion):
            raise TypeError(
                "a cache hit is a Completion that was served; a Failure is a fallback, and "
                "a fallback is never a cache entry"
            )
        return self._ledger.add(run_id, Spend(cache_hits=1))

    async def complete(self, run_id: str, prompt: Prompt, key: CacheKey | None = None) -> Answer:
        """One call, against this run's budget. Returns an answer or a typed failure.

        Every refusal below happens with the provider untouched, which is the reason the
        ceiling is a wrapper rather than a check inside `ModelGateway.complete`.

        **`key` is optional so that a caller with no situation to address stays unchanged.** The
        cache is content-addressed on the assembled prompt, the authorization scope and a purpose
        (R3), and only the bench knows those — a caller that passes nothing gets the ceiling
        alone, which is what every test of this class and every future non-bench caller wants.

        **The cache is consulted before the ceiling, and that ordering is deliberate.** The
        ceiling bounds what a run *spends*; a hit spends nothing, contacts nothing and counts as
        no call, so refusing to serve one at an exhausted ceiling would withhold a briefing that
        was already paid for. It does not weaken the off switch: entries are scoped to a lineage,
        so a lineage started under a ceiling of zero has none to serve and the bench is silent for
        its whole life, which is what setting zero is for. What it does mean is that *lowering* a
        ceiling mid-lineage stops new calls rather than repeated situations, and the remedy for
        that — emptying the table — loses nothing the log does not already hold.

        A failed lookup is a miss and a failed write costs one provider call next time. Neither
        raises: this runs on a worker thread whose whole job is to be optional.
        """
        if self._cache is not None and key is not None:
            served = self._cache.get(key)
            if served is not None:
                self.note_cache_hit(run_id, served)
                return served

        spend = self._ledger.read(run_id)

        if spend.calls >= self._ceiling.max_calls:
            return self._refused(
                f"run {run_id} has made {spend.calls} model calls, which is its ceiling of "
                f"{int(self._ceiling.max_calls)}. Raise {ENV_MAX_CALLS} to lift it; the run "
                "continues either way, on scripted replies."
            )

        remaining = self._ceiling.max_tokens - spend.tokens
        if remaining <= 0:
            return self._refused(
                f"run {run_id} has spent {spend.tokens} tokens, which is its ceiling of "
                f"{int(self._ceiling.max_tokens)}. Raise {ENV_MAX_TOKENS} to lift it; the run "
                "continues either way, on scripted replies."
            )

        estimated = estimated_input_tokens(prompt)
        if estimated > remaining:
            # The case a check inside the gateway could not make: this one call will not
            # fit, so it is refused rather than sent and then counted. Only the input is
            # weighed — a reply is bounded by the prompt's own `max_output_tokens`, so a
            # call that passes here overshoots by at most one reply, while weighing both
            # would refuse calls that would in fact have fitted.
            return self._refused(
                f"run {run_id} has {int(remaining)} tokens left and this call's input is "
                f"about {estimated}. Refused before the provider was contacted; raise "
                f"{ENV_MAX_TOKENS} to lift it."
            )

        answer = await self._gateway.complete(prompt)

        if isinstance(answer, Completion):
            self._ledger.add(
                run_id,
                Spend(
                    calls=1,
                    input_tokens=answer.usage.input_tokens,
                    output_tokens=answer.usage.output_tokens,
                ),
            )
            if self._cache is not None and key is not None:
                # Staged, not written. Whether this answer was *usable* is the caller's
                # verdict — a reply that ranks the options is a provider response and still
                # must not be kept — so the write waits for `keep()`. See its docstring.
                self._unkept = (key, answer)
        elif answer.kind is not FailureKind.NOT_CONFIGURED:
            # A 500, a timeout and a 429 each cost an attempt. Not counting them would let
            # a misconfigured run retry forever against a counter that never moves — and R5
            # has already decided the outcome is a fallback rather than a second attempt, so
            # the attempt is spent either way. `NOT_CONFIGURED` is the exception because it
            # contacted nothing, which is what keeps a keyless run's counter at zero (M20).
            self._ledger.add(run_id, ONE_CALL)

        return answer

    def keep(self) -> None:
        """Write what the last call produced, now that the caller has accepted it.

        **The write is deferred because "the provider answered" and "the answer was usable"
        are different facts, and only the caller knows the second.** A reply that ranks the
        options is a good HTTP response and this repository refuses it; caching it would serve
        that refusal back on every future visit to the situation, and the operator would switch
        to a better model and see no change at all. That is the execution decision "guard
        rejections write nothing" failing through a door it did not name.

        A no-op unless the last call reached a provider, answered, and carried a key: a served
        hit needs no write, and a `Failure` never stages anything, so a fallback cannot be kept
        even by a caller that calls this unconditionally. Callable after `aclose`, because a
        cache is not the HTTP client.

        Safe to hold on the instance because a gateway is built per statement and makes one call.
        A caller that made two would keep only the second, which is why this is not a general
        commit protocol and why the docstring says so rather than the type.
        """
        staged, self._unkept = self._unkept, None
        if staged is None or self._cache is None:
            return
        key, answer = staged
        self._cache.put(key, answer)

    async def aclose(self) -> None:
        await self._gateway.aclose()

    def _refused(self, detail: str) -> Failure:
        """One `FailureKind`, not two, and no provider identity on it.

        `CEILING_REACHED` covers both halves of the budget. The distinction an operator
        acts on — which variable to raise — is in `detail`, which reaches stdout; `kind` is
        what reaches an event payload, the exported report and the player's screen, where
        "calls" against "tokens" is noise and where two members would let a diff between
        two timelines turn on which half of a budget ran out first.
        """
        failure = Failure(kind=FailureKind.CEILING_REACHED, detail=detail)
        log.warning(
            "the run reached its model ceiling; falling back to scripted replies",
            extra={"kind": str(failure.kind), "detail": failure.detail},
        )
        return failure
