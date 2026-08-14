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


@dataclass(frozen=True)
class StoreTarget:
    """Where the log store lives, as the status payload reports it."""

    backend: str  # "postgres" | "sqlite" | "unknown"
    url: str
    host: str | None = None
    port: int | None = None
    path: str | None = None

    def describe(self) -> str:
        if self.backend == "postgres":
            return f"{self.host}:{self.port}"
        if self.backend == "sqlite":
            return str(self.path)
        return self.url


def store_url() -> str:
    return os.environ.get("COMPANY_OS_STORE_URL", DEFAULT_STORE_URL)


def parse_store_url(url: str) -> StoreTarget:
    parts = urlsplit(url)
    scheme = parts.scheme.split("+", 1)[0].lower()

    if scheme in {"postgres", "postgresql"}:
        return StoreTarget(
            backend="postgres",
            url=url,
            host=parts.hostname or "localhost",
            port=parts.port or 5432,
        )

    if scheme == "sqlite":
        # sqlite:///relative/path and sqlite:////absolute/path both occur.
        raw = parts.path
        path = raw[1:] if raw.startswith("/") and not raw.startswith("//") else raw
        return StoreTarget(backend="sqlite", url=url, path=path or ":memory:")

    return StoreTarget(backend="unknown", url=url)


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

    return False, f"unrecognised store url scheme: {target.url!r}"


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
