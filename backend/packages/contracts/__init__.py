"""Inter-service contracts: the event envelope and the generated gRPC stubs.

Every inter-service boundary is defined here (R4) so that no service imports
another service's internals.

**This module stays free of gRPC.** `simcore` imports the envelope, and R5 forbids
the kernel library from acquiring a transport dependency — so the pure contract
(`contracts.envelope`, `contracts.canonical`) is importable on its own, and the
generated stubs are quarantined in `contracts.grpc`, which only the services import.
The import-boundary suite asserts this: importing `simcore` must not pull `grpc` into
`sys.modules`.
"""

from contracts.canonical import NotCanonical
from contracts.envelope import (
    EVENT_SCHEMA_VERSION,
    HASHED_FIELDS,
    KIND_SCHEMA_VERSIONS,
    METADATA_FIELDS,
    Envelope,
    EnvelopeInvalid,
    EventKind,
    UnknownEventKind,
    build,
    canonical_log_projection,
    request_id_for,
)

__all__ = [
    "EVENT_SCHEMA_VERSION",
    "HASHED_FIELDS",
    "KIND_SCHEMA_VERSIONS",
    "METADATA_FIELDS",
    "Envelope",
    "EnvelopeInvalid",
    "EventKind",
    "NotCanonical",
    "UnknownEventKind",
    "build",
    "canonical_log_projection",
    "request_id_for",
]
