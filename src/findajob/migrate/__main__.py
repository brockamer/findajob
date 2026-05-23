"""CLI entry: ``python -m findajob.migrate <subcommand> ...`` (#816).

Three subcommands:

- ``export`` — produce a tarball from a stopped stack's state dir.
- ``import-fly`` — push a tarball into a freshly-provisioned Fly app.
- ``verify`` — read a state dir's bundled manifest.json and emit a JSON
  ``VerifyResult`` to stdout. Invoked over ssh by the importer; also
  runnable locally against an extracted tarball for spot-checks.

Why ``import-fly`` not ``import``: ``import`` is a Python keyword. Even
in an argparse subcommand string, having a function or module named
``import`` invites typos and tooling pain. ``import-fly`` also makes
the transport explicit, in case a future ``import-volume`` or
``import-local`` shows up.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from findajob.migrate import exporter, importer, verifier


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m findajob.migrate")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_export = sub.add_parser("export", help="export a stopped stack's state to a tarball")
    p_export.add_argument("--state-dir", type=Path, required=True)
    p_export.add_argument("--tarball", type=Path, required=True)
    p_export.add_argument("--stack-tag", required=True, help="e.g. findajob-staging")
    p_export.add_argument("--dry-run", action="store_true")

    p_import = sub.add_parser("import-fly", help="push a tarball into a Fly app")
    p_import.add_argument("--tarball", type=Path, required=True)
    p_import.add_argument("--app", required=True, help="Fly app slug")
    p_import.add_argument("--force", action="store_true", help="bypass manifest-exists guard")

    p_verify = sub.add_parser("verify", help="verify an extracted state dir against its manifest")
    p_verify.add_argument("--state-dir", type=Path, required=True)

    args = parser.parse_args(argv)

    if args.cmd == "export":
        return _cmd_export(args)
    if args.cmd == "import-fly":
        return _cmd_import(args)
    if args.cmd == "verify":
        return _cmd_verify(args)
    parser.error(f"unknown subcommand: {args.cmd}")
    return 2  # unreachable; argparse exits


def _cmd_export(args: argparse.Namespace) -> int:
    try:
        result = exporter.export(
            state_dir=args.state_dir,
            tarball_path=args.tarball,
            source_stack_tag=args.stack_tag,
            dry_run=args.dry_run,
        )
    except (FileNotFoundError, FileExistsError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"tarball": str(result.tarball_path), "manifest": asdict(result.manifest), "dry_run": result.dry_run},
            indent=2,
        )
    )
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    transport = importer.FlyTransport(app=args.app)
    try:
        result = importer.import_to_fly(tarball=args.tarball, transport=transport, force=args.force)
    except (FileNotFoundError, importer.TargetNotEmptyError, importer.RemoteCommandError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    payload = {"ok": result.ok, "failures": result.failures, "observed": result.observed}
    print(json.dumps(payload, indent=2))
    return 0 if result.ok else 1


def _cmd_verify(args: argparse.Namespace) -> int:
    try:
        result = verifier.verify(state_dir=args.state_dir)
    except FileNotFoundError as e:
        # Emit JSON even on the missing-manifest case so callers (the importer)
        # never have to handle "is this stdout or an exception" ambiguity.
        print(json.dumps({"ok": False, "failures": [str(e)], "observed": {}, "manifest_path": ""}))
        return 1
    payload = {
        "ok": result.ok,
        "failures": result.failures,
        "observed": result.observed,
        "manifest_path": str(result.manifest_path),
    }
    print(json.dumps(payload, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
