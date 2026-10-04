"""The GitLoom API client: memory, media, and the conversation factory."""

from __future__ import annotations

import base64
import datetime as dt
import math
import os
import warnings
from typing import Any, Optional, Union
from urllib.parse import quote, urlencode

import httpx

from .memory import Skills, Vocab

DEFAULT_BASE_URL = "https://api.gitloom.cloud"

When = Union[dt.datetime, dt.date, int, float, str]

_FILTERS = ("tags", "tags_all", "since", "until", "tiers", "paths")
_TIMES = ("created_at", "updated_at", "occurred_at", "expires_at")


class GitloomError(Exception):
    """A refusal from the API, carrying its machine-readable code."""

    def __init__(self, code: str, message: str, status: int):
        super().__init__(f"{message} ({status} {code})")
        self.code = code
        self.message = message
        self.status = status


class MissingQueryError(GitloomError, ValueError):
    """``missing_query``, raised before sending: a recall with neither a
    query nor a filter. Still a ``ValueError`` for code that caught one."""


class Gitloom:
    """The client. `Gitloom()` reads GITLOOM_API_KEY from the environment,
    and raises ``GitloomError("missing_api_key")`` when there is none.

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
        if not self.api_key.strip():
            raise GitloomError("missing_api_key", "No API key. Pass api_key= or set GITLOOM_API_KEY.", 0)
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
        url = path
        # %20, not +, for a space: a + reads as a space only to form decoders.
        qs = urlencode({k: v for k, v in (params or {}).items() if v is not None}, quote_via=quote, safe="")
        if qs:
            url += "?" + qs
        try:
            res = self._http.request(method, url, json=json)
        except httpx.TransportError as e:
            raise GitloomError("network_error", str(e) or type(e).__name__, 0) from e
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
        tags: Optional[list[str]] = None,
        occurred_at: Optional[When] = None,
        timezone: Optional[str] = None,
        date: Optional[str] = None,
    ) -> None:
        """Submit a conversation for ingestion. Asynchronous by design —
        extraction runs model calls the caller must not wait on.

        ``tags`` land on every memory extracted from it. ``occurred_at`` is when
        the conversation happened: an aware ``datetime`` (a naive one is read
        as UTC), a ``date`` for that calendar day, epoch seconds, or a string
        the API reads (RFC 3339, ``YYYY-MM-DD``, or a datetime without an
        offset, read in ``timezone``). ``timezone`` is IANA, e.g.
        ``"Asia/Kolkata"``. ``date`` is the deprecated name of ``occurred_at``.
        """
        body: dict[str, Any] = {"namespace": namespace or self.namespace, "messages": messages}
        if session_id:
            body["session_id"] = session_id
        if tags:
            body["tags"] = list(tags)
        if occurred_at is not None:
            body["occurred_at"] = _when(occurred_at)
        if timezone:
            body["timezone"] = timezone
        if date:
            warnings.warn("remember(date=) is deprecated; pass occurred_at=", DeprecationWarning, stacklevel=2)
            body["date"] = date
        self._request("POST", "/v1/memories", json=body)

    def write(
        self,
        memories: list[dict[str, Any]],
        *,
        namespace: Optional[str] = None,
        timezone: Optional[str] = None,
    ) -> None:
        """Store already-formed memories, as given — no model decides what to
        keep. Asynchronous like ``remember``: they reach retrieval in seconds.

        Each is ``{"path", "content", "tags", "occurred_at", "confidence",
        "ttl", "supersedes", "cues", "related"}``; only ``path`` (under
        ``facts/``, ``incidents/``, ``rules/`` or ``skills/``, ending in
        ``.md``) and ``content`` are required. ``occurred_at`` is what the
        memory is about, converted as in ``remember``; ``date`` is its
        deprecated name. Send batches: one call is one commit.
        """
        if not memories:
            return None
        wire = []
        for i, m in enumerate(memories):
            path = m.get("path") or ""
            if not path.endswith(".md"):
                raise ValueError(f"memory {i}: path {path!r} must end in .md")
            m = dict(m)
            if m.get("occurred_at") is not None:
                m["occurred_at"] = _when(m["occurred_at"])
            if m.get("date"):
                warnings.warn("a memory's date is deprecated; set occurred_at", DeprecationWarning, stacklevel=2)
            wire.append(m)
        body: dict[str, Any] = {"namespace": namespace or self.namespace, "memories": wire}
        if timezone:
            body["timezone"] = timezone
        self._request("POST", "/v1/memories", json=body)

    def get(self, path: str, *, namespace: Optional[str] = None) -> dict[str, Any]:
        """Read one memory by path — a file, or ``file.md#section``: what a
        recall hit names. Raises ``GitloomError("not_found")`` when it is gone.
        Its times and tags read as ``recall``'s do."""
        m = self._request(
            "GET", "/v1/memories", params={"path": path, "namespace": namespace or self.namespace}
        ) or {}
        _read_memory(m)
        return m

    def forget(self, paths: list[str], *, namespace: Optional[str] = None) -> None:
        """Delete memories by path. Asynchronous. It unpublishes them from
        retrieval; git history keeps the earlier revisions."""
        if isinstance(paths, str):
            paths = [paths]
        if not paths:
            return None
        self._request(
            "DELETE",
            "/v1/memories",
            params={"path": ",".join(paths), "namespace": namespace or self.namespace},
        )

    def tree(
        self, *, namespace: Optional[str] = None, path: Optional[str] = None, depth: Optional[int] = None
    ) -> dict[str, Any]:
        """The table of contents: tier → topic → file → sections, rooted at
        ``path`` (the whole memory by default), ``depth`` levels down (default
        2, at most 8)."""
        params: dict[str, Any] = {"namespace": namespace or self.namespace}
        if path:
            params["path"] = path
        if depth and depth > 0:
            params["depth"] = depth
        return self._request("GET", "/v1/tree", params=params)

    def topics(
        self,
        *,
        namespace: Optional[str] = None,
        tier: Optional[str] = None,
        prefix: Optional[str] = None,
        like: Optional[str] = None,
        max_depth: Optional[int] = None,
        min_files: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> dict[str, Any]:
        """Every topic directory with its memory count. Check it before filing
        under a new topic, so ``facts/database`` is not invented beside
        ``facts/databases``. ``like`` matches the leaf name, case-insensitively."""
        params: dict[str, Any] = {"namespace": namespace or self.namespace}
        for k, v in (("tier", tier), ("prefix", prefix), ("like", like)):
            if v:
                params[k] = v
        for k, n in (("max_depth", max_depth), ("min_files", min_files), ("limit", limit)):
            if n and n > 0:
                params[k] = n
        res = self._request("GET", "/v1/topics", params=params) or {}
        res.setdefault("topics", [])
        return res

    def graph(self, *, namespace: Optional[str] = None, limit: Optional[int] = None) -> dict[str, Any]:
        """The relationship graph: ``nodes`` and ``edges``, with ``truncated``
        set when it was larger than one response."""
        params: dict[str, Any] = {"namespace": namespace or self.namespace}
        if limit and limit > 0:
            params["limit"] = limit
        res = self._request("GET", "/v1/graph", params=params) or {}
        res.setdefault("nodes", [])
        res.setdefault("edges", [])
        return res

    def recall(
        self,
        query: Optional[str] = None,
        *,
        namespace: Optional[str] = None,
        limit: Optional[int] = None,
        mode: Optional[str] = None,
        tiers: Optional[list[str]] = None,
        paths: Optional[list[str]] = None,
        tags: Optional[list[str]] = None,
        tags_all: Optional[list[str]] = None,
        since: Optional[When] = None,
        until: Optional[When] = None,
        time_field: Optional[str] = None,
        tz: Optional[str] = None,
        min_score: Optional[float] = None,
        context: Optional[bool] = None,
        detail: Optional[str] = None,
        include_expired: bool = False,
        rank: Optional[str] = None,
        max_chars: Optional[int] = None,
        model: Optional[str] = None,
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

        ``rank`` of ``fused`` or ``jev`` retrieves on the lane path, which also
        reaches conversation turns and the dates in a question; each memory
        then carries its ``store`` and the days it was ``said``, and ``jev``
        sets ``rank_fallback`` when it answers in lane order. ``max_chars``
        caps the memory content returned, marking what it cut ``excerpted``.
        ``model`` of ``haiku`` or ``sonnet`` picks the reader in ``summary`` or
        ``agentic`` mode.

        ``since`` and ``until`` bound ``time_field`` — ``occurred``,
        ``created`` or ``updated`` (the default) — and take what ``remember``'s
        ``occurred_at`` takes; a date-only ``until`` includes that whole day in
        ``tz`` (IANA). The query is optional once a filter (``tags``,
        ``tags_all``, ``since``, ``until``, ``tiers`` or ``paths``) says what
        to list: every match comes back newest first, each scored 1. With
        neither, it raises ``GitloomError`` ``missing_query`` before sending.

        Each memory's ``created_at``, ``updated_at``, ``occurred_at`` and
        ``expires_at`` are aware UTC datetimes, None when unknown.
        ``occurred_precision`` of ``day`` means only the date is known, held as
        noon UTC; ``occurred_source`` says where the time came from.
        ``user_tags`` are the tags a caller set; ``tags`` lists them first.
        """
        params: dict[str, Any] = {"namespace": namespace or self.namespace}
        if query and query.strip():
            params["q"] = query
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
            params["since"] = _when(since)
        if until is not None:
            params["until"] = _when(until)
        if time_field:
            params["time_field"] = time_field
        if tz:
            params["tz"] = tz
        if min_score is not None:
            params["min_score"] = min_score
        if context is False:
            params["context"] = "0"
        if detail:
            params["detail"] = detail
        if include_expired:
            params["include_expired"] = "1"
        if rank:
            params["rank"] = rank
        if max_chars and max_chars > 0:
            params["max_chars"] = max_chars
        if model:
            params["model"] = model
        if "q" not in params and not any(params.get(k) for k in _FILTERS):
            raise MissingQueryError(
                "missing_query",
                "recall needs a query, or a filter (tags, tags_all, since, until, tiers or paths) "
                "saying what to list",
                0,
            )
        res = self._request("GET", "/v1/retrieve", params=params) or {}
        res.setdefault("memories", [])
        for m in res["memories"]:
            _read_memory(m)
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

    def context(self, query: Optional[str] = None, **kwargs: Any) -> Optional[dict[str, str]]:
        """Retrieval rendered as a system message, ready to prepend. None when
        nothing relevant is stored. Takes ``recall``'s filters, and like it
        needs no query when one of them says what to list."""
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


_GATEWAY_REFUSALS = {
    401: "No API key was accepted (401 Unauthorized) — check GITLOOM_API_KEY.",
    403: "The API key was not accepted (403 Forbidden) — check GITLOOM_API_KEY, "
    "or whether the key has been revoked.",
}


def _error_from(res: httpx.Response) -> GitloomError:
    status = res.status_code
    try:
        body = res.json()
    except ValueError:
        body = None
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        code = err.get("code") or f"http_{status}"
        return GitloomError(code, err.get("message") or _reason(res), status)
    # API Gateway answers a missing or rejected key itself, without the envelope.
    if status in _GATEWAY_REFUSALS:
        return GitloomError("unauthorized", _GATEWAY_REFUSALS[status], status)
    if isinstance(err, str) and err:
        return GitloomError(f"http_{status}", err, status)
    return GitloomError(f"http_{status}", _fallback_message(res, body), status)


def _fallback_message(res: httpx.Response, body: Any) -> str:
    if isinstance(body, dict) and isinstance(body.get("message"), str) and body["message"]:
        return body["message"]
    text = res.text.strip()
    return text[:300] if text else _reason(res)


def _reason(res: httpx.Response) -> str:
    return res.reason_phrase or httpx.codes.get_reason_phrase(res.status_code) or f"HTTP {res.status_code}"


def _when(value: When) -> Union[int, str]:
    """A time as the API reads it: a datetime as epoch seconds (naive is UTC),
    a date as YYYY-MM-DD, a number floored to whole seconds, a string as is."""
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        secs = math.floor(value.timestamp())
        # The API reads epoch seconds as 9 to 11 digits; RFC 3339 reaches the rest.
        if 10**8 <= secs < 10**11:
            return secs
        return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return math.floor(value)
    if isinstance(value, str):
        return value
    raise TypeError(f"a time is a datetime, date, epoch seconds or string, not {type(value).__name__}")


def _read_memory(m: dict[str, Any]) -> None:
    for k in _TIMES:
        v = m.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            m[k] = dt.datetime.fromtimestamp(v, tz=dt.timezone.utc)
        elif v is None:
            m[k] = None
    for k in ("user_tags", "tags"):
        if m.get(k) is None:
            m[k] = []
    m.setdefault("occurred_source", None)
    m.setdefault("occurred_precision", None)
