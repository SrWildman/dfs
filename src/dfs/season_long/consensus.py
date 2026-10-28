"""Consensus value: how the other manager is likely to price a player.

The market's view is FantasyPros' public rest-of-season expert consensus ranking (ECR, the page anyone sees,
no login; their robots.txt allows `/nfl/rankings/` and asks for 5 s between requests, so it is fetched at most
once a day per scoring format and cached under `data/season/fantasypros/`). The scoring format is the one
closest to the league's points per reception.

A rank becomes points on the same scale as ours: the consensus value of the player ranked r is OUR r-th
highest rest-of-season value in this league, minus the waiver level (our value at the rank of the last
rostered player), floored at 0. So consensus and our model differ only in WHO they rank where, which is what
"buy low" / "sell high" means, and a pile of waiver-level players is worth nothing in a trade.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx
import pandas as pd

from dfs.log import get_logger
from dfs.paths import DATA_DIR
from dfs.player_join import join_key, normalize_team

log = get_logger("season.consensus")

CACHE_DIR = DATA_DIR / "season" / "fantasypros"
MAX_AGE_S = 24 * 3600
PAGES = {1.0: "ros-ppr-overall", 0.5: "ros-half-point-ppr-overall", 0.0: "ros-overall"}
URL = "https://www.fantasypros.com/nfl/rankings/{page}.php"
_ECR = re.compile(r"var ecrData = (\{.*?\});\s*\n", re.S)


class ConsensusError(Exception):
    pass


def page_for(scoring: dict[str, float]) -> str:
    rec = scoring.get("rec", 0.0)
    return PAGES[min(PAGES, key=lambda r: abs(r - rec))]


def parse_ecr(html: str) -> pd.DataFrame:
    m = _ECR.search(html)
    if not m:
        raise ConsensusError("FantasyPros' rankings page has no ecrData; the page may have changed.")
    players = json.loads(m.group(1)).get("players") or []
    rows = [
        {
            "name": p.get("player_name"),
            "team": p.get("player_team_id"),
            "position": "DEF" if p.get("player_position_id") == "DST" else p.get("player_position_id"),
            "ecr": int(p["rank_ecr"]),
        }
        for p in players
        if p.get("rank_ecr") is not None
    ]
    return pd.DataFrame(rows)


def fetch_ecr(scoring: dict[str, float]) -> pd.DataFrame:
    page = page_for(scoring)
    cache: Path = CACHE_DIR / f"{page}.html"
    if not cache.exists() or time.time() - cache.stat().st_mtime > MAX_AGE_S:
        try:
            resp = httpx.get(URL.format(page=page), headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            if not cache.exists():
                raise ConsensusError(f"FantasyPros rankings could not be read: {e}") from e
            log.warning("FantasyPros rankings could not be refreshed (%s); using the cached copy", e)
        else:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(resp.text)
    return parse_ecr(cache.read_text())


def match_to_sleeper(ecr: pd.DataFrame, table: pd.DataFrame) -> pd.Series:
    """ECR rank per Sleeper player_id (a DEF is keyed by its team code, as Sleeper keys it)."""
    off = table[table["position"] != "DEF"]
    keys = {
        join_key(n, t, p): pid
        for pid, n, t, p in zip(off.index, off["name"], off["team"], off["position"], strict=True)
    }
    out = {}
    for name, team, pos, rank in zip(ecr["name"], ecr["team"], ecr["position"], ecr["ecr"], strict=True):
        if pos == "DEF":
            pid = normalize_team(team)
            if pid in table.index:
                out[pid] = rank
            continue
        pid = keys.get(join_key(name, team, pos))
        if pid is not None:
            out[pid] = rank
    return pd.Series(out, name="ecr", dtype="Int64")


def consensus_values(ours: pd.Series, ecr_rank: pd.Series, rostered: int) -> pd.Series:
    """Per player: our value at his consensus rank, over the waiver level. `ours` is our value per player."""
    curve = ours.sort_values(ascending=False).to_numpy()
    if not len(curve):
        return pd.Series(dtype=float)
    waiver = curve[min(max(rostered, 1), len(curve)) - 1]
    ranks = ecr_rank.reindex(ours.index)
    vals = ranks.map(lambda r: curve[min(int(r), len(curve)) - 1] if pd.notna(r) else 0.0)
    return (vals.astype(float) - waiver).clip(lower=0.0).round(1)


def our_over_waiver(ours: pd.Series, rostered: int) -> pd.Series:
    curve = ours.sort_values(ascending=False).to_numpy()
    waiver = curve[min(max(rostered, 1), len(curve)) - 1] if len(curve) else 0.0
    return (ours - waiver).clip(lower=0.0).round(1)
