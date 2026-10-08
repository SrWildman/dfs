"""Download helpers shared by the context sources (`ffopportunity`, `nflverse_injuries`, `nflverse_depth`).

Release-asset URLs only, same hosts the other nflverse sources use. A download or parse problem raises
`ContextFetchError`; the sources catch it and return an EMPTY frame (columns only), so one missing file
blanks only the columns that depend on it and a sync carries on. The empty frame also overwrites any
stale `data/current/<name>.csv`, so a failed fetch can never leave last week's numbers on the sheet.
"""

from __future__ import annotations

import io

import httpx
import pandas as pd

NFLVERSE_RELEASES = "https://github.com/nflverse/nflverse-data/releases/download"
FFOPPORTUNITY_URL = (
    "https://github.com/ffverse/ffopportunity/releases/download/latest-data/ep_weekly_{season}.parquet"
)
INJURIES_URL = NFLVERSE_RELEASES + "/injuries/injuries_{season}.parquet"
DEPTH_CHARTS_URL = NFLVERSE_RELEASES + "/depth_charts/depth_charts_{season}.parquet"


class ContextFetchError(Exception):
    """A context file could not be fetched or read (reported as a warning, never fatal)."""

    pass


def download(url: str, *, what: str) -> bytes:
    """GET `url` and return the bytes, or raise `ContextFetchError`."""
    try:
        resp = httpx.get(url, timeout=90, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise ContextFetchError(f"Request for {what} failed: {e}") from e
    return resp.content


def read_parquet(content: bytes, columns: list[str], *, what: str) -> pd.DataFrame:
    """The listed columns that exist in the parquet (a column nflverse drops costs only itself)."""
    try:
        available = pd.read_parquet(io.BytesIO(content)).columns
        return pd.read_parquet(io.BytesIO(content), columns=[c for c in columns if c in available])
    except Exception as e:  # noqa: BLE001 - pyarrow raises its own exception types
        raise ContextFetchError(f"Could not parse {what}: {e}") from e
