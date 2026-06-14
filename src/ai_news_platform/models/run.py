from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RunContext:
    run_id: str
    started_at: datetime

