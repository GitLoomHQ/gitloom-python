"""Vocabulary and skills: the two kinds of knowledge a namespace holds beside
its memories."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover
    from .client import Gitloom


class Vocab:
    """A namespace's custom vocabulary — the terms and abbreviations its
    memories are written in.

    Search expands a query across a term's surface forms, so once a namespace
    knows ``k8s`` means ``kubernetes`` a query for either finds memories
    written with the other.
    """

    def __init__(self, client: "Gitloom"):
        self._c = client

    def learn(self, terms: list[dict[str, Any]], *, namespace: Optional[str] = None) -> dict[str, Any]:
        """Teach terms. Asynchronous like every write: they land in seconds.

        Each term is ``{"term": ..., "aliases": [...], "definition": ...}``.
        Learning one the namespace already has rewrites it.
        """
        return self._c._request(
            "POST",
            "/v1/vocab",
            json={"namespace": namespace or self._c.namespace, "terms": terms},
        )

    def list(
        self, *, namespace: Optional[str] = None, like: Optional[str] = None, limit: Optional[int] = None
    ) -> list[dict[str, Any]]:
        """Every learned term, alphabetically."""
        params: dict[str, Any] = {"namespace": namespace or self._c.namespace}
        if like:
            params["like"] = like
        if limit:
            params["limit"] = limit
        return (self._c._request("GET", "/v1/vocab", params=params) or {}).get("terms") or []

    def lookup(self, word: str, *, namespace: Optional[str] = None) -> Optional[dict[str, Any]]:
        """Resolve any surface form to its term, or None when unknown."""
        res = self._c._request(
            "GET", "/v1/vocab", params={"namespace": namespace or self._c.namespace, "word": word}
        ) or {}
        return res.get("term") if res.get("found") else None

    def forget(self, terms: list[str], *, namespace: Optional[str] = None) -> dict[str, Any]:
        """Forget terms by canonical form. A term never learned is skipped."""
        return self._c._request(
            "DELETE",
            "/v1/vocab",
            params={"namespace": namespace or self._c.namespace, "term": ",".join(terms)},
        )


class Skills:
    """Procedural know-how: how something is done.

    Skills are ordinary memories under the ``skills`` tier, so ``recall`` with
    ``tiers=["skills"]`` reaches them too.
    """

    def __init__(self, client: "Gitloom"):
        self._c = client

    def store(self, skills: list[dict[str, Any]], *, namespace: Optional[str] = None) -> dict[str, Any]:
        """Store skills. Each becomes a memory at ``skills/<topic>/<slug>.md``.

        A skill is ``{"name", "description", "content", "topic", "tags",
        "triggers", ...}``. ``triggers`` become its retrieval cues, so write
        them as the question someone would actually ask.
        """
        return self._c._request(
            "POST",
            "/v1/skills",
            json={"namespace": namespace or self._c.namespace, "skills": skills},
        )

    def find(
        self,
        task: str,
        *,
        namespace: Optional[str] = None,
        paths: Optional[list[str]] = None,
        tags: Optional[list[str]] = None,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """The skills that bear on a task, best first."""
        return self._query(task, namespace=namespace, paths=paths, tags=tags, limit=limit)

    def list(
        self,
        *,
        namespace: Optional[str] = None,
        paths: Optional[list[str]] = None,
        tags: Optional[list[str]] = None,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Every skill."""
        return self._query("", namespace=namespace, paths=paths, tags=tags, limit=limit)

    def _query(
        self,
        q: str,
        *,
        namespace: Optional[str],
        paths: Optional[list[str]],
        tags: Optional[list[str]],
        limit: Optional[int],
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"namespace": namespace or self._c.namespace}
        if q:
            params["q"] = q
        if paths:
            params["paths"] = ",".join(paths)
        if tags:
            params["tags"] = ",".join(tags)
        if limit:
            params["limit"] = limit
        return (self._c._request("GET", "/v1/skills", params=params) or {}).get("skills") or []
