"""#1219 — a row that leaves the board tints for its outcome, then fades out.

Server half: tests/test_board_actions.py::TestRowOutcomeHeader (the X-Row-Outcome
header). Client half, pinned here: /static/row_fade.js reads that header on
htmx:beforeSwap, tints the row and delays the swap; app.css draws the tint and
the fade.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from findajob.onboarding import mark_complete
from findajob.web.app import create_app
from findajob.web.routes.board_actions import ROW_OUTCOME_HEADER
from tests.conftest import init_test_db

STATIC = Path(__file__).resolve().parent.parent / "src" / "findajob" / "web" / "static"
OUTCOMES = ("rejected", "waitlisted", "applied", "flagged")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "pipeline.db"
    init_test_db(db)
    companies = tmp_path / "companies"
    companies.mkdir()
    mark_complete(tmp_path)
    return TestClient(create_app(companies_root=companies, db_path=db, base_root=tmp_path))


def test_board_pages_load_the_row_fade_script(client: TestClient) -> None:
    r = client.get("/board/waitlist")
    assert r.status_code == 200
    assert '<script src="/static/row_fade.js" defer></script>' in r.text


def test_script_reads_the_header_the_server_sends() -> None:
    js = (STATIC / "row_fade.js").read_text()
    assert ROW_OUTCOME_HEADER in js


@pytest.mark.parametrize("outcome", OUTCOMES)
def test_every_server_outcome_has_a_tint(outcome: str) -> None:
    css = (STATIC / "app.css").read_text()
    assert f'tr[data-row-outcome="{outcome}"]' in css


def test_fade_duration_is_one_custom_property_inside_the_allowed_range() -> None:
    css = (STATIC / "app.css").read_text()
    m = re.search(r"--row-fade-ms:\s*(\d+)ms", css)
    assert m is not None, "app.css must define --row-fade-ms"
    assert 300 <= int(m.group(1)) <= 1000


def test_reduced_motion_turns_the_fade_off_but_keeps_the_tint() -> None:
    css = (STATIC / "app.css").read_text()
    block = re.search(r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\}", css, re.S)
    assert block is not None, "app.css must have a prefers-reduced-motion block"
    assert "htmx-swapping" in block.group(1)
    assert "transition: none" in block.group(1)
    assert "opacity: 1" in block.group(1)


# ── listener behaviour, run in node against a stubbed browser ──────────────

_HARNESS = textwrap.dedent(
    """
    const fs = require('fs');
    const vm = require('vm');
    const cfg = JSON.parse(process.argv[1]);  // `node -e`: argv[0] is node, extras start at [1]
    let handler = null;
    const sandbox = {
      document: { body: { addEventListener: (name, fn) => { if (name === 'htmx:beforeSwap') handler = fn; } },
                  documentElement: {} },
      window: {
        getComputedStyle: () => ({ getPropertyValue: () => cfg.cssValue }),
        matchMedia: () => ({ matches: cfg.reduced }),
      },
    };
    sandbox.window.document = sandbox.document;
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(cfg.script, 'utf8'), sandbox);
    const tr = { tagName: cfg.tag, dataset: {} };
    const header = (n) => (n.toLowerCase() === cfg.header.toLowerCase() ? cfg.outcome : null);
    const evt = { detail: { shouldSwap: cfg.shouldSwap, target: tr, xhr: { getResponseHeader: header } } };
    handler(evt);
    console.log(JSON.stringify({
      outcome: tr.dataset.rowOutcome ?? null,
      swapOverride: evt.detail.swapOverride ?? null,
    }));
    """
)


def _fire(**overrides: object) -> dict[str, str | None]:
    cfg = {
        "script": str(STATIC / "row_fade.js"),
        "header": ROW_OUTCOME_HEADER,
        "outcome": "rejected",
        "tag": "TR",
        "shouldSwap": True,
        "reduced": False,
        "cssValue": " 500ms",
    }
    cfg.update(overrides)
    out = subprocess.run(
        ["node", "-e", _HARNESS, json.dumps(cfg)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@needs_node
def test_listener_tints_the_row_and_delays_the_swap_by_the_css_duration() -> None:
    assert _fire() == {"outcome": "rejected", "swapOverride": "outerHTML swap:500ms"}


@needs_node
@pytest.mark.parametrize(("css", "expected"), [("100ms", 300), ("5000ms", 1000), ("junk", 500)])
def test_listener_clamps_the_duration_to_300_1000_ms(css: str, expected: int) -> None:
    assert _fire(cssValue=css)["swapOverride"] == f"outerHTML swap:{expected}ms"


@needs_node
def test_listener_keeps_the_tint_brief_under_reduced_motion() -> None:
    assert _fire(reduced=True) == {"outcome": "rejected", "swapOverride": "outerHTML swap:300ms"}


@needs_node
def test_listener_ignores_a_response_without_the_header() -> None:
    assert _fire(outcome=None) == {"outcome": None, "swapOverride": None}


@needs_node
def test_listener_ignores_a_target_that_is_not_a_table_row() -> None:
    assert _fire(tag="TD") == {"outcome": None, "swapOverride": None}


@needs_node
def test_listener_ignores_a_response_htmx_will_not_swap() -> None:
    assert _fire(shouldSwap=False) == {"outcome": None, "swapOverride": None}
