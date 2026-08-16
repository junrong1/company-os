"""The OpenAI-compatible wire: `POST {base}/chat/completions`.

Seven of the eight providers in the table speak this, which is the whole reason
this package is two files of wire code rather than a dependency. OpenAI, Azure,
OpenRouter, Ollama, LM Studio, vLLM and SGLang differ in a base URL, an auth header
name and a token-limit field name — all three are rows in `PROVIDERS`, none of them
is a branch here.

Pure functions, no transport. The gateway does the sending and attaches the auth
header; this module never sees the key, which is why a `WireRequest` can be logged
or asserted on without anything to redact.
"""

from __future__ import annotations

from typing import Any

from modelgw.config import GatewayConfig, Prompt, WireMismatch, WireReply, WireRequest

PATH = "chat/completions"


def request_for(config: GatewayConfig, prompt: Prompt) -> WireRequest:
    messages: list[dict[str, str]] = []
    if prompt.system.strip():
        # "system", not "developer". The newer role name is OpenAI's alone and every
        # other server in the table would either ignore it or reject the request.
        messages.append({"role": "system", "content": prompt.system})
    messages.extend({"role": turn.role, "content": turn.text} for turn in prompt.turns)

    body: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        config.spec.max_output_field: prompt.max_output_tokens,
        "temperature": prompt.temperature,
        # Explicit, because a server that defaults to streaming would hand us an
        # SSE body that `response.json()` cannot read, and the failure would look
        # like a malformed provider rather than a missing field.
        "stream": False,
    }

    return WireRequest(method="POST", url=config.endpoint(PATH), headers={}, body=body)


def read(payload: Any) -> WireReply:
    """Pull the text and the usage out of a 2xx body, or say the shape is wrong."""
    if not isinstance(payload, dict):
        raise WireMismatch(f"expected a JSON object, got {type(payload).__name__}")

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        # An OpenAI-compatible server signals a refusal this way too, with an
        # `error` object and no choices; the gateway reports the status code it came
        # with rather than quoting the object.
        raise WireMismatch("no choices in the response")

    first = choices[0]
    if not isinstance(first, dict):
        raise WireMismatch("choices[0] is not an object")

    message = first.get("message")
    if not isinstance(message, dict):
        raise WireMismatch("choices[0].message is missing")

    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return WireReply(
        text=_content_text(message.get("content")),
        input_tokens=_count(usage.get("prompt_tokens")),
        output_tokens=_count(usage.get("completion_tokens")),
        model=str(payload.get("model") or ""),
    )


def _content_text(content: Any) -> str:
    """Accept both shapes servers actually return.

    A string is what OpenAI returns. A list of content parts is what several
    self-hosted servers return, and treating that as malformed would fail a call
    that in fact succeeded.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") in {None, "text"}
        ]
        return "".join(str(part) for part in parts)
    raise WireMismatch(f"content is a {type(content).__name__}, not text")


def _count(value: Any) -> int:
    return int(value) if isinstance(value, int) and value >= 0 else 0
