"""Reachability probes behind the status endpoint's per-dependency report.

These are deliberately shallow. A TCP connect proves a port is open, not that
credentials work or that the store's DDL version matches — that check arrives
with the store in U6 and replaces the store probe here. The detail strings say
which of the two a given answer is, because "store: reachable" meaning two
different things at two different units is how a status endpoint stops being
worth pasting into an issue.
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_STORE_URL = "sqlite:///./var/company-os.sqlite3"
_PROBE_TIMEOUT_SECONDS = 1.0

#: The writer's DSN. The kernel's, and the default for anything that does not ask
#: for another.
ENV_STORE_URL = "COMPANY_OS_STORE_URL"

#: The report's DSN: the same store reached through the role `infra/postgres/init`
#: provisions with SELECT and nothing else. A separate variable because a SQLAlchemy
#: engine is per-DSN rather than per-process, which is what keeps the read-only role
#: a real boundary now that the report is mounted in the process that also appends.
ENV_REPORT_STORE_URL = "COMPANY_OS_REPORT_STORE_URL"


@dataclass(frozen=True)
class StoreTarget:
    """Where the log store lives, as the status payload reports it.

    **It does not carry the URL.** It used to, and `describe()` returned it verbatim
    for a scheme it did not recognise — so a typo in the driver name published the
    password on an endpoint whose whole purpose is to be pasted into an issue (R6).
    Holding only the parsed parts is what makes that unreachable rather than
    remembered: there is no field here a future reporter could put on the wire.
    """

    backend: str  # "postgres" | "sqlite" | "unknown"
    #: The scheme as written, driver suffix stripped. Enough to name what was
    #: misconfigured, and it carries no credential.
    scheme: str = ""
    host: str | None = None
    port: int | None = None
    path: str | None = None

    def describe(self) -> str:
        if self.backend == "postgres":
            return f"{self.host}:{self.port}"
        if self.backend == "sqlite":
            return str(self.path)
        return f"{self.scheme or 'none'} (unrecognised scheme)"


def store_url(env_var: str = ENV_STORE_URL) -> str:
    """Where a component's store is, named by the variable that says so.

    One unnamed variable was enough while every component was its own process: each
    had its own environment, so "the store URL" meant a different string in the report
    container than in the kernel's. In one process it means one string, and the
    report's read-only credential would quietly become the writer's — the only
    boundary that survives U20 through U22 being written into the process that also
    appends.

    A named variable **falls back to the writer's** rather than refusing. On a laptop
    the store is a SQLite file with no roles to separate, so demanding a second DSN
    there would turn a supported path into a configuration error for no gain.
    """
    value = (os.environ.get(env_var) or "").strip()
    if value:
        return value
    if env_var != ENV_STORE_URL:
        return store_url()
    return DEFAULT_STORE_URL


def parse_store_url(url: str) -> StoreTarget:
    """Split a DSN into the parts a status payload may report.

    Every parse failure lands on `unknown`, and none of them raises. `urlsplit` rejects
    an unbalanced `[` outright and raises again on `.port` when the port is not a number
    — and `ServiceStatus.build` calls this *outside* the guard it wraps its probes in, so
    a malformed DSN raising here would be a 500 on the endpoint whose job is to say what
    is wrong.
    """
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.split("+", 1)[0].lower()
    except ValueError:
        return StoreTarget(backend="unknown")

    if scheme in {"postgres", "postgresql"}:
        try:
            # Read through `urlsplit` rather than off the string, so a password
            # containing an `@` does not become part of the reported host.
            host, port = parts.hostname or "localhost", parts.port or 5432
        except ValueError:
            return StoreTarget(backend="unknown", scheme=scheme)
        return StoreTarget(backend="postgres", scheme=scheme, host=host, port=port)

    if scheme == "sqlite":
        # sqlite:///relative/path and sqlite:////absolute/path both occur.
        raw = parts.path
        path = raw[1:] if raw.startswith("/") and not raw.startswith("//") else raw
        return StoreTarget(backend="sqlite", scheme=scheme, path=path or ":memory:")

    return StoreTarget(backend="unknown", scheme=scheme)


def probe_store(url: str | None = None) -> tuple[bool, str]:
    """Is the store reachable? Returns (reachable, detail)."""
    target = parse_store_url(url or store_url())

    if target.backend == "postgres":
        try:
            with socket.create_connection(
                (target.host, target.port), timeout=_PROBE_TIMEOUT_SECONDS
            ):
                return True, f"tcp reachable at {target.describe()}; the DDL version is under `versions`"
        except OSError as exc:
            return False, f"tcp connect to {target.describe()} failed: {exc}"

    if target.backend == "sqlite":
        path = Path(target.path or "")
        if str(path) == ":memory:":
            return True, "in-memory"
        parent = path.parent if str(path.parent) else Path(".")
        if path.exists():
            return True, f"file present at {path}; the DDL version is under `versions`"
        if parent.exists() and os.access(parent, os.W_OK):
            return True, f"{parent} writable; database not created yet"
        return False, f"{parent} missing or not writable"

    # The scheme, never the URL. This branch returned the whole DSN, so a typo in the
    # driver name — `postgres+psycopg2` for `postgresql+psycopg`, the commonest one
    # there is — published the password on the status endpoint (R6).
    return False, (
        f"unrecognised store url scheme {target.scheme or '(none)'!r}: expected a "
        f"postgresql:// or sqlite:// url. Check {ENV_STORE_URL}."
    )


def probe_http(url: str) -> tuple[bool, str]:
    """GET a peer's status endpoint. Reports the peer's own verdict, not just 200."""
    import httpx

    try:
        response = httpx.get(url, timeout=_PROBE_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - any transport failure is unreachable
        return False, f"{url} unreachable: {type(exc).__name__}: {exc}"

    if response.status_code == 200:
        return True, f"{url} healthy"

    # A peer answering 503 is reachable but unhealthy. That distinction is the
    # whole point of the gateway being able to say "the kernel is up but sick".
    return False, f"{url} answered {response.status_code}"
