"""Single-line JSON logging to stdout, shared by every service.

`docker compose logs -f` is the aggregator. Naming that as the answer is
deliberate: one machine, one operator, no log pipeline. What makes it work at all
is the causation id — a command id greppable across seven streams — so the
formatter treats run, tick, sequence and command id as first-class fields rather
than as message text.

Tick-level logging is off unless asked for. At the prototype's clock constants a
run emits 36 tick records per wall second at x1 and 108 at x3, which drowns
everything else in the stream.

**Every record goes through a redacting filter on its way out (R6).** R6 says
"enforced by a masked key type and a redacting log filter, not by convention", and
the two halves cover different call sites. `modelgw.SecretKey` masks the *configured*
key wherever it is rendered, and `modelgw.scrub` cleans a provider's own words before
they leave that package. Neither can reach the idiom the rest of the repo uses —
`log.warning(..., extra={"error": str(exc)})` — where the exception has quoted a 401
body, or a driver has quoted the DSN it failed to connect with. This filter is what
holds there, at call sites no provider-aware unit ever sees.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import traceback
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

# =========================================================================
# Redaction (R6)
# =========================================================================

#: What a redacted span renders as. Deliberately the same three characters
#: `modelgw.config.MASK` uses, so one grep finds every redaction in the stream
#: whichever half produced it.
MASK = "***"

#: A URL wherever one appears in text, bounded by whitespace and the quoting
#: characters JSON and shell messages use. Matching the URL rather than hunting for
#: a password is what makes this hold for a DSN nobody anticipated: the shape is
#: what leaks, and the shape is what is recognised.
_URL = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s\"'<>|\\]+")

#: Punctuation that ends a sentence rather than a URL. Trimmed off a match so
#: "connecting to postgresql://host/db." keeps its full stop.
_SENTENCE_TAIL = ".,;:!?)]}"

#: Secret-shaped spans that are not inside a URL. The first three are the same
#: shapes `modelgw.config._LEAK_PATTERNS` catches — a provider echoing our own auth
#: header back inside an error body — and the pair is pinned against each other by a
#: test rather than kept in step by hand.
#:
#: They are not *imported* from there, and the reason is the import graph rather
#: than taste: `modelgw` pulls in `httpx`, `servicekit.probes` imports httpx lazily
#: inside `probe_http` precisely to keep it off the startup path, and this module is
#: imported by every service before it has done anything. A redaction table is not
#: worth making the logging installer depend on the provider package.
_SECRETS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-~+/=]+", re.IGNORECASE), f"Bearer {MASK}"),
    (
        re.compile(
            r"(?i)\b(x-api-key|api[-_]?key|apikey|authorization|password|passwd|pwd"
            r"|secret|token)\b(\"?\s*[:=]\s*\"?)([^\s\"',}&]+)"
        ),
        rf"\1\2{MASK}",
    ),
    (re.compile(r"\bsk-[A-Za-z0-9._\-]{6,}"), MASK),
)


#: Where a URL's authority ends. Whichever comes first.
_AUTHORITY_END = "/?#"


def _redact_one_url(match: re.Match[str]) -> str:
    """Strip a URL's userinfo and its query, keeping enough to identify the store.

    Two removals, and each has its own reason.

    **Userinfo goes whole, username included.** `scheme://user:pass@host` is the shape
    the compose DSN has, and half of it is a password. Keeping the username would buy
    an operator nothing they cannot read out of the environment, and would leave the
    sweep that guards this asking whether a given userinfo segment happens to contain
    a secret rather than whether one is present at all.

    **The query is dropped rather than masked.** A DSN's query carries `sslmode`,
    `options` and — on more than one hosted Postgres — the password itself. Masking it
    would leave a `?` behind, and then "does this line carry a query string" stops
    being a question with a yes-or-no answer.

    **Split by hand rather than with `urlsplit`, because `urlsplit` raises.** It rejects
    an unbalanced `[` with `ValueError("Invalid IPv6 URL")`, and the first thing that
    reached this filter with one was uvicorn's own startup line: its `color_message`
    field is `http://\\x1b[1m%s\\x1b[0m:...`, an ANSI escape immediately after the
    scheme. A `ValueError` raised inside a `logging.Filter` propagates out of the
    `logger.info` call that triggered it, so that took the container down at startup —
    found by running it, because nothing in the suite ever lets uvicorn print that line.
    Nothing below can raise on any input.
    """
    raw = match.group(0)
    tail = ""
    while raw and raw[-1] in _SENTENCE_TAIL:
        tail = raw[-1] + tail
        raw = raw[:-1]

    scheme, separator, rest = raw.partition("://")
    if not separator:
        return raw + tail

    ends = [rest.find(char) for char in _AUTHORITY_END]
    cut = min((where for where in ends if where != -1), default=len(rest))
    authority, remainder = rest[:cut], rest[cut:]

    _userinfo, at, host = authority.rpartition("@")
    if at:
        authority = f"{MASK}@{host}"

    # Everything from the first `?` or `#` goes with the query.
    for char in "?#":
        remainder = remainder.split(char, 1)[0]

    return f"{scheme}://{authority}{remainder}{tail}"


def redact_text(text: str) -> str:
    """Remove anything secret-shaped from one string.

    URLs first, then the loose patterns: a DSN's `?password=` is gone with the query,
    so the pair does not have to agree about which of them owns it.
    """
    cleaned = _URL.sub(_redact_one_url, text)
    for pattern, replacement in _SECRETS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def redact(value: Any) -> Any:
    """`redact_text` over a value of any shape, leaving non-strings alone.

    Recursive, because the repo's `extra=` idiom passes dicts and lists as often as it
    passes strings, and a secret one level down is still in the stream.

    A sequence comes back as a `list` rather than as its own type. Rebuilding the type
    would raise on a `namedtuple`, whose constructor takes positional fields rather than
    an iterable — and a `TypeError` here costs the whole record. JSON has no tuples, so
    nothing downstream can tell.
    """
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


class Redaction(logging.Filter):
    """Redacts a record in place, before any formatter sees it.

    A filter on the handler rather than a step inside `JsonFormatter`, so it holds for
    whatever renders the record — and so the redaction is testable on a record without
    a formatter. It is installed by `configure`, which owns the only handler.

    **`args` is collapsed into the message first.** A record logged as
    `("connecting to %s", dsn)` keeps the secret in `args`, where redacting the message
    alone would miss it — and redacting the two separately would miss a secret that
    straddles the substitution. Rendering once, here, is the only shape with no seam:
    the message is interpolated exactly once and this module owns the one place it
    happens.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            self._redact(record)
        except Exception:  # noqa: BLE001 - see _drop
            self._drop(record)
        return True

    @staticmethod
    def _redact(record: logging.LogRecord) -> None:
        if record.args:
            record.msg = record.getMessage()
            record.args = ()

        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)

        for key, value in list(record.__dict__.items()):
            if key in _RESERVED or key.startswith("_"):
                continue
            record.__dict__[key] = redact(value)

        if record.exc_info and not record.exc_text:
            # Rendered here rather than left to the formatter, because a traceback's
            # frames quote the arguments a driver was called with — which is where a
            # DSN reaches stdout without any call site having logged one.
            record.exc_text = redact_text(
                "".join(traceback.format_exception(*record.exc_info)).rstrip()
            )

    @staticmethod
    def _drop(record: logging.LogRecord) -> None:
        """Replace a record this filter could not redact, rather than emitting it.

        **A redactor that can take the process down is worse than one that loses a line**,
        and this is not hypothetical: an earlier version raised on uvicorn's own startup
        message, and because an exception in a `logging.Filter` propagates out of the
        `logger.info` call that triggered it, the container died at startup instead of
        serving. The specific cause is fixed above; the guard is for the next one.

        It fails **closed**. The record is replaced, not passed through — a bug here
        costs a log line, never a credential.
        """
        record.msg = "a log record could not be redacted, and was dropped rather than printed"
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        for key in [
            key
            for key in record.__dict__
            if key not in _RESERVED and not key.startswith("_")
        ]:
            del record.__dict__[key]


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

        if record.exc_text or record.exc_info:
            # `exc_text` first: the redacting filter renders the traceback there, and
            # calling `formatException` again would put the unredacted one back.
            payload["exception"] = record.exc_text or self.formatException(record.exc_info)

        return json.dumps(payload, default=str, separators=(",", ":"))


def _safe(value: Any) -> Any:
    """Make a value JSON-safe without letting a logging call raise.

    The `repr` fallback is redacted, because it is the one path a secret reaches the
    stream through without ever having been a string the filter could see: a `set` or a
    custom object is not JSON-encodable, so its contents arrive here as text produced
    after the filter ran.
    """
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return redact_text(repr(value))


def configure(service: str, level: str | None = None) -> None:
    """Install the JSON formatter on the root logger. Idempotent.

    Uvicorn installs its own handlers; replacing the root handler set rather than
    adding to it is what keeps one event from being logged twice in two formats.
    """
    resolved = (level or os.environ.get("COMPANY_OS_LOG_LEVEL") or "INFO").upper()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    # R6, on every record this process emits, including the ones written by code that
    # has never heard of a provider key. See the module docstring for what this covers
    # that `modelgw`'s masked key type cannot.
    handler.addFilter(Redaction())

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
