"""The model gateway: two wires, eight providers, and a key that cannot be printed.

Three claims are worth more than the rest of this file, because each one is a failure
that would be discovered in production and never in review:

* **No exception escapes the package.** A malformed body, a 429, a 500, a timeout, a
  refused connection and a 200 with nothing in it all come back as values. R5 sends
  every one of them down one fallback path, and a path that catches exceptions from
  some sources and reads values from others has a hole in it exactly where the
  provider is least reliable.
* **A key cannot be rendered.** `str`, `repr`, an f-string, a traceback and a log
  `extra` are all checked against the raw value, because a masked type that leaks
  through one of five paths is a masked type that leaks.
* **A provider's own words are scrubbed before they are kept.** A 401 body echoing
  the `Authorization` header back is the specific way a key reaches a log store
  through code that never touched the key.

The mock provider is `httpx.MockTransport`, not a socket server. It builds a real
`httpx.Request` — so the auth header, the URL and the JSON body are all assertable —
and it makes no test depend on a port being free or on how long a machine takes to
accept a connection. The one case that needs a real socket is a refused connection,
and that one gets a real closed port.
"""

from __future__ import annotations

import ast
import copy
import io
import json
import logging
import math
import pickle
import re
import socket
import subprocess
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

import modelgw
from modelgw import Completion, Failure, FailureKind, Prompt, Turn, Usage
from modelgw import anthropic_native, cache as cache_module, openai_compatible
from modelgw.cache import CacheKey, MemoryResponseCache, Purpose
from modelgw.ceiling import (
    DEFAULT_MAX_CALLS,
    DEFAULT_MAX_TOKENS,
    ENV_MAX_CALLS,
    ENV_MAX_TOKENS,
    UNLIMITED_SPELLING,
    BoundedGateway,
    Ceiling,
    CeilingSource,
    MemorySpendLedger,
    Spend,
    announce,
    estimated_input_tokens,
)
from modelgw.config import (
    DETAIL_LIMIT,
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_MODEL,
    ENV_PROVIDER,
    ENV_TIMEOUT,
    MASK,
    PROVIDERS,
    WIRE_ANTHROPIC,
    WIRE_OPENAI,
    GatewayConfig,
    SecretKey,
    resolve,
)

BACKEND = Path(__file__).resolve().parent.parent

#: Shaped like a real key on purpose: the scrubber's `sk-` pattern and the
#: substitution of the configured value are two different defences, and a test key
#: that matched neither would prove neither.
RAW_KEY = "sk-live-4f8a2b7c9d1e6003-DO-NOT-LOG"

AZURE_ENDPOINT = "https://demo.openai.azure.com/openai/v1"

PROMPT = Prompt(
    system="You are the engineering director.",
    turns=(Turn(role="user", text="Brief me on the platform migration."),),
)


# --- the mock provider ----------------------------------------------------


class MockProvider:
    """One canned reply, and every request it was asked for.

    Recording the requests is what makes the wire assertions possible: the auth
    header, the URL, the query string and the body are all read back off a real
    `httpx.Request` rather than off an intercepted call to our own code.
    """

    def __init__(self, reply: Callable[[httpx.Request], httpx.Response]) -> None:
        self._reply = reply
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._reply(request)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    @property
    def last(self) -> httpx.Request:
        assert self.requests, "the gateway never called the provider"
        return self.requests[-1]

    def sent_body(self) -> dict[str, Any]:
        return json.loads(self.last.content)


def answering(payload: Any, status: int = 200) -> MockProvider:
    return MockProvider(lambda _request: httpx.Response(status, json=payload))


def answering_text(body: str, status: int = 200) -> MockProvider:
    return MockProvider(lambda _request: httpx.Response(status, text=body))


def raising(build: Callable[[httpx.Request], Exception]) -> MockProvider:
    def reply(request: httpx.Request) -> httpx.Response:
        raise build(request)

    return MockProvider(reply)


def env_for(provider: str, **overrides: str) -> dict[str, str]:
    env = {ENV_PROVIDER: provider, ENV_MODEL: "a-model", ENV_API_KEY: RAW_KEY}
    if PROVIDERS[provider].default_base_url is None:
        env[ENV_BASE_URL] = AZURE_ENDPOINT
    env.update(overrides)
    return env


def ok_payload(wire: str, text: str = "Lead time is fourteen days.") -> dict[str, Any]:
    if wire == WIRE_ANTHROPIC:
        return {
            "model": "a-model-2026",
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 41, "output_tokens": 7},
        }
    return {
        "model": "a-model-2026",
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 41, "completion_tokens": 7},
    }


async def call(env: dict[str, str], provider: MockProvider, prompt: Prompt = PROMPT) -> Any:
    async with modelgw.from_environment(env, transport=provider.transport) as gateway:
        return await gateway.complete(prompt)


# --- M23, M24: both code paths, and every documented provider on one of them ---


@pytest.mark.parametrize("wire", [WIRE_OPENAI, WIRE_ANTHROPIC])
async def test_both_wires_answer_through_one_call_shape(wire: str) -> None:
    provider = "ollama" if wire == WIRE_OPENAI else "anthropic"
    mock = answering(ok_payload(wire))

    answer = await call(env_for(provider), mock)

    assert isinstance(answer, Completion), answer
    assert answer.text == "Lead time is fourteen days."
    assert answer.usage == Usage(input_tokens=41, output_tokens=7)
    assert answer.usage.total_tokens == 48
    assert answer.provider == provider
    assert answer.model == "a-model-2026", "the model that answered, not the one we asked for"


@pytest.mark.parametrize("provider", sorted(PROVIDERS))
def test_every_documented_provider_resolves_to_one_of_the_two_wires(provider: str) -> None:
    assert modelgw.wire_for(provider) in {WIRE_OPENAI, WIRE_ANTHROPIC}


@pytest.mark.parametrize("provider", sorted(PROVIDERS))
async def test_every_documented_provider_completes_a_call_through_its_wire(provider: str) -> None:
    """M23, M24 together: eight configuration shapes, two code paths, one call shape."""
    wire = modelgw.wire_for(provider)
    mock = answering(ok_payload(wire))

    answer = await call(env_for(provider), mock)

    assert isinstance(answer, Completion), f"{provider}: {answer}"
    assert mock.last.method == "POST"
    path = httpx.URL(str(mock.last.url)).path
    assert path.endswith("/chat/completions" if wire == WIRE_OPENAI else "/v1/messages")
    assert mock.sent_body()["model"] == "a-model"


@pytest.mark.parametrize(
    ("provider", "header", "expected"),
    [
        ("openai", "authorization", f"Bearer {RAW_KEY}"),
        ("openrouter", "authorization", f"Bearer {RAW_KEY}"),
        ("ollama", "authorization", f"Bearer {RAW_KEY}"),
        ("lmstudio", "authorization", f"Bearer {RAW_KEY}"),
        ("vllm", "authorization", f"Bearer {RAW_KEY}"),
        ("sglang", "authorization", f"Bearer {RAW_KEY}"),
        ("azure", "api-key", RAW_KEY),
        ("anthropic", "x-api-key", RAW_KEY),
    ],
)
async def test_each_provider_gets_the_auth_header_it_documents(
    provider: str, header: str, expected: str
) -> None:
    """The mask must not reach the wire. A masked key in the header is a 401 that
    looks like a bad key, which is the one failure mode of a masked type."""
    mock = answering(ok_payload(modelgw.wire_for(provider)))

    await call(env_for(provider), mock)

    assert mock.last.headers[header] == expected


async def test_the_anthropic_wire_pins_its_api_version_header() -> None:
    mock = answering(ok_payload(WIRE_ANTHROPIC))

    await call(env_for("anthropic"), mock)

    assert mock.last.headers["anthropic-version"] == anthropic_native.ANTHROPIC_VERSION


async def test_a_local_provider_with_no_key_sends_no_auth_header() -> None:
    """The keyless path is a supported mode, not a degraded one (M26).

    Ollama and LM Studio need no key; sending an empty or masked bearer token would
    turn a working local server into a 401 on the providers that do check.
    """
    env = {ENV_PROVIDER: "lmstudio", ENV_MODEL: "a-local-model"}
    mock = answering(ok_payload(WIRE_OPENAI))

    async with modelgw.from_environment(env, transport=mock.transport) as gateway:
        assert gateway.present, gateway.describe()
        answer = await gateway.complete(PROMPT)

    assert isinstance(answer, Completion), answer
    assert "authorization" not in mock.last.headers


async def test_the_openai_wire_carries_the_token_field_each_provider_accepts() -> None:
    """OpenAI's reasoning models reject `max_tokens`; most other servers have never
    heard of the replacement. So the field name is a row in the table."""
    openai_mock = answering(ok_payload(WIRE_OPENAI))
    await call(env_for("openai"), openai_mock)
    assert openai_mock.sent_body()["max_completion_tokens"] == PROMPT.max_output_tokens
    assert "max_tokens" not in openai_mock.sent_body()

    ollama_mock = answering(ok_payload(WIRE_OPENAI))
    await call(env_for("ollama"), ollama_mock)
    assert ollama_mock.sent_body()["max_tokens"] == PROMPT.max_output_tokens


async def test_the_anthropic_wire_puts_the_system_instruction_outside_the_turns() -> None:
    mock = answering(ok_payload(WIRE_ANTHROPIC))

    await call(env_for("anthropic"), mock)

    body = mock.sent_body()
    assert body["system"] == PROMPT.system
    assert [turn["role"] for turn in body["messages"]] == ["user"]
    assert body["max_tokens"] == PROMPT.max_output_tokens


async def test_the_openai_wire_puts_the_system_instruction_in_the_turns() -> None:
    mock = answering(ok_payload(WIRE_OPENAI))

    await call(env_for("ollama"), mock)

    body = mock.sent_body()
    assert [message["role"] for message in body["messages"]] == ["system", "user"]
    assert body["messages"][0]["content"] == PROMPT.system


async def test_azure_versions_by_query_parameter_without_a_code_path() -> None:
    """Azure is the one provider that versions by query string. A version in the
    configured endpoint wins; the table's default is what makes a pasted endpoint
    without one still resolve."""
    defaulted = answering(ok_payload(WIRE_OPENAI))
    await call(env_for("azure"), defaulted)
    assert httpx.URL(str(defaulted.last.url)).params["api-version"] == "preview"

    pinned = answering(ok_payload(WIRE_OPENAI))
    versioned = env_for("azure", **{ENV_BASE_URL: f"{AZURE_ENDPOINT}?api-version=2024-10-21"})
    await call(versioned, pinned)
    assert httpx.URL(str(pinned.last.url)).params["api-version"] == "2024-10-21"


def test_a_wire_request_needs_no_transport_and_carries_no_key() -> None:
    """Both adapters are pure functions from a prompt to data.

    That is what makes the auth header attachable at one call site instead of eight:
    the request an adapter builds has nothing in it to redact, so logging or
    asserting on one cannot leak.
    """
    for module, provider in ((openai_compatible, "openai"), (anthropic_native, "anthropic")):
        config = resolve(env_for(provider))
        assert isinstance(config, GatewayConfig)
        request = module.request_for(config, PROMPT)

        rendered = repr(request)
        assert RAW_KEY not in rendered
        assert not {key.lower() for key in request.headers} & {
            "authorization",
            "api-key",
            "x-api-key",
        }


# --- M25: provider, model and base URL are configuration ------------------


def test_changing_provider_model_or_base_url_needs_no_code_change() -> None:
    """One resolver, three variables, and no branch anywhere that names a provider."""
    ollama = resolve(env_for("ollama"))
    assert isinstance(ollama, GatewayConfig)
    assert ollama.wire == WIRE_OPENAI
    assert ollama.endpoint(openai_compatible.PATH).startswith("http://127.0.0.1:11434/v1/")

    relocated = resolve(env_for("ollama", **{ENV_BASE_URL: "http://gpu-box.local:9001/v1"}))
    assert isinstance(relocated, GatewayConfig)
    assert relocated.endpoint(openai_compatible.PATH) == (
        "http://gpu-box.local:9001/v1/chat/completions"
    )

    renamed = resolve(env_for("ollama", **{ENV_MODEL: "qwen3:32b"}))
    assert isinstance(renamed, GatewayConfig)
    assert renamed.model == "qwen3:32b"

    switched = resolve(env_for("anthropic"))
    assert isinstance(switched, GatewayConfig)
    assert switched.wire == WIRE_ANTHROPIC


async def test_the_environment_is_the_interface(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default source is `os.environ`; the injected mapping above is a test seam
    and not a second configuration path."""
    for variable in (ENV_PROVIDER, ENV_MODEL, ENV_BASE_URL, ENV_API_KEY, ENV_TIMEOUT):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv(ENV_PROVIDER, "sglang")
    monkeypatch.setenv(ENV_MODEL, "a-served-model")

    mock = answering(ok_payload(WIRE_OPENAI))
    async with modelgw.from_environment(transport=mock.transport) as gateway:
        assert gateway.present
        answer = await gateway.complete(PROMPT)

    assert isinstance(answer, Completion), answer
    assert str(mock.last.url).startswith("http://127.0.0.1:30000/v1/")


def test_a_providers_own_key_variable_is_read_when_this_repos_is_not_set() -> None:
    """The key a bring-your-own-key user has already exported is the one they will
    forget to re-export under a new name."""
    config = resolve(
        {ENV_PROVIDER: "anthropic", ENV_MODEL: "a-model", "ANTHROPIC_API_KEY": RAW_KEY}
    )

    assert isinstance(config, GatewayConfig)
    assert config.key is not None
    assert config.key.reveal() == RAW_KEY


def test_this_repos_key_variable_wins_over_the_providers() -> None:
    config = resolve(
        {
            ENV_PROVIDER: "openai",
            ENV_MODEL: "a-model",
            ENV_API_KEY: RAW_KEY,
            "OPENAI_API_KEY": "sk-the-other-one",
        }
    )

    assert isinstance(config, GatewayConfig)
    assert config.key is not None
    assert config.key.reveal() == RAW_KEY


# --- M29, R6: the key cannot be rendered ----------------------------------


def test_a_key_is_masked_through_str_repr_and_every_format_spec() -> None:
    key = SecretKey(RAW_KEY)

    assert str(key) == MASK
    assert repr(key) == f"SecretKey({MASK})"
    assert f"{key}" == MASK
    assert f"{key!r}" == f"SecretKey({MASK})"
    assert f"{key!s}" == MASK
    # Without an explicit __format__ this raises, and a TypeError inside a logging
    # call is how a logging call gets "fixed" into f"{key.reveal()}".
    assert f"{key:>8}" == MASK.rjust(8)
    assert "%s" % (key,) == MASK
    assert " ".join([str(key)]) == MASK
    assert RAW_KEY not in "".join(
        (str(key), repr(key), f"{key}", f"{key!r}", f"{key:^12}", format(key))
    )


def test_a_key_is_masked_in_a_traceback_however_it_reached_the_exception() -> None:
    key = SecretKey(RAW_KEY)

    for build in (
        lambda: RuntimeError(f"provider refused {key}"),
        lambda: RuntimeError("provider refused", key),
        lambda: ValueError({"authorization": key}),
    ):
        try:
            raise build()
        except Exception:  # noqa: BLE001 - rendering the traceback is the assertion
            rendered = traceback.format_exc()

        assert RAW_KEY not in rendered, rendered
        assert MASK in rendered, rendered


def test_a_key_is_masked_in_a_log_extra_through_the_repos_own_formatter() -> None:
    """The `extra=` path, against `servicekit.logging.JsonFormatter` rather than a
    formatter written for the test.

    This holds today, with no redacting filter installed: the formatter cannot
    serialise a `SecretKey`, falls back to `repr`, and the repr is the mask. U24's
    filter in `servicekit.logging` covers the call sites this package never sees —
    `extra={"error": str(exc)}` where the exception quoted a 401 body — and is
    complementary to this, not a prerequisite for it.
    """
    from servicekit.logging import JsonFormatter

    key = SecretKey(RAW_KEY)
    config = resolve(env_for("openai"))
    assert isinstance(config, GatewayConfig)

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter("agents"))
    logger = logging.getLogger("test_modelgw.masking")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info("calling the provider", extra={"key": key, "config": config, "run": "run-1"})

    line = stream.getvalue()
    assert RAW_KEY not in line, line
    assert MASK in line, line
    assert json.loads(line)["run"] == "run-1", "still one line of valid JSON"


def test_a_key_cannot_be_serialised_at_all() -> None:
    """The paths R6 names that no mask can cover: JSON and pickle.

    Both refuse loudly. An event payload, the response cache, the exported report and
    a scenario file are all JSON, and `json.dumps` has no rendering of an unknown
    type to fall back to — which is the outcome we want.
    """
    key = SecretKey(RAW_KEY)

    with pytest.raises(TypeError):
        json.dumps({"key": key})
    with pytest.raises(TypeError):
        pickle.dumps(key)

    # Copying is allowed where pickling is not: a secret is immutable, and breaking
    # deepcopy of a config would be a surprise with no security value.
    assert copy.deepcopy({"key": key})["key"].reveal() == RAW_KEY


def test_the_resolved_config_masks_the_key_it_holds() -> None:
    config = resolve(env_for("openai"))
    assert isinstance(config, GatewayConfig)

    assert RAW_KEY not in repr(config)
    assert RAW_KEY not in str(config)
    assert MASK in repr(config)
    assert config.describe()["key"] == MASK
    assert RAW_KEY not in json.dumps(config.describe())


def test_the_key_is_revealed_in_exactly_two_places_and_both_are_named() -> None:
    """R6 asks for structure rather than convention, and this is the audit.

    Two call sites, and the reason each exists: the send path, which is the only
    moment the raw key has to be a string, and the scrubber, which has to know what
    to remove from a provider's own words. A third is not necessarily wrong — it is
    the change that has to be argued for, and this is where the argument happens.
    """
    sites: set[str] = set()

    for path in sorted((BACKEND / "packages" / "modelgw").glob("*.py")):
        tree = ast.parse(path.read_text())
        for scope in ast.walk(tree):
            if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(scope):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "reveal"
                ):
                    sites.add(f"{path.name}:{scope.name}")

    assert sites == {"__init__.py:_send", "config.py:scrub"}, sites


# --- the provider's own words never become ours ---------------------------


async def test_a_401_body_echoing_the_auth_header_does_not_reach_the_failure() -> None:
    echoed = {
        "error": {
            "message": (
                f"Incorrect API key provided: {RAW_KEY}. "
                f"Request headers: Authorization: Bearer {RAW_KEY}"
            ),
            "code": "invalid_api_key",
        }
    }
    mock = answering(echoed, status=401)

    answer = await call(env_for("openai"), mock)

    assert isinstance(answer, Failure)
    assert answer.kind is FailureKind.AUTH_REJECTED
    assert answer.status_code == 401
    assert RAW_KEY not in answer.detail, answer.detail
    assert RAW_KEY not in str(answer)
    assert RAW_KEY not in repr(answer)
    assert MASK in answer.detail
    assert "401" in answer.detail, "the status is ours to keep; the key is not"


async def test_the_failure_the_gateway_logs_carries_no_key_either(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`extra={"detail": ...}` is this repo's established shape and the exact hazard
    R6 names. Formatted through the real formatter, because that is what a service
    would write to stdout."""
    from servicekit.logging import JsonFormatter

    mock = answering({"error": f"bad key {RAW_KEY}"}, status=403)
    formatter = JsonFormatter("agents")

    with caplog.at_level(logging.WARNING, logger="modelgw"):
        await call(env_for("openai"), mock)

    written = [formatter.format(record) for record in caplog.records]
    assert written, "a failed call must say so in the log"
    for line in written:
        assert RAW_KEY not in line, line
    assert any('"kind":"auth_rejected"' in line for line in written), written


async def test_a_key_that_is_not_key_shaped_is_still_scrubbed_from_a_providers_words() -> None:
    """The scrubber's two defences are independent, and this is the one that needs
    the configuration.

    Azure's keys are bare hex: no `sk-` prefix, nothing a pattern can recognise, and
    the body that echoes one back does not label it. Substituting the *configured*
    value is the only thing that catches it — which is why the scrubber takes the key
    rather than a list of patterns alone.
    """
    hex_key = "0f9c4a2b7e1d48f3a6c05b8e2d7f1a94"
    mock = answering(
        {"error": {"code": "401", "message": f"Access denied due to invalid key {hex_key}"}},
        status=401,
    )

    answer = await call(env_for("azure", **{ENV_API_KEY: hex_key}), mock)

    assert isinstance(answer, Failure)
    assert answer.kind is FailureKind.AUTH_REJECTED
    assert hex_key not in answer.detail, answer.detail
    assert MASK in answer.detail


async def test_a_long_error_body_is_bounded_before_it_is_kept() -> None:
    """A 401 body can paste an entire request back. The detail is a diagnostic, not
    an archive."""
    mock = answering_text("x" * 20_000, status=500)

    answer = await call(env_for("ollama"), mock)

    assert isinstance(answer, Failure)
    assert len(answer.detail) <= DETAIL_LIMIT + 1


def test_a_failure_scrubs_its_detail_whoever_built_it() -> None:
    """Construction, not the gateway, is where the scrubbing happens — so a caller
    who builds a failure of their own cannot skip it."""
    hand_built = Failure(
        kind=FailureKind.PROVIDER_ERROR,
        detail=f'upstream said {{"api-key": "{RAW_KEY}"}} and Bearer {RAW_KEY}',
    )

    assert RAW_KEY not in hand_built.detail
    assert MASK in hand_built.detail


# --- every failure is a value ---------------------------------------------


@pytest.mark.parametrize(
    ("name", "mock_factory", "kind"),
    [
        (
            "malformed json",
            lambda: answering_text("not json at all"),
            FailureKind.MALFORMED_RESPONSE,
        ),
        ("wrong shape", lambda: answering({"choices": []}), FailureKind.MALFORMED_RESPONSE),
        (
            "rate limited",
            lambda: answering({"error": "slow down"}, status=429),
            FailureKind.RATE_LIMITED,
        ),
        (
            "server error",
            lambda: answering({"error": "boom"}, status=500),
            FailureKind.PROVIDER_ERROR,
        ),
        (
            "bad request",
            lambda: answering({"error": "no such model"}, status=404),
            FailureKind.INVALID_REQUEST,
        ),
        (
            "read timeout",
            lambda: raising(lambda request: httpx.ReadTimeout("too slow", request=request)),
            FailureKind.TIMEOUT,
        ),
        (
            "connect timeout",
            lambda: raising(lambda request: httpx.ConnectTimeout("no answer", request=request)),
            FailureKind.TIMEOUT,
        ),
        (
            "empty content",
            lambda: answering(
                {"choices": [{"message": {"role": "assistant", "content": "   "}}]}
            ),
            FailureKind.EMPTY_RESPONSE,
        ),
        (
            "a bug of ours",
            lambda: raising(lambda _request: ZeroDivisionError("a parse bug, not the provider's")),
            FailureKind.GATEWAY_FAULT,
        ),
    ],
)
async def test_every_provider_outcome_is_a_typed_failure_not_an_exception(
    name: str, mock_factory: Callable[[], MockProvider], kind: FailureKind
) -> None:
    answer = await call(env_for("ollama"), mock_factory())

    assert isinstance(answer, Failure), f"{name} escaped as {answer!r}"
    assert answer.kind is kind, f"{name}: {answer}"
    assert answer.provider == "ollama"
    assert answer.detail, "a failure has to say something an operator can act on"


async def test_an_empty_anthropic_reply_is_empty_rather_than_malformed() -> None:
    """A reply carrying only blocks this package does not read is a 200 with no text,
    which is a different thing from a body in the wrong shape."""
    mock = answering({"content": [{"type": "tool_use", "id": "t1", "name": "x", "input": {}}]})

    answer = await call(env_for("anthropic"), mock)

    assert isinstance(answer, Failure)
    assert answer.kind is FailureKind.EMPTY_RESPONSE


async def test_a_connection_refused_at_a_configured_local_base_url_is_typed() -> None:
    """The one case that needs a real socket. A local base URL pointing at nothing is
    the most likely misconfiguration on the keyless path, and it must not be a
    stack trace."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]

    env = env_for("vllm", **{ENV_BASE_URL: f"http://127.0.0.1:{closed_port}/v1"})

    # No mock transport: this call has to reach a real socket to be refused by one.
    async with modelgw.from_environment(env) as gateway:
        answer = await gateway.complete(PROMPT)

    assert isinstance(answer, Failure), answer
    assert answer.kind is FailureKind.UNREACHABLE
    assert str(closed_port) in answer.detail


async def test_a_failure_is_returned_once_and_never_retried() -> None:
    """R5 makes a failure a fallback, not a second attempt. A retry here would spend
    against a per-run ceiling for an outcome the bench has already decided to
    replace, and it would put wall-clock latency inside a run whose deadlines are
    counted in sim-ticks."""
    mock = answering({"error": "boom"}, status=503)

    answer = await call(env_for("ollama"), mock)

    assert isinstance(answer, Failure)
    assert len(mock.requests) == 1


async def test_a_reply_without_usage_counts_as_zero_rather_than_malformed() -> None:
    """Some self-hosted OpenAI-compatible servers omit usage. Refusing the call would
    fail a call that succeeded; U9's token ceiling is the thing that cannot constrain
    such a server, and its call ceiling is the one that does."""
    mock = answering({"choices": [{"message": {"content": "A short briefing."}}]})

    answer = await call(env_for("lmstudio"), mock)

    assert isinstance(answer, Completion)
    assert answer.usage == Usage(0, 0)
    assert answer.model == "a-model", "no model reported: fall back to the configured one"


async def test_content_parts_are_read_as_text() -> None:
    """A list of content parts is what several self-hosted servers return, and
    treating it as malformed would fail a call that in fact succeeded."""
    mock = answering(
        {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "Two "},
                            {"type": "text", "text": "parts."},
                        ]
                    }
                }
            ]
        }
    )

    answer = await call(env_for("vllm"), mock)

    assert isinstance(answer, Completion)
    assert answer.text == "Two parts."


def test_a_prompt_refuses_a_shape_neither_wire_accepts() -> None:
    """Caller-side mistakes are refused at construction, where the caller is looking,
    rather than surfacing as a provider failure that blames the provider."""
    with pytest.raises(ValueError, match="at least one turn"):
        Prompt(system="s", turns=())
    with pytest.raises(ValueError, match="not user or assistant"):
        Prompt(system="s", turns=(Turn(role="system", text="smuggled"),))
    with pytest.raises(ValueError, match="max_output_tokens"):
        Prompt(system="s", turns=(Turn(role="user", text="x"),), max_output_tokens=0)


# --- absence is a state, not an error -------------------------------------


@pytest.mark.parametrize(
    ("name", "env", "names_variable"),
    [
        ("nothing configured", {}, ENV_PROVIDER),
        ("a typo in the provider", {ENV_PROVIDER: "openai-ish"}, ENV_PROVIDER),
        ("a provider with no model", {ENV_PROVIDER: "openai", ENV_MODEL: ""}, ENV_MODEL),
        (
            "a key-requiring provider with no key",
            {ENV_PROVIDER: "openai", ENV_MODEL: "a-model"},
            ENV_API_KEY,
        ),
        (
            "azure without its per-resource endpoint",
            {ENV_PROVIDER: "azure", ENV_MODEL: "a-model", ENV_API_KEY: RAW_KEY},
            ENV_BASE_URL,
        ),
    ],
)
async def test_an_unconfigured_gateway_reports_itself_absent_rather_than_raising(
    name: str, env: dict[str, str], names_variable: str
) -> None:
    gateway = modelgw.from_environment(env)

    assert not gateway.present, name
    assert gateway.absence is not None
    assert names_variable in gateway.absence.reason, f"{name}: {gateway.absence.reason}"

    described = gateway.describe()
    assert described["present"] is False
    assert names_variable in described["absent_because"]

    answer = await gateway.complete(PROMPT)
    assert isinstance(answer, Failure)
    assert answer.kind is FailureKind.NOT_CONFIGURED
    assert names_variable in answer.detail
    # Nothing to close: an absent gateway never built a client.
    await gateway.aclose()


def test_an_absent_gateway_names_the_providers_it_would_accept() -> None:
    absence = modelgw.from_environment({}).absence
    assert absence is not None
    for provider in PROVIDERS:
        assert provider in absence.reason


def test_the_package_imports_with_no_provider_configured() -> None:
    """The verification U8 asks for, in a subprocess with the environment stripped.

    An unconfigured clone is the default state of every contributor's checkout, so
    import-time work that needed a key would fail the suite for everyone who has not
    exported one.
    """
    probe = """
import modelgw
gateway = modelgw.from_environment()
assert not gateway.present
assert gateway.describe()["present"] is False
print("absent")
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=BACKEND,
        env={"PYTHONPATH": "packages:services", "PATH": ""},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "absent"


# --- M26: no LLM framework, in the lockfile or in the imports -------------


def test_the_lockfile_gains_no_llm_framework() -> None:
    """M26. Two HTTP shapes do not justify that dependency surface, and a
    bring-your-own-key user would meet its configuration surface as a support burden.

    The official SDKs are on the list too. Neither is a framework, but taking either
    would mean the two wires were no longer written here — and the reason to write
    them here is that a provider table plus two request builders is smaller than
    either SDK's own configuration.
    """
    forbidden = {
        "langchain",
        "langchain-core",
        "langchain-community",
        "langgraph",
        "llama-index",
        "llama-index-core",
        "litellm",
        "haystack-ai",
        "guidance",
        "dspy",
        "dspy-ai",
        "semantic-kernel",
        "instructor",
        "openai",
        "anthropic",
        "cohere",
        "mistralai",
        "google-generativeai",
        "google-genai",
        "transformers",
        "tiktoken",
        "ollama",
        "outlines",
        "autogen-agentchat",
        "crewai",
    }
    locked = set(re.findall(r'^name = "(.+)"$', (BACKEND / "uv.lock").read_text(), re.MULTILINE))

    grew = sorted(locked & forbidden)
    assert not grew, f"the lockfile grew an LLM dependency: {grew}"
    assert "httpx" in locked, "the gateway's one transport dependency, already pinned"


def test_the_package_imports_nothing_outside_the_stdlib_but_httpx() -> None:
    """The other half of M26, and the cheaper half to keep true.

    A lockfile check catches a dependency that was declared. This catches the import
    that would make someone declare one.
    """
    stdlib = set(sys.stdlib_module_names)
    third_party: dict[str, set[str]] = {}

    for path in sorted((BACKEND / "packages" / "modelgw").glob("*.py")):
        roots: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
        outside = roots - stdlib - {"modelgw"}
        if outside:
            third_party[path.name] = outside

    assert set().union(*third_party.values()) == {"httpx"}, third_party


# =========================================================================
# U9: the ceiling and its counter (M27, M28; R4)
# =========================================================================
#
# The ceiling sits *in front of* `complete()`, so most of these tests assert on
# `mock.requests` as much as on the answer: "refused before the provider was contacted" is
# the whole difference between a ceiling and a receipt. The store-backed half is here
# rather than in `test_store.py` because what is under test is the counter's contract, not
# the schema's — the schema's half of it (the table is mutable, and it is registered as
# such) is asserted over there, where the append-only triggers live.

#: Small enough that a test can reach the ceiling in two calls and still be reading about
#: a ceiling rather than about a loop.
TINY = Ceiling(max_calls=2, max_tokens=200)


def bounded(
    env: dict[str, str],
    provider: MockProvider,
    ceiling: Ceiling = TINY,
    ledger: MemorySpendLedger | None = None,
) -> BoundedGateway:
    return BoundedGateway(
        modelgw.from_environment(env, transport=provider.transport),
        ceiling,
        ledger if ledger is not None else MemorySpendLedger(),
    )


# --- the shipped defaults, and the one way to remove them -----------------


def test_the_shipped_ceiling_is_a_finite_number_and_an_absent_setting_means_it() -> None:
    """The failure mode this guards is `None` meaning unbounded on somebody else's key."""
    ceiling = Ceiling.from_environment({})

    assert ceiling.max_calls == DEFAULT_MAX_CALLS
    assert ceiling.max_tokens == DEFAULT_MAX_TOKENS
    assert math.isfinite(ceiling.max_calls) and math.isfinite(ceiling.max_tokens)
    assert ceiling.calls_source is CeilingSource.DEFAULT
    assert ceiling.tokens_source is CeilingSource.DEFAULT


def test_a_configured_ceiling_is_read_and_zero_means_zero() -> None:
    """`0` is the one number that must not be read as "unset" — that is what turning the
    bench off looks like, and `if not value` would have silently restored the default."""
    configured = Ceiling.from_environment({ENV_MAX_CALLS: "7", ENV_MAX_TOKENS: "1234"})
    assert (configured.max_calls, configured.max_tokens) == (7, 1234)
    assert configured.calls_source is CeilingSource.CONFIGURED

    off = Ceiling.from_environment({ENV_MAX_CALLS: "0"})
    assert off.max_calls == 0
    assert off.calls_source is CeilingSource.CONFIGURED


@pytest.mark.parametrize("raw", ["nonsense", "-5", "12.5", "unlimited-ish"])
def test_a_setting_that_cannot_be_read_keeps_the_default_rather_than_removing_it(
    raw: str,
) -> None:
    """The direction of the failure is the point. An unreadable ceiling that resolved to
    "no ceiling" would be a typo that spends money."""
    ceiling = Ceiling.from_environment({ENV_MAX_CALLS: raw})

    assert ceiling.max_calls == DEFAULT_MAX_CALLS
    assert ceiling.calls_source is CeilingSource.UNREADABLE


def test_only_the_word_removes_the_bound() -> None:
    ceiling = Ceiling.from_environment(
        {ENV_MAX_CALLS: UNLIMITED_SPELLING, ENV_MAX_TOKENS: "UNLIMITED"}
    )

    assert math.isinf(ceiling.max_calls)
    assert math.isinf(ceiling.max_tokens), "case-insensitive; an operator types what they type"
    assert ceiling.calls_source is CeilingSource.UNLIMITED
    assert ceiling.describe()["max_calls"] is None, "null on the wire, never in configuration"


def test_an_unlimited_ceiling_announces_itself_at_warning_and_names_the_variable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """"Logged loudly" as something assertable: the level, the variable and the consequence.

    An operator who typed the word out on their own key should meet it in the same scan as
    a failed dependency, not filed under normal operation.
    """
    with caplog.at_level(logging.INFO, logger="modelgw.ceiling"):
        announce(Ceiling.from_environment({ENV_MAX_CALLS: UNLIMITED_SPELLING}))

    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(warnings) == 1, [record.getMessage() for record in caplog.records]
    said = warnings[0].getMessage()
    assert ENV_MAX_CALLS in said
    assert "UNLIMITED" in said
    assert "without bound" in said

    # The finite half is still stated, just not shouted.
    quiet = [record for record in caplog.records if record.levelno == logging.INFO]
    assert any(str(DEFAULT_MAX_TOKENS) in record.getMessage() for record in quiet)


def test_an_unreadable_setting_also_says_so_rather_than_passing_in_silence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="modelgw.ceiling"):
        announce(Ceiling.from_environment({ENV_MAX_TOKENS: "lots"}))

    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert ENV_MAX_TOKENS in warnings[0].getMessage()
    assert UNLIMITED_SPELLING in warnings[0].getMessage(), "it must say what to type instead"


# --- M27: the ceiling stops the calls and never the run -------------------


async def test_a_run_at_its_call_ceiling_gets_a_typed_refusal_and_the_provider_is_untouched() -> (
    None
):
    """Covers M27. The bench goes quiet; nothing raises, and nothing stops.

    Asserted as a value on the same union a 429 arrives on, which is what keeps U11's
    fallback path at one branch. A third return type for "refused locally" would be an arm
    every caller could forget, and forgetting it is an exception escaping into a tick.
    """
    mock = answering(ok_payload(WIRE_OPENAI))
    gateway = bounded(env_for("ollama"), mock, Ceiling(max_calls=2, max_tokens=10_000))

    first = await gateway.complete("run-1", PROMPT)
    second = await gateway.complete("run-1", PROMPT)
    assert isinstance(first, Completion) and isinstance(second, Completion)
    assert len(mock.requests) == 2

    # Ten more attempts past the ceiling. Each one is a value, none of them is an
    # exception, and the provider is never contacted again.
    for _ in range(10):
        refused = await gateway.complete("run-1", PROMPT)
        assert isinstance(refused, Failure)
        assert refused.kind is FailureKind.CEILING_REACHED

    assert len(mock.requests) == 2, "refused before the provider was contacted, every time"

    reading = gateway.reading("run-1")
    assert reading.calls_exhausted
    assert reading.quiet, "the bench is quiet"
    assert reading.bench_present, "...and still present. Quiet is not absent, and not failed."
    assert reading.spend.calls == 2, "a refusal spends nothing, so it cannot run the count up"


async def test_the_ceiling_reached_condition_is_logged_as_an_enum_and_names_no_provider(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """R5's half of M27: the logged condition is a closed enum, never a sentence.

    `kind` is the only field of a `Failure` that may enter an event payload, and this is
    the one condition U9 adds to that closed set. `detail` carries the variable to raise
    for an operator reading stdout, and that is the only place it goes.
    """
    from servicekit.logging import JsonFormatter

    mock = answering(ok_payload(WIRE_OPENAI))
    gateway = bounded(env_for("ollama"), mock, Ceiling(max_calls=0, max_tokens=10_000))

    with caplog.at_level(logging.WARNING, logger="modelgw.ceiling"):
        refused = await gateway.complete("run-1", PROMPT)

    assert isinstance(refused, Failure)
    assert refused.kind is FailureKind.CEILING_REACHED
    assert refused.provider == "", "a local refusal names no provider; there was not one"
    assert refused.model == ""
    assert refused.status_code is None
    assert ENV_MAX_CALLS in refused.detail, "the operator's remedy rides detail, not kind"

    formatter = JsonFormatter("agents")
    written = [formatter.format(record) for record in caplog.records]
    assert any('"kind":"ceiling_reached"' in line for line in written), written
    for line in written:
        assert RAW_KEY not in line


def test_the_ceiling_condition_is_a_member_of_the_one_closed_set() -> None:
    """Where the condition lives, asserted rather than assumed.

    A separate exception type or a third return value would each have given U11 something
    to forget. `NOT_CONFIGURED` is the precedent this follows: a refusal that never reaches
    a wire is still a typed failure, and it is still a `Failure`.
    """
    assert FailureKind.CEILING_REACHED in set(FailureKind)
    assert FailureKind("ceiling_reached") is FailureKind.CEILING_REACHED
    # And it carries no provider wording, which is the whole reason it is an enum.
    assert str(FailureKind.CEILING_REACHED) == "ceiling_reached"


async def test_the_token_ceiling_refuses_a_call_whose_input_alone_will_not_fit() -> None:
    """The case a check inside `complete()` could not make.

    Refused before the provider is contacted rather than sent and then counted, which is
    the whole reason the ceiling is a wrapper and not a field on the gateway.
    """
    long_prompt = Prompt(
        system="You are the engineering director.",
        turns=(Turn(role="user", text="x" * 4_000),),
    )
    assert estimated_input_tokens(long_prompt) > 500

    mock = answering(ok_payload(WIRE_OPENAI))
    gateway = bounded(env_for("ollama"), mock, Ceiling(max_calls=100, max_tokens=500))

    refused = await gateway.complete("run-1", long_prompt)

    assert isinstance(refused, Failure)
    assert refused.kind is FailureKind.CEILING_REACHED
    assert mock.requests == [], "the provider was never contacted"
    assert ENV_MAX_TOKENS in refused.detail
    assert gateway.reading("run-1").spend == Spend(), "a refusal spends nothing"

    # A prompt that fits still goes through, so this is a bound and not an outage.
    assert isinstance(await gateway.complete("run-1", PROMPT), Completion)


async def test_the_token_ceiling_closes_once_the_tokens_are_spent() -> None:
    mock = answering(ok_payload(WIRE_OPENAI, text="A briefing."))
    gateway = bounded(env_for("ollama"), mock, Ceiling(max_calls=100, max_tokens=60))

    # 48 tokens on the first call, so the second finds twelve left and refuses.
    assert isinstance(await gateway.complete("run-1", PROMPT), Completion)
    refused = await gateway.complete("run-1", PROMPT)

    assert isinstance(refused, Failure)
    assert refused.kind is FailureKind.CEILING_REACHED
    assert gateway.reading("run-1").tokens_exhausted is False, (
        "48 of 60 is not exhausted; this call was refused because it would not fit, and the "
        "two conditions are worth telling apart"
    )
    assert len(mock.requests) == 1


async def test_a_failed_call_counts_and_an_absent_bench_does_not() -> None:
    """A 500 cost an attempt; a missing key cost nothing.

    Without the first half, a run pointed at a dead port would retry forever against a
    counter that never moved. Without the second, a keyless run's counter would climb —
    and M20 says a keyless run is the Phase 2 conversation exactly.
    """
    broken = answering({"error": "boom"}, status=500)
    gateway = bounded(env_for("ollama"), broken, Ceiling(max_calls=3, max_tokens=10_000))

    for _ in range(3):
        answer = await gateway.complete("run-1", PROMPT)
        assert isinstance(answer, Failure)
        assert answer.kind is FailureKind.PROVIDER_ERROR

    assert gateway.reading("run-1").spend.calls == 3
    fourth = await gateway.complete("run-1", PROMPT)
    assert isinstance(fourth, Failure)
    assert fourth.kind is FailureKind.CEILING_REACHED
    assert len(broken.requests) == 3, "the ceiling stopped the retrying, not the provider"


async def test_with_no_key_the_counter_reads_zero_and_the_bench_is_absent_not_failed() -> None:
    """M20 through the counter. "No provider" is a state, and it is a supported one."""
    gateway = BoundedGateway(modelgw.from_environment({}), Ceiling(), MemorySpendLedger())

    for _ in range(5):
        answer = await gateway.complete("run-1", PROMPT)
        assert isinstance(answer, Failure)
        assert answer.kind is FailureKind.NOT_CONFIGURED, "absent, not ceiling-limited"

    reading = gateway.reading("run-1")
    assert reading.spend == Spend(), "nothing was contacted, so nothing was spent"
    assert reading.bench_present is False
    assert reading.quiet is False, "a run with no bench has not reached a ceiling"

    payload = reading.to_payload()
    assert payload["calls"] == 0
    assert payload["tokens"] == 0
    assert payload["bench_present"] is False
    assert payload["max_calls"] == DEFAULT_MAX_CALLS
    assert json.dumps(payload), "the whole reading is JSON, and carries no key to leak"


# --- a cache hit is not a call, and a fallback is not a hit ---------------


async def test_a_cache_hit_does_not_count_against_the_call_ceiling_and_the_counter_says_so() -> (
    None
):
    """Both halves. The count does not move, and "not a call" is something the counter can
    *say* — a hit that left no trace would be indistinguishable from a turn that never
    happened, and an operator asking why the ceiling is not moving would have nothing to
    read."""
    mock = answering(ok_payload(WIRE_OPENAI))
    gateway = bounded(env_for("ollama"), mock, Ceiling(max_calls=1, max_tokens=10_000))

    served = await gateway.complete("run-1", PROMPT)
    assert isinstance(served, Completion)
    assert gateway.reading("run-1").spend.calls == 1

    for _ in range(20):
        gateway.note_cache_hit("run-1", served)

    reading = gateway.reading("run-1")
    assert reading.spend.calls == 1, "twenty hits, still one call"
    assert reading.spend.cache_hits == 20, "and the counter says so"
    assert reading.spend.tokens == served.usage.total_tokens, (
        "no provider was contacted, so no tokens were spent again"
    )
    assert len(mock.requests) == 1


async def test_a_scripted_fallback_cannot_be_recorded_as_a_cache_hit() -> None:
    """The execution decision "a fallback is never a cache entry", as a signature.

    A cached wrong answer has a long life, and the operator's remedy — raise the ceiling,
    configure a key — would silently not work. So the method takes the `Completion` it
    served, and a `Failure` has nowhere to go.
    """
    mock = answering({"error": "boom"}, status=500)
    gateway = bounded(env_for("ollama"), mock)

    fallback = await gateway.complete("run-1", PROMPT)
    assert isinstance(fallback, Failure)

    with pytest.raises(TypeError, match="never a cache entry"):
        gateway.note_cache_hit("run-1", fallback)  # type: ignore[arg-type]

    assert gateway.reading("run-1").spend.cache_hits == 0


# --- R4: the ceiling is per run; only the display crosses the lineage -----


async def test_the_lineage_total_sits_beside_this_runs_count_without_being_it() -> None:
    """M28's two figures, and the reason they are two.

    The ceiling is checked against the run (M27) because a lineage-wide budget would leave
    a child at its parent's exhaustion point — the bench going quiet at a different moment
    in each timeline, with the diff presenting *budget* as consequence. The player still
    wants one number for what the session cost, so the aggregate is a display.
    """
    ledger = MemorySpendLedger({"child": "parent", "parent": "parent"})
    mock = answering(ok_payload(WIRE_OPENAI))
    gateway = bounded(env_for("ollama"), mock, Ceiling(max_calls=2, max_tokens=10_000), ledger)

    await gateway.complete("parent", PROMPT)
    await gateway.complete("parent", PROMPT)
    await gateway.complete("child", PROMPT)

    parent = gateway.reading("parent")
    child = gateway.reading("child")

    assert parent.spend.calls == 2 and parent.calls_exhausted
    assert child.spend.calls == 1 and not child.calls_exhausted, "its own budget"
    assert parent.lineage_spend.calls == 3 == child.lineage_spend.calls, "one session total"


# --- the counter in the store: it survives a restart, and it is per run ---


PARENT = "run-parent"
CHILD = "run-child"


@pytest.fixture
def spend_store(tmp_path):
    """A provisioned store, a parent run with one event in it, and a ledger on the same file.

    SQLite only, deliberately. The dialect question for this table is whether it stays
    mutable on both, and `test_store.py` answers that on both, where the append-only
    triggers are. What is under test here is the counter's arithmetic and its lifetime.
    """
    from contracts.envelope import EventKind
    from kernel import lease as lease_module
    from kernel.store import LogStore, make_engine
    from simcore.rates import RULES_VERSION
    from simcore.step import Emitted

    from agents.main import StoreSpendLedger

    url = f"sqlite:///{tmp_path}/spend.sqlite3"
    store = LogStore(make_engine(url))
    store.create_all()
    store.create_run(
        run_id=PARENT,
        run_seed=0xC0FFEE,
        rules_ver=RULES_VERSION,
        quantum_sim_seconds=60,
        grid=(31, 18),
    )
    with store.engine.begin() as connection:
        handle = lease_module.acquire(connection, owner="u9-test")
    store.append_tick(
        run_id=PARENT,
        emitted=[Emitted(kind=EventKind.WORK_ASSIGNED, payload={"item": "wi_1"})],
        lease_handle=handle,
        rules_ver=RULES_VERSION,
        tick=1,
    )

    ledger = StoreSpendLedger(url)
    try:
        yield store, handle, url, ledger
    finally:
        ledger.dispose()
        store.engine.dispose()


def test_a_run_is_its_own_lineage_root_at_creation(spend_store) -> None:
    """R21's first half, which is what makes the HUD's aggregate correct today.

    A recursive walk of `parent_run_id` was the alternative, and a deleted mid-lineage row
    would have split one lineage into two with every reader agreeing about the wrong answer.
    """
    store, _handle, _url, _ledger = spend_store

    row = store.run_row(PARENT)
    assert row is not None
    assert row["lineage_root_id"] == PARENT
    assert row["parent_run_id"] is None


def test_the_counter_survives_a_restart(spend_store) -> None:
    """M28 is a figure the player watches during a run; a counter that reset on restart
    would let a run spend its ceiling twice."""
    from agents.main import StoreSpendLedger

    _store, _handle, url, ledger = spend_store

    ledger.add(PARENT, Spend(calls=3, input_tokens=120, output_tokens=30, cache_hits=1))
    assert ledger.read(PARENT) == Spend(calls=3, input_tokens=120, output_tokens=30, cache_hits=1)

    # The restart: this ledger's engine goes away entirely, and a fresh one opens the file.
    ledger.dispose()
    after = StoreSpendLedger(url)
    try:
        assert after.read(PARENT) == Spend(
            calls=3, input_tokens=120, output_tokens=30, cache_hits=1
        )
        # And it keeps counting from there rather than from zero.
        assert after.add(PARENT, Spend(calls=1)).calls == 4
    finally:
        after.dispose()


def test_an_increment_does_not_depend_on_a_read_that_happened_first(spend_store) -> None:
    """The read-modify-write this avoids would lose an increment whenever two directors
    were briefed at once — and the number it would lose is the one the ceiling is checked
    against. So the arithmetic is in the statement."""
    from agents.main import StoreSpendLedger

    _store, _handle, url, first = spend_store
    second = StoreSpendLedger(url)
    try:
        first.read(PARENT)  # a stale zero, if anything cached it
        second.add(PARENT, Spend(calls=1))
        total = first.add(PARENT, Spend(calls=1))

        assert total.calls == 2, "neither increment was lost"
    finally:
        second.dispose()


def test_a_run_with_no_row_reads_zero_rather_than_being_created_by_a_read(spend_store) -> None:
    _store, _handle, _url, ledger = spend_store

    assert ledger.read("run-never-called") == Spend()
    assert ledger.read(PARENT) == Spend(), "reading did not write a row"


def test_a_fork_of_a_ceiling_exhausted_parent_has_its_own_budget(spend_store) -> None:
    """Covers R4. The parent's exhaustion is invisible to the child, in both directions.

    A lineage-wide budget would leave the child at its parent's exhaustion point, so the
    bench would go quiet at a different moment in each timeline and the diff would present
    *budget* as consequence — which is exactly what M34 forbids.

    And the parent's exhaustion does not appear in the child's timeline in the literal sense
    either: the counter is not in the log, so the fork's prefix copy carries no ceiling
    state, and there is nothing about a budget in the child's events.
    """
    from sqlalchemy import select

    from logschema import event_log

    store, handle, _url, ledger = spend_store

    exhausted = Ceiling(max_calls=2, max_tokens=10_000)
    ledger.add(PARENT, Spend(calls=2, input_tokens=90, output_tokens=10))
    assert ledger.read(PARENT).calls == 2

    forked = store.fork_run(
        parent_run_id=PARENT, at_seq=1, child_run_id=CHILD, lease_handle=handle
    )
    assert forked.forked, forked.refusal

    # The child's own budget is untouched...
    assert ledger.read(CHILD) == Spend()
    assert ledger.read(CHILD).calls < exhausted.max_calls
    # ...and the parent's is unmoved by the fork.
    assert ledger.read(PARENT).calls == 2

    # Nothing about a ceiling is in the child's copied prefix. The counter is bookkeeping
    # beside the log, not in it — an event carrying what a provider counted would be an
    # output the fold cannot reproduce, and strict replay would fail on every run that
    # used the bench.
    with store.engine.connect() as connection:
        kinds = connection.execute(
            select(event_log.c.kind).where(event_log.c.run_id == CHILD)
        ).all()
    assert len(kinds) == 1, "the prefix copy, and nothing added by the ceiling"


def test_the_lineage_total_reads_through_the_column_rather_than_walking_parents(
    spend_store,
) -> None:
    """The aggregate M28 renders beside the run's own count.

    A fork's `lineage_root_id` is its own id until U16 copies the parent's, so today this
    equals the run's own figure — which is the correct answer for an unforked run and the
    reason the tile is testable now. Pointing the child's column at the parent's root is
    what makes it interesting, and that is asserted here by doing it directly.
    """
    from sqlalchemy import update

    from logschema import runs as runs_table

    store, handle, _url, ledger = spend_store

    ledger.add(PARENT, Spend(calls=2, input_tokens=80, output_tokens=20))
    assert ledger.lineage(PARENT) == ledger.read(PARENT), "no forks: the root is the run"

    store.fork_run(parent_run_id=PARENT, at_seq=1, child_run_id=CHILD, lease_handle=handle)
    ledger.add(CHILD, Spend(calls=1, input_tokens=40, output_tokens=10))

    # What U16 will do at fork, done here so the aggregate's behaviour is pinned before the
    # unit that produces it lands.
    with store.engine.begin() as connection:
        connection.execute(
            update(runs_table).where(runs_table.c.run_id == CHILD).values(lineage_root_id=PARENT)
        )

    lineage = ledger.lineage(CHILD)
    assert lineage.calls == 3
    assert lineage.tokens == 150
    assert ledger.lineage(PARENT) == lineage, "one session total, read from either end"
    assert ledger.read(CHILD).calls == 1, "and the per-run figure the ceiling uses is untouched"


def test_the_lineage_of_an_unknown_run_is_zero_rather_than_an_error(spend_store) -> None:
    _store, _handle, _url, ledger = spend_store
    assert ledger.lineage("run-that-never-existed") == Spend()


def test_the_spend_endpoint_answers_zeros_for_a_run_that_never_called(
    spend_store, monkeypatch
) -> None:
    """The endpoint an operator and the surface both read.

    Zeros with the bench marked absent, not a 404 and not a 503: "no key configured" is a
    supported mode, and a counter that errored would read as a broken bench rather than as
    an absent one.
    """
    import importlib

    from fastapi.testclient import TestClient

    _store, _handle, url, _ledger = spend_store
    monkeypatch.setenv("COMPANY_OS_STORE_URL", url)
    for variable in (ENV_PROVIDER, ENV_MODEL, ENV_API_KEY):
        monkeypatch.delenv(variable, raising=False)

    import agents.main as agents_main

    agents_main = importlib.reload(agents_main)
    try:
        with TestClient(agents_main.app) as client:
            body = client.get(f"/runs/{PARENT}/spend").json()

            assert body["calls"] == 0
            assert body["tokens"] == 0
            assert body["bench_present"] is False
            assert body["quiet"] is False
            assert body["max_calls"] == DEFAULT_MAX_CALLS
            assert body["lineage_calls"] == 0
            assert RAW_KEY not in json.dumps(body)

            # And it moves during a run, which is the half of M28 a static reading cannot show.
            agents_main.ledger().add(PARENT, Spend(calls=2, input_tokens=100, output_tokens=25))
            moved = client.get(f"/runs/{PARENT}/spend").json()
            assert moved["calls"] == 2
            assert moved["tokens"] == 125
            assert moved["lineage_calls"] == 2
    finally:
        agents_main.ledger().dispose()


def test_the_agents_service_reaches_the_store_without_importing_the_kernels(
) -> None:
    """R4, at the one place this unit could have broken it.

    `kernel.store` owns the write path and `kernel.lease` has the timestamp helper this
    module needed; reaching for either would have been the exact import the boundary test
    caught when the report service tried it.
    """
    source = (BACKEND / "services" / "agents" / "main.py").read_text()
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])

    assert "kernel" not in roots, "R4: services meet at a contract, not by importing each other"
    assert "logschema" in roots, "the shared table definitions are how it reads the store"


# =========================================================================
# U12: caching on the situation (M33; R3)
# =========================================================================
#
# Two claims carry this section. The first is that the address is the *situation* — the
# assembled prompt, the scope it was drawn under, the purpose it was for — so that a
# hit is the same question asked twice and never merely a similar one. The second is
# that the cache is authoritative for nothing: emptying it changes no state, because a
# statement lives in the log and a fold cannot reach a cache at all.
#
# The store-backed half is here rather than in `test_store.py` for the reason U9's
# counter is: what is under test is the cache's contract. The schema's half of it — the
# table is mutable, and registered as such — is asserted over there.

#: A director's line, in the shape `Authorized.to_payload()` produces.
A_SCOPE = {"people": ["dir_hr", "stf_rec"], "items": ["wi_hiring"]}

#: The same line after U15 grants a cross-line read. A wider scope assembles a wider
#: context, so the two must never share an entry.
A_GRANTED_SCOPE = {
    "people": ["dir_hr", "dir_sales", "stf_rec"],
    "items": ["wi_hiring", "wi_pipeline"],
}


def keyed(
    prompt: Prompt = PROMPT,
    *,
    scope: dict[str, list[str]] | None = None,
    purpose: Purpose = Purpose.DIRECTOR_STATEMENT,
    run_id: str = "run-1",
) -> CacheKey:
    return CacheKey.derive(
        prompt, scope=A_SCOPE if scope is None else scope, purpose=purpose, run_id=run_id
    )


def a_completion(text: str = "BRIEFING: The recruiter is the constraint.") -> Completion:
    return Completion(text=text, usage=Usage(41, 7), provider="ollama", model="a-model-2026")


# --- the content address --------------------------------------------------


def test_the_same_situation_is_one_address_and_a_changed_prompt_is_another() -> None:
    """M33 in one assertion pair, before any store is involved.

    The prompt is assembled from the persona, the checkpoint and the retrieved evidence, so
    "the same prompt" is "the same situation" — which is what makes a digest the right key
    and a tick the wrong one (R29). Two runs standing at the same checkpoint with the same
    evidence *are* in the same situation, whatever tick each of them reached it at.
    """
    same = Prompt(system=PROMPT.system, turns=PROMPT.turns)
    assert keyed().digest == keyed(same).digest

    moved_on = Prompt(
        system=PROMPT.system,
        turns=(Turn(role="user", text="Brief me on the migration. One post has closed."),),
    )
    assert keyed(moved_on).digest != keyed().digest


def test_a_statement_drawn_under_a_grant_is_not_served_where_none_was_granted() -> None:
    """R3's scope half, and the reason it is in the key rather than assumed by the prompt.

    Two scopes frequently assemble identical prompts — most obviously when both retrieved
    nothing at all — and a briefing produced under an Authorization the CEO granted in one
    timeline must not appear in a timeline where they never granted it. The prompt is the
    situation; the scope is what the situation was allowed to be drawn from.
    """
    assert keyed(scope=A_GRANTED_SCOPE).digest != keyed(scope=A_SCOPE).digest


def test_the_scope_is_a_set_of_facts_rather_than_the_order_they_arrived_in() -> None:
    """A key that depended on iteration order would miss at random, which is the worst
    failure a cache has: it looks like it works and costs money on every other call."""
    shuffled = {"items": list(A_SCOPE["items"]), "people": list(reversed(A_SCOPE["people"]))}
    assert keyed(scope=shuffled).digest == keyed().digest


def test_a_ceo_summary_and_a_director_statement_do_not_collide() -> None:
    """The purpose namespace. Both can be raised at one tick about one person, and a
    summary served where a briefing was asked for is impossible to notice from the text —
    so the namespace is in the key before U14 writes the second producer, not after
    somebody sees the wrong block on screen."""
    assert keyed(purpose=Purpose.CEO_SUMMARY).digest != keyed().digest


@pytest.mark.parametrize(
    ("label", "prompt"),
    [
        ("system", Prompt(system="You are the sales director.", turns=PROMPT.turns)),
        (
            "turn text",
            Prompt(system=PROMPT.system, turns=(Turn(role="user", text="Brief me on hiring."),)),
        ),
        (
            "turn count",
            Prompt(
                system=PROMPT.system,
                turns=(*PROMPT.turns, Turn(role="assistant", text="Understood.")),
            ),
        ),
        ("max output", Prompt(system=PROMPT.system, turns=PROMPT.turns, max_output_tokens=800)),
        ("temperature", Prompt(system=PROMPT.system, turns=PROMPT.turns, temperature=0.7)),
    ],
)
def test_every_field_of_the_prompt_moves_the_address(label: str, prompt: Prompt) -> None:
    """`max_output_tokens` and `temperature` are not prose, and they still change the answer.
    A key that ignored them would serve a reply produced under settings since changed."""
    assert keyed(prompt).digest != keyed().digest, f"{label} is not in the address"


def test_the_derivations_own_version_is_in_the_address(monkeypatch) -> None:
    """`KEY_VERSION` is what makes a change to *what goes into the key* safe to ship.

    Without it, adding a field the derivation forgot would leave every existing entry
    answering lookups made under the new rule — a stale hit, which costs a briefing drawn
    from a situation that was never the same. Bumping it costs one provider call per entry.
    """
    before = keyed().digest
    monkeypatch.setattr(cache_module, "KEY_VERSION", cache_module.KEY_VERSION + 1)
    assert keyed().digest != before


def test_the_address_does_not_depend_on_this_processs_hash_seed() -> None:
    """The cache is read by a process that did not write it, so a digest that varied with
    `PYTHONHASHSEED` would be a cache that never hit after a restart — and would look like
    a cache that merely was not warm yet.

    Two subprocesses with different seeds, because the failure this catches cannot be
    reproduced inside one interpreter.
    """
    probe = """
from modelgw.cache import CacheKey, Purpose
from modelgw.config import Prompt, Turn

prompt = Prompt(system="You are the engineering director.",
                turns=(Turn(role="user", text="Brief me."),))
print(CacheKey.derive(prompt, scope={"people": ["b", "a"], "items": ["i"]},
                      purpose=Purpose.DIRECTOR_STATEMENT, run_id="run-1").digest)
"""
    digests = set()
    for seed in ("0", "12345"):
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            cwd=BACKEND,
            env={"PYTHONPATH": "packages:services", "PATH": "", "PYTHONHASHSEED": seed},
        )
        assert result.returncode == 0, result.stderr
        digests.add(result.stdout.strip())

    assert len(digests) == 1, f"the digest moved with the hash seed: {digests}"


# --- what an entry is, and is not -----------------------------------------


def test_a_served_entry_reports_no_tokens_and_names_no_provider() -> None:
    """What a hit *cost* is zero, and the entry has to say so.

    Reporting the original call's usage would invite a caller to count it a second time,
    which is the one way a cache could move a ceiling it is supposed to protect. The model
    is kept because the statement in the log names it and must stay true; the provider is
    not, because naming one would name a call that did not happen.
    """
    cache = MemoryResponseCache()
    cache.put(keyed(), a_completion())

    served = cache.get(keyed())
    assert served is not None
    assert served.text == a_completion().text
    assert served.model == "a-model-2026", "who wrote the prose, so the log stays true"
    assert served.usage == Usage(), "no provider was contacted"
    assert served.provider == ""


def test_a_fallback_cannot_be_written_to_the_cache() -> None:
    """The execution decision, as a signature rather than a comment.

    A cached fallback is a wrong answer with a long life, and the operator's remedy for one
    — raise the ceiling, configure a key — would silently not work.
    """
    cache = MemoryResponseCache()
    fallback = Failure(kind=FailureKind.CEILING_REACHED, detail="no budget left")

    with pytest.raises(TypeError, match="never a cache entry"):
        cache.put(keyed(), fallback)  # type: ignore[arg-type]

    assert cache.get(keyed()) is None


def test_an_entry_is_scoped_to_a_lineage_and_another_lineage_misses() -> None:
    """R3's scoping half. A fork reads its parent's entries; an unrelated run does not."""
    cache = MemoryResponseCache({"child": "run-1", "run-1": "run-1", "stranger": "stranger"})
    cache.put(keyed(run_id="run-1"), a_completion())

    assert cache.get(keyed(run_id="child")) is not None, "one lineage, one situation"
    assert cache.get(keyed(run_id="stranger")) is None, "another timeline pays for its own"


# --- the lookup in front of the ceiling -----------------------------------


def cached(
    env: dict[str, str],
    provider: MockProvider,
    ceiling: Ceiling = TINY,
    cache: MemoryResponseCache | None = None,
    ledger: MemorySpendLedger | None = None,
) -> BoundedGateway:
    return BoundedGateway(
        modelgw.from_environment(env, transport=provider.transport),
        ceiling,
        ledger if ledger is not None else MemorySpendLedger(),
        cache if cache is not None else MemoryResponseCache(),
    )


async def test_the_second_time_a_situation_is_reached_the_provider_is_not_contacted() -> None:
    """M33 through the one call site. The answer is the same, and it cost nothing."""
    mock = answering(ok_payload(WIRE_OPENAI))
    gateway = cached(env_for("ollama"), mock, Ceiling(max_calls=10, max_tokens=10_000))

    first = await gateway.complete("run-1", PROMPT, keyed())
    gateway.keep()
    second = await gateway.complete("run-1", PROMPT, keyed())

    assert isinstance(first, Completion) and isinstance(second, Completion)
    assert second.text == first.text
    assert len(mock.requests) == 1, "the provider was asked once"

    reading = gateway.reading("run-1")
    assert reading.spend.calls == 1, "a hit is not a call"
    assert reading.spend.cache_hits == 1, "and the counter can say what it was"
    assert reading.spend.tokens == first.usage.total_tokens, "no tokens counted twice"


async def test_a_different_situation_in_the_same_run_misses() -> None:
    mock = answering(ok_payload(WIRE_OPENAI))
    gateway = cached(env_for("ollama"), mock, Ceiling(max_calls=10, max_tokens=10_000))

    await gateway.complete("run-1", PROMPT, keyed())
    gateway.keep()
    other = Prompt(system=PROMPT.system, turns=(Turn(role="user", text="Brief me on hiring."),))
    await gateway.complete("run-1", other, keyed(other))
    gateway.keep()

    assert len(mock.requests) == 2
    assert gateway.reading("run-1").spend.cache_hits == 0


async def test_a_hit_is_served_at_an_exhausted_ceiling_because_it_spends_nothing() -> None:
    """The ordering, and the reason it is the one chosen.

    The ceiling bounds what a run *spends*; a hit contacts nothing. Refusing to serve one
    would withhold a briefing already paid for. It does not weaken the off switch either:
    entries are scoped to a lineage, so a lineage that never called anything has none —
    which is every lineage started at a ceiling of zero, asserted below.
    """
    mock = answering(ok_payload(WIRE_OPENAI))
    cache = MemoryResponseCache()
    gateway = cached(env_for("ollama"), mock, Ceiling(max_calls=1, max_tokens=10_000), cache)

    assert isinstance(await gateway.complete("run-1", PROMPT, keyed()), Completion)
    gateway.keep()
    assert gateway.reading("run-1").calls_exhausted

    served = await gateway.complete("run-1", PROMPT, keyed())
    assert isinstance(served, Completion), "an entry already paid for is still served"
    assert len(mock.requests) == 1

    # And a run that never called anything has nothing to be served, which is what setting
    # the ceiling to zero is for.
    silent = cached(env_for("ollama"), mock, Ceiling(max_calls=0, max_tokens=10_000))
    refused = await silent.complete("run-2", PROMPT, keyed(run_id="run-2"))
    assert isinstance(refused, Failure) and refused.kind is FailureKind.CEILING_REACHED


async def test_a_call_that_failed_leaves_the_cache_empty_so_raising_a_ceiling_recovers() -> None:
    """Covers the execution decision at the level a run meets it.

    Every way a call can fail — a 500 here, and a refusal by the ceiling — writes nothing.
    So the operator's remedy works: with the provider fixed, the next attempt is a real call
    rather than a stored fallback replayed forever.
    """
    cache = MemoryResponseCache()
    broken = answering({"error": "boom"}, status=500)
    gateway = cached(env_for("ollama"), broken, Ceiling(max_calls=1, max_tokens=10_000), cache)

    failed = await gateway.complete("run-1", PROMPT, keyed())
    gateway.keep()
    assert isinstance(failed, Failure)
    assert cache.get(keyed()) is None, "a fallback is never an entry, even if the caller keeps"

    refused = await gateway.complete("run-1", PROMPT, keyed())
    gateway.keep()
    assert isinstance(refused, Failure) and refused.kind is FailureKind.CEILING_REACHED
    assert cache.get(keyed()) is None, "and neither is a ceiling refusal"

    fixed = answering(ok_payload(WIRE_OPENAI))
    recovered = cached(env_for("ollama"), fixed, Ceiling(max_calls=5, max_tokens=10_000), cache)
    assert isinstance(await recovered.complete("run-1", PROMPT, keyed()), Completion)


async def test_an_answer_the_caller_did_not_accept_is_never_written() -> None:
    """The deferred write, and the failure it exists to prevent.

    A reply that ranks the options is a perfectly good HTTP response, so the gateway cannot tell
    it from a usable one — only the guards can, and they run after this. Writing on arrival would
    serve that refusal back on every future visit to the situation, with "switch to a better
    model" as the remedy that changes nothing: the address is the situation, not the model.
    """
    mock = answering(ok_payload(WIRE_OPENAI))
    cache = MemoryResponseCache()
    gateway = cached(env_for("ollama"), mock, Ceiling(max_calls=10, max_tokens=10_000), cache)

    assert isinstance(await gateway.complete("run-1", PROMPT, keyed()), Completion)
    assert cache.by_lineage == {}, "answered, staged, and not yet anyone's to keep"

    await gateway.complete("run-1", PROMPT, keyed())
    assert len(mock.requests) == 2, "so the next visit asks again rather than serving a refusal"

    gateway.keep()
    assert cache.get(keyed()) is not None, "and an accepted answer is kept when it is accepted"


async def test_keeping_twice_or_keeping_a_hit_writes_nothing_extra() -> None:
    """`keep()` is safe to call unconditionally, which is what makes the call site one line."""
    mock = answering(ok_payload(WIRE_OPENAI))
    cache = MemoryResponseCache()
    gateway = cached(env_for("ollama"), mock, Ceiling(max_calls=10, max_tokens=10_000), cache)

    await gateway.complete("run-1", PROMPT, keyed())
    gateway.keep()
    gateway.keep()
    assert len(cache.by_lineage) == 1

    served = await gateway.complete("run-1", PROMPT, keyed())
    assert isinstance(served, Completion)
    gateway.keep()
    assert len(cache.by_lineage) == 1, "a hit was already in there"


async def test_a_keyless_run_writes_nothing_and_is_served_nothing() -> None:
    """M20 again, through the cache: with no provider there is nothing to keep."""
    cache = MemoryResponseCache()
    gateway = BoundedGateway(
        modelgw.from_environment({}), Ceiling(), MemorySpendLedger(), cache
    )

    assert isinstance(await gateway.complete("run-1", PROMPT, keyed()), Failure)
    gateway.keep()
    assert cache.by_lineage == {}


async def test_a_caller_with_no_situation_to_address_neither_reads_nor_writes() -> None:
    """`key` is optional so that every other caller of this class stays exactly as it was.

    The address is the assembled prompt, the scope and the purpose, and only the bench knows
    those. A caller that passes nothing gets the ceiling alone.
    """
    cache = MemoryResponseCache()
    mock = answering(ok_payload(WIRE_OPENAI))
    gateway = cached(env_for("ollama"), mock, Ceiling(max_calls=10, max_tokens=10_000), cache)

    await gateway.complete("run-1", PROMPT)
    gateway.keep()
    await gateway.complete("run-1", PROMPT)
    gateway.keep()

    assert len(mock.requests) == 2
    assert cache.by_lineage == {}


# --- the cache in the store: it survives a restart, and it is per lineage --


@pytest.fixture
def cache_store(spend_store):
    """The provisioned store from the counter's fixture, with a cache on the same file."""
    from agents.main import StoreResponseCache

    store, handle, url, _ledger = spend_store
    cache = StoreResponseCache(url)
    try:
        yield store, handle, url, cache
    finally:
        cache.dispose()


def test_the_cache_survives_a_restart(cache_store) -> None:
    """The half a dictionary cannot do, and the reason the table exists at all.

    A cache that emptied on restart would make the one expensive thing in the store the one
    thing that did not survive one — and re-earning it costs real money on the operator's own
    key.
    """
    from agents.main import StoreResponseCache

    _store, _handle, url, cache = cache_store
    cache.put(keyed(run_id=PARENT), a_completion())

    cache.dispose()
    after = StoreResponseCache(url)
    try:
        served = after.get(keyed(run_id=PARENT))
        assert served is not None and served.text == a_completion().text
    finally:
        after.dispose()


def test_a_rules_version_change_evicts_rather_than_serving(cache_store) -> None:
    """Advice assembled under different tuning is not the same advice.

    Two defences, and the order between them is the point: the *lookup* filters on the rules
    version, so nothing stale is served in the window between a change and the next restart,
    and the startup sweep then reclaims the rows. A sweep alone would have made housekeeping
    the thing correctness rested on.
    """
    from agents.main import StoreResponseCache

    _store, _handle, url, cache = cache_store
    cache.put(keyed(run_id=PARENT), a_completion())
    assert cache.get(keyed(run_id=PARENT)) is not None

    retuned = StoreResponseCache(url, rules_version="rules-after-a-tuning-change")
    try:
        assert retuned.get(keyed(run_id=PARENT)) is None, "refused before any sweep runs"
        assert retuned.evict_other_rules_versions() == 1
        assert cache.get(keyed(run_id=PARENT)) is None, "and the row is gone"
    finally:
        retuned.dispose()


def test_a_fork_reaches_its_parents_entry_once_the_lineage_root_is_copied(cache_store) -> None:
    """M33's fork half, and the one line of it that is not this unit's to write.

    `fork_run` sets a child's `lineage_root_id` to its own id, so today a fork misses its
    parent's entries — U16 owns copying the parent's root, and the assertion before the update
    below is what that unit will change. Pinning both halves here means the behaviour is
    specified before the unit that produces it lands, the way U9 pinned the spend aggregate.
    """
    from sqlalchemy import update

    from logschema import runs as runs_table

    store, handle, _url, cache = cache_store
    cache.put(keyed(run_id=PARENT), a_completion())

    store.fork_run(parent_run_id=PARENT, at_seq=1, child_run_id=CHILD, lease_handle=handle)
    assert cache.get(keyed(run_id=CHILD)) is None, "its own root until U16 copies the parent's"

    with store.engine.begin() as connection:
        connection.execute(
            update(runs_table).where(runs_table.c.run_id == CHILD).values(lineage_root_id=PARENT)
        )

    served = cache.get(keyed(run_id=CHILD))
    assert served is not None and served.text == a_completion().text
    assert cache.get(keyed(run_id=CHILD, scope=A_GRANTED_SCOPE)) is None, (
        "and only for the situation it was written under"
    )


def test_two_siblings_at_one_tick_with_different_options_do_not_share_an_entry(
    cache_store,
) -> None:
    """One lineage, two branches, two situations.

    They share a root, so nothing about the scoping keeps them apart — what does is that the
    option each took is in the evidence, so the assembled prompt differs, so the address does.
    """
    from sqlalchemy import update

    from logschema import runs as runs_table

    store, handle, _url, cache = cache_store
    sibling = "run-sibling"
    store.fork_run(parent_run_id=PARENT, at_seq=1, child_run_id=CHILD, lease_handle=handle)
    store.fork_run(parent_run_id=PARENT, at_seq=1, child_run_id=sibling, lease_handle=handle)
    with store.engine.begin() as connection:
        connection.execute(
            update(runs_table)
            .where(runs_table.c.run_id.in_([CHILD, sibling]))
            .values(lineage_root_id=PARENT)
        )

    took_the_first = Prompt(
        system=PROMPT.system, turns=(Turn(role="user", text="The post was rewritten."),)
    )
    took_the_second = Prompt(
        system=PROMPT.system, turns=(Turn(role="user", text="The post was left as it was."),)
    )
    cache.put(keyed(took_the_first, run_id=CHILD), a_completion("BRIEFING: A."))

    assert cache.get(keyed(took_the_first, run_id=sibling)) is not None, "same situation"
    assert cache.get(keyed(took_the_second, run_id=sibling)) is None, "different one"


def test_an_entry_carries_the_answer_and_never_the_question(cache_store) -> None:
    """R6, and the reason only a digest is stored.

    A table holding assembled prompts would be a second copy of the run's world sitting
    outside the append-only log, and the prompts carry the whole retrieved context.
    """
    from sqlalchemy import select

    from logschema import model_cache

    store, _handle, _url, cache = cache_store
    secret_situation = Prompt(
        system=PROMPT.system,
        turns=(Turn(role="user", text="[EVIDENCE] seq 9 | day 5 | WORK_ASSIGNED [/EVIDENCE]"),),
    )
    cache.put(keyed(secret_situation, run_id=PARENT), a_completion())

    with store.engine.connect() as connection:
        row = connection.execute(select(model_cache)).mappings().one()

    written = json.dumps({key: str(value) for key, value in row.items()})
    assert "WORK_ASSIGNED" not in written, "the question is not kept, only its digest"
    assert RAW_KEY not in written and "ollama" not in written
    assert row["purpose"] == str(Purpose.DIRECTOR_STATEMENT), "readable without recomputing"
    assert row["model_identity"] == "a-model-2026"


def test_a_write_for_a_run_the_store_does_not_know_is_dropped_rather_than_attempted(
    cache_store, caplog
) -> None:
    """An entry keyed to a lineage that does not exist could never be matched, and never
    removed with the lineage it claims to belong to.

    Dropped where it is decided rather than attempted and caught, which is the assertion that
    distinguishes the two: without the check the insert would violate the foreign key, be
    swallowed by the same `except` that covers a store being down, and log a warning saying the
    cache could not be written — an operator would go looking for a broken database.
    """
    _store, _handle, _url, cache = cache_store

    with caplog.at_level(logging.WARNING):
        cache.put(keyed(run_id="run-that-never-existed"), a_completion())

    assert cache.get(keyed(run_id="run-that-never-existed")) is None
    assert "could not write the response cache" not in caplog.text, (
        "a run this store does not know is not a database failure"
    )


def test_a_cache_write_that_fails_leaves_the_run_correct_and_the_log_unchanged(
    cache_store, caplog
) -> None:
    """A failed write costs a provider call. It does not cost a briefing, a counter or a row.

    Pointed at a database it cannot open, which is what an operator's store being down looks
    like from this thread — and this thread's whole job is to be optional.
    """
    from sqlalchemy import func, select

    from agents.main import StoreResponseCache
    from logschema import event_log

    store, _handle, _url, _cache = cache_store
    with store.engine.connect() as connection:
        before = connection.execute(select(func.count()).select_from(event_log)).scalar_one()

    unreachable = StoreResponseCache("sqlite:////no-such-directory-for-u12/cache.sqlite3")
    try:
        with caplog.at_level(logging.WARNING):
            unreachable.put(keyed(run_id=PARENT), a_completion())
            assert unreachable.get(keyed(run_id=PARENT)) is None, "an unreadable cache is a miss"
    finally:
        unreachable.dispose()

    assert "could not write the response cache" in caplog.text
    with store.engine.connect() as connection:
        after = connection.execute(select(func.count()).select_from(event_log)).scalar_one()
    assert after == before, "the log is not where a cache failure lands"


# --- the verification: the property lives in the log ----------------------


def test_a_fold_cannot_reach_a_cache_at_all() -> None:
    """Why emptying the table cannot change a state, stated structurally.

    The plan's verification is that emptying the cache between a fork and a re-fold changes
    nothing about the resulting states. The reason it cannot is that the fold is `simcore`,
    the cache is `modelgw`, and `simcore` does not import the model gateway — a rule
    `test_import_boundaries.py` holds for the whole package and this restates for the one
    module whose absence is the property.
    """
    offenders = []
    for path in sorted((BACKEND / "packages" / "simcore").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = {node.module.split(".")[0]}
            else:
                continue
            if "modelgw" in roots:
                offenders.append(path.name)

    assert not offenders, (
        "the fold reached the model gateway, so a cached response could enter a replayed "
        f"state: {sorted(set(offenders))}"
    )


def test_emptying_the_cache_changes_nothing_about_what_the_log_folds_to() -> None:
    """The plan's verification, run rather than argued.

    A run whose log carries a statement, a cache holding the answer to that same situation,
    and the same fold before and after the cache is emptied. The hashes match because a
    statement is an event: the fork copies the rows and the fold reads them, so what the
    cache saves is a provider call and never a fact.
    """
    from simcore import hashing
    from simcore import log as folder
    from simcore import step as sim
    from test_pending_input import Recorder

    recorder = Recorder()
    request = recorder.open_a_checkpoint_in_person()
    recorder.record(
        sim.receive_answer(
            recorder.state, request.request_id, recorder.statement_answer(request)
        )
    )
    # Past the landing tick, so the statement has been applied rather than merely queued: an
    # answer waiting in `pending` would fold identically for a duller reason.
    recorder.advance_until(lambda state: request.request_id not in state.pending)

    cache = MemoryResponseCache()
    cache.put(keyed(run_id="run-folded"), a_completion())

    def refolded() -> str:
        folded = folder.fold(
            recorder.log, at_live_head=False, strict=True, through_tick=recorder.state.tick
        )
        return hashing.state_hash(sim.snapshot(folded.state)).overall

    before = refolded()
    assert before == recorder.hash, "sanity: the fold reproduces the live run"

    cache.by_lineage.clear()

    assert refolded() == before, "the cache is authoritative for nothing"
    assert any(
        "briefing" in envelope.decoded_payload().get("answer", {})
        for envelope in recorder.log
    ), "and the statement itself is still in the log, where it always was"
