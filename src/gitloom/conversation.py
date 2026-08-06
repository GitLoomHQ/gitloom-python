"""A stored chat with a rolling context window.

The loop a developer writes is the provider's own — append what was said, ask
``for_model()`` for the messages, call OpenAI or Anthropic, append the reply
with its usage object. Everything else (deciding when the window is full,
summarizing what falls out, handing those turns to memory) happens without
being asked.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .tokens import context_limit, text_of, total_tokens, usage_total

Summarizer = Callable[[list[dict[str, Any]]], str]


class Conversation:
    """Obtain from ``Gitloom.conversation()`` or ``load_conversation()``."""

    def __init__(self, client: Any, conversation_id: str, options: dict[str, Any]):
        self.id = conversation_id
        self.branch = "main"
        self.title = ""
        self._client = client
        self._opts = options
        self._history: list[dict[str, Any]] = []
        self._summary = ""
        self._next_seq = 0
        self._first_live_seq = 0
        self._exchanges = 0
        self._reported_tokens = 0

    # -- construction ------------------------------------------------------

    @classmethod
    def _create(cls, client: Any, conversation_id: str, options: dict[str, Any]) -> "Conversation":
        conv = cls(client, conversation_id, options)
        body: dict[str, Any] = {
            "id": conversation_id,
            "namespace": options.get("namespace") or client.namespace,
        }
        if options.get("title"):
            body["title"] = options["title"]
        if options.get("model"):
            body["model"] = options["model"]
        res = client._request("POST", "/v1/conversations", json=body)
        conv.branch = res["branch"]
        conv._next_seq = res["next_seq"]
        conv.title = options.get("title", "")
        return conv

    def load(
        self, *, full: bool = False, branch: Optional[str] = None, at: Optional[int] = None
    ) -> "Conversation":
        """Replace local state with the server's."""
        params: dict[str, Any] = {}
        if full:
            params["full"] = "1"
        if branch:
            params["branch"] = branch
        if at is not None:
            params["at"] = at
        res = self._client._request("GET", f"/v1/conversations/{self.id}", params=params)
        self.branch = res["branch"]
        self.title = res.get("title", "")
        self._next_seq = res["next_seq"]
        msgs = res.get("messages") or []
        self._history = [_strip(m) for m in msgs]
        self._first_live_seq = msgs[0]["seq"] if msgs else res["next_seq"]
        cmp = res.get("compaction")
        self._summary = cmp["summary"] if cmp else ""
        self._reported_tokens = 0
        if res.get("model") and not self._opts.get("model"):
            self._opts["model"] = res["model"]
        return self

    # -- the loop ----------------------------------------------------------

    @property
    def seq(self) -> int:
        """Sequence the next appended message will take."""
        return self._next_seq

    def messages(self) -> list[dict[str, Any]]:
        """What is held locally: the summary of what came before, then live turns."""
        out = []
        if self._summary:
            out.append({"role": "system", "content": f"Earlier in this conversation: {self._summary}"})
        out.extend(self._history)
        return out

    def append(
        self, messages: dict[str, Any] | list[dict[str, Any]], *, usage: Any = None
    ) -> "Conversation":
        """Store messages, compacting first when the window or the exchange
        cadence demand it.

        ``usage`` is the provider response's usage object (OpenAI or Anthropic,
        object or dict) — the real count of this conversation's tokens, which
        beats the estimator for timing compaction.
        """
        batch = messages if isinstance(messages, list) else [messages]
        if not batch:
            return self

        reported = usage_total(usage)
        if reported:
            self._reported_tokens = reported
        self._exchanges += sum(1 for m in batch if m.get("role") == "assistant")

        if self._opts.get("summarize") and (self._would_overflow(batch) or self._cadence_due()):
            self.compact()

        wire = [self._to_wire(m) for m in batch]
        res = self._client._request(
            "POST",
            f"/v1/conversations/{self.id}/messages",
            json={"branch": self.branch, "messages": wire},
        )
        self._history.extend(batch)
        self._next_seq = res["next_seq"]
        return self

    def for_model(self) -> list[dict[str, Any]]:
        """The messages to send, guaranteed inside the model's window —
        provider-shaped, so they pass straight to OpenAI or Anthropic."""
        return self._fitted()

    def with_context(self, user_message: str) -> Optional[dict[str, str]]:
        """Memory bearing on the user's message, per the conversation's memory
        mode ('query' by default, 'tools', 'off'). None when there is nothing
        relevant or retrieval is configured away."""
        mode = self._opts.get("memory", "query")
        if mode != "query" or not user_message.strip():
            return None
        return self._client.context(user_message, namespace=self._opts.get("namespace"))

    # -- compaction --------------------------------------------------------

    def compact(self) -> Optional[dict[str, Any]]:
        """Summarize what the window can no longer hold and hand the evicted
        turns to memory. The summary is produced locally by the caller's
        ``summarize`` function; GitLoom never sees the conversation to compact
        it — it receives the evicted turns for ingestion, so the flattened
        detail stays recallable."""
        summarize: Optional[Summarizer] = self._opts.get("summarize")
        if summarize is None:
            raise GitloomUsageError(
                "compaction needs a summarize= option; without one the evicted turns would be dropped"
            )
        evicted = self._evictable()
        if not evicted:
            # The estimator sees room, but the trigger knew better — the
            # cadence fired, or the provider reported more than the estimate.
            # Evict all but the latest exchange; always at least one message.
            if len(self._history) < 2:
                return None
            keep = min(2, len(self._history) - 1)
            evicted = self._history[: len(self._history) - keep]

        summary = summarize(evicted)
        start = self._first_live_seq
        end = start + len(evicted) - 1
        self._client._request(
            "POST",
            f"/v1/conversations/{self.id}/compact",
            json={"branch": self.branch, "summary": summary, "from_seq": start, "to_seq": end},
        )
        self._summary = f"{self._summary}\n\nThen: {summary}" if self._summary else summary
        self._history = self._history[len(evicted):]
        self._first_live_seq = end + 1
        self._exchanges = 0
        self._reported_tokens = 0
        return {"summary": summary, "from": start, "to": end}

    # -- branching, edits, titles -----------------------------------------

    def rewind(self, seq: int, *, name: Optional[str] = None) -> "Conversation":
        """Fork a new branch after ``seq`` and switch to it. Nothing is
        deleted — rewinding past a compaction is an ordinary read."""
        body: dict[str, Any] = {"to": seq, "branch": self.branch}
        if name:
            body["name"] = name
        res = self._client._request("POST", f"/v1/conversations/{self.id}/rewind", json=body)
        return self.load(full=True, branch=res["branch"], at=seq)

    def edit(self, seq: int, message: dict[str, Any], *, name: Optional[str] = None) -> "Conversation":
        """Replace the message at ``seq`` on a NEW branch — the edit every chat
        UI offers. The original line is untouched."""
        body: dict[str, Any] = {"seq": seq, "branch": self.branch, "message": self._to_wire(message)}
        if name:
            body["name"] = name
        res = self._client._request("POST", f"/v1/conversations/{self.id}/edit", json=body)
        return self.load(full=True, branch=res["branch"])

    def edit_in_place(self, seq: int, content: str) -> None:
        """Rewrite the message at ``seq`` on this branch, destroying the
        original — the one edit that does not fork, for content that must stop
        existing (a leaked secret, PII)."""
        self._client._request(
            "PATCH",
            f"/v1/conversations/{self.id}/messages/{seq}",
            json={"branch": self.branch, "content": content},
        )
        idx = seq - self._first_live_seq
        if 0 <= idx < len(self._history):
            self._history[idx]["content"] = content

    def set_title(self, title: str) -> None:
        """Name the conversation, overwriting any automatic title."""
        self._client._request("PATCH", f"/v1/conversations/{self.id}", json={"title": title})
        self.title = title

    def branches(self) -> list[dict[str, Any]]:
        """Every line of this conversation."""
        res = self._client._request("GET", f"/v1/conversations/{self.id}/branches")
        return res.get("branches") or []

    def ingest(self, *, from_seq: int = 0, to_seq: int = 0) -> None:
        """Hand a range of turns to memory without compacting."""
        self._client._request(
            "POST",
            f"/v1/conversations/{self.id}/ingest",
            json={"branch": self.branch, "from_seq": from_seq, "to_seq": to_seq},
        )

    # -- internals ---------------------------------------------------------

    def _to_wire(self, m: dict[str, Any]) -> dict[str, Any]:
        """One message for storage: multimodal content becomes a parts array
        plus flattened text; any part still carrying bytes is uploaded first so
        the stored message references the attachment."""
        out: dict[str, Any] = {"role": m.get("role")}
        for k in ("name", "tool_calls", "tool_call_id"):
            if m.get(k):
                out[k] = m[k]
        content = m.get("content")
        if isinstance(content, list):
            parts = []
            for p in content:
                data = p.get("data") if isinstance(p, dict) else None
                if isinstance(data, dict) and data.get("base64"):
                    import base64 as _b64

                    info = self._client.upload_media(
                        data["media_type"], _b64.b64decode(data["base64"])
                    )
                    clean = {k: v for k, v in p.items() if k != "data"}
                    clean["media_id"] = info["id"]
                    parts.append(clean)
                else:
                    parts.append(p)
            out["parts"] = parts
            out["content"] = text_of(m)
        else:
            out["content"] = content or ""
        return out

    def _budget(self) -> int:
        ceiling = self._opts.get("max_tokens") or int(context_limit(self._opts.get("model", "")) * 0.9)
        return ceiling - self._opts.get("reserve_for_reply", 0)

    def _fitted(self) -> list[dict[str, Any]]:
        budget = self._budget()
        model = self._opts.get("model", "")
        msgs = self.messages()
        # The summary system message is never evicted; live turns drop oldest first.
        start = 1 if self._summary else 0
        while len(msgs) - start > 1 and total_tokens(msgs, model) > budget:
            del msgs[start]
        return msgs

    def _evictable(self) -> list[dict[str, Any]]:
        fitted_live = len(self._fitted()) - (1 if self._summary else 0)
        return self._history[: len(self._history) - fitted_live]

    def _would_overflow(self, batch: list[dict[str, Any]]) -> bool:
        model = self._opts.get("model", "")
        threshold = self._budget() * self._opts.get("compact_at", 0.85)
        held = self._reported_tokens or total_tokens(self.messages(), model)
        return held + total_tokens(batch, model) > threshold

    def _cadence_due(self) -> bool:
        every = self._opts.get("compact_every", 5)
        return every > 0 and self._exchanges >= every


class GitloomUsageError(Exception):
    """The SDK was asked to do something its configuration cannot support."""


def _strip(m: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"role": m.get("role"), "content": m.get("content")}
    for k in ("name", "tool_calls", "tool_call_id"):
        if m.get(k):
            out[k] = m[k]
    return out
