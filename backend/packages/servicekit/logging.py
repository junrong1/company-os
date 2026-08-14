"""Single-line JSON logging to stdout, shared by every service.

`docker compose logs -f` is the aggregator. Naming that as the answer is
deliberate: one machine, one operator, no log pipeline. What makes it work at all
is the causation id — a command id greppable across seven streams — so the
formatter treats run, tick, sequence and command id as first-class fields rather
than as message text.

Tick-level logging is off unless asked for. At the prototype's clock constants a
run emits 36 tick records per wall second at x1 and 108 at x3, which drowns
everything else in the stream.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

# Fields the formatter lifts out of `extra=` and puts at the top level of the
# record. Anything else passed via `extra=` lands under "detail".
_CAUSATION_FIELDS = ("run", "tick", "seq", "command_id", "request_id")

_RESERVED = frozenset(
    """args asctime created exc_info exc_text filename funcName levelname levelno
    lineno module msecs message msg name pathname process processName relativeCreated
    stack_info thread threadName taskName""".split()
)


def ticks_enabled() -> bool:
    """Whether per-tick records are emitted. Off by default; see module docstring."""
    return os.environ.get("COMPANY_OS_LOG_TICKS", "").lower() in {"1", "true", "yes"}


class JsonFormatter(logging.Formatter):
    """Formats one record as one line of JSON, key order stable for grepping."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            # Built rather than handed to formatTime: strftime has no
            # milliseconds directive, and its output is local time, which
            # labelled "Z" would be a timestamp that lies about its timezone.
            "ts": datetime.fromtimestamp(record.created, UTC).strftime("%Y-%m-%dT%H:%M:%S.")
            + f"{int(record.msecs):03d}Z",
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for field in _CAUSATION_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value

        detail = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED and key not in _CAUSATION_FIELDS and not key.startswith("_")
        }
        if detail:
            payload["detail"] = _safe(detail)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, separators=(",", ":"))


def _safe(value: Any) -> Any:
    """Make a value JSON-safe without letting a logging call raise."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


def configure(service: str, level: str | None = None) -> None:
    """Install the JSON formatter on the root logger. Idempotent.

    Uvicorn installs its own handlers; replacing the root handler set rather than
    adding to it is what keeps one event from being logged twice in two formats.
    """
    resolved = (level or os.environ.get("COMPANY_OS_LOG_LEVEL") or "INFO").upper()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(resolved)

    # Uvicorn's access log duplicates what the status probe already reports, and
    # the healthcheck hits /status on an interval. Keep it at WARNING. httpx logs
    # a line per request, which would mean one record per reachability probe —
    # the gateway probes the kernel on every status call.
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    for inherited in ("uvicorn", "uvicorn.error"):
        logging.getLogger(inherited).handlers.clear()
        logging.getLogger(inherited).propagate = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
