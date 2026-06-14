from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_news_platform.settings.models import DomainConfig, Settings


@dataclass(frozen=True)
class PromptLoader:
    """
    Loads per-domain single pipeline prompt files from the repository.
    """

    settings: Settings

    def load_domain_pipeline_prompt(self, *, domain: DomainConfig) -> str:
        if not domain.llm:
            raise ValueError(f"Domain {domain.id!r} is missing llm config")
        prompt_file = domain.llm.pipeline_prompt
        base = Path(self.settings.config.llm.prompts_dir)
        path = (base / prompt_file).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Prompt file not found: {path}")
        return path.read_text(encoding="utf-8")

