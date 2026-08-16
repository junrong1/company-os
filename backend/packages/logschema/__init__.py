"""The log's table definitions, shared by the one writer and the read-side.

This lives in `packages/` rather than in `services/kernel/` because both the kernel and the
report service legitimately depend on it, and R4 forbids a service from importing another
service's internals. The distinction that matters is between the *schema* and the *writer*:

* the schema is a contract — what a row looks like, and which table refuses mutation;
* the write path (`kernel.store`, `kernel.lease`) belongs to the kernel alone, because the
  kernel is the only component that may append (R1).

So the report service reads rows described here with a read-only credential, and has no way
to reach the code that appends them. Discovered by the import-boundary test, which failed the
moment the report service tried to import `kernel.store` to read the log.
"""

from logschema.tables import (
    APPEND_ONLY_TABLES,
    DDL_VERSION,
    MUTABLE_TABLES,
    append_only_ddl,
    event_log,
    is_append_only_refusal,
    metadata,
    model_spend,
    runs,
    snapshots,
    store_version,
    writer_lease,
)

__all__ = [
    "APPEND_ONLY_TABLES",
    "DDL_VERSION",
    "MUTABLE_TABLES",
    "append_only_ddl",
    "event_log",
    "is_append_only_refusal",
    "metadata",
    "model_spend",
    "runs",
    "snapshots",
    "store_version",
    "writer_lease",
]
