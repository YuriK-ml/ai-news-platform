from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Domain:
    id: str
    name: str

