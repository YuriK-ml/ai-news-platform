from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from ai_news_platform.settings.models import RootConfig, Settings


def load_settings(config_path: Path) -> Settings:
    """
    Load and validate settings.
    """

    load_dotenv(override=False)

    data: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    root = RootConfig.model_validate(data)

    # Resolve storage database path from env (fallback to default).
    env_key = root.storage.database_path_env
    if os.getenv(env_key) is None:
        os.environ[env_key] = root.storage.database_path_default

    return Settings(raw=data, config=root)
