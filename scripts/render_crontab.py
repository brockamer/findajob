#!/usr/bin/env python3
"""Render the supercronic crontab from ops/scheduled-jobs.yaml + env overrides.

Pure render() function: dict + env mapping → crontab string. Called by
`ops/entrypoint.sh` before `exec supercronic`, with the rendered output
written to /app/crontab. Documented invariant: with no env overrides, the
rendered output must be functionally equivalent to the legacy hand-edited
crontab (verified pre-merge in #344 against an `ops/crontab.legacy` snapshot;
`tests/test_render_crontab.py` retains a skip-when-absent guard for any
future migration that reintroduces the snapshot).

Per-job env-var overrides (#344):
    FINDAJOB_<JOB>_SCHEDULE — cron expression replacement
    FINDAJOB_<JOB>_ENABLED  — "true"/"false"; false drops the line

<JOB> = upper-cased job key with `-` → `_`.

Fail-fast: malformed YAML, missing required fields, or unparseable enabled
override exits non-zero. Entrypoint surfaces this as a noisy restart loop —
silent fallback would mask a broken weekly schedule for days.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping
from pathlib import Path

import yaml

_HEADER = (
    "# /app/crontab — RENDERED at container start by scripts/render_crontab.py.\n"
    "# Source of truth: ops/scheduled-jobs.yaml.\n"
    "# Per-job env-var overrides:\n"
    "#   FINDAJOB_<JOB>_SCHEDULE — cron expression replacement\n"
    "#   FINDAJOB_<JOB>_ENABLED  — 'false' drops the line\n"
    "# DO NOT EDIT in-container — re-render after editing the YAML.\n"
    "\n"
    "PYTHONUNBUFFERED=1\n"
)

_REQUIRED_FIELDS = ("schedule", "command")


class RenderError(Exception):
    """Raised on malformed YAML or invalid env-var override value."""


def _env_var_for(name: str, knob: str) -> str:
    return f"FINDAJOB_{name.replace('-', '_').upper()}_{knob.upper()}"


def _parse_enabled(val: str) -> bool:
    s = val.strip().lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    raise RenderError(f"unrecognized enabled override value: {val!r}")


def render(jobs_yaml: Mapping, env: Mapping[str, str]) -> str:
    """Render the supercronic crontab from the parsed YAML + env overrides.

    Job order in output matches insertion order in the YAML (operator-curated
    ordering — keeps related jobs grouped in the rendered file).
    """
    if "jobs" not in jobs_yaml or not isinstance(jobs_yaml["jobs"], Mapping):
        raise RenderError("scheduled-jobs.yaml: missing or non-mapping `jobs` key")

    parts: list[str] = [_HEADER]
    for name, spec in jobs_yaml["jobs"].items():
        if not isinstance(spec, Mapping):
            raise RenderError(f"scheduled-jobs.yaml: job {name!r} is not a mapping")
        for field in _REQUIRED_FIELDS:
            if field not in spec:
                raise RenderError(f"scheduled-jobs.yaml: job {name!r} missing required field {field!r}")

        # Empty string is treated as "no override" — compose.yaml.example
        # sets FINDAJOB_<JOB>_* to default-empty so operators only need to
        # populate the var in .env to take effect. Without this, a missing
        # .env line would render as "" and break the crontab.
        enabled_env = env.get(_env_var_for(name, "ENABLED"))
        enabled = _parse_enabled(enabled_env) if enabled_env else bool(spec.get("enabled", True))

        description = spec.get("description") or ""
        if not enabled:
            parts.append(f"\n# {name}: DISABLED")
            if description:
                parts.append(f" — {description}")
            parts.append("\n")
            continue

        schedule_env = env.get(_env_var_for(name, "SCHEDULE"))
        schedule = schedule_env if schedule_env else spec["schedule"]
        if description:
            parts.append(f"\n# {name}: {description}\n")
        else:
            parts.append(f"\n# {name}\n")
        parts.append(f"{schedule}   {spec['command']}\n")

    return "".join(parts)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Render supercronic crontab from YAML.")
    p.add_argument("--input", type=Path, required=True, help="path to scheduled-jobs.yaml")
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="output path; defaults to stdout",
    )
    args = p.parse_args(argv)

    try:
        text = args.input.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if not isinstance(data, Mapping):
            raise RenderError(f"{args.input}: top-level YAML must be a mapping")
        rendered = render(data, os.environ)
    except (OSError, yaml.YAMLError, RenderError) as e:
        print(f"render_crontab: FATAL: {e}", file=sys.stderr)
        return 1

    if args.output is not None:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
