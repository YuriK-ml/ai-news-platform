from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


Decision = Literal["REJECT", "READY_TO_PUBLISH"]


@dataclass(frozen=True)
class ParsedLLMOutput:
    decision: Decision
    rejection_explanation: str | None = None
    final_title: str | None = None
    final_text: str | None = None


_TITLE_RE = re.compile(r"^Title:\s*(?P<title>.+?)\s*$")
_TEXT_RE = re.compile(r"^Text:\s*(?P<text>.*)$")


def parse_llm_output(text: str) -> ParsedLLMOutput:
    """
    Parses the approved non-JSON LLM output contract.
    """

    raw = (text or "").strip()
    if not raw:
        raise ValueError("Empty LLM output")

    lines = raw.splitlines()
    first = lines[0].strip()

    if first == "REJECT":
        explanation = "\n".join(lines[1:]).strip()
        return ParsedLLMOutput(
            decision="REJECT", rejection_explanation=explanation if explanation else None
        )

    if first == "READY_TO_PUBLISH":
        title, body = _parse_title_and_text(lines[1:])
        return ParsedLLMOutput(decision="READY_TO_PUBLISH", final_title=title, final_text=body)

    raise ValueError(f"Unrecognized LLM output header: {first!r}")


def _parse_title_and_text(rest: list[str]) -> tuple[str, str]:
    title: str | None = None
    text_lines: list[str] = []
    in_text = False

    for line in rest:
        if not in_text:
            if not line.strip():
                continue
            if title is None:
                m = _TITLE_RE.match(line.strip())
                if not m:
                    raise ValueError("READY_TO_PUBLISH output must contain 'Title: ...'")
                title = m.group("title").strip()
                continue
            m = _TEXT_RE.match(line)
            if m:
                in_text = True
                first_text = m.group("text")
                text_lines.append(first_text)
                continue
            if line.strip() == "Text:":
                in_text = True
                continue
            raise ValueError("READY_TO_PUBLISH output must contain 'Text: ...'")
        else:
            text_lines.append(line)

    if not title:
        raise ValueError("READY_TO_PUBLISH output is missing title")
    body = "\n".join(text_lines).strip()
    if not body:
        raise ValueError("READY_TO_PUBLISH output is missing text body")
    return title, body


_TAG_RE = re.compile(r"<\s*/?\s*([a-zA-Z0-9]+)[^>]*>")


def validate_telegram_html(text: str) -> None:
    """
    Best-effort validation: allow only <b>, <i>, <u> tags (and their closing forms).
    """

    allowed = {"b", "i", "u"}
    for m in _TAG_RE.finditer(text or ""):
        tag = (m.group(1) or "").lower()
        if tag and tag not in allowed:
            raise ValueError(f"Disallowed HTML tag in output: <{tag}>")
