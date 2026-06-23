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


@dataclass(frozen=True)
class BatchParseResult:
    results_by_article_id: dict[str, ParsedLLMOutput]
    errors_by_article_id: dict[str, str]
    unknown_article_ids: list[str]
    duplicate_article_ids: list[str]


_ARTICLE_ID_RE = re.compile(r"^Article-ID:\s*(?P<id>[A-Za-z0-9_\-]+)\s*$")
_STATUS_RE = re.compile(r"^Status:\s*(?P<status>REJECT|READY_TO_PUBLISH)\s*$")


def parse_batch_output(text: str, *, expected_article_ids: set[str] | None = None) -> BatchParseResult:
    """
    Parse batch output with RESULT blocks.

    Expected structure:
    ### RESULT
    Article-ID: ...
    Status: REJECT|READY_TO_PUBLISH
    (optional Reason / Title / Text sections)
    ### END RESULT
    """

    raw = (text or "").strip()
    if not raw:
        raise ValueError("Empty LLM batch output")

    blocks = _extract_blocks(raw, start="### RESULT", end="### END RESULT")
    if not blocks:
        raise ValueError("No RESULT blocks found in LLM batch output")

    results: dict[str, ParsedLLMOutput] = {}
    errors: dict[str, str] = {}
    duplicates: list[str] = []

    for block in blocks:
        try:
            parsed_id, parsed, err = _parse_result_block(block)
        except Exception as exc:
            parsed_id = _try_extract_article_id(block)
            parsed = None
            err = f"parser_error: {exc}"

        if not parsed_id:
            continue

        if parsed_id in results or parsed_id in errors:
            duplicates.append(parsed_id)
            continue

        if err:
            errors[parsed_id] = err
            continue

        if parsed is not None:
            results[parsed_id] = parsed

    unknown: list[str] = []
    if expected_article_ids is not None:
        produced_ids = set(results.keys()) | set(errors.keys())
        unknown = sorted([aid for aid in produced_ids if aid not in expected_article_ids])

    return BatchParseResult(
        results_by_article_id=results,
        errors_by_article_id=errors,
        unknown_article_ids=unknown,
        duplicate_article_ids=duplicates,
    )


def _extract_blocks(text: str, *, start: str, end: str) -> list[str]:
    lines = text.splitlines()
    blocks: list[list[str]] = []
    current: list[str] | None = None

    for line in lines:
        if line.strip() == start:
            current = []
            continue
        if line.strip() == end:
            if current is not None:
                blocks.append(current)
            current = None
            continue
        if current is not None:
            current.append(line)

    return ["\n".join(b).strip() for b in blocks if "\n".join(b).strip()]


def _parse_result_block(block: str) -> tuple[str | None, ParsedLLMOutput | None, str | None]:
    lines = [l.rstrip("\r") for l in (block or "").splitlines()]
    article_id: str | None = None
    status: str | None = None

    # find required headers anywhere in block
    for line in lines:
        if article_id is None:
            m = _ARTICLE_ID_RE.match(line.strip())
            if m:
                article_id = m.group("id")
                continue
        if status is None:
            m = _STATUS_RE.match(line.strip())
            if m:
                status = m.group("status")
                continue

    if not article_id or not status:
        if article_id and not status:
            return article_id, None, "missing_status"
        return None, None, None

    if status == "REJECT":
        explanation = _parse_reason_section(lines)
        return article_id, ParsedLLMOutput(decision="REJECT", rejection_explanation=explanation), None

    title, body, err = _parse_title_text_sections(lines)
    if err:
        return article_id, None, err
    return article_id, ParsedLLMOutput(decision="READY_TO_PUBLISH", final_title=title, final_text=body), None


def _try_extract_article_id(block: str) -> str | None:
    for line in (block or "").splitlines():
        m = _ARTICLE_ID_RE.match(line.strip())
        if m:
            return m.group("id")
    return None


def _parse_reason_section(lines: list[str]) -> str | None:
    """
    Reason section is optional. If present, return its contents. Otherwise None.
    """

    reason_start = None
    for i, line in enumerate(lines):
        if line.strip() == "Reason:":
            reason_start = i + 1
            break
    if reason_start is None:
        # fallback: any non-header trailing text after Status line
        trailing: list[str] = []
        started = False
        for line in lines:
            if started:
                trailing.append(line)
            if line.strip().startswith("Status:"):
                started = True
        txt = "\n".join([t for t in trailing if t.strip()]).strip()
        return txt or None

    reason_lines = []
    for line in lines[reason_start:]:
        if line.strip().startswith("Title:") or line.strip().startswith("Text:"):
            break
        reason_lines.append(line)
    txt = "\n".join([t for t in reason_lines if t.strip()]).strip()
    return txt or None


def _parse_title_text_sections(lines: list[str]) -> tuple[str, str, str | None]:
    """
    Parse Title and Text sections for READY_TO_PUBLISH.
    """

    title: str | None = None
    text: str | None = None

    # Title: may be single line "Title: ..." or block starting at "Title:"
    for i, line in enumerate(lines):
        if line.strip().startswith("Title:"):
            after = line.split("Title:", 1)[1].strip()
            if after:
                title = after
                break
            # block title begins next non-empty line
            for j in range(i + 1, len(lines)):
                if lines[j].strip():
                    title = lines[j].strip()
                    break
            break

    # Text: may be inline "Text: ..." then rest of block, or block after "Text:"
    for i, line in enumerate(lines):
        if line.strip().startswith("Text:"):
            after = line.split("Text:", 1)[1]
            text_lines: list[str] = []
            if after.strip():
                text_lines.append(after.lstrip())
            # take all following lines
            text_lines.extend(lines[i + 1 :])
            text = "\n".join(text_lines).strip()
            break

    if not title or not title.strip():
        return "", "", "missing_title"
    if not text or not text.strip():
        return "", "", "missing_text"
    return title.strip(), text.strip(), None
