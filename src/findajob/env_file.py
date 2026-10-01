"""Atomic, owner-only writes for files that hold secrets (``data/.env``, ``config/gmail.json``).

Every writer of a secret-bearing file goes through :func:`write_private`, and every
``data/.env`` read-modify-write goes through :func:`set_vars`. The rules they enforce:

- **Owner-only from the first byte.** The temp file comes from :func:`tempfile.mkstemp`,
  which creates it 0600 regardless of the process umask, and ``os.replace`` carries that
  mode onto the destination. A secret is never on disk at the umask mode (0644 in the
  image), not even briefly, and an existing world-readable file is tightened by the write.
- **Atomic.** Readers see the old file or the new one, never a partial write. A failure
  removes the temp file and leaves the destination untouched.
- **One line per value.** A value that contains a line break would inject a second
  ``KEY=value`` line, so :func:`set_vars` rejects it before touching the file.
- **Exact keys.** A line is replaced only when it defines the key, parsed the way
  :func:`findajob.paths.load_env` parses it. Comments that mention a key are left alone.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path

_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# State-relative paths of every file that holds a credential: API keys and the Basic
# Auth pair (data/.env), the Gmail app password (config/gmail.json). The web backup
# leaves them out, and restore keeps the target's own copies when a tarball has none.
# gmail_token.json, gsheets_creds.json and ntfy_topic.txt are no longer written, but
# an older state directory can still hold them (ntfy reads the topic file first).
SECRET_STATE_FILES = (
    "data/.env",
    "config/gmail.json",
    "config/gmail_token.json",
    "config/gsheets_creds.json",
    "config/ntfy_topic.txt",
)

# Every character str.splitlines() splits on, plus NUL. The readers of data/.env
# split lines with both ``for line in f`` (\n only) and ``.splitlines()`` (all of
# these), so any one of them can end a value early and start an injected line.
_FORBIDDEN_CHARS = frozenset("\n\r\v\f\x1c\x1d\x1e\x85  \x00")


def write_private(path: Path | str, content: str | bytes) -> None:
    """Atomically replace ``path`` with ``content``, mode 0600 throughout."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8") if isinstance(content, str) else content
    fd, tmp_name = tempfile.mkstemp(prefix=dest.name + ".", suffix=".tmp", dir=str(dest.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, dest)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def check_value(key: str, value: str) -> None:
    """Raise ValueError unless ``key=value`` is exactly one well-formed env line."""
    if not _KEY_RE.fullmatch(key):
        raise ValueError(f"invalid env var name: {key!r}")
    if any(ch in _FORBIDDEN_CHARS for ch in value):
        raise ValueError(f"value for {key} must not contain line breaks or NUL characters")


def _line_key(line: str) -> str | None:
    """The key a line defines, or None for blanks and comments (mirrors paths.load_env)."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    return stripped.partition("=")[0].strip()


def set_vars(path: Path | str, updates: Mapping[str, str | None]) -> None:
    """Set (or, for a ``None`` value, remove) keys in an env file, atomically at mode 0600.

    Other lines, comments and blank lines are kept as they are. An existing
    definition is replaced in place; a later duplicate of it is dropped. A key the
    file does not define yet is appended. All values are validated before the file
    is read, so a rejected value leaves the file untouched.
    """
    for key, value in updates.items():
        check_value(key, value or "")

    env_path = Path(path)
    lines: list[str] = []
    if env_path.is_file():
        text = env_path.read_text(encoding="utf-8")
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()

    out: list[str] = []
    written: set[str] = set()
    for line in lines:
        line_key = _line_key(line)
        if line_key is None or line_key not in updates:
            out.append(line)
            continue
        new_value = updates[line_key]
        if new_value is not None and line_key not in written:
            out.append(f"{line_key}={new_value}")
        written.add(line_key)
    for key, value in updates.items():
        if value is not None and key not in written:
            out.append(f"{key}={value}")

    write_private(env_path, "".join(line + "\n" for line in out))
