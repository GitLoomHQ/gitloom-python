"""Content-part helpers for multimodal messages."""

from __future__ import annotations

from typing import Any


def text_part(text: str) -> dict[str, Any]:
    """A text block."""
    return {"type": "text", "text": text}


def image_part(media_id: str) -> dict[str, Any]:
    """An image block referencing an uploaded attachment."""
    return {"type": "image", "media_id": media_id}


def image_data(b64: str, media_type: str) -> dict[str, Any]:
    """An image block carrying bytes, uploaded transparently on append — the
    stored message references the uploaded copy, never contains it."""
    return {"type": "image", "data": {"base64": b64, "media_type": media_type}}
