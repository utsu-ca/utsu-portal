"""Version and source revision of the running server, shown in the site footer."""
from __future__ import annotations

import re
import subprocess
from importlib import metadata
from pathlib import Path

from .std.utils import find_project_root

_COMMIT = re.compile(r"[0-9a-fA-F]{7,40}")


def package_version() -> str:
    try:
        return metadata.version("utsu-core")
    except metadata.PackageNotFoundError:
        return "unknown"


def _git_commit() -> str | None:
    root = find_project_root(Path(__file__))
    if not (root / ".git").exists():
        return None
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=2, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out or None


def server_version(configured_commit: str | None = None) -> dict[str, str | None]:
    """Configured commit wins (deployed images carry no .git); a development checkout falls back to git."""
    commit = (configured_commit or "").strip() or _git_commit()
    if commit and not _COMMIT.fullmatch(commit):
        commit = None
    return {"version": package_version(), "commit": commit[:12].lower() if commit else None}
