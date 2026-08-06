"""The GitLoom API client: memory, media, and the conversation factory."""

from __future__ import annotations

import base64
import os
from typing import Any, Optional

import httpx

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
        self, query: str, *, namespace: Optional[str] = None, limit: Optional[int] = None
    ) -> dict[str, Any]:
        """Retrieve what is known that bears on the query. Every hit carries
        its evidence: per-arm scores, git history with the last diff, and
        labelled relation snippets."""
        params: dict[str, Any] = {"q": query, "namespace": namespace or self.namespace}
        if limit:
            params["limit"] = limit
        return self._request("GET", "/v1/retrieve", params=params)

    def context(self, query: str, *, namespace: Optional[str] = None) -> Optional[dict[str, str]]:
        """Retrieval rendered as a system message, ready to prepend. None when
        nothing relevant is stored."""
        res = self.recall(query, namespace=namespace)
        hits = res.get("hits") or []
        if not hits:
            return None
        lines = "\n".join(f"- {h['snippet']}" for h in hits)
        return {
            "role": "system",
            "content": (
                "What you already know about this user, from earlier conversations. "
                "Treat it as background, not as something they just said:\n" + lines
            ),
        }

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
