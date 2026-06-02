"""scripts/resolve_timezone.py — entrypoint shim that prints the picked IANA
zone (exit 0) or exits 1 silently. Driven via JSP_BASE so the test points it at
a tmp state root (paths.BASE honors JSP_BASE; see src/findajob/paths.py)."""

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "resolve_timezone.py"


def _run(base: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "JSP_BASE": str(base)}
    return subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, env=env)


def test_prints_zone_and_exits_zero_for_valid_pick(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "timezone").write_text("Asia/Tokyo\n", encoding="utf-8")
    result = _run(tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "Asia/Tokyo"


def test_exits_one_silently_when_missing(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    result = _run(tmp_path)
    assert result.returncode == 1
    assert result.stdout.strip() == ""


def test_exits_one_for_invalid_zone(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "timezone").write_text("Not/AZone\n", encoding="utf-8")
    result = _run(tmp_path)
    assert result.returncode == 1
