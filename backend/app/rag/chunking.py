from __future__ import annotations

import re
from dataclasses import dataclass

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
ENDPOINT_RE = re.compile(
    r"^(GET|POST|PUT|PATCH|DELETE)\s+(?:https?://\S+?)?(/\S*)\s*(?:HTTP/\d(?:\.\d)?)?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Chunk:
    text: str
    section: str
    operation: str | None
    endpoint: str | None
    line_start: int
    line_end: int


def _open_fence(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith("```"):
        return None
    return stripped


def split_markdown(
    text: str,
    *,
    max_chars: int = 1200,
    overlap_chars: int = 150,
    source: str = "hh_api.md",
) -> list[Chunk]:
    """Split Markdown by headings while keeping fenced code blocks together."""

    if max_chars < 200:
        raise ValueError("max_chars must be at least 200")
    if overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be between 0 and max_chars")

    lines = text.splitlines()
    sections: list[dict[str, object]] = []
    operations: list[str] = []
    section = "Документация HH API"
    buffer: list[str] = []
    start_line = 1
    in_fence = False

    def flush(end_line: int) -> None:
        nonlocal buffer, start_line
        body = "\n".join(buffer).strip()
        if body:
            operations.append(_detect_operation(section))
            sections.append(
                {
                    "section": section,
                    "operation": operations[-1],
                    "text": body,
                    "line_start": start_line,
                    "line_end": end_line,
                }
            )
        buffer = []

    for index, line in enumerate(lines, start=1):
        fence = _open_fence(line)
        if fence is not None:
            in_fence = not in_fence
        heading = None if in_fence else HEADING_RE.match(line)
        if heading and len(heading.group(1)) <= 3:
            flush(index - 1)
            section = heading.group(2).strip()
            start_line = index
            operations.append(_detect_operation(section))
        buffer.append(line)
    flush(len(lines))

    chunks: list[Chunk] = []
    for item in sections:
        body = str(item["text"])
        endpoint = _detect_endpoint(body)
        for part, offset in _split_body(body, max_chars, overlap_chars):
            chunks.append(
                Chunk(
                    text=part,
                    section=str(item["section"]),
                    operation=item["operation"] if isinstance(item["operation"], str) else None,
                    endpoint=endpoint,
                    line_start=int(item["line_start"]) + offset,
                    line_end=int(item["line_start"]) + offset + part.count("\n"),
                )
            )
    return chunks


def _detect_endpoint(text: str) -> str | None:
    for line in text.splitlines():
        match = ENDPOINT_RE.match(line.strip())
        if match:
            return f"{match.group(1).upper()} {match.group(2)}"
    return None


def _detect_operation(section: str) -> str | None:
    match = re.match(r"^([A-Za-z]+)-(.+)$", section)
    if not match:
        return None
    return match.group(1).lower()


def _split_body(text: str, max_chars: int, overlap_chars: int) -> list[tuple[str, int]]:
    if len(text) <= max_chars:
        return [(text, 0)]

    parts: list[tuple[str, int]] = []
    cursor = 0
    while cursor < len(text):
        end = min(cursor + max_chars, len(text))
        if end < len(text):
            newline = text.rfind("\n", cursor + max_chars // 2, end)
            if newline > cursor:
                end = newline
        part = text[cursor:end].strip()
        if part:
            parts.append((part, text.count("\n", 0, cursor)))
        if end >= len(text):
            break
        cursor = max(end - overlap_chars, cursor + 1)
    return parts
