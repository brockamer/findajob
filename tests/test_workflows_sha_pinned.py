"""Every GitHub Actions `uses:` reference must be pinned to a full commit SHA.

Float tags (`actions/checkout@v5`) are mutable: a tag-move or upstream-repo
compromise silently runs new code with the job's token — and in
`build-image.yml` that token can publish the `:latest` image the whole fleet
pulls. A 40-char commit SHA freezes the bytes; `.github/dependabot.yml` is the
controlled, reviewable channel that bumps the pins. This guard fails CI if any
workflow regresses to a float tag. See #962.

It asserts the *form* (SHA-pinned), not specific SHAs, so it stays green across
Dependabot bumps.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# tests/ -> repo root -> .github/workflows/. Resolved from this file rather than
# findajob.paths.BASE because the workflows live in the source tree, not under
# the container's JSP_BASE (where .github/ is absent).
WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / ".github" / "workflows"

_SHA_RE = re.compile(r"[0-9a-f]{40}")
# Matches both `- uses: x` (step list item) and `  uses: x` (keyed) forms.
_USES_RE = re.compile(r"^\s*-?\s*uses:\s*(?P<ref>\S+)")


def _uses_refs() -> list[tuple[Path, int, str]]:
    refs: list[tuple[Path, int, str]] = []
    files = sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(WORKFLOWS_DIR.glob("*.yaml"))
    for wf in files:
        for lineno, line in enumerate(wf.read_text().splitlines(), start=1):
            m = _USES_RE.match(line)
            if m:
                refs.append((wf, lineno, m.group("ref")))
    return refs


def test_workflows_dir_exists() -> None:
    assert WORKFLOWS_DIR.is_dir(), f"workflows dir not found at {WORKFLOWS_DIR}"


def test_at_least_one_uses_present() -> None:
    # Guard against a path/glob regression silently making the SHA check vacuous
    # (zero parametrized cases would otherwise pass green).
    assert _uses_refs(), "no `uses:` lines found — path or glob regression?"


@pytest.mark.parametrize(
    "wf, lineno, ref",
    [pytest.param(wf, ln, ref, id=f"{wf.name}:{ln}") for wf, ln, ref in _uses_refs()],
)
def test_uses_is_sha_pinned(wf: Path, lineno: int, ref: str) -> None:
    # Published actions look like 'owner/repo@<git-ref>'. A local action
    # ('./.github/actions/foo') has no '@' and is exempt — there's no upstream
    # tag to move.
    if "@" not in ref:
        pytest.skip(f"{ref} is a local action (no @ref to pin)")
    action, _, git_ref = ref.rpartition("@")
    assert _SHA_RE.fullmatch(git_ref), (
        f"{wf.name}:{lineno} — {action} is pinned to '{git_ref}', not a full "
        f"40-char commit SHA. Float tags are mutable; pin to a SHA "
        f"(Dependabot bumps them). See #962."
    )
