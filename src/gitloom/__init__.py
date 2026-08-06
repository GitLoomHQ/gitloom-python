"""GitLoom: conversations that cannot outgrow their context window.

Sits beside the OpenAI and Anthropic SDKs rather than replacing them: messages
go in and come out in the provider's own shape, usage objects from their
responses time compaction, and each compaction hands the summarized turns to
memory ingestion.
"""

from .client import Gitloom, GitloomError
from .wrap import wrap
from .conversation import Conversation
from .media import image_data, image_part, text_part
from .tokens import context_limit, estimate_tokens, message_tokens, text_of, total_tokens

__all__ = [
    "Gitloom",
    "GitloomError",
    "wrap",
    "Conversation",
    "context_limit",
    "estimate_tokens",
    "message_tokens",
    "total_tokens",
    "text_of",
    "text_part",
    "image_part",
    "image_data",
]

__version__ = "0.2.0"
