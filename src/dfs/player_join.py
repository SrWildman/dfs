"""Part C, C1: joins a free external source (Sleeper, FantasyPros, nflverse
snap counts -- none of which carry a DraftKings player ID) onto DK's own
`Id`, by `(normalized name, team, position)`. One matching function, used
by every source, so a join fix in one place fixes it everywhere.

DSTs match by team only (DK names them by nickname, "Texans"; every free
source names them by team code or full name) -- callers pass `is_dst=True`
for the DST slice of a source's data and this module ignores name/position
entirely for that slice, matching purely on the normalized team code.

`data/raw/`-style local caching is source-specific (see sources/sleeper_
projections.py etc.), not this module's job -- this module is pure and
offline-testable, like derived.py.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from dfs.paths import REPO_ROOT

# Committed, hand-maintained residue file for the join misses normalization
# and team/position matching can't fix: apostrophes a source drops, a
# nickname a source uses instead of a legal name, a mid-season trade where
# DK's own Team hasn't caught up yet. Lives outside `data/` (gitignored
# wholesale -- see .gitignore) because this one, unlike everything under
# `data/`, is source code: hand-edited, reviewed, and meant to survive
# `data/`'s own churn. Holds no secrets -- just (DK Id, source, alias
# name) triples.
PLAYER_ALIASES_FILE = REPO_ROOT / "src" / "dfs" / "player_aliases.csv"

_SUFFIXES = {"JR", "SR", "II", "III", "IV", "V"}
# Matches nflverse's own team-code drift already handled at
# sources/nflverse_games.py's NFLVERSE_TO_DK_TEAM -- kept as a SEPARATE
# constant (not imported from there) because it's addressing the same
# real-world fact (nflverse calls the Rams "LA") for a different pair of
# columns, and because nflverse's own `teams.csv` `draft_kings` column
# turned out NOT to be a clean drop-in replacement (see this module's
# `load_team_crosswalk` docstring) -- so there's deliberately no shared
# import to accidentally couple the two if one changes for its own reason.
TEAM_ALIASES = {"LA": "LAR", "JAC": "JAX", "WSH": "WAS", "GBP": "GB", "KAN": "KC", "NWE": "NE", "NOR": "NO"}


def normalize_name(name: object) -> str:
    """casefold, strip punctuation and diacritics, drop suffix tokens
    (Jr/Sr/II/III/IV/V) -- "Michael Pittman Jr." and "Odell Beckham Jr"
    both normalize to a name with no trailing suffix, "gabe davis" and
    "gabriel davis" still don't match (this function doesn't guess
    nicknames; that's what the alias file is for)."""
    if not isinstance(name, str) or not name.strip():
        return ""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    text = text.casefold()
    text = re.sub(r"[^\w\s]", "", text)
    tokens = [t for t in text.split() if t.upper() not in _SUFFIXES]
    return " ".join(tokens)


def normalize_team(team: object) -> str:
    """Uppercased team code, with the handful of confirmed source-side
    drifts in `TEAM_ALIASES` rewritten to DK's own code. Add to that dict
    only once a real drift is confirmed against a real pull -- don't guess
    ahead of what's actually been seen (same discipline nflverse_games.py's
    own crosswalk uses)."""
    if not isinstance(team, str) or not team.strip():
        return ""
    code = team.strip().upper()
    return TEAM_ALIASES.get(code, code)


def join_key(name: object, team: object, position: object) -> str:
    """The (normalized name, team, position) tuple as a single string key --
    never name alone (same-name players exist across teams/positions)."""
    return f"{normalize_name(name)}|{normalize_team(team)}|{str(position).strip().upper()}"


def dst_join_key(team: object) -> str:
    """DSTs match by team only -- DK's own DST `Name` is a nickname
    ("Texans"), not the source's team code/full name, so there is no
    normalized-name field in common to match on."""
    return f"DST|{normalize_team(team)}"


@dataclass
class PlayerAlias:
    dk_id: str
    source: str
    alias_name: str


def load_player_aliases(path: Path | None = None) -> list[PlayerAlias]:
    """The hand-maintained override file, or an empty list if it doesn't
    exist yet (a fresh checkout before anyone's needed an override) --
    never an error; this file starts empty and grows only as real misses
    turn up."""
    path = path or PLAYER_ALIASES_FILE
    if not path.exists():
        return []
    df = pd.read_csv(path, dtype=str).fillna("")
    return [PlayerAlias(row["dk_id"], row["source"], row["alias_name"]) for _, row in df.iterrows()]


@dataclass
class JoinResult:
    matched: (
        pd.DataFrame
    )  # dk_frame's own rows/columns, plus every source_frame column, for matched players only
    pool_total: int
    pool_matched: int
    unmatched_pool_names: list[str] = field(default_factory=list)
    # {position: (matched, pool_total)} -- C1's own required "per source,
    # per position" breakdown, over the same rosterable-pool population.
    by_position: dict[str, tuple[int, int]] = field(default_factory=dict)


def join_source_to_dk(
    dk_frame: pd.DataFrame,
    source_frame: pd.DataFrame,
    *,
    source_name_col: str,
    source_team_col: str,
    source_position_col: str,
    source: str,
    dk_id_col: str = "Id",
    dk_name_col: str = "Name",
    dk_team_col: str = "Team",
    dk_position_col: str = "Position",
    pool_mask: pd.Series | None = None,
) -> JoinResult:
    """Match every `source_frame` row to a DK player Id in `dk_frame`.

    Primary match: `(normalized name, team, position)`, DSTs by team alone
    (`dk_position_col == "DST"`). Residue goes through `PLAYER_ALIASES_FILE`
    next, keyed by DK Id: an alias row says "this source's normalized name
    for this DK Id is `alias_name`", so a row that still doesn't match by
    key gets one more chance via the alias's own normalized name.

    `pool_mask` (aligned to `dk_frame`'s index) restricts what counts
    toward `pool_matched`/`pool_total`/`unmatched_pool_names`/`by_position`
    to the rosterable pool (`VAL_ADJ_ROSTERABLE_TOP_N`) -- per C1, a missed
    third-string TE is noise, a missed starter is a bug. When omitted,
    every `dk_frame` row counts.

    Never fails silently: an unmatched ROSTERABLE player is named in
    `unmatched_pool_names`, meant to be printed and/or written to a file
    by the caller on every sync (see C1's own "never fail silently" rule)."""
    dk = dk_frame.copy()
    is_dst = dk[dk_position_col].astype(str).str.upper() == "DST"
    dk["_join_key"] = [
        dst_join_key(t) if dst else join_key(n, t, p)
        for dst, n, t, p in zip(is_dst, dk[dk_name_col], dk[dk_team_col], dk[dk_position_col], strict=True)
    ]

    src = source_frame.copy()
    src_is_dst = src[source_position_col].astype(str).str.upper() == "DST"
    src["_join_key"] = [
        dst_join_key(t) if dst else join_key(n, t, p)
        for dst, n, t, p in zip(
            src_is_dst, src[source_name_col], src[source_team_col], src[source_position_col], strict=True
        )
    ]

    merged = dk.merge(src, on="_join_key", how="left", suffixes=("", "_src"), indicator=True)
    unmatched_mask = merged["_merge"] == "left_only"

    if unmatched_mask.any():
        aliases = [a for a in load_player_aliases() if a.source == source]
        alias_by_id = {a.dk_id: a.alias_name for a in aliases}
        if alias_by_id:
            src_by_alias_key: dict[str, pd.Series] = {}
            for _, row in src.iterrows():
                src_by_alias_key.setdefault(normalize_name(row[source_name_col]), row)
            for idx in merged.index[unmatched_mask]:
                dk_id = str(merged.at[idx, dk_id_col])
                alias_name = alias_by_id.get(dk_id)
                if alias_name is None:
                    continue
                src_row = src_by_alias_key.get(normalize_name(alias_name))
                if src_row is None:
                    continue
                for col in src.columns:
                    if col == "_join_key":
                        continue
                    target_col = col if col not in dk.columns else f"{col}_src"
                    merged.at[idx, target_col] = src_row[col]
                merged.at[idx, "_merge"] = "both"

    matched_mask = merged["_merge"] == "both"
    merged = merged.drop(columns=["_join_key", "_merge"])

    if pool_mask is not None:
        in_pool = pool_mask.reindex(dk_frame.index, fill_value=False).to_numpy()
    else:
        in_pool = pd.Series(True, index=dk_frame.index).to_numpy()
    matched_arr = matched_mask.to_numpy()

    by_position: dict[str, tuple[int, int]] = {}
    for pos in sorted(dk_frame[dk_position_col].unique()):
        pos_mask = (dk_frame[dk_position_col] == pos).to_numpy() & in_pool
        by_position[pos] = (int((matched_arr & pos_mask).sum()), int(pos_mask.sum()))

    unmatched_pool_names = dk_frame.loc[(~matched_arr) & in_pool, dk_name_col].tolist()

    return JoinResult(
        matched=merged[matched_arr].reset_index(drop=True),
        pool_total=int(in_pool.sum()),
        pool_matched=int((matched_arr & in_pool).sum()),
        unmatched_pool_names=unmatched_pool_names,
        by_position=by_position,
    )


def match_rate_report(results: dict[str, JoinResult]) -> pd.DataFrame:
    """C1's own required output, as a printable table: match rate per
    source, per position, over the rosterable pool -- one row per (source,
    position), built straight from each `JoinResult.by_position`."""
    rows = [
        {
            "Source": source,
            "Position": pos,
            "Matched": matched,
            "Pool": pool_total,
            "Rate": round(100 * matched / pool_total, 1) if pool_total else 0.0,
        }
        for source, result in results.items()
        for pos, (matched, pool_total) in sorted(result.by_position.items())
    ]
    return pd.DataFrame(rows)
