"""In-app feedback widget that files a GitHub issue per submission (#227).

Reads three env vars at request time (so per-stack `state/data/.env` edits
take effect on container restart with no rebuild):

- ``GITHUB_FEEDBACK_PAT`` — fine-grained PAT scoped to ``Issues: read+write``
  on the target repo. Held server-side; never reaches the browser.
- ``FEEDBACK_STACK_LABEL`` — single label identifying which stack the
  report came from (e.g. ``from:operator`` / ``from:alice-doe``). Optional;
  defaults to no second label.
- ``FEEDBACK_REPO`` — ``owner/repo`` to file into. Defaults to
  ``brockamer/findajob``.

The PAT is intentionally not pre-validated at app startup; a missing PAT
surfaces as a 503 on the first submit attempt with a user-friendly message.
That keeps the widget out of the way on dev stacks that don't have it set.
"""

from __future__ import annotations

import os

import requests
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

router = APIRouter()

_GITHUB_API = "https://api.github.com"
_DEFAULT_REPO = "brockamer/findajob"
_TITLE_MAX = 70
_BODY_MAX = 4000


@router.post("/feedback/submit", response_class=HTMLResponse)
def submit_feedback(
    request: Request,
    text: str = Form(...),
    page_url: str = Form(""),
) -> HTMLResponse:
    """Accept a feedback submission, file a GitHub issue, return a result partial."""
    text = text.strip()
    if not text:
        return _result_partial(request, ok=False, message="Please write something before submitting.", status=400)
    if len(text) > _BODY_MAX:
        return _result_partial(
            request,
            ok=False,
            message=f"Too long ({len(text)} chars). Please trim to under {_BODY_MAX}.",
            status=400,
        )

    pat = os.environ.get("GITHUB_FEEDBACK_PAT", "").strip()
    if not pat:
        return _result_partial(
            request,
            ok=False,
            message="Feedback isn't configured on this stack. Tell the operator.",
            status=503,
        )

    repo = os.environ.get("FEEDBACK_REPO", _DEFAULT_REPO).strip() or _DEFAULT_REPO
    stack_label = os.environ.get("FEEDBACK_STACK_LABEL", "").strip()
    labels = ["feedback"]
    if stack_label:
        labels.append(stack_label)

    title = _build_title(text)
    body = _build_body(text=text, page_url=page_url.strip(), stack_label=stack_label)

    try:
        resp = requests.post(
            f"{_GITHUB_API}/repos/{repo}/issues",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {pat}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"title": title, "body": body, "labels": labels},
            timeout=10,
        )
    except requests.RequestException:
        return _result_partial(
            request,
            ok=False,
            message="Couldn't reach GitHub. Try again in a moment.",
            status=502,
        )

    if resp.status_code >= 300:
        return _result_partial(
            request,
            ok=False,
            message=f"GitHub rejected the submission (HTTP {resp.status_code}). Tell the operator.",
            status=502,
        )

    issue_url = (resp.json() or {}).get("html_url", "")
    return _result_partial(request, ok=True, message="Thanks — feedback filed.", issue_url=issue_url)


def _build_title(text: str) -> str:
    first_line = text.splitlines()[0].strip() if text else "feedback"
    if len(first_line) > _TITLE_MAX:
        first_line = first_line[: _TITLE_MAX - 1].rstrip() + "…"
    return first_line or "feedback"


def _build_body(*, text: str, page_url: str, stack_label: str) -> str:
    parts = [text, "", "---", "_Filed via in-app feedback widget._"]
    if page_url:
        parts.append(f"- Page: {page_url}")
    if stack_label:
        parts.append(f"- Stack: `{stack_label}`")
    return "\n".join(parts)


def _result_partial(
    request: Request,
    *,
    ok: bool,
    message: str,
    status: int = 200,
    issue_url: str = "",
) -> HTMLResponse:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request=request,
        name="_feedback_result.html",
        context={"ok": ok, "message": message, "issue_url": issue_url},
        status_code=status,
    )
