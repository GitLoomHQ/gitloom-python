"""The GitLoom API client: memory, media, and the conversation factory."""

from __future__ import annotations

import base64
import os
from typing import Any, Optional

import httpx

from .memory import Skills, Vocab

DEFAULT_BASE_URL = "https://api.gitloom.cloud"


class GitloomError(Exception):
    """A refusal from the API, carrying its machine-readable code."""

    def __init__(self, code: str, message: str, status: int):
        super().__init__(f"{message} ({status} {code})")
        self.code = code
        self.message = message
        self.status = status


class Gitloom:
    """The client. `Gitloom()` reads GITLOOM_API_KEY from the environment.

    Writes are never retried: a retried write that half-succeeded
    double-charges the meter and double-stores the message.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        namespace: str = "default",
        timeout: float = 60.0,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.api_key = api_key or os.environ.get("GITLOOM_API_KEY", "")
        self.namespace = namespace
        self._vocab: Optional["Vocab"] = None
        self._skills: Optional["Skills"] = None
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {self.api_key}"},
        )

    # -- transport ---------------------------------------------------------

    def _request(self, method: str, path: str, *, json: Any = None, params: Any = None) -> Any:
        res = self._http.request(method, path, json=json, params=params)
        if res.status_code >= 400:
            raise _error_from(res)
        if not res.content:
            return None
        try:
            return res.json()
        except ValueError as e:
            raise GitloomError("bad_response", f"{method} {path} returned undecodable JSON", res.status_code) from e

    # -- memory ------------------------------------------------------------

    def remember(
        self,
        messages: list[dict[str, str]],
        *,
        namespace: Optional[str] = None,
        session_id: Optional[str] = None,
        date: Optional[str] = None,
    ) -> None:
        """Submit a conversation for ingestion. Asynchronous by design —
        extraction runs model calls the caller must not wait on."""
        body: dict[str, Any] = {"namespace": namespace or self.namespace, "messages": messages}
        if session_id:
            body["session_id"] = session_id
        if date:
            body["date"] = date
        self._request("POST", "/v1/memories", json=body)

    def recall(
        self,
        query: str,
        *,
        namespace: Optional[str] = None,
        limit: Optional[int] = None,
        mode: Optional[str] = None,
        tiers: Optional[list[str]] = None,
        paths: Optional[list[str]] = None,
        tags: Optional[list[str]] = None,
        tags_all: Optional[list[str]] = None,
        since: Any = None,
        until: Any = None,
        min_score: Optional[float] = None,
        context: Optional[bool] = None,
        detail: Optional[str] = None,
        include_expired: bool = False,
    ) -> dict[str, Any]:
        """Retrieve what is known that bears on the query.

        Every entry is one whole memory: ``content`` is the body, ``score`` a
        calibrated relevance in [0, 1] comparable across queries, and
        ``matched`` the arms that produced it — a memory matched only by
        ``graph`` is context that rode in beside a real match, not evidence.

        The filters narrow every retrieval arm server-side, so confining a
        query to a directory is a boundary rather than a cut made afterwards.
        ``mode`` of ``summary`` or ``agentic`` also returns an ``answer``; both
        meter as chats rather than reads.
        """
        params: dict[str, Any] = {"q": query, "namespace": namespace or self.namespace}
        if limit:
            params["limit"] = limit
        if mode and mode != "raw":
            params["mode"] = mode
        if tiers:
            params["tiers"] = ",".join(tiers)
        if paths:
            params["paths"] = ",".join(paths)
        if tags:
            params["tags"] = ",".join(tags)
        if tags_all:
            params["tags_all"] = ",".join(tags_all)
        if since is not None:
            params["since"] = _as_date(since)
        if until is not None:
            params["until"] = _as_date(until)
        if min_score is not None:
            params["min_score"] = min_score
        if context is False:
            params["context"] = "0"
        if detail:
            params["detail"] = detail
        if include_expired:
            params["include_expired"] = "1"
        res = self._request("GET", "/v1/retrieve", params=params) or {}
        res.setdefault("memories", [])
        return res

    def answer(self, query: str, *, agentic: bool = False, **kwargs: Any) -> dict[str, Any]:
        """One text answer to a question, from the memory.

        A fast model summarizes one retrieval by default; ``agentic=True`` lets
        a stronger model search the memory itself with tools and return its
        trace. The memories the answer rests on come back alongside. Both
        meter as chats, not reads.
        """
        res = self.recall(query, mode="agentic" if agentic else "summary", **kwargs)
        if not res.get("answer"):
            raise GitloomError("no_answer", "The model did not produce an answer", 0)
        return res

    def context(self, query: str, **kwargs: Any) -> Optional[dict[str, str]]:
        """Retrieval rendered as a system message, ready to prepend. None when
        nothing relevant is stored."""
        memories = self.recall(query, **kwargs).get("memories") or []
        if not memories:
            return None
        lines = "\n".join(f"- {m.get('content') or m.get('snippet', '')}" for m in memories)
        return {
            "role": "system",
            "content": (
                "What you already know about this user, from earlier conversations. "
                "Treat it as background, not as something they just said:\n" + lines
            ),
        }

    # -- vocabulary and skills ---------------------------------------------

    @property
    def vocab(self) -> "Vocab":
        """The namespace's custom vocabulary: learn, list, look up, forget."""
        if self._vocab is None:
            self._vocab = Vocab(self)
        return self._vocab

    @property
    def skills(self) -> "Skills":
        """Procedural know-how: store skills, find the one that fits a task."""
        if self._skills is None:
            self._skills = Skills(self)
        return self._skills

    def create_namespace(self, name: str) -> None:
        """Make a namespace exist. Idempotent."""
        self._request("POST", "/v1/namespaces", json={"namespace": name})

    # -- media -------------------------------------------------------------

    def upload_media(self, content_type: str, data: bytes) -> dict[str, Any]:
        """Store one attachment (images, audio, PDF, text; 10MB cap) and get
        the id messages reference it by."""
        return self._request(
            "POST",
            "/v1/media",
            json={"content_type": content_type, "data": base64.b64encode(data).decode()},
        )

    def get_media(self, media_id: str) -> dict[str, Any]:
        """The attachment's description plus a short-lived URL for its bytes."""
        return self._request("GET", f"/v1/media/{media_id}")

    # -- conversations -----------------------------------------------------

    def conversation(self, conversation_id: str, **options: Any) -> "Conversation":
        """Create (idempotently) a stored conversation."""
        from .conversation import Conversation

        return Conversation._create(self, conversation_id, options)

    def load_conversation(self, conversation_id: str, **options: Any) -> "Conversation":
        """Resume a stored conversation from its last compaction."""
        from .conversation import Conversation

        conv = Conversation(self, conversation_id, options)
        conv.load()
        return conv


def _error_from(res: httpx.Response) -> GitloomError:
    code, message = "http_error", res.text.strip()
    try:
        err = res.json().get("error")
        if isinstance(err, str):
            message = err
        elif isinstance(err, dict):
            code = err.get("code") or code
            message = err.get("message") or message
    except ValueError:
        pass
    return GitloomError(code, message, res.status_code)


def _as_date(value: Any) -> str:
    """A date filter accepts what the caller already has: a string, or a date
    or datetime, which the API reads as YYYY-MM-DD or RFC 3339."""
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)
