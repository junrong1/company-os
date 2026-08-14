"""The generated stubs, and the seam between the proto files and the Python contract.

Two things are checked that a compile step alone would not catch: that the Python
EventKind enum has not drifted from the proto one (they are written twice, so they
can disagree), and that importing the stubs pulls in no service module.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from contracts.envelope import EventKind

BACKEND = Path(__file__).resolve().parent.parent
GENERATED = BACKEND / "packages" / "contracts" / "grpc"

pytestmark = pytest.mark.skipif(
    not (GENERATED / "events_pb2.py").exists(),
    reason="stubs not generated; run `uv run python scripts/generate_protos.py`",
)


def _proto_event_kinds() -> dict[str, int]:
    from contracts.grpc import events_pb2

    enum = events_pb2.DESCRIPTOR.enum_types_by_name["EventKind"]
    return {value.name: value.number for value in enum.values}


def _python_event_kinds() -> dict[str, int]:
    # The proto spells the zero value EVENT_KIND_UNSPECIFIED; the Python enum
    # spells it UNSPECIFIED, since it is already inside an EventKind namespace.
    return {
        ("EVENT_KIND_UNSPECIFIED" if kind.name == "UNSPECIFIED" else kind.name): int(kind)
        for kind in EventKind
    }


# --- proto <-> python parity ----------------------------------------------


def test_event_kinds_match_between_proto_and_python() -> None:
    """The enum is written twice, so it can drift. This is the only thing stopping it.

    A drifted number is the worst case: an event written as one kind and read as
    another folds to a state no step produced, and nothing about the failure points
    at the enum.
    """
    proto = _proto_event_kinds()
    python = _python_event_kinds()

    assert proto == python, (
        f"only in proto: {sorted(set(proto) - set(python))}; "
        f"only in python: {sorted(set(python) - set(proto))}; "
        f"number mismatches: "
        f"{ {k: (proto[k], python[k]) for k in set(proto) & set(python) if proto[k] != python[k]} }"
    )


def test_envelope_fields_match_between_proto_and_python() -> None:
    import dataclasses

    from contracts.envelope import Envelope
    from contracts.grpc import events_pb2

    proto_fields = {field.name for field in events_pb2.Envelope.DESCRIPTOR.fields}
    python_fields = {field.name for field in dataclasses.fields(Envelope)}

    assert proto_fields == python_fields


def test_metadata_fields_are_numbered_separately_in_the_proto() -> None:
    """Metadata sits at field 100+ so the grouping is visible on the wire too."""
    from contracts.envelope import HASHED_FIELDS
    from contracts.grpc import events_pb2

    for field in events_pb2.Envelope.DESCRIPTOR.fields:
        if field.name in HASHED_FIELDS:
            assert field.number < 100, f"{field.name} is hashed but numbered {field.number}"
        else:
            assert field.number >= 100, f"{field.name} is metadata but numbered {field.number}"


# --- the service surfaces the plan names ----------------------------------


def test_the_kernel_surface_carries_the_named_methods() -> None:
    from contracts.grpc import kernel_pb2

    methods = {
        method.name for method in kernel_pb2.DESCRIPTOR.services_by_name["Kernel"].methods
    }

    # submit-command, subscribe-to-events, fold, replay, fork, plus the two the
    # plan requires elsewhere: an outcome-by-key query (R30) and diagnose (U9).
    assert {
        "SubmitCommand",
        "SubscribeEvents",
        "Fold",
        "Replay",
        "Fork",
        "CommandOutcomeByKey",
        "Diagnose",
        "ExportRun",
    } <= methods


def test_the_fold_takes_live_head_as_a_parameter() -> None:
    """R3: whether a fold is at the live head is a parameter, not an implicit property.

    Re-issuing an unanswered external request is permitted only at the live head —
    never during historical replay, fork reconstruction or a report fold. Making it
    a field means a caller cannot forget to say which it is.
    """
    from contracts.grpc import kernel_pb2

    fields = {field.name for field in kernel_pb2.FoldRequest.DESCRIPTOR.fields}
    assert "at_live_head" in fields


def test_the_answer_streams_are_bidirectional_and_kernel_initiated() -> None:
    """R17: the kernel opens the stream; no service holds the kernel's address.

    In gRPC the client opens the stream, so the kernel being the client is what
    makes this one-directional. A bidirectional stream also turns a connection drop
    into a failure signal rather than a timeout: every request outstanding on it is
    known-unanswered.
    """
    from contracts.grpc import agents_pb2, domain_pb2

    consult = domain_pb2.DESCRIPTOR.services_by_name["DomainModel"].methods_by_name["Consult"]
    assert consult.client_streaming and consult.server_streaming

    resolve = agents_pb2.DESCRIPTOR.services_by_name["AgentResolver"].methods_by_name["Resolve"]
    assert resolve.client_streaming and resolve.server_streaming


def test_no_service_definition_lives_on_the_kernel_for_inbound_answers() -> None:
    """There must be no inbound delivery endpoint on the kernel.

    One would create a dependency cycle and an externally reachable write entrance
    to a permanent log.
    """
    from contracts.grpc import kernel_pb2

    methods = {
        method.name.lower()
        for method in kernel_pb2.DESCRIPTOR.services_by_name["Kernel"].methods
    }
    for forbidden in ("deliveranswer", "submitanswer", "answer", "receiveanswer"):
        assert forbidden not in methods


def test_the_resolution_contract_is_producer_agnostic() -> None:
    """Origin R37: a resolution carries a resolver identity and never assumes human origin.

    This is what lets Phase 2 add an LLM resolver with no change to the advancement
    contract.
    """
    from contracts.grpc import agents_pb2

    kinds = {
        value.name
        for value in agents_pb2.ResolverIdentity.DESCRIPTOR.enum_types_by_name["Kind"].values
    }
    assert {"HUMAN_IN_PERSON", "HUMAN_FROM_TRAY", "AGENT", "STUB"} <= kinds


# --- import hygiene -------------------------------------------------------


def test_importing_the_pure_contract_does_not_pull_in_grpc() -> None:
    """R5 depends on this.

    `simcore` imports `contracts.envelope`. If importing `contracts` pulled in the
    generated stubs, the kernel library would acquire a transport dependency
    transitively and R5 would be broken without any simcore file mentioning grpc.
    """
    probe = """
import sys
import contracts  # noqa: F401
from contracts import canonical, envelope  # noqa: F401
roots = {name.split(".")[0] for name in sys.modules}
print("grpc" if "grpc" in roots else "")
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=BACKEND,
        env={"PYTHONPATH": "packages:services", "PATH": ""},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", "importing contracts pulled in grpc; R5 is at risk"


def test_generated_stubs_import_without_pulling_any_service() -> None:
    """The stubs are the meeting point; they must not know about implementations."""
    probe = """
import sys
from contracts.grpc import agents_pb2, domain_pb2, events_pb2, kernel_pb2  # noqa: F401
from contracts.grpc import agents_pb2_grpc, domain_pb2_grpc, kernel_pb2_grpc  # noqa: F401
services = {"kernel", "gateway", "domain", "agents", "report"}
print(",".join(sorted(services & {name.split(".")[0] for name in sys.modules})))
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=BACKEND,
        env={"PYTHONPATH": "packages:services", "PATH": ""},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", f"stubs pulled in service modules: {result.stdout.strip()}"


@pytest.mark.parametrize("service", ["kernel", "gateway", "domain", "agents", "report"])
def test_every_service_can_import_the_generated_contracts(service: str) -> None:
    """U2's verification: every service compiles against the generated contracts."""
    probe = f"""
import {service}.main  # noqa: F401
from contracts.grpc import kernel_pb2, kernel_pb2_grpc  # noqa: F401
from contracts.envelope import Envelope, EventKind  # noqa: F401
print("ok")
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=BACKEND,
        env={
            "PYTHONPATH": "packages:services",
            "PATH": "",
            "COMPANY_OS_STORE_URL": "sqlite:///./var/test.sqlite3",
        },
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
