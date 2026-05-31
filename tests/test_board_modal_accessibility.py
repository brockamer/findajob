"""Accessibility invariant for board action partials (#886).

Every interactive form control rendered into a board partial must expose an
accessible name to assistive tech. Screen readers announce a control by its
accessible name; an unnamed ``<select>`` is read as a bare "combo box" with no
indication of what it does.

This is a *static-source* invariant: it parses the Jinja templates directly
rather than rendering them, so it needs no DB, app, or request context and runs
on the dev VM. Jinja control/expression tokens are stripped before parsing —
we care about the HTML control structure and its ``aria-*`` / ``<label>``
plumbing, not the interpolated values.

Sibling in spirit to ``tests/test_transparency_invariants.py``: a failure here
means a board control is invisible to screen-reader users.

#886 scope note: the board "modals" are HTMX cell-swaps (a ``<td>`` is replaced
inline), not focus-trapping overlays. ``aria-modal``/``role="dialog"`` are
therefore inapplicable (they require focus management this UI doesn't have); the
swap panels use an honest ``role="group"`` instead. This test guards the part
that *is* enforceable without JS — accessible names on every control.
"""

import re
from pathlib import Path

from bs4 import BeautifulSoup, Tag

BOARD_TEMPLATES = Path(__file__).resolve().parent.parent / "src" / "findajob" / "web" / "templates" / "board"

# Control types that do not surface to the user as named, operable widgets and
# so are exempt from the accessible-name requirement.
_EXEMPT_INPUT_TYPES = {"hidden", "submit", "button", "reset", "image"}

_JINJA = re.compile(r"\{%.*?%\}|\{\{.*?\}\}", re.DOTALL)


def _strip_jinja(source: str) -> str:
    """Remove Jinja control/expression tokens, leaving the HTML skeleton.

    ``name="{{ fp }}"`` becomes ``name=""`` and ``{% if x %}<i>{% endif %}``
    becomes ``<i>`` — both fine for structural + attribute-presence checks.
    """
    return _JINJA.sub("", source)


def _has_accessible_name(control: Tag, label_fors: set[str]) -> bool:
    if control.get("aria-label", "").strip():
        return True
    if control.get("aria-labelledby", "").strip():
        return True
    # Explicit <label for="id"> association.
    control_id = control.get("id")
    if control_id and control_id in label_fors:
        return True
    # Implicit association — control nested inside a <label>.
    if control.find_parent("label") is not None:
        return True
    # A <title> child (rare for form controls, but valid).
    if control.find("title") is not None:
        return True
    return False


def _requires_name(control: Tag) -> bool:
    if control.name in ("select", "textarea"):
        return True
    if control.name == "input":
        return control.get("type", "text").lower() not in _EXEMPT_INPUT_TYPES
    return False


def test_every_board_control_has_accessible_name() -> None:
    """No ``<select>``/``<textarea>``/named ``<input>`` in board/ is unnamed."""
    failures: list[str] = []

    for template in sorted(BOARD_TEMPLATES.glob("*.html")):
        soup = BeautifulSoup(_strip_jinja(template.read_text()), "html.parser")
        label_fors = {lbl["for"] for lbl in soup.find_all("label") if lbl.get("for")}
        for control in soup.find_all(("select", "input", "textarea")):
            if not _requires_name(control):
                continue
            if not _has_accessible_name(control, label_fors):
                snippet = " ".join(str(control).split())[:120]
                failures.append(f"{template.name}: {snippet}")

    assert not failures, "Board controls missing an accessible name:\n" + "\n".join(failures)


def test_swap_panels_declare_group_role() -> None:
    """The inline cell-swap panels expose a named ``role='group'`` boundary.

    Guards the #886 reshape: these are not overlay dialogs, so the honest role
    is ``group`` with a label — never ``aria-modal``/``role='dialog'``, which
    would falsely promise focus containment.
    """
    panels = {
        "_exclude_modal.html",
        "_reattribute_modal.html",
        "_confirm_modal.html",
    }
    for name in panels:
        soup = BeautifulSoup(_strip_jinja((BOARD_TEMPLATES / name).read_text()), "html.parser")
        group = soup.find(attrs={"role": "group"})
        assert group is not None, f"{name}: expected a role='group' container"
        named = bool(group.get("aria-label", "").strip()) or bool(group.get("aria-labelledby", "").strip())
        assert named, f"{name}: role='group' container lacks an accessible name"
        # The anti-pattern guard: no false modal semantics anywhere in the panel.
        assert soup.find(attrs={"aria-modal": True}) is None, (
            f"{name}: aria-modal is inappropriate for an inline cell-swap panel"
        )
        assert soup.find(attrs={"role": "dialog"}) is None, (
            f"{name}: role='dialog' implies focus semantics this panel lacks"
        )
