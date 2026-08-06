"""Token counting and per-model context limits.

Every guarantee about not overflowing a window rests on a number produced
here, so being wrong in the unsafe direction is the one unacceptable failure.
The estimator overcounts on purpose; real counts from provider usage objects
override it everywhere they exist.
"""

from __future__ import annotations

from typing import Any

_CONTEXT_LIMITS: list[tuple[str, int]] = [
    ("claude-opus-5", 1_000_000),
    ("claude-sonnet-5", 1_000_000),
    ("claude-fable-5", 1_000_000),
    ("claude-haiku-4-5", 200_000),
    ("claude-", 200_000),
    ("gpt-5", 400_000),
    ("gpt-4.1", 1_047_576),
    ("gpt-4o", 128_000),
    ("gpt-4", 8_192),
    ("o1", 200_000),
    ("o3", 200_000),
    ("gemini-1.5-pro", 2_000_000),
    ("gemini-", 1_000_000),
    ("llama-3", 128_000),
    ("mistral-", 32_000),
]

_CHARS_PER_TOKEN = 3.5
_PER_MESSAGE_OVERHEAD = 4
_PER_REQUEST_OVERHEAD = 3
# Above OpenAI's high-detail single-tile cost and near Anthropic's ceiling:
# wrong in the safe direction, like everything else here.
_PER_IMAGE_TOKENS = 1_100


def context_limit(model: str, fallback: int = 128_000) -> int:
    """The model's input window, by longest matching prefix."""
    m = model.lower()
    best, limit = -1, fallback
    for prefix, value in _CONTEXT_LIMITS:
        if m.startswith(prefix) and len(prefix) > best:
            best, limit = len(prefix), value
    return limit


def estimate_tokens(text: str, model: str = "") -> int:
    """Estimate tokens in a string. A ratio, not a tokenizer — the SDK stays
    dependency-light — absorbed by the safety margin."""
    if not text:
        return 0
    return int(len(text) / _CHARS_PER_TOKEN) + 1


def text_of(message: dict[str, Any]) -> str:
    """The flattened text of a message, whatever shape its content takes."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("text"))
    return ""


def message_tokens(message: dict[str, Any], model: str = "") -> int:
    """Tokens in one message, including structural overhead."""
    n = _PER_MESSAGE_OVERHEAD
    content = message.get("content")
    if isinstance(content, str):
        n += estimate_tokens(content, model)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                n += estimate_tokens(part["text"], model)
            else:
                # Anything that is not text is billed as an image: audio and
                # documents cost at least as much, keeping this an overcount.
                n += _PER_IMAGE_TOKENS
    for call in message.get("tool_calls") or []:
        fn = call.get("function", {}) if isinstance(call, dict) else {}
        n += estimate_tokens(fn.get("name", ""), model)
        n += estimate_tokens(fn.get("arguments", ""), model)
        n += _PER_MESSAGE_OVERHEAD
    return n


def total_tokens(messages: list[dict[str, Any]], model: str = "") -> int:
    """Tokens in a whole conversation."""
    return _PER_REQUEST_OVERHEAD + sum(message_tokens(m, model) for m in messages)


def usage_total(usage: Any) -> int:
    """The total from a provider usage object — OpenAI's attribute style,
    Anthropic's, or a plain dict; both spellings of the fields."""
    if usage is None:
        return 0

    def field(name: str) -> int:
        if isinstance(usage, dict):
            v = usage.get(name)
        else:
            v = getattr(usage, name, None)
        return v if isinstance(v, int) else 0

    total = field("total_tokens")
    if total:
        return total
    return (field("prompt_tokens") or field("input_tokens")) + (
        field("completion_tokens") or field("output_tokens")
    )
