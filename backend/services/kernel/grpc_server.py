"""The kernel's gRPC surface: the gateway's only way in.

Thin by design. Every method translates a request, delegates to `KernelRuntime`, and translates
the result back. No decision lives here — putting one here would put it outside the thing that
owns the clock and the log.

Note the direction of everything in this file: callers come *to* the kernel. The kernel's own
outbound calls, to the domain and agent services, are streams it opens itself (R17), which is
why there is no inbound delivery endpoint for answers here. One would create a dependency cycle
and an externally reachable write entrance to a permanent log.
"""

from __future__ import annotations

from typing import Any

from contracts.grpc import kernel_pb2, kernel_pb2_grpc
from kernel.loop import KernelRuntime
from servicekit import logging as svclog
from simcore import hashing
from simcore import log as folder
from simcore import step as sim

log = svclog.get_logger("kernel")


class KernelServicer(kernel_pb2_grpc.KernelServicer):
    """Implements the Kernel service defined in proto/kernel.proto."""

    def __init__(self, runtime: KernelRuntime) -> None:
        self.runtime = runtime

    # --- commands ---------------------------------------------------------

    def SubmitCommand(self, request: Any, context: Any) -> Any:  # noqa: N802 - gRPC naming
        """Enqueued for a tick boundary; the response reports the outcome.

        Validating and applying in separate steps would leave a race where a command validates
        against a state the tick then changes, so the two happen together.
        """
        outcome = kernel_pb2.CommandOutcome()

        run = self.runtime.runs.get(request.run_id)
        if run is None:
            outcome.status = kernel_pb2.CommandOutcome.RUN_NOT_FOUND
            outcome.reason = f"no run {request.run_id}"
            return outcome

        if run.state.terminal_reason:
            outcome.status = kernel_pb2.CommandOutcome.RUN_TERMINATED
            outcome.reason = f"the run ended: {run.state.terminal_reason}"
            return outcome

        try:
            envelopes = self.runtime.apply_command(
                request.run_id, request.kind, request.payload, request.command_id
            )
        except sim.CommandRejected as rejected:
            outcome.status = kernel_pb2.CommandOutcome.REJECTED
            outcome.reason = str(rejected)
            return outcome

        outcome.status = kernel_pb2.CommandOutcome.APPLIED
        outcome.applied_tick = run.state.tick
        outcome.produced_seq.extend(envelope.seq for envelope in envelopes)
        return outcome

    def CommandOutcomeByKey(self, request: Any, context: Any) -> Any:  # noqa: N802
        """R30: a reconnecting client resolves an in-flight command instead of retrying blind."""
        outcome = kernel_pb2.CommandOutcome()
        recorded = self.runtime.outcome_by_key(request.run_id, request.idempotency_key)

        if recorded is None:
            outcome.status = kernel_pb2.CommandOutcome.STATUS_UNSPECIFIED
            outcome.reason = "no command recorded under that key"
            return outcome

        outcome.status = kernel_pb2.CommandOutcome.DUPLICATE
        outcome.applied_tick = recorded["applied_tick"]
        outcome.produced_seq.extend(recorded["produced_seq"])
        return outcome

    # --- reads ------------------------------------------------------------

    def SubscribeEvents(self, request: Any, context: Any) -> Any:  # noqa: N802
        """Read-only, and with no effect on simulation state (R18)."""
        raise NotImplementedError(
            "the streaming subscription is served by the gateway's WebSocket at U10; the "
            "kernel-side stream is wired there rather than duplicated here"
        )

    def Fold(self, request: Any, context: Any) -> Any:  # noqa: N802
        """Fold a log prefix. `at_live_head` is a parameter, never inferred (R3)."""
        events = self.runtime.store.read_events(
            request.run_id, through_seq=request.through_seq or None
        )
        row = self.runtime.store.run_row(request.run_id)

        result = folder.fold(
            events,
            at_live_head=request.at_live_head,
            through_tick=int(row["current_tick"]) if row else None,
        )
        hashed = hashing.state_hash(sim.snapshot(result.state))

        response = kernel_pb2.FoldResponse()
        response.through_seq = result.through_seq
        response.tick = result.state.tick
        response.rules_ver = events[0].rules_ver if events else ""
        response.state_hash = hashed.overall
        response.state_shape_ver = hashed.shape_version
        for name, digest in hashed.subsystems.items():
            response.subsystem_hashes[name] = digest
        return response

    def Replay(self, request: Any, context: Any) -> Any:  # noqa: N802
        """Historical replay: reads logged answers, never re-issues (R3)."""
        response = kernel_pb2.ReplayResponse()
        events = self.runtime.store.read_events(
            request.run_id, through_seq=request.through_seq or None
        )
        row = self.runtime.store.run_row(request.run_id)

        try:
            state = folder.replay_to_state(
                events, through_tick=int(row["current_tick"]) if row else None
            )
        except folder.FoldRefused as refusal:
            response.matched = False
            response.refusal = str(refusal)
            return response
        except folder.ReplayDiverged as diverged:
            response.matched = False
            response.refusal = str(diverged)
            return response

        response.matched = True
        response.state_hash = hashing.state_hash(sim.snapshot(state)).overall
        return response

    def Fork(self, request: Any, context: Any) -> Any:  # noqa: N802
        """Committed inside the lease-fenced transaction, and refused above the size bound."""
        response = kernel_pb2.ForkResponse()
        result = self.runtime.fork(request.parent_run_id, request.at_seq)

        if not result.forked:
            response.refusal = result.refusal
            return response

        response.child_run_id = result.child_run_id
        response.copied_through_seq = result.copied_through_seq
        return response

    def ExportRun(self, request: Any, context: Any) -> Any:  # noqa: N802
        """R32: the run leaves the machine as a self-contained artifact."""
        from contracts.envelope import EVENT_SCHEMA_VERSION
        from simcore import export as exporter
        from simcore.rates import RULES_VERSION

        artifact, state_hash = self.runtime.export(request.run_id)

        response = kernel_pb2.ExportResponse()
        response.artifact = artifact
        response.state_hash = state_hash
        response.rules_ver = RULES_VERSION
        response.envelope_schema_ver = EVENT_SCHEMA_VERSION
        return response

    def Diagnose(self, request: Any, context: Any) -> Any:  # noqa: N802
        """Why did the clock stop? The highest-leverage call in the system."""
        diagnosis = self.runtime.diagnose(request.run_id)

        response = kernel_pb2.DiagnoseResponse()
        response.rate = diagnosis.rate
        response.rate_effective_tick = diagnosis.rate_effective_tick
        response.subscribers = diagnosis.subscribers
        response.tick_task_state = diagnosis.tick_task_state
        response.tick_task_exception = diagnosis.tick_task_exception
        response.last_wake_at = diagnosis.last_wake_at
        response.sim_time_lag_ticks = diagnosis.sim_time_lag_ticks
        # The proto carries a double here; it is derived for display only and never enters
        # kernel state, so it cannot reach the log or the state hash.
        response.achieved_multiplier = diagnosis.achieved_multiplier_permille / 1000
        response.unresolved_checkpoints.extend(diagnosis.unresolved_checkpoints)
        response.store_reachable = diagnosis.store_reachable
        response.lease_held = diagnosis.lease_held
        return response
