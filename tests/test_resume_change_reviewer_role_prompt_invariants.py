"""Static invariants for `config/roles/resume_change_reviewer.md` (#1099).

The reviewer's output is parsed by `findajob.prep.change_report`, which needs
one JSON object with these keys and verbatim quotes it can look up. These
tests keep the prompt and the parser in step.
"""

from __future__ import annotations

from pathlib import Path

_ROLE_PATH = Path(__file__).parent.parent / "config" / "roles" / "resume_change_reviewer.md"


def _read_role() -> str:
    return _ROLE_PATH.read_text(encoding="utf-8")


def test_frontmatter_model_and_temperature_unchanged() -> None:
    text = _read_role()

    assert "model: openrouter:google/gemini-3-flash-preview" in text
    assert "temperature: 0.1" in text


def test_prompt_asks_for_json_with_verbatim_quotes_from_the_line_diff() -> None:
    text = _read_role()

    assert "LINE DIFF" in text
    assert "character for character" in text
    assert "Return only one JSON object" in text
    for key in ("reordered_sections", "added", "reworded", "removed", "missing_info"):
        assert f'"{key}"' in text, key


def test_prompt_drops_the_free_form_field_headers() -> None:
    assert "ADDED_KEYWORDS" not in _read_role()
