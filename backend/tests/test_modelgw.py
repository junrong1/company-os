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
from modelgw import anthropic_native, openai_compatible
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
