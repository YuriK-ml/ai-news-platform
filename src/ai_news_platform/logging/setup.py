from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog


def configure_logging(*, level: str | None = None, service_name: str = "ai-news-platform") -> None:
    """
    Configure structured logging (JSON) for application and pipeline events.
    """

    effective_level = (level or os.getenv("LOG_LEVEL") or "INFO").upper()

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, effective_level, logging.INFO),
    )

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _add_static_fields(service_name=service_name),
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def _add_static_fields(*, service_name: str):
    def processor(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        event_dict.setdefault("service", service_name)
        event_dict.setdefault("env", os.getenv("ENV", "local"))
        return event_dict

    return processor
