"""Tile registry for the `/tools/` page (#150).

A *tile* is one entry on the tools page. Two kinds:

* ``link`` — points at another route in the app (e.g. ``/config/``,
  ``/onboarding/?mode=rerun``). Renders as a single anchor.
* ``prompt`` — loads a user-facing prompt from ``config/tool_prompts/{slug}.md``
  and renders "Copy prompt" + "Open in Claude" affordances. The prompt is meant
  to be pasted into another LLM (Claude.ai or similar) where the operator will
  have a conversation that produces config edits.

Phase 1 ships a static tile list. The data shape is plain dicts (not
TypedDict / dataclass) because the template iterates over heterogeneous tiles
and Jinja's attribute access is easier against dicts.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

CLAUDE_NEW_CHAT_URL = "https://claude.ai/new?q="

# Conservative URL-length cap for ``claude.ai/new?q=``. Browsers and edge
# proxies are not consistent about how long a query string they will pass
# through; claude.ai itself accepts longer, but the affordance must be
# reliable. Above this, hide the Open-in-Claude button and show copy-only.
MAX_PROMPT_URL_LEN = 6000


TILES: list[dict[str, str]] = [
    {
        "slug": "full_rerun",
        "title": "Run a full onboarding interview",
        "description": (
            "Initial setup or full re-run after a major role pivot. Backs up "
            "existing config before overwriting. For partial updates, use the "
            "prompts below or 'Edit config files directly'."
        ),
        "kind": "link",
        "href": "/onboarding/?mode=rerun",
        "prompt_file": "",
    },
    {
        "slug": "profile_refresh",
        "title": "Refresh your profile",
        "description": (
            "Conversational walkthrough of target role, target companies, and "
            "what to avoid — when your search focus has shifted since "
            "onboarding. Output edits go into profile.md sections."
        ),
        "kind": "prompt",
        "prompt_file": "profile_refresh.md",
        "href": "",
    },
    {
        "slug": "exclusion_tuning",
        "title": "Tune what gets rejected",
        "description": (
            "Articulate new hard-reject categories — title patterns that "
            "always score 1, or JD-content shapes the scorer should "
            "down-weight. Output steers into profile.md (## Excluded "
            "Categories, ## Title Calibration Notes) or prefilter_rules.yaml."
        ),
        "kind": "prompt",
        "prompt_file": "exclusion_tuning.md",
        "href": "",
    },
    {
        "slug": "cover_letter_voice",
        "title": "Calibrate cover-letter voice",
        "description": (
            "Extract voice patterns from a sample cover letter and emit a new "
            "entry under candidate_context/voice_samples/."
        ),
        "kind": "prompt",
        "prompt_file": "cover_letter_voice.md",
        "href": "",
    },
    {
        "slug": "config_editor",
        "title": "Edit config files directly",
        "description": (
            "Profile, master resume, search config, role prompts. Direct text "
            "editor for everything in config/ and candidate_context/."
        ),
        "kind": "link",
        "href": "/config/",
        "prompt_file": "",
    },
]


def load_prompt(base_root: Path, prompt_file: str) -> str:
    """Read a prompt body from ``config/tool_prompts/<prompt_file>``.

    Returns an empty string when the file is missing or unreadable — the
    template renders an empty tile-body in that case, which is preferable
    to a 500 (a missing prompt is a config gap, not a route bug).
    """
    if not prompt_file:
        return ""
    path = base_root / "config" / "tool_prompts" / prompt_file
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def claude_url_for(prompt: str) -> str | None:
    """Build a ``claude.ai/new?q=`` URL for the prompt.

    Returns None when the prompt is empty or when the URL-encoded form
    exceeds :data:`MAX_PROMPT_URL_LEN`. Callers should fall back to
    copy-only when None.
    """
    if not prompt.strip():
        return None
    encoded = quote(prompt)
    if len(encoded) > MAX_PROMPT_URL_LEN:
        return None
    return CLAUDE_NEW_CHAT_URL + encoded


def hydrate_tiles(base_root: Path) -> list[dict[str, object]]:
    """Return :data:`TILES` with per-tile prompt body and claude_url filled in.

    Templates iterate over the result and switch on ``kind``. Link tiles
    pass through unchanged. Prompt tiles get two extra keys:

    * ``body`` — the prompt text (string; empty if file missing).
    * ``claude_url`` — claude.ai/new?q= URL, or ``None`` if too long.
    """
    out: list[dict[str, object]] = []
    for tile in TILES:
        item: dict[str, object] = dict(tile)
        if tile["kind"] == "prompt":
            body = load_prompt(base_root, tile["prompt_file"])
            item["body"] = body
            item["claude_url"] = claude_url_for(body)
        out.append(item)
    return out
