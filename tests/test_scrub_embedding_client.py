"""tests/test_scrub_embedding_client.py

Tests for scripts/scrub_embedding_client.py.

Run via:  uv run pytest tests/test_scrub_embedding_client.py -v
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent


def run_scrub(config_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "scripts/scrub_embedding_client.py", "--config-dir", str(config_dir)],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )


# ---------------------------------------------------------------------------
# Shared test fixture — mirrors ops/aichat-ng/config.yaml.example from
# *before* the #455 removal, i.e., what's on existing operator stacks.
# This is the content the script must be able to scrub.
# ---------------------------------------------------------------------------

FRESH_CONFIG = """\
# aichat-ng config — seeded by ops/entrypoint.sh when state/aichat_ng/config.yaml
# is absent. Re-pulling the image will NOT overwrite your local edits; delete
# the file first if you want to re-seed from this template.
#
# API key placeholders (${VAR}) are substituted by ops/entrypoint.sh at
# seed time from the container environment. Do not paste keys directly
# into this template — they are injected once when config.yaml is first
# created and live only in the gitignored state/aichat_ng/config.yaml.
#
# As of #67 (2026-05-04), the only direct provider clients the pipeline
# uses are:
#   - openrouter (every chat call: scoring, prep, briefing, outreach, ...)
#   - gemini-embed (RAG embeddings only — opt-in REPL feature)
# Direct openai / claude / perplexity / gemini-chat / groq / xai clients
# were retired (#250 + #251 + #67); all chat models now reach those
# providers through the openrouter client.  This collapses the plaintext-
# keys surface from 7 → 2 keys at first-seed.
#
# See https://github.com/sigoden/aichat/blob/main/config.example.yaml
# for the full set of supported options.
#
# Note: the model catalog (pricing, token limits, thinking-mode flags)
# lives in models-override.yaml, which entrypoint seeds separately.
# Do NOT add a top-level `models:` block here or inline `models:` blocks
# under existing clients — they will shadow models-override.yaml.

model: openrouter:google/gemini-3-flash-preview
temperature: 0
rag_embedding_model: gemini-embed:gemini-embedding-001
rag_reranker_model: null
rag_top_k: 6
rag_chunk_size: 1200
rag_chunk_overlap: 200

document_loaders:
  docx: 'pandoc -f docx -t plain $1'
  pdf:  'pdftotext $1 -'

function_calling: false

# Roles are loaded from /app/config/roles (seeded by the image entrypoint);
# ops/entrypoint.sh also creates the expected symlink at
# $AICHAT_CFG_DIR/roles -> /app/config/roles on first container start.

clients:
  - type: openai-compatible
    name: openrouter
    api_base: https://openrouter.ai/api/v1
    api_key: ${OPENROUTER_API_KEY}

  # gemini-embed: dedicated embedding endpoint. Declared inline (not
  # cataloged in models-override.yaml) because the pipeline uses
  # gemini-embedding-001, which differs from the upstream catalog's
  # gemini text-embedding-004. RAG is the only consumer; see #267 for
  # the deprecation track.
  - type: gemini
    name: gemini-embed
    api_base: https://generativelanguage.googleapis.com/v1beta
    api_key: ${GOOGLE_API_KEY}
    models:
      - name: gemini-embedding-001
        type: embedding
        default_chunk_size: 1500
        max_batch_size: 100
"""

# What should survive after scrubbing FRESH_CONFIG.
CLEAN_CONFIG = """\
# aichat-ng config — seeded by ops/entrypoint.sh when state/aichat_ng/config.yaml
# is absent. Re-pulling the image will NOT overwrite your local edits; delete
# the file first if you want to re-seed from this template.
#
# API key placeholders (${VAR}) are substituted by ops/entrypoint.sh at
# seed time from the container environment. Do not paste keys directly
# into this template — they are injected once when config.yaml is first
# created and live only in the gitignored state/aichat_ng/config.yaml.
#
# As of #67 (2026-05-04), the only direct provider clients the pipeline
# uses are:
#   - openrouter (every chat call: scoring, prep, briefing, outreach, ...)
#   - gemini-embed (RAG embeddings only — opt-in REPL feature)
# Direct openai / claude / perplexity / gemini-chat / groq / xai clients
# were retired (#250 + #251 + #67); all chat models now reach those
# providers through the openrouter client.  This collapses the plaintext-
# keys surface from 7 → 2 keys at first-seed.
#
# See https://github.com/sigoden/aichat/blob/main/config.example.yaml
# for the full set of supported options.
#
# Note: the model catalog (pricing, token limits, thinking-mode flags)
# lives in models-override.yaml, which entrypoint seeds separately.
# Do NOT add a top-level `models:` block here or inline `models:` blocks
# under existing clients — they will shadow models-override.yaml.

model: openrouter:google/gemini-3-flash-preview
temperature: 0
rag_reranker_model: null
rag_top_k: 6
rag_chunk_size: 1200
rag_chunk_overlap: 200

document_loaders:
  docx: 'pandoc -f docx -t plain $1'
  pdf:  'pdftotext $1 -'

function_calling: false

# Roles are loaded from /app/config/roles (seeded by the image entrypoint);
# ops/entrypoint.sh also creates the expected symlink at
# $AICHAT_CFG_DIR/roles -> /app/config/roles on first container start.

clients:
  - type: openai-compatible
    name: openrouter
    api_base: https://openrouter.ai/api/v1
    api_key: ${OPENROUTER_API_KEY}

"""

RAG_INDEX_CONTENT = "some: rag yaml content\n"


# ---------------------------------------------------------------------------
# Test 1: Fresh state — all three targets present
# ---------------------------------------------------------------------------


def test_fresh_state_all_removed(tmp_path: Path) -> None:
    """gemini-embed + rag_embedding_model + rag index all present → all removed."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(FRESH_CONFIG, encoding="utf-8")

    rags_dir = tmp_path / "rags"
    rags_dir.mkdir()
    rag_index = rags_dir / "job_search_rag.yaml"
    rag_index.write_text(RAG_INDEX_CONTENT, encoding="utf-8")

    result = run_scrub(tmp_path)
    assert result.returncode == 0
    # Operational logs go to stderr (stdout must stay clean for entrypoint
    # smoke tests that capture the output of unrelated container commands).
    assert result.stdout == ""

    stderr = result.stderr
    assert "removed rag_embedding_model setting from" in stderr
    assert "removed gemini-embed client from" in stderr
    assert "removed rag index" in stderr
    assert "no-op" not in stderr

    # --- Content assertions ---
    after = cfg.read_text(encoding="utf-8")

    # Byte-perfect check: output must equal CLEAN_CONFIG exactly
    assert after == CLEAN_CONFIG, (
        "config.yaml content after scrub does not match expected.\n"
        f"--- expected ---\n{CLEAN_CONFIG!r}\n--- got ---\n{after!r}"
    )

    # openrouter client survived
    assert "name: openrouter" in after
    assert "openrouter.ai" in after

    # rag_embedding_model gone
    assert "rag_embedding_model" not in after

    # gemini-embed client block gone (type: gemini, api_base, models section)
    # NOTE: the top-level file-header comments may still mention "gemini-embed"
    # (e.g. "# - gemini-embed (RAG embeddings only ...)") — those are column-0
    # comment lines not part of the client block, so we don't assert their absence.
    # What MUST be absent: the actual client entry and its body.
    assert "  - type: gemini" not in after
    assert "name: gemini-embed" not in after
    assert "gemini-embedding-001" not in after
    assert "max_batch_size" not in after
    assert "generativelanguage.googleapis.com" not in after

    # Other top-level config survived
    assert "model: openrouter:google/gemini-3-flash-preview" in after
    assert "temperature: 0" in after
    assert "document_loaders:" in after
    assert "function_calling: false" in after
    assert "rag_reranker_model: null" in after

    # RAG index deleted
    assert not rag_index.exists()


# ---------------------------------------------------------------------------
# Test 2: Already-clean state — no-op
# ---------------------------------------------------------------------------


def test_already_clean_noop(tmp_path: Path) -> None:
    """No targets present → no-op, exits 0, emits no-op log line."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CLEAN_CONFIG, encoding="utf-8")
    # No rags dir created intentionally

    result = run_scrub(tmp_path)
    assert result.returncode == 0
    assert result.stdout == ""
    assert "no-op (nothing to remove)" in result.stderr

    # File is unchanged
    assert cfg.read_text(encoding="utf-8") == CLEAN_CONFIG


def test_already_clean_second_run_noop(tmp_path: Path) -> None:
    """Running on already-clean config again is also a no-op."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CLEAN_CONFIG, encoding="utf-8")

    run_scrub(tmp_path)  # first run

    result2 = run_scrub(tmp_path)  # second run
    assert result2.returncode == 0
    assert "no-op (nothing to remove)" in result2.stderr
    assert cfg.read_text(encoding="utf-8") == CLEAN_CONFIG


# ---------------------------------------------------------------------------
# Test 3: Idempotency — fresh state, run twice
# ---------------------------------------------------------------------------


def test_idempotent_fresh_state(tmp_path: Path) -> None:
    """Second run after fresh-state scrub must no-op."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(FRESH_CONFIG, encoding="utf-8")

    rags_dir = tmp_path / "rags"
    rags_dir.mkdir()
    (rags_dir / "job_search_rag.yaml").write_text(RAG_INDEX_CONTENT, encoding="utf-8")

    # First run — does work
    r1 = run_scrub(tmp_path)
    assert r1.returncode == 0
    assert "no-op" not in r1.stderr

    after_first = cfg.read_text(encoding="utf-8")

    # Second run — must no-op
    r2 = run_scrub(tmp_path)
    assert r2.returncode == 0
    assert "no-op (nothing to remove)" in r2.stderr

    # File unchanged between runs
    assert cfg.read_text(encoding="utf-8") == after_first


# ---------------------------------------------------------------------------
# Test 4: Custom client survives — gemini-embed gone, others untouched
# ---------------------------------------------------------------------------

CUSTOM_CLIENT_CONFIG = """\
model: openrouter:google/gemini-3-flash-preview
temperature: 0
rag_embedding_model: gemini-embed:gemini-embedding-001

clients:
  - type: openai-compatible
    name: openrouter
    api_base: https://openrouter.ai/api/v1
    api_key: somekey

  # gemini-embed comment block
  - type: gemini
    name: gemini-embed
    api_base: https://generativelanguage.googleapis.com/v1beta
    api_key: somekey2
    models:
      - name: gemini-embedding-001
        type: embedding
        default_chunk_size: 1500
        max_batch_size: 100

  - type: openai-compatible
    name: my-custom
    api_base: https://custom.example.com/v1
    api_key: customkey
"""


def test_custom_client_survives(tmp_path: Path) -> None:
    """openrouter + my-custom survive; gemini-embed is removed."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CUSTOM_CLIENT_CONFIG, encoding="utf-8")

    result = run_scrub(tmp_path)
    assert result.returncode == 0

    after = cfg.read_text(encoding="utf-8")

    # gemini-embed gone
    assert "gemini-embed" not in after
    assert "gemini-embedding-001" not in after
    assert "max_batch_size" not in after

    # openrouter survived
    assert "name: openrouter" in after
    assert "openrouter.ai" in after

    # my-custom survived — this is the key invariant
    assert "name: my-custom" in after
    assert "custom.example.com" in after
    assert "customkey" in after


def test_custom_client_before_gemini_embed(tmp_path: Path) -> None:
    """Custom client BEFORE gemini-embed in the list — both survive correctly."""
    config = """\
model: openrouter:something

clients:
  - type: openai-compatible
    name: my-custom
    api_base: https://custom.example.com/v1
    api_key: customkey

  - type: gemini
    name: gemini-embed
    api_base: https://generativelanguage.googleapis.com/v1beta
    api_key: gkey
    models:
      - name: gemini-embedding-001
        type: embedding

  - type: openai-compatible
    name: openrouter
    api_base: https://openrouter.ai/api/v1
    api_key: orkey
"""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(config, encoding="utf-8")

    result = run_scrub(tmp_path)
    assert result.returncode == 0

    after = cfg.read_text(encoding="utf-8")
    assert "name: my-custom" in after
    assert "name: openrouter" in after
    assert "gemini-embed" not in after


# ---------------------------------------------------------------------------
# Test 5: Malformed YAML (non-UTF-8 bytes) — exit 0, file unchanged
# ---------------------------------------------------------------------------


def test_malformed_non_utf8_file_unchanged(tmp_path: Path) -> None:
    """Random non-UTF-8 bytes: exit 0, stderr SKIPPED, file left unchanged."""
    cfg = tmp_path / "config.yaml"
    raw = b"\xff\xfe\x00invalid\xc0\xc1bytes"
    cfg.write_bytes(raw)

    result = run_scrub(tmp_path)
    assert result.returncode == 0
    assert "SKIPPED" in result.stderr

    # File left unchanged
    assert cfg.read_bytes() == raw


def test_malformed_no_top_level_keys(tmp_path: Path) -> None:
    """File with no top-level keys (all indented garbage) — SKIPPED, unchanged."""
    cfg = tmp_path / "config.yaml"
    content = "  totally: indented\n  and: unparseable\n"
    cfg.write_text(content, encoding="utf-8")

    result = run_scrub(tmp_path)
    assert result.returncode == 0
    assert "SKIPPED" in result.stderr
    assert cfg.read_text(encoding="utf-8") == content


# ---------------------------------------------------------------------------
# Test 6: Missing config.yaml — exit 0, SKIPPED to stderr
# ---------------------------------------------------------------------------


def test_missing_config_yaml(tmp_path: Path) -> None:
    """No config.yaml present: exit 0, stderr SKIPPED, no crash."""
    # Don't create config.yaml
    result = run_scrub(tmp_path)
    assert result.returncode == 0
    assert "SKIPPED" in result.stderr


# ---------------------------------------------------------------------------
# Test 7: Missing rags dir — rag-index removal is a no-op
# ---------------------------------------------------------------------------


def test_missing_rags_dir_noop(tmp_path: Path) -> None:
    """No rags/ dir: rag-index removal step is a no-op; no crash."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CLEAN_CONFIG, encoding="utf-8")
    # No rags dir

    result = run_scrub(tmp_path)
    assert result.returncode == 0
    # Since everything's clean and no rag dir, should be a full no-op
    assert "no-op (nothing to remove)" in result.stderr


# ---------------------------------------------------------------------------
# Test 8: gemini-embed is last client (EOF terminator — no next "  - type:")
# ---------------------------------------------------------------------------


def test_gemini_embed_last_client(tmp_path: Path) -> None:
    """gemini-embed as the last client block (no following '  - type:' line)."""
    config = """\
model: openrouter:something
rag_embedding_model: gemini-embed:gemini-embedding-001

clients:
  - type: openai-compatible
    name: openrouter
    api_base: https://openrouter.ai/api/v1
    api_key: orkey

  - type: gemini
    name: gemini-embed
    api_base: https://generativelanguage.googleapis.com/v1beta
    api_key: gkey
    models:
      - name: gemini-embedding-001
        type: embedding
        default_chunk_size: 1500
        max_batch_size: 100
"""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(config, encoding="utf-8")

    result = run_scrub(tmp_path)
    assert result.returncode == 0

    after = cfg.read_text(encoding="utf-8")
    assert "gemini-embed" not in after
    assert "gemini-embedding-001" not in after
    assert "max_batch_size" not in after
    assert "name: openrouter" in after
    assert "rag_embedding_model" not in after
