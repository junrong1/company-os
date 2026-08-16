"""The model gateway: one call shape, two wires, and a supported keyless mode.

`complete()` is the whole public surface. It returns a `Completion` or a `Failure`
and it raises nothing — every provider outcome, including the ones that arrive as
transport exceptions, is classified here and returned as a value. That is not
politeness: R5 sends guard rejection, provider error, timeout and exhausted ceiling
down one fallback path, and a path that has to catch exceptions from one of its four
sources and read a value from the other three is a path with a hole in it.

**Two code paths, eight providers.** `openai_compatible` and `anthropic_native` are
pure functions from a `Prompt` to a `WireRequest`; everything that differs between
OpenAI, Azure, OpenRouter, Ollama, LM Studio, vLLM and SGLang is a row in
`config.PROVIDERS`. No general-purpose LLM framework is in the tree, and M26's test
is what keeps it out: two HTTP shapes do not justify that dependency surface, and a
bring-your-own-key user would meet its configuration surface as a support burden.

**No retries.** A failure goes back to the caller once. Retrying here would put
wall-clock latency inside a run whose deadlines are counted in sim-ticks, and it
would spend against a ceiling U9 enforces per run for an outcome R5 already says is
a fallback rather than a second attempt.

**The seams left open.** U9's per-run ceiling and U12's content-addressed cache both
sit *in front of* this call, not inside it — the ceiling because it must refuse
before the provider is contacted, the cache because a hit must not count as a call.
Neither is stubbed here; `complete()` is the one entry point either wraps.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import ModuleType
from typing import Any

import httpx

from modelgw import anthropic_native, openai_compatible
from modelgw.config import (
    CONNECT_TIMEOUT_SECONDS,
    MASK,
    PROVIDERS,
    WIRE_ANTHROPIC,
    WIRE_OPENAI,
    Absence,
    GatewayConfig,
    Prompt,
    ProviderSpec,
    SecretKey,
    Turn,
    WireMismatch,
    WireRequest,
    resolve,
    scrub,
)

__all__ = [
    "Absence",
    "Answer",
    "Completion",
    "Failure",
    "FailureKind",
    "GatewayConfig",
    "MASK",
    "ModelGateway",
    "PROVIDERS",
    "Prompt",
    "ProviderSpec",
    "SecretKey",
    "Turn",
    "Usage",
    "WireRequest",
    "from_environment",
    "resolve",
    "scrub",
    "wire_for",
]

# The stdlib logger, not `servicekit.logging.get_logger`. servicekit's package
# __init__ imports the status router and therefore FastAPI, and `import modelgw`
# pulling in a web framework would be a dependency this package has no use for.
# servicekit's `configure()` installs its formatter on the *root* logger, so this
# logger is formatted as JSON in a service and stays quiet in a test.
log = logging.getLogger("modelgw")

_ADAPTERS: dict[str, ModuleType] = {
    WIRE_OPENAI: openai_compatible,
    WIRE_ANTHROPIC: anthropic_native,
}


def wire_for(provider: str) -> str:
    """Which of the two code paths a provider resolves to. M24 in one function."""
    return PROVIDERS[provider].wire


@dataclass(frozen=True)
class Usage:
    """What one call cost, as the provider counted it.

    U9's ceiling reads these. Zeros mean the provider reported nothing, which some
    self-hosted servers do — not that the call was free.
    """

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class Completion:
    """A provider answered, and the answer has text in it."""

    text: str
    usage: Usage
    provider: str
    model: str


class FailureKind(StrEnum):
    """Why a call produced no text. A closed set, and no provider wording in it.

    R5 requires the logged condition to be an enum rather than a message: a
    provider's phrasing in the log reaches the exported report, and it breaks seed
    determinism at the same time, because two runs of one seed would carry different
    strings for the same event.

    The distinctions are the ones an operator would act on differently.
    `INVALID_REQUEST` and `PROVIDER_ERROR` are separated because a 400 from a local
    server usually means a wrong model name and a 500 never does; `UNREACHABLE` and
    `TIMEOUT` because the first is a wrong port and the second is a slow model.
    """

    NOT_CONFIGURED = "not_configured"
    UNREACHABLE = "unreachable"
    TIMEOUT = "timeout"
    AUTH_REJECTED = "auth_rejected"
    RATE_LIMITED = "rate_limited"
    INVALID_REQUEST = "invalid_request"
    PROVIDER_ERROR = "provider_error"
    MALFORMED_RESPONSE = "malformed_response"
    EMPTY_RESPONSE = "empty_response"
    #: This package itself broke. Named honestly rather than filed under
    #: PROVIDER_ERROR, because the alternative — letting the exception escape — is
    #: what the fallback path cannot survive, and blaming the provider for our bug
    #: is how a support thread starts in the wrong place.
    GATEWAY_FAULT = "gateway_fault"


@dataclass(frozen=True)
class Failure:
    """A typed non-answer. Its detail is scrubbed and bounded by construction.

    `__post_init__` runs the pattern scrubber on every instance, whoever built it and
    whatever they passed. Redaction that depends on each call site remembering to
    scrub is redaction that holds until the next call site — R6 says structural, so
    the type does it. The gateway additionally scrubs against the *configured* key
    before it gets here, which is what catches a provider echoing our own auth header
    back inside a 401 body.

    **`kind` is the only field that may enter an event payload, and `detail` is the
    reason that boundary has to be stated rather than assumed.** Scrubbed and bounded
    is not the same as loggable: `detail`, `provider`, `model` and `status_code` all
    carry provider-supplied or provider-identifying words, and R5 puts a closed enum
    in the log precisely so that two runs of one seed carry the same bytes and so
    that nothing a provider phrased reaches the exported report. `detail` exists for
    an operator reading stdout, where a local server's "unknown field" is a
    thirty-second fix and a bare `invalid_request` is a support thread. U11 writes
    `kind` to the log and renders `kind`; if a diff ever disagrees between two
    timelines that made the same decisions, this field reaching a payload is the
    first place to look.
    """

    kind: FailureKind
    detail: str
    provider: str = ""
    model: str = ""
    status_code: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "detail", scrub(self.detail))

    def __str__(self) -> str:
        return f"{self.kind}: {self.detail}"


#: What `complete()` answers with. `isinstance(answer, Failure)` is the branch;
#: U11's fallback path is the other side of it.
Answer = Completion | Failure


class ModelGateway:
    """Talks to the one configured provider, or reports that there is none.

    Constructible in every environment, including a clone with nothing exported.
    An absent gateway is a real object whose `complete()` returns
    `FailureKind.NOT_CONFIGURED` — so the bench, the ceiling counter and the status
    endpoint all take one code path instead of three, and "no key" stops being a
    special case anybody can forget to handle.
    """

    def __init__(
        self,
        resolved: GatewayConfig | Absence,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = resolved if isinstance(resolved, GatewayConfig) else None
        self._absence = resolved if isinstance(resolved, Absence) else None
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    @classmethod
    def from_environment(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> ModelGateway:
        return cls(resolve(env), transport=transport)

    # --- what the rest of the system asks it ------------------------------

    @property
    def present(self) -> bool:
        return self._config is not None

    @property
    def absence(self) -> Absence | None:
        return self._absence

    @property
    def config(self) -> GatewayConfig | None:
        return self._config

    def describe(self) -> dict[str, Any]:
        """The bench, as a status payload may report it. Carries no key by construction."""
        if self._config is None:
            reason = self._absence.reason if self._absence else "no provider configured"
            return {"present": False, "absent_because": reason}
        return {"present": True, **self._config.describe()}

    async def complete(self, prompt: Prompt) -> Answer:
        """One call. Returns an answer or a typed failure; raises nothing."""
        config = self._config
        if config is None:
            reason = self._absence.reason if self._absence else "no provider configured"
            return Failure(kind=FailureKind.NOT_CONFIGURED, detail=reason)

        adapter = _ADAPTERS[config.wire]
        try:
            request = adapter.request_for(config, prompt)
        except Exception as exc:  # noqa: BLE001 - a build error is ours, and must not escape
            return self._failure(FailureKind.GATEWAY_FAULT, f"{type(exc).__name__}: {exc}")

        try:
            response = await self._send(config, request)
        except httpx.TimeoutException as exc:
            # Checked before TransportError, which it subclasses: a connect timeout
            # is a timeout, and reporting it as unreachable would send an operator
            # looking for a closed port that is in fact open and slow.
            return self._failure(
                FailureKind.TIMEOUT, f"{type(exc).__name__} after {config.timeout_seconds}s"
            )
        except httpx.TransportError as exc:
            return self._failure(
                FailureKind.UNREACHABLE, f"{request.url} unreachable: {type(exc).__name__}: {exc}"
            )
        except Exception as exc:  # noqa: BLE001 - nothing may escape this package
            return self._failure(FailureKind.GATEWAY_FAULT, f"{type(exc).__name__}: {exc}")

        if response.status_code >= 400:
            return self._refused(config, response)

        try:
            payload = response.json()
        except Exception:  # noqa: BLE001 - json() raises several types across versions
            return self._failure(
                FailureKind.MALFORMED_RESPONSE,
                f"{response.status_code} body is not JSON: {response.text}",
                status_code=response.status_code,
            )

        try:
            reply = adapter.read(payload)
        except WireMismatch as exc:
            return self._failure(
                FailureKind.MALFORMED_RESPONSE,
                f"{config.wire} wire: {exc}",
                status_code=response.status_code,
            )
        except Exception as exc:  # noqa: BLE001 - a parse bug is ours, not the provider's
            return self._failure(FailureKind.GATEWAY_FAULT, f"{type(exc).__name__}: {exc}")

        if not reply.text.strip():
            return self._failure(
                FailureKind.EMPTY_RESPONSE,
                f"{response.status_code} with no text in it",
                status_code=response.status_code,
            )

        return Completion(
            text=reply.text,
            usage=Usage(input_tokens=reply.input_tokens, output_tokens=reply.output_tokens),
            provider=config.provider,
            # What the provider says it used, falling back to what we asked for. The
            # two differ on OpenRouter and on any server that resolves an alias, and
            # the one worth recording is the one that answered.
            model=reply.model or config.model,
        )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> ModelGateway:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    # --- the call itself --------------------------------------------------

    async def _send(self, config: GatewayConfig, request: WireRequest) -> httpx.Response:
        headers = dict(request.headers)
        if config.key is not None:
            # One of two `reveal()` call sites in the package — the other is the
            # scrubber, which has to know what to remove — and the only moment the
            # raw key exists as a string. A test asserts both by name, which is the
            # audit R6 asks for. The header is absent entirely when no key is
            # configured, which is what makes Ollama and LM Studio work unauthenticated.
            headers[config.spec.auth_header] = config.key.reveal(config.spec.auth_prefix)

        return await self._http(config).request(
            request.method, request.url, headers=headers, json=dict(request.body)
        )

    def _http(self, config: GatewayConfig) -> httpx.AsyncClient:
        """Built on first use, not in `__init__`.

        An absent gateway is constructed in every process whether or not a provider
        exists; giving it a client would mean a socket pool nobody closes for a
        bench that never calls anything.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                transport=self._transport,
                timeout=httpx.Timeout(
                    config.timeout_seconds,
                    connect=min(CONNECT_TIMEOUT_SECONDS, config.timeout_seconds),
                ),
            )
        return self._client

    # --- failures ---------------------------------------------------------

    def _refused(self, config: GatewayConfig, response: httpx.Response) -> Failure:
        status = response.status_code
        if status in {401, 403}:
            kind = FailureKind.AUTH_REJECTED
        elif status == 429:
            kind = FailureKind.RATE_LIMITED
        elif status < 500:
            kind = FailureKind.INVALID_REQUEST
        else:
            kind = FailureKind.PROVIDER_ERROR

        # The body is carried, bounded and scrubbed, rather than dropped: the most
        # common failure on a bring-your-own-key setup is a local server rejecting
        # one field name, and hiding what it said turns a thirty-second fix into a
        # support thread. What must never survive is the auth header a 401 body can
        # echo back — which is why the configured key goes into the scrubber.
        return self._failure(
            kind, f"{status} from {config.provider}: {response.text}", status_code=status
        )

    def _failure(
        self, kind: FailureKind, detail: str, *, status_code: int | None = None
    ) -> Failure:
        config = self._config
        key = config.key if config is not None else None
        failure = Failure(
            kind=kind,
            detail=scrub(detail, key),
            provider=config.provider if config is not None else "",
            model=config.model if config is not None else "",
            status_code=status_code,
        )
        # `extra={"detail": ...}` is the repo's established shape and the exact
        # hazard R6 names: the value here has been through the scrubber twice, but
        # the same idiom at a call site *outside* this package — `str(exc)` on
        # something that quoted a 401 body — has not. The redacting log filter U24
        # adds to `servicekit.logging` is what covers those; this line is correct
        # without it, and both are wanted.
        log.warning(
            "model call failed",
            extra={
                "kind": str(failure.kind),
                "provider": failure.provider,
                "status": failure.status_code,
                "detail": failure.detail,
            },
        )
        return failure


def from_environment(
    env: Mapping[str, str] | None = None, *, transport: httpx.AsyncBaseTransport | None = None
) -> ModelGateway:
    """The one way a service gets a gateway. Never raises, never needs a key."""
    return ModelGateway.from_environment(env, transport=transport)
