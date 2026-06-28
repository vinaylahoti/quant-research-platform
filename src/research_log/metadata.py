"""
Helpers to capture code/data identity for WS1.

These functions keep the logging call sites small and consistent.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
from typing import Iterable

from config.settings import FEATURE_STORE_DIR


REPO_ROOT = Path(__file__).resolve().parents[2]
GIT_EXECUTABLE = (
    Path.home()
    / ".cache"
    / "codex-runtimes"
    / "codex-primary-runtime"
    / "dependencies"
    / "native"
    / "git"
    / "cmd"
    / "git.exe"
)


def resolve_git_commit() -> str:
    """
    Return the current git commit if available.

    This workspace is not guaranteed to be a git repository, so we return the
    explicit string 'unversioned' when a commit cannot be resolved.
    """

    if not GIT_EXECUTABLE.exists():
        return "unversioned"

    try:
        result = subprocess.run(
            [str(GIT_EXECUTABLE), "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return "unversioned"

    commit = result.stdout.strip()
    return commit or "unversioned"


def compute_data_snapshot_id(paths: Iterable[Path] | None = None) -> str:
    """
    Build a lightweight, deterministic fingerprint of the current parquet store.

    We hash file paths, sizes, and modification times. That is fast enough for
    solo research while still changing whenever the underlying snapshot changes.
    """

    roots = list(paths or [FEATURE_STORE_DIR])
    digest = hashlib.sha256()

    for root in sorted(roots):
        root_path = Path(root)
        if not root_path.exists():
            digest.update(f"missing:{root_path}".encode("utf-8"))
            continue

        for path in sorted(root_path.rglob("*")):
            if not path.is_file():
                continue
            stat = path.stat()
            digest.update(str(path.relative_to(root_path)).encode("utf-8"))
            digest.update(str(stat.st_size).encode("utf-8"))
            digest.update(str(stat.st_mtime_ns).encode("utf-8"))

    return digest.hexdigest()
