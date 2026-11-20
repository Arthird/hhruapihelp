from __future__ import annotations

import pytest

from backend.app.rag.chunking import split_markdown


def test_sections_and_endpoint_metadata() -> None:
    text = (
        "# Общая информация\n\nВведение.\n\n"
        "## get-vacancies\n\n"
        "GET https://api.hh.ru/vacancies HTTP/1.1\n\n"
        "Возвращает список вакансий.\n"
    )
    chunks = split_markdown(text, max_chars=300, overlap_chars=20)
    endpoint_chunk = next(chunk for chunk in chunks if chunk.endpoint)
    assert endpoint_chunk.section == "get-vacancies"
    assert endpoint_chunk.operation == "get"
    assert endpoint_chunk.endpoint == "GET /vacancies"
    assert endpoint_chunk.text.startswith("## get-vacancies")


def test_code_fence_is_not_split_at_heading_inside() -> None:
    text = "## example\n\n```python\n# это комментарий, а не заголовок\nprint('ok')\n```\n"
    chunks = split_markdown(text, max_chars=300, overlap_chars=20)
    assert len(chunks) == 1
    assert "print('ok')" in chunks[0].text


def test_long_section_is_split_but_bounded() -> None:
    text = "## section\n\n" + "\n".join(f"Строка {index} " + "x" * 80 for index in range(20))
    chunks = split_markdown(text, max_chars=300, overlap_chars=50)
    assert len(chunks) > 1
    assert all(len(chunk.text) <= 300 for chunk in chunks)
    assert all(chunk.section == "section" for chunk in chunks)


def test_invalid_overlap_is_rejected() -> None:
    with pytest.raises(ValueError):
        split_markdown("text", max_chars=200, overlap_chars=200)
