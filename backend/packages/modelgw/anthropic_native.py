"""The Anthropic wire: `POST {base}/v1/messages`.

The second of the two shapes, and the reason there are two. Three differences make
it a code path rather than another row in the provider table: the system
instruction sits outside `messages`, the reply is a list of content blocks rather
than a message with a string, and `max_tokens` is required rather than optional.
Config could not express any of those without becoming a template language.

Same posture as the OpenAI-compatible module: pure functions, no transport, and no
sight of the key.
"""

from __future__ import annotations

from typing import Any

from modelgw.config import GatewayConfig, Prompt, WireMismatch, WireReply, WireRequest

PATH = "v1/messages"

#: Pinned like every other version in this repo. The API requires the header and
#: treats it as the contract for the response shape `read()` below expects, so a
#: floating value would be an untested wire change arriving without a commit.
ANTHROPIC_VERSION = "2023-06-01"


def request_for(config: GatewayConfig, prompt: Prompt) -> WireRequest:
    body: dict[str, Any] = {
        "model": config.model,
        # Required by this wire, which is why `Prompt` carries it as a validated
        # field rather than an optional one.
        config.spec.max_output_field: prompt.max_output_tokens,
        "messages": [{"role": turn.role, "content": turn.text} for turn in prompt.turns],
        "temperature": prompt.temperature,
        "stream": False,
    }
    if prompt.system.strip():
        body["system"] = prompt.system

    return WireRequest(
        method="POST",
        url=config.endpoint(PATH),
        headers={"anthropic-version": ANTHROPIC_VERSION},
        body=body,
    )


def read(payload: Any) -> WireReply:
    if not isinstance(payload, dict):
        raise WireMismatch(f"expected a JSON object, got {type(payload).__name__}")

    blocks = payload.get("content")
    if not isinstance(blocks, list):
        raise WireMismatch("no content blocks in the response")

    text_parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            raise WireMismatch("a content block is not an object")
        # A reply may carry blocks this package does not read — a tool use, a
        # thinking block. Skipping them rather than refusing is what keeps a model
        # that volunteers one from looking like a broken provider; a reply with no
        # text block at all lands as an empty response, which the gateway types.
        if block.get("type") == "text":
            text_parts.append(str(block.get("text", "")))

    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return WireReply(
        text="".join(text_parts),
        input_tokens=_count(usage.get("input_tokens")),
        output_tokens=_count(usage.get("output_tokens")),
        model=str(payload.get("model") or ""),
    )


def _count(value: Any) -> int:
    return int(value) if isinstance(value, int) and value >= 0 else 0
