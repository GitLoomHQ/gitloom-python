"""The drop-in: wrap the OpenAI or Anthropic client you already use, and its
call sites gain memory and managed conversations — one field richer.

    openai = gitloom.wrap(OpenAI(), memory)
    res = openai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],   # only the NEW messages
        conversation="chat-42",                         # <- the one change
    )

The stored conversation supplies the window, memory supplies the context, both
turns are stored with the response's usage, and compaction runs on cadence.
Everything else on the client passes through untouched.
"""

from __future__ import annotations

from typing import Any, Optional

from .conversation import Conversation
from .tokens import text_of


def wrap(client: Any, memory: Any, **conversation_defaults: Any) -> Any:
    """Wrap an OpenAI-shaped (`.chat.completions.create`) or Anthropic-shaped
    (`.messages.create`) client. `conversation_defaults` configure the
    conversations the wrapper opens: summarize=, compact_every=, namespace=…"""
    return _Wrapped(client, memory, conversation_defaults)


class _Wrapped:
    def __init__(self, client: Any, memory: Any, defaults: dict[str, Any]):
        self._client = client
        self._memory = memory
        self._defaults = defaults
        self._conversations: dict[str, Conversation] = {}

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._client, name)
        if name == "chat":
            return _ChatProxy(value, self, "openai")
        if name == "messages" and hasattr(value, "create"):
            return _CreateProxy(value, self, "anthropic")
        return value

    # -- the loop, hidden --------------------------------------------------

    def _conversation(self, conv_id: str, model: str) -> Conversation:
        conv = self._conversations.get(conv_id)
        if conv is None:
            conv = self._memory.conversation(conv_id, model=model, **self._defaults)
            conv.load()
            self._conversations[conv_id] = conv
        return conv

    def _call(self, create: Any, flavor: str, kwargs: dict[str, Any]) -> Any:
        conv_id = kwargs.pop("conversation", None)
        if not conv_id:
            return create(**kwargs)

        conv = self._conversation(conv_id, str(kwargs.get("model", "")))
        fresh = list(kwargs.get("messages") or [])
        last_user = next((m for m in reversed(fresh) if m.get("role") == "user"), None)

        context = None
        if last_user is not None:
            try:
                context = conv.with_context(text_of(last_user))
            except Exception:  # noqa: BLE001 — memory failing must not fail the call
                context = None

        window = conv.for_model()
        if flavor == "anthropic":
            # Anthropic takes system content as a top-level field; the
            # compaction summary and memory context move there.
            systems = [kwargs["system"]] if kwargs.get("system") else []
            chat = []
            for m in window + fresh:
                if m.get("role") == "system":
                    if isinstance(m.get("content"), str):
                        systems.append(m["content"])
                else:
                    chat.append(m)
            if context:
                systems.append(context["content"])
            if systems:
                kwargs["system"] = "\n\n".join(systems)
            kwargs["messages"] = chat
        else:
            merged = window + fresh
            head = [m for m in merged if m.get("role") == "system"]
            tail = [m for m in merged if m.get("role") != "system"]
            kwargs["messages"] = head + ([context] if context else []) + tail

        response = create(**kwargs)

        reply = _assistant_text(response)
        turns = list(fresh)
        if reply:
            turns.append({"role": "assistant", "content": reply})
        conv.append(turns, usage=getattr(response, "usage", None))
        return response


class _ChatProxy:
    def __init__(self, chat: Any, wrapped: _Wrapped, flavor: str):
        self._chat = chat
        self._wrapped = wrapped
        self._flavor = flavor

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._chat, name)
        if name == "completions":
            return _CreateProxy(value, self._wrapped, self._flavor)
        return value


class _CreateProxy:
    def __init__(self, target: Any, wrapped: _Wrapped, flavor: str):
        self._target = target
        self._wrapped = wrapped
        self._flavor = flavor

    def __getattr__(self, name: str) -> Any:
        value = getattr(self._target, name)
        if name == "create":
            return lambda **kwargs: self._wrapped._call(value, self._flavor, kwargs)
        return value


def _assistant_text(response: Any) -> Optional[str]:
    choices = getattr(response, "choices", None)
    if choices:
        msg = getattr(choices[0], "message", None)
        if msg is not None:
            return getattr(msg, "content", None)
    content = getattr(response, "content", None)
    if isinstance(content, list):
        return "".join(
            getattr(b, "text", "") for b in content if getattr(b, "type", "") == "text"
        )
    return None
