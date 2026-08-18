"""The agents-side call site: ask the provider, and refuse what it says here as well as in the kernel.

**One implementation, two call sites** (execution decision §1). Every predicate lives in
`simcore.statement`, which both services may import and neither may reach past. This module is the
cheap rejection — a statement refused here never crosses the wire, and the guard sits next to the
prompt that produced it — while the kernel's copy at answer-application time is the auditable one,
because its verdict is an output event the fold regenerates. Nothing here reimplements a rule. If a
predicate is missing, it is missing from `simcore.statement` and that is where it goes.

**Every way this can fail takes one exit.** A guard refusal, a provider error, a timeout, a 429 and an
exhausted ceiling all produce the same thing: that turn's scripted reply, carrying the closed-enum
condition that fired (R5, M21). One exit rather than five is what keeps the surface from having a
state per failure mode, and the enum rather than a message is what keeps a provider's phrasing out of
an append-only log.

**A cached answer is re-read and re-guarded every time it is served.** What the cache keeps is the
provider's reply exactly as it arrived, so `prompts.parse` and both guard predicates run over a hit
on the way out just as they did on the way in. A rule tightened tomorrow therefore refuses an entry
written today, which is the property that makes caching a *cost* optimisation rather than a hole in
the guards — storing the parsed, approved statement instead would have made every entry a permanent
exemption from whatever the rules became.

Nothing enters the cache until the guards have passed it, which is the other half of the same rule
and the reason `BoundedGateway.complete` stages a write that `keep()` commits. What survives is one
narrow case, named rather than papered over: an entry written before a rule was tightened is refused
on every serve and never re-asked, because a hit reaches no provider. That is a mid-lineage code
change, the remedy is emptying the table, and the README says so.

**With no provider configured there is no fallback, and that is M20.** `NOT_CONFIGURED` returns
nothing at all, so the request reaches its deadline exactly as it did before this unit existed and the
conversation is the Phase 2 conversation. A canned block on a keyless run would be this repository
inventing a bench nobody asked for.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from modelgw import Completion, Failure, FailureKind
from modelgw.cache import CacheKey, Purpose
from modelgw.ceiling import BoundedGateway
from modelgw.config import Prompt
from simcore import statement as stmt

from agents.bench import personas, prompts
from agents.bench.context import Retrieved
from agents.bench.personas import Persona

@dataclass(frozen=True, slots=True)
class Situation:
    """Everything one statement needs, assembled from the run's log.

    `offered` is the guards' view of the checkpoint and `checkpoint` is the prompt's. Both come from
    the same genesis catalog entry, which is what keeps "what the director was shown" and "what the
    director may say" from being two readings of two things.
    """

    request: stmt.StatementRequest
    persona: Persona
    #: The genesis catalog's projection of the checkpoint: its label, its rendered prompt, its options.
    checkpoint: dict[str, Any]
    offered: stmt.Offered
    retrieved: Retrieved


#: The shape the prose seam answers in: briefing, objection, citations, model identity, producer kind,
#: fallback reason. A tuple rather than a `Statement`, because the caller owns attaching the retrieved
#: context — the prose half must not be able to choose what evidence it is recorded against.
Prose = tuple[str, str, tuple[int, ...], str, str, str]


def prose_from_provider(situation: Situation, gateway: BoundedGateway) -> Prose | None:
    """Ask the provider for a briefing, and answer with prose or with the fallback's.

    `None` only for `NOT_CONFIGURED` (M20). Every other outcome is prose, because every other outcome
    is a bench that exists and did not answer — which the player is entitled to be told about.
    """
    if not gateway.present:
        return None

    prompt = prompts.build(
        persona=situation.persona,
        checkpoint=situation.checkpoint,
        retrieved=situation.retrieved,
    )
    answer = _completed(gateway, situation.request.run_id, prompt, cache_key_for(situation, prompt))

    if isinstance(answer, Failure):
        if answer.kind is FailureKind.NOT_CONFIGURED:
            # Reachable even past `gateway.present`: the ceiling wrapper is in front of a gateway
            # whose configuration is read at construction, and the two are checked at different
            # moments. Treated as absence rather than as failure, so the answer is the same either
            # way round.
            return None
        return fallback_prose(reason_for(answer))

    if not isinstance(answer, Completion):  # pragma: no cover - the union has two arms
        return fallback_prose(stmt.FALLBACK_GUARD_REFUSED)

    reply = prompts.parse(answer.text)
    if reply is None:
        # Not a wire-level malformation but a reply that ignored the format, and the same member
        # covers both: from an operator's side "the response was not the shape required" is one
        # condition with one remedy, which is to look at what the model actually returned.
        return fallback_prose(str(FailureKind.MALFORMED_RESPONSE))

    return (
        reply.briefing,
        reply.objection,
        reply.citations,
        # The model, never the provider. R6 keeps a provider name and a base URL out of the log
        # entirely, and `Failure.detail`'s docstring is explicit that `kind` is the only field of a
        # failure that may enter a payload — the same line applies to a completion's fields.
        answer.model[: stmt.MAX_IDENTITY_CHARS],
        stmt.PRODUCER_MODEL,
        "",
    )


def cache_key_for(situation: Situation, prompt: Prompt) -> CacheKey:
    """Where this statement's answer is kept, if one was ever kept (R3).

    Derived here rather than inside the gateway because the two things beyond the prompt that
    decide it — the authorization scope the context was drawn under, and what the call is *for* —
    are facts about the situation, and a gateway that inferred either would be inferring the scope
    it exists to be constrained by.

    The scope is the one the request *carried*, which is the one the retrieval was actually run
    under: `Authorized.to_payload()`, already sorted, already recorded on `REQUEST_RAISED`. So the
    key can be re-derived from the log by anyone auditing why a hit was served.
    """
    return CacheKey.derive(
        prompt,
        scope=situation.request.authorized.to_payload(),
        purpose=Purpose.DIRECTOR_STATEMENT,
        run_id=situation.request.run_id,
    )


def fallback_prose(reason: str) -> Prose:
    """That turn's scripted reply, naming the condition that fired.

    The one constructor for a fallback, called from the provider path here and from the guard path in
    the service module. Two constructions of a fallback is how a run ends up with one that names no
    reason, which `simcore.statement.refusal` refuses — so the refusal would surface as a *second*
    fallback and the original condition would be lost.
    """
    return (
        personas.FALLBACK_BRIEFING,
        personas.FALLBACK_OBJECTION,
        (),
        "",
        stmt.PRODUCER_SCRIPTED,
        reason if reason in stmt.FALLBACK_REASONS else stmt.FALLBACK_GUARD_REFUSED,
    )


def reason_for(failure: Failure) -> str:
    """The logged condition for one provider failure.

    `FailureKind`'s values and `FALLBACK_REASONS` are the same set of strings minus `not_configured`,
    which is asserted in `tests/test_bench.py` rather than assumed here — `simcore` may not import the
    model gateway, so the two lists cannot be one list. An unmapped kind becomes `gateway_fault`
    rather than passing an unknown string into an append-only log: a kind this repository forgot to
    map is this repository's bookkeeping failing, not the provider's.
    """
    value = str(failure.kind)
    return value if value in stmt.FALLBACK_REASONS else str(FailureKind.GATEWAY_FAULT)


def refusal_of(answer: dict[str, Any], situation: Situation) -> str:
    """Why this statement may not cross the wire, or the empty string.

    Checked against the scope the request *carried*, which is the scope this side was told to work
    under, and against the offer this side read from the catalog. The kernel re-derives both from
    folded state, so the two agreeing is a property rather than an assumption — and when they
    disagree, the kernel's is the one that decides.
    """
    return stmt.refusal(answer, authorized=situation.request.authorized, offered=situation.offered)


def _completed(
    gateway: BoundedGateway, run_id: str, prompt: Prompt, key: CacheKey | None = None
) -> Completion | Failure:
    """One bounded call, from a worker thread with no event loop of its own.

    The one place in the bench that reaches a provider, which is why the cache lookup is a
    parameter of this call rather than a step threaded through the leg: `key` addresses the
    answer, and the gateway serves it from the store before it spends anything, or writes it there
    after it does.

    The kernel dispatches the producer through `asyncio.to_thread`, so this runs on a thread where
    `asyncio.run` is legal and the gateway's `httpx.AsyncClient` is created and closed inside one
    loop. That is why the caller builds a *fresh* gateway per statement rather than holding one: a
    client created in the launcher's loop and awaited in this one is the cross-loop bug this shape
    cannot have. One client per briefing is a real cost and a small one at a briefing every two
    sim-days.

    It raises nothing. `BoundedGateway.complete` already answers with a typed failure rather than an
    exception, and the `except` here is for this module's own mistakes — an exception crossing back
    would land on a worker thread whose whole job is to be optional.
    """

    async def call() -> Completion | Failure:
        try:
            return await gateway.complete(run_id, prompt, key)
        finally:
            await gateway.aclose()

    try:
        return asyncio.run(call())
    except Exception as exc:  # noqa: BLE001 - an optional leg does not raise into the kernel
        return Failure(kind=FailureKind.GATEWAY_FAULT, detail=f"{type(exc).__name__}: {exc}")
