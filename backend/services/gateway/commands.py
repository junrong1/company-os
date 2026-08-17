"""Commands: applied at a tick boundary, deduplicated structurally, never left hanging.

Three decisions, each preventing a failure the client cannot recover from on its own.

**Validate and apply together, at a tick boundary.** Doing them as separate steps leaves a race:
a command that validated against a state the tick then changed would be applied against a world
that no longer matches what it was checked against. So the outcome the caller receives is the
outcome of *applying* it, not of accepting it.

**Retry is expected behaviour, so deduplication is structural.** A client that never saw a
response has no way to know whether the command landed, and its only sane move is to retry.
Deduplicating on a client-supplied key — and offering an outcome-by-key query — means a retry is
answered with the original outcome rather than applied twice (R30).

**A paused run has no tick boundary, so the semantics are stated rather than left to hang.** The
tempting implementation waits for the next boundary, which never comes, and the client sees a
request that neither succeeds nor fails. Instead the command is rejected with a reason that says
what to do, and the caller decides whether to resume the clock.

That rejection has one exemption, and without it the guard is a trap: `set_rate` is the only
command that can lift a pause, so rejecting it on a paused run makes the pause permanent and the
advice in the rejection impossible to follow. The exemption is sound rather than a special case,
because the guard's own premise does not hold for `set_rate` — it never waits for a boundary. It
writes at the current tick and appends `RATE_CHANGED`, which is exactly why it can restart a clock
that is not running.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from servicekit import logging as svclog

log = svclog.get_logger("gateway")


#: Commands that do not need a tick boundary, and are therefore accepted on a paused run.
#:
#: `set_rate` writes at the current tick rather than waiting for the next, and it is the sole
#: way to lift a pause — guarding it would make a paused run unrecoverable.
#:
#: `compare_options` is here for the other half of the guard's premise. The rule exists because
#: a command that mutates state needs a boundary to mutate it at, and a paused run never
#: reaches one. A comparison mutates nothing: it steps a copy and appends a record of what it
#: showed. Pausing to weigh two options is also exactly when a CEO wants one, so rejecting it
#: on a paused run would refuse the mechanic at the moment it is most useful. The append still
#: works — pause stops the clock, not the writer.
NEEDS_NO_TICK_BOUNDARY = frozenset({"set_rate", "compare_options"})


class Outcome:
    """Status values, mirroring `CommandOutcome.Status` in proto/kernel.proto."""

    APPLIED = "applied"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"
    RUN_PAUSED = "run_paused"
    RUN_TERMINATED = "run_terminated"
    RUN_NOT_FOUND = "run_not_found"


@dataclass(slots=True)
class CommandResult:
    status: str
    applied_tick: int = 0
    produced_seq: list[int] = field(default_factory=list)
    reason: str = ""

    @property
    def mutated(self) -> bool:
        """Whether anything changed. An empty produced list is what "mutates nothing" means."""
        return bool(self.produced_seq)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "applied_tick": self.applied_tick,
            "produced_seq": list(self.produced_seq),
            "reason": self.reason,
        }


class KernelClient(Protocol):
    """What the gateway needs from the kernel.

    A protocol rather than a concrete class because there are two real implementations, and
    both are production paths: gRPC across the compose network, and in-process for the
    documented single-process mode (R16), which composes the same application objects rather
    than reimplementing them. Tests use the in-process one for the same reason a contributor
    does — it is the same code with a different topology.
    """

    def submit(self, run_id: str, kind: str, payload: dict[str, Any], command_id: str) -> CommandResult:
        ...

    def create_run(
        self,
        run_id: str,
        run_seed: int,
        horizon_tick: int | None = None,
        scenario: str | None = None,
    ) -> dict[str, Any]:
        """Create a run and start its clock. Deliberately not a command.

        A command never creates a run — `submit` answers not-found for an unknown one, on purpose,
        so that a typo'd id cannot conjure a simulation. Creation is therefore its own verb rather
        than a kind, which is also why it does not carry an idempotency key: the run id *is* the
        key, and creating one that exists returns the existing run.

        `scenario` is the *name* of a company, never a path, and `None` means the shipped one.
        A name the loader will not resolve arrives back as a `ValueError` carrying the loader's
        own sentence — which is how the gateway answers 400 with the list of names that do
        resolve without importing the loader to recognise its exception.
        """
        ...

    def scenarios(self) -> list[dict[str, Any]]:
        """The companies a run could be created against, for the surface that offers the choice.

        On the protocol rather than read off the filesystem by the gateway, for the same reason
        every other read is: the gateway holds no simulation knowledge, and "which companies
        exist" is simulation knowledge that happens to be answerable from a directory listing.
        """
        ...

    def run_status(self, run_id: str) -> dict[str, Any] | None:
        ...

    def read_events(self, run_id: str, after_seq: int, limit: int | None = None) -> list:
        ...

    def head_seq(self, run_id: str) -> int:
        ...

    def snapshot(self, run_id: str) -> dict[str, Any]:
        ...

    def subscribe(self, run_id: str, connection: Any) -> Any:
        """Attach a subscriber. Read-only, with no effect on simulation state (R18)."""
        ...

    def unsubscribe(self, run_id: str, token: Any) -> None:
        ...


class CommandLedger:
    """Remembers each idempotency key's outcome, per run.

    Keyed on run *and* key: two runs may legitimately use the same key, and a fork shares its
    parent's history without sharing its future.
    """

    def __init__(self) -> None:
        self._outcomes: dict[tuple[str, str], CommandResult] = {}

    def recorded(self, run_id: str, key: str) -> CommandResult | None:
        return self._outcomes.get((run_id, key))

    def record(self, run_id: str, key: str, result: CommandResult) -> None:
        self._outcomes[(run_id, key)] = result

    def __len__(self) -> int:
        return len(self._outcomes)


def submit(
    kernel: KernelClient,
    ledger: CommandLedger,
    run_id: str,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str,
    command_id: str,
) -> CommandResult:
    """Apply a command once, whatever the client does.

    The order here is the contract: check the ledger first, so a retry never reaches the kernel
    twice; check the run's state second, so a paused or ended run is answered rather than
    queued; only then apply.
    """
    if not idempotency_key:
        return CommandResult(
            status=Outcome.REJECTED,
            reason=(
                "an idempotency key is required. A command whose response you may never see "
                "has to be retryable, and a retry without a key would apply twice."
            ),
        )

    previously = ledger.recorded(run_id, idempotency_key)
    if previously is not None:
        # The original outcome, not a fresh one. That is the whole point of the key.
        return CommandResult(
            status=Outcome.DUPLICATE,
            applied_tick=previously.applied_tick,
            produced_seq=list(previously.produced_seq),
            reason="already applied under this idempotency key",
        )

    status = kernel.run_status(run_id)
    if status is None:
        return CommandResult(
            status=Outcome.RUN_NOT_FOUND,
            reason=f"no run {run_id}. A command never creates one.",
        )

    if status.get("terminal_reason"):
        return CommandResult(
            status=Outcome.RUN_TERMINATED,
            reason=(
                f"the run ended ({status['terminal_reason']}); nothing appends after a terminal "
                "event"
            ),
        )

    if status.get("rate", 0) == 0 and kind not in NEEDS_NO_TICK_BOUNDARY:
        # Stated explicitly rather than waiting for a boundary that will not arrive. `set_rate`
        # is exempt: it is what lifts the pause, so guarding it would strand the run here.
        return CommandResult(
            status=Outcome.RUN_PAUSED,
            reason=(
                "the run is paused, so there is no tick boundary to apply this at. Submit "
                "set_rate with a non-zero rate to resume, then send this again — the command "
                "was not queued, so nothing will happen unexpectedly later."
            ),
        )

    result = kernel.submit(run_id, kind, payload, command_id)
    ledger.record(run_id, idempotency_key, result)

    log.info(
        "command applied" if result.mutated else "command rejected",
        extra={
            "run": run_id,
            "command_id": command_id,
            "kind": kind,
            "status": result.status,
            "produced": len(result.produced_seq),
        },
    )
    return result
