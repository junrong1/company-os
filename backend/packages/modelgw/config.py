"""What a model call is, where it goes, and the key that must never be printed.

Three things live here, and they are together because both wire adapters and the
gateway that drives them have to agree on all three. Putting any of them in an
adapter would make the other adapter import it, and two peers importing each other
is how a two-path package becomes a framework.

**The provider table is the product's configuration surface.** Seven of the eight
entries speak the OpenAI-compatible shape and one speaks Anthropic's, which is why
there are two code paths and not eight. What differs between the seven is data: a
base URL, an auth header name, a token-limit field name, a default query parameter.
Adding a ninth OpenAI-compatible server is a row here, and M25 is the claim that it
is never anything more.

**A key is a type, not a string.** `SecretKey` renders as a mask through `str`,
`repr`, `format` and every path that reaches either — which is every path R6 names:
a log record, a JSON payload, an exception's args, a traceback. `reveal()` is the
only exit, and it has two call sites in the whole package: the send path, which is
the one moment the key has to be a string, and `scrub()` below, which has to know
what to remove from a provider's own words. A test names both. The failure mode of
the mask is an auth error, which is loud and harmless; the failure mode of a bare
string is a key in the log store forever.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

#: What a masked secret renders as. Short and unmistakable in a log line.
MASK = "***"

WIRE_OPENAI = "openai"
WIRE_ANTHROPIC = "anthropic"

# Environment is the whole interface (M25). Nothing below is read anywhere else,
# and no provider, model or URL is written into a code path.
ENV_PROVIDER = "COMPANY_OS_MODEL_PROVIDER"
ENV_MODEL = "COMPANY_OS_MODEL"
ENV_BASE_URL = "COMPANY_OS_MODEL_BASE_URL"
ENV_API_KEY = "COMPANY_OS_MODEL_API_KEY"
ENV_TIMEOUT = "COMPANY_OS_MODEL_TIMEOUT_SECONDS"

#: Seconds a single provider call may take. Comfortably inside the statement
#: deadline U10 sizes against the fastest clock rate, and generous enough for a
#: first token from a local model that has to load weights first.
DEFAULT_TIMEOUT_SECONDS = 30.0

#: Connect gets its own, much smaller budget: a wrong port in a local base URL is
#: the most common misconfiguration here, and it should fail in a second rather
#: than look like a slow model for thirty.
CONNECT_TIMEOUT_SECONDS = 5.0


class SecretKey:
    """A provider key whose string forms are masked.

    Not a `str` subclass on purpose. A subclass would inherit every rendering path
    `str` has — `%`-formatting, `str.join`, `json.dumps` — and each one would print
    the key while the type name suggested otherwise. A separate type makes the
    default *unprintable* and the reveal explicit.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self, prefix: str = "") -> str:
        """The raw key, optionally behind an auth-scheme prefix.

        The prefix is an argument rather than the caller's f-string because
        `f"Bearer {key}"` would render the mask and produce a 401 that looked like a
        bad key. One exit, two call sites, no way to build the header wrong.
        """
        return prefix + self._value

    def __str__(self) -> str:
        return MASK

    def __repr__(self) -> str:
        return f"SecretKey({MASK})"

    def __format__(self, spec: str) -> str:
        # Without this, `f"{key:>20}"` would raise from object.__format__ — and a
        # TypeError inside a log call is how logging calls get "fixed" into
        # `f"{key.reveal()}"`. Padding a mask is harmless; refusing to is not.
        return format(MASK, spec)

    def __bool__(self) -> bool:
        return bool(self._value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SecretKey):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._value)

    def __reduce__(self) -> Any:
        # Pickle would carry the raw value into a file or a socket, which is the
        # one rendering path a mask cannot cover. Refusing is louder than masking.
        raise TypeError("a SecretKey is not serialisable; call reveal() at the call site")

    def __copy__(self) -> SecretKey:
        # Copying is allowed where pickling is not: a secret is immutable, so
        # sharing the instance is safe, and breaking `copy.deepcopy` of a config
        # would be a surprise with no security value.
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> SecretKey:
        return self


# Patterns that catch a key we were never given — a provider echoing our own auth
# header back inside a 401 body, most often. The configured key is substituted
# separately; these cover the case where the text carries a *different* secret,
# such as a proxy's own credential in an upstream error.
_LEAK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-~+/=]+", re.IGNORECASE), f"Bearer {MASK}"),
    (
        re.compile(
            r"(?i)\b(x-api-key|api-key|api_key|apikey|authorization)\b(\"?\s*[:=]\s*\"?)"
            r"([^\s\"',}&]+)"
        ),
        rf"\1\2{MASK}",
    ),
    # Covers sk-, sk-proj-, sk-ant-, sk-or-v1- and every other vendor prefix built
    # on the same convention.
    (re.compile(r"\bsk-[A-Za-z0-9._\-]{6,}"), MASK),
)

#: How much of a provider's own words a failure detail may carry. Long enough to
#: show which field a local server rejected, short enough that a 401 body cannot
#: paste an entire request back into the log.
DETAIL_LIMIT = 300


def scrub(text: str, key: SecretKey | None = None) -> str:
    """Remove anything key-shaped from text, then bound its length.

    Substitution runs before truncation deliberately: truncating first could cut a
    key in half and leave the first half in the log, which is still a leak and a
    harder one to notice.
    """
    cleaned = text
    if key is not None:
        raw = key.reveal()
        if raw:
            cleaned = cleaned.replace(raw, MASK)
    for pattern, replacement in _LEAK_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > DETAIL_LIMIT:
        cleaned = cleaned[:DETAIL_LIMIT] + "…"
    return cleaned


@dataclass(frozen=True)
class ProviderSpec:
    """One row of the configuration surface. Everything here is data, not a branch."""

    provider: str
    wire: str
    #: `None` means the operator must supply one — Azure's endpoint is per-resource
    #: and there is no sensible default to invent.
    default_base_url: str | None
    auth_header: str
    auth_prefix: str
    #: Whether a call can be made without a key. False for a local server, which is
    #: the whole point of the keyless path.
    key_required: bool
    #: The provider's own conventional variable, checked after this repo's. A
    #: bring-your-own-key user has already exported it, and the one they forget to
    #: re-export under a new name is the one that makes the bench look broken.
    key_env: tuple[str, ...] = ()
    #: OpenAI deprecated `max_tokens` and its reasoning models reject it outright;
    #: every other OpenAI-compatible server accepts `max_tokens` and most have never
    #: heard of the replacement. So the field name is configuration, not a branch.
    max_output_field: str = "max_tokens"
    default_query: Mapping[str, str] = field(default_factory=dict)
    note: str = ""


PROVIDERS: Mapping[str, ProviderSpec] = {
    "openai": ProviderSpec(
        provider="openai",
        wire=WIRE_OPENAI,
        default_base_url="https://api.openai.com/v1",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        key_required=True,
        key_env=("OPENAI_API_KEY",),
        max_output_field="max_completion_tokens",
    ),
    "azure": ProviderSpec(
        provider="azure",
        wire=WIRE_OPENAI,
        default_base_url=None,
        # Azure accepts its own header on both the deployment-scoped surface and the
        # OpenAI-compatible `/openai/v1` one, so one header name covers both.
        auth_header="api-key",
        auth_prefix="",
        key_required=True,
        key_env=("AZURE_OPENAI_API_KEY", "OPENAI_API_KEY"),
        max_output_field="max_completion_tokens",
        # Azure is the one provider that versions by query parameter. A version in
        # the configured base URL wins; this is the fallback so a pasted endpoint
        # without one still resolves.
        default_query={"api-version": "preview"},
        note=(
            "endpoint is per-resource: set the base URL to "
            "https://<resource>.openai.azure.com/openai/v1"
        ),
    ),
    "openrouter": ProviderSpec(
        provider="openrouter",
        wire=WIRE_OPENAI,
        default_base_url="https://openrouter.ai/api/v1",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        key_required=True,
        key_env=("OPENROUTER_API_KEY",),
    ),
    "ollama": ProviderSpec(
        provider="ollama",
        wire=WIRE_OPENAI,
        default_base_url="http://127.0.0.1:11434/v1",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        key_required=False,
    ),
    "lmstudio": ProviderSpec(
        provider="lmstudio",
        wire=WIRE_OPENAI,
        default_base_url="http://127.0.0.1:1234/v1",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        key_required=False,
    ),
    "vllm": ProviderSpec(
        provider="vllm",
        wire=WIRE_OPENAI,
        default_base_url="http://127.0.0.1:8000/v1",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        key_required=False,
        note="a vLLM started with --api-key needs one; without that flag it needs none",
    ),
    "sglang": ProviderSpec(
        provider="sglang",
        wire=WIRE_OPENAI,
        default_base_url="http://127.0.0.1:30000/v1",
        auth_header="Authorization",
        auth_prefix="Bearer ",
        key_required=False,
    ),
    "anthropic": ProviderSpec(
        provider="anthropic",
        wire=WIRE_ANTHROPIC,
        # No /v1 here: the Anthropic wire's path is /v1/messages, and putting the
        # version in the base URL would make the two wires' base URLs mean
        # different things.
        default_base_url="https://api.anthropic.com",
        auth_header="x-api-key",
        auth_prefix="",
        key_required=True,
        key_env=("ANTHROPIC_API_KEY",),
    ),
}


@dataclass(frozen=True)
class Turn:
    """One turn of the conversation sent to the provider."""

    role: str
    text: str


@dataclass(frozen=True)
class Prompt:
    """A call, in the one shape both wires are built from.

    The system instruction is a field rather than a turn because Anthropic's wire
    carries it outside `messages`. Representing it as a turn would mean one of the
    two adapters had to pull it back out, and a scenario-authored string that
    arrived with `role="system"` would become an instruction on one provider and
    data on the other — the exact asymmetry U11's guards must not have to reason
    about.
    """

    system: str
    turns: tuple[Turn, ...]
    max_output_tokens: int = 1024
    #: Zero by default. Provider sampling is not reproducible at any temperature,
    #: so this buys no determinism — what it buys is that a re-run during
    #: development differs as little as the provider allows, and R2's logged
    #: statement stays the thing replay trusts.
    temperature: float = 0.0

    def __post_init__(self) -> None:
        if not self.turns:
            raise ValueError("a prompt needs at least one turn; both wires reject an empty one")
        for turn in self.turns:
            if turn.role not in {"user", "assistant"}:
                raise ValueError(
                    f"turn role {turn.role!r} is not user or assistant; "
                    "the system instruction is a field, not a turn"
                )
        if self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive; Anthropic's wire requires it")


@dataclass(frozen=True)
class WireRequest:
    """One HTTP request, described without a transport.

    Deliberately data. Both adapters are pure functions from a `Prompt` to this,
    which is what lets the wire shapes be tested with no client, no server and no
    port — and it is why the auth header is *not* here: the gateway attaches it at
    the moment of the call, so a request that gets logged or asserted on carries no
    key to leak.
    """

    method: str
    url: str
    headers: Mapping[str, str]
    body: Mapping[str, Any]


@dataclass(frozen=True)
class WireReply:
    """What an adapter got out of a 2xx body, before the gateway judges it.

    Token counts are best-effort by design: every hosted provider reports usage,
    and some self-hosted OpenAI-compatible servers do not. Missing usage counts as
    zero rather than as a malformed reply, which means U9's token ceiling cannot
    constrain such a server and its call ceiling is the one that does.
    """

    text: str
    input_tokens: int
    output_tokens: int
    model: str


class WireMismatch(Exception):
    """A 2xx body that is not the shape this wire promised.

    Raised by an adapter and caught by the gateway, which turns it into a typed
    failure. It never leaves the package.
    """


@dataclass(frozen=True)
class Absence:
    """Why there is no bench. Not an error: the keyless path is a supported mode.

    Every scenario the product ships is playable without a provider, so "no key" has
    to be a state the gateway reports rather than a condition it raises. The reason
    is what a status payload and a startup log line say out loud, so it names the
    variable to set.
    """

    reason: str


@dataclass(frozen=True)
class GatewayConfig:
    """A resolved provider: where to call, as what model, with which key."""

    spec: ProviderSpec
    model: str
    base_url: str
    key: SecretKey | None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @property
    def provider(self) -> str:
        return self.spec.provider

    @property
    def wire(self) -> str:
        return self.spec.wire

    def endpoint(self, path: str) -> str:
        """Join the wire's path onto the configured base URL, keeping its query.

        Keeping the base URL's own query parameters is what makes Azure a row in a
        table rather than a code path: an operator who pastes an endpoint carrying
        `?api-version=2024-10-21` gets that version, and one who pastes a bare
        endpoint gets the spec's default.
        """
        parts = urlsplit(self.base_url)
        merged = dict(self.spec.default_query)
        merged.update(dict(parse_qsl(parts.query, keep_blank_values=True)))
        joined = parts.path.rstrip("/") + "/" + path.lstrip("/")
        return urlunsplit((parts.scheme, parts.netloc, joined, urlencode(merged), parts.fragment))

    def describe(self) -> dict[str, Any]:
        """What a status payload may say about the bench. Never touches the key."""
        return {
            "provider": self.provider,
            "wire": self.wire,
            "model": self.model,
            "base_url": self.base_url,
            "key": MASK if self.key else None,
        }


def resolve(env: Mapping[str, str] | None = None) -> GatewayConfig | Absence:
    """Read the environment and return either a provider to call or why there isn't one.

    Every refusal is an `Absence` carrying the variable to set. Raising instead would
    make an unconfigured checkout — the default state of every contributor's clone —
    fail at import, and M26's keyless path is precisely the one that has to work.
    """
    environ = os.environ if env is None else env

    name = (environ.get(ENV_PROVIDER) or "").strip().lower()
    if not name:
        return Absence(f"no provider configured; set {ENV_PROVIDER} to one of {provider_names()}")

    spec = PROVIDERS.get(name)
    if spec is None:
        return Absence(
            f"unknown provider {name!r}; {ENV_PROVIDER} must be one of {provider_names()}"
        )

    model = (environ.get(ENV_MODEL) or "").strip()
    if not model:
        # No shipped default. A model name that was current when this was written
        # would be a constant that quietly names a retired model, and the failure
        # would arrive as a 404 from the provider rather than as a missing setting.
        return Absence(f"provider {name} is configured but no model is; set {ENV_MODEL}")

    base_url = (environ.get(ENV_BASE_URL) or "").strip() or spec.default_base_url
    if not base_url:
        return Absence(
            f"provider {name} has no default endpoint; set {ENV_BASE_URL} — {spec.note}"
        )

    key = _read_key(environ, spec)
    if key is None and spec.key_required:
        wanted = " or ".join((ENV_API_KEY, *spec.key_env))
        return Absence(f"provider {name} requires a key; set {wanted}")

    return GatewayConfig(
        spec=spec,
        model=model,
        base_url=base_url.rstrip("/"),
        key=key,
        timeout_seconds=_read_timeout(environ),
    )


def provider_names() -> str:
    return ", ".join(sorted(PROVIDERS))


def _read_key(environ: Mapping[str, str], spec: ProviderSpec) -> SecretKey | None:
    for variable in (ENV_API_KEY, *spec.key_env):
        raw = (environ.get(variable) or "").strip()
        if raw:
            return SecretKey(raw)
    return None


def _read_timeout(environ: Mapping[str, str]) -> float:
    raw = (environ.get(ENV_TIMEOUT) or "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        seconds = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    # A zero or negative timeout in httpx means "wait forever", which on a run whose
    # deadlines are counted in sim-ticks is a request that never abandons.
    return seconds if seconds > 0 else DEFAULT_TIMEOUT_SECONDS
