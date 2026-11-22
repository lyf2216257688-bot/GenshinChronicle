"""Workspace-local scratch space for tests that need repository paths."""

from pathlib import Path


def scratch_root() -> Path:
    path = Path(__file__).resolve().parents[1] / ".local" / "tmp"
    path.mkdir(parents=True, exist_ok=True)
    return path
