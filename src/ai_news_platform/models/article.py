from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ArticleLifecycleState(str, Enum):
    RAW = "raw"
    NORMALIZED = "normalized"
    REWRITTEN = "rewritten"
    PUBLISHED = "published"


@dataclass(frozen=True)
class Article:
    """
    Domain-agnostic, normalized internal article representation.
    """

    id: str
    domain_id: str
    source_id: str
    canonical_url: str
    title: str | None = None
    content: str | None = None
    metadata: dict[str, Any] | None = None

