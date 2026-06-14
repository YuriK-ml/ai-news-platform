from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Source:
    """
    A configured ingestion source (RSS or website).
    """

    id: str
    domain_id: str
    type: str
    name: str
    config: dict[str, Any]
    url: str | None = None
