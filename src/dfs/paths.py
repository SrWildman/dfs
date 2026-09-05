"""Filesystem layout, anchored to the repo root -- never to the caller's CWD.

The old scripts resolved credentials and data paths relative to whatever
directory the process happened to be launched from (see sheets_uploader.py's
Path(credentials_path) and validate_credentials in the pre-rewrite code), so
`dfs sync` only worked if you happened to be sitting in the project root.
Everything here is computed once, relative to this file's location on disk.
"""

from __future__ import annotations

from pathlib import Path

# src/dfs/paths.py -> src/dfs -> src -> <repo root>
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

CONFIG_FILE = REPO_ROOT / "config.toml"
CONFIG_EXAMPLE_FILE = REPO_ROOT / "config.example.toml"

DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
CURRENT_DIR = DATA_DIR / "current"
PROFILES_DIR = DATA_DIR / "profiles"
MANIFEST_FILE = DATA_DIR / "manifest.json"


def credentials_path(filename: str) -> Path:
    """Resolve a credentials filename against the repo root, not the CWD."""
    p = Path(filename)
    return p if p.is_absolute() else REPO_ROOT / p


def ensure_data_dirs() -> None:
    for d in (RAW_DIR, CURRENT_DIR, PROFILES_DIR):
        d.mkdir(parents=True, exist_ok=True)
