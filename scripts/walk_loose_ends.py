#!/usr/bin/env python3
"""Dynamic UX loose-end walkthrough entry point (#572 Phase 2).

Walks two personas (nux_user @ findajob-clean, established_user @
findajob-staging) through the YAML itinerary, evaluates each evaluate_dom
step against the cat-2 / cat-3 rubrics, writes a dated roll-up report.

Runs on the dev VM against findajob.paths.BASE — not in the container.

Usage:
    uv run python scripts/walk_loose_ends.py \\
        --persona all \\
        --nux-url https://findajob-clean.<operator-domain>/ \\
        --established-url https://findajob-staging.<operator-domain>/ \\
        --output-dir docs/personal/audit-reports/2026-05-19-walkthrough-run/ \\
        --secrets-file ~/.secrets

Exit codes:
    0 — audit completed within budget
    1 — LLM/network failure or unhandled exception
    2 — cost ceiling exceeded
    4 — target unreachable (connection refused, basic-auth rejected)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from findajob.loose_ends.finding import write_finding
from findajob.loose_ends.roll_up import write_report
from findajob.loose_ends.rubrics import load_exclusions
from findajob.loose_ends.walkthrough import (
    Walkthrough,
    load_walkthroughs,
    run_walkthrough,
)
from findajob.paths import BASE

COST_CEILING_USD = 2.00


def _load_secrets(path: Path) -> dict[str, str]:
    """Parse a KEY=value secrets file. Tolerates `export KEY=value` lines."""
    if not path.exists():
        raise FileNotFoundError(f"Secrets file not found: {path}")
    secrets: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        secrets[k.strip()] = v.strip().strip('"').strip("'")
    return secrets


def _filter_walkthroughs(walkthroughs: list[Walkthrough], persona: str) -> list[Walkthrough]:
    if persona == "all":
        return walkthroughs
    return [w for w in walkthroughs if w.persona == persona]


def _resolve_creds(secrets: dict[str, str], persona: str) -> tuple[str, str]:
    """Pick basic-auth creds per persona, with FINDAJOB_TEST_* as legacy fallback.

    Per-persona keys reflect the per-stack split (clean for nux, staging for
    established); FINDAJOB_TEST_* predates the #565 findajob-test → findajob-clean
    rename and is kept as a fallback so older ~/.secrets layouts keep working.
    """
    primary_user_key = {
        "nux_user": "FINDAJOB_CLEAN_USER",
        "established_user": "FINDAJOB_STAGING_USER",
    }.get(persona, "FINDAJOB_TEST_USER")
    primary_pass_key = {
        "nux_user": "FINDAJOB_CLEAN_PASS",
        "established_user": "FINDAJOB_STAGING_PASS",
    }.get(persona, "FINDAJOB_TEST_PASS")
    user = secrets.get(primary_user_key) or secrets.get("FINDAJOB_TEST_USER", "")
    password = secrets.get(primary_pass_key) or secrets.get("FINDAJOB_TEST_PASS", "")
    return user, password


def _run_with_playwright(
    *,
    walkthroughs: list[Walkthrough],
    base_url: str,
    secrets: dict[str, str],
    exclusions: dict[str, str],
    findings_jsonl: Path,
    persona: str,
) -> tuple[float, int]:
    """Launch Playwright, run each walkthrough, write findings. Returns (cost, count)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "ERROR: playwright not installed. Run: uv run playwright install chromium",
            file=sys.stderr,
        )
        sys.exit(1)

    total_cost = 0.0
    total_findings = 0
    user, password = _resolve_creds(secrets, persona)
    with sync_playwright() as pw:
        launch_kwargs: dict = {"headless": True}
        channel = secrets.get("PLAYWRIGHT_BROWSER_CHANNEL", "")
        if channel:
            launch_kwargs["channel"] = channel
        try:
            browser = pw.chromium.launch(**launch_kwargs)
        except Exception as exc:
            print(f"ERROR: failed to launch browser: {exc}", file=sys.stderr)
            sys.exit(1)
        ctx_kwargs: dict = {}
        if user and password:
            ctx_kwargs["http_credentials"] = {"username": user, "password": password}
        ctx = browser.new_context(**ctx_kwargs)
        page = ctx.new_page()

        # Smoke check: can we even reach the target?
        try:
            page.goto(base_url, wait_until="networkidle", timeout=15000)
        except Exception as exc:
            print(f"ERROR: target unreachable at {base_url}: {exc}", file=sys.stderr)
            browser.close()
            sys.exit(4)

        try:
            for walkthrough in walkthroughs:
                print(f"  → {walkthrough.persona}/{walkthrough.name}")
                findings, cost = run_walkthrough(
                    page=page,
                    walkthrough=walkthrough,
                    base_url=base_url.rstrip("/"),
                    exclusions=exclusions,
                )
                for f in findings:
                    write_finding(findings_jsonl, f)
                    total_findings += 1
                total_cost += cost
                if total_cost > COST_CEILING_USD:
                    print(
                        f"ERROR: cost ${total_cost:.4f} exceeded ${COST_CEILING_USD:.2f} ceiling",
                        file=sys.stderr,
                    )
                    browser.close()
                    sys.exit(2)
        except Exception as exc:
            print(f"ERROR: walkthrough failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            browser.close()
            sys.exit(1)
        browser.close()
    return total_cost, total_findings


def main() -> int:
    parser = argparse.ArgumentParser(description="#572 Phase 2 dynamic UX walkthrough")
    parser.add_argument("--persona", choices=["nux_user", "established_user", "all"], default="all")
    parser.add_argument("--nux-url", default="")
    parser.add_argument("--established-url", default="")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--secrets-file", type=Path, default=None)
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Skip walks; regenerate roll-up from existing findings.jsonl",
    )
    args = parser.parse_args()

    repo_root = Path(BASE)
    walkthroughs_path = repo_root / "config" / "loose_ends_walkthroughs.yaml"
    exclusions_path = repo_root / "config" / "loose_ends_walkthrough_exclusions.yaml"
    if not exclusions_path.exists():
        print(
            f"ERROR: {exclusions_path} missing. Recovery:\n"
            f"  git checkout HEAD -- {exclusions_path.relative_to(repo_root)}",
            file=sys.stderr,
        )
        return 1
    exclusions = load_exclusions(exclusions_path)
    walkthroughs = _filter_walkthroughs(load_walkthroughs(walkthroughs_path), args.persona)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    findings_jsonl = args.output_dir / "findings.jsonl"
    total_cost = 0.0
    total_findings = 0

    if not args.report_only:
        if args.secrets_file is None:
            print("ERROR: --secrets-file is required unless --report-only is set", file=sys.stderr)
            return 1
        secrets = _load_secrets(args.secrets_file.expanduser())
        # NUX persona block
        if args.persona in ("nux_user", "all"):
            nux_walkthroughs = [w for w in walkthroughs if w.persona == "nux_user"]
            if nux_walkthroughs:
                if not args.nux_url:
                    print(
                        "ERROR: --nux-url required when persona=nux_user or all",
                        file=sys.stderr,
                    )
                    return 1
                print(f"NUX persona ({len(nux_walkthroughs)} walkthroughs) @ {args.nux_url}")
                cost, count = _run_with_playwright(
                    walkthroughs=nux_walkthroughs,
                    base_url=args.nux_url,
                    secrets=secrets,
                    exclusions=exclusions,
                    findings_jsonl=findings_jsonl,
                    persona="nux_user",
                )
                total_cost += cost
                total_findings += count
        # Established persona block
        if args.persona in ("established_user", "all"):
            est_walkthroughs = [w for w in walkthroughs if w.persona == "established_user"]
            if est_walkthroughs:
                if not args.established_url:
                    print(
                        "ERROR: --established-url required when persona=established_user or all",
                        file=sys.stderr,
                    )
                    return 1
                print(f"Established persona ({len(est_walkthroughs)} walkthroughs) @ {args.established_url}")
                cost, count = _run_with_playwright(
                    walkthroughs=est_walkthroughs,
                    base_url=args.established_url,
                    secrets=secrets,
                    exclusions=exclusions,
                    findings_jsonl=findings_jsonl,
                    persona="established_user",
                )
                total_cost += cost
                total_findings += count

    # Roll-up
    report_path, prose_cost = write_report(
        findings_jsonl=findings_jsonl,
        exclusions=exclusions,
        output_dir=args.output_dir,
    )
    total_cost += prose_cost

    print(f"\nWalkthrough complete. Findings: {total_findings}")
    print(f"Report: {report_path}")
    print(f"Cost: ${total_cost:.4f} (ceiling ${COST_CEILING_USD:.2f})")

    if total_cost > COST_CEILING_USD:
        print(
            f"ERROR: post-roll-up cost ${total_cost:.4f} exceeded ${COST_CEILING_USD:.2f} ceiling",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
