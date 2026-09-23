"""Actual DK contest ownership (Phase 6, Part 7.8) -- the highest-
compounding item in the Phase 6 review, per Sam. DraftKings only publishes
real ownership for contests you entered; Sam plays small-field
single-entry, so his own contest history is exactly the sample that
isn't sold commercially, and exactly the thing TFFB's large-field
`ProjOwn` projection is wrong about.

**Investigated live with Sam present, 2026-09-23 (not guessed):**

- DK's per-contest "export full standings" CSV is the source -- a real
  file (`contest-standings-<id>.csv`), found by inspecting that button's
  own href on a real completed contest's results page. Two side-by-side
  tables share the same rows: columns A-F are the entry leaderboard
  (Rank/EntryId/EntryName/TimeRemaining/Points/Lineup), a blank column,
  then H-K are per-player ownership across the whole field
  (Player/Roster Position/%Drafted/FPTS) -- unrelated to the entry table
  except for sharing row numbers by coincidence of the export format.
  Confirmed on a real 100-entry contest that a player used in more than
  one roster-slot TYPE across the field (e.g. some entries started him at
  RB, others in FLEX) gets one row per slot type, each with its own
  partial `%Drafted` -- total ownership is the SUM across a player's rows,
  not any single one of them.
- A per-contest, authenticated-fetch automation (a real export URL,
  `https://www.draftkings.com/contest/exportfullstandingscsv/<id>`, also
  found live the same way) was investigated and built, then deliberately
  NOT shipped: exercising `dfs auth dk`'s saved session tripped DraftKings'
  own bot/geo detection during that same investigation, and Sam decided
  the risk to his real-money account wasn't worth it. This module stays
  file-based on purpose -- `dfs ownership log --csv <file>` reads
  whatever contest-standings export you download by hand, at whatever
  pace is sustainable for you. Revisit the automated path only if Sam
  explicitly asks to reconsider that tradeoff.

**Dropped, not "not yet built":** the calibration view (ActualOwn -
ProjOwn by ownership decile and position, Part 7.8's own Step 3) needed
several weeks of hand-logged contests to be a meaningful analysis. Week 3
follow-ups, Item 3 (2026-09-23): Sam, *"if we can't automate it, I'm not
doing it"* -- with the automated path already ruled out above, that
hand-logging volume isn't coming, so this module's job stops at what it
already does (making one contest's real ownership exist locally, queryable,
for whoever wants to look at it directly). See `docs/planning/PROMPT_DATA.md`'s
7.8 entry and `docs/planning/ROADMAP.md`'s "Deliberately not doing"
section for the full reasoning. `dfs ownership log` itself is unaffected
-- it works, it's tested, and Sam may still use it occasionally; nothing
downstream is waiting on it any more.
"""

from __future__ import annotations

import pandas as pd

from dfs.paths import OWNERSHIP_LOG_FILE, ensure_data_dirs

OWNERSHIP_LOG_COLUMNS = [
    "season",
    "week",
    "contest_id",
    "contest_entries",
    "player",
    "dk_position",
    "pct_drafted",
    "fpts",
]


def _pick_position(positions: pd.Series) -> str:
    """A player can have one ownership row per roster-slot TYPE the field
    used him in (e.g. a real `RB` row and a separate `FLEX` row) -- this
    picks the real, non-FLEX position when one exists, since `FLEX` isn't
    actually a position, just a fallback for the rare case where a player
    somehow has no non-FLEX row at all."""
    non_flex = [p for p in positions if p != "FLEX"]
    return non_flex[0] if non_flex else positions.iloc[0]


def parse_ownership_export(df: pd.DataFrame) -> pd.DataFrame:
    """Parses DK's own "export full standings" CSV shape (see this
    module's docstring) into one row per player: `player`, `dk_position`,
    `pct_drafted` (a 0-1 fraction, summed across that player's roster-slot
    rows), `fpts` (that player's real scored points -- identical across a
    player's rows, confirmed live), `contest_entries` (the real entry
    count, from the unrelated entries table sharing these same CSV rows).

    Raises `KeyError` for a genuinely different CSV shape (a column DK's
    export is expected to have is missing) -- same "raise, don't guess"
    contract every other CSV parser in this codebase already uses
    (`bankroll.parse_contest_history`).
    """
    contest_entries = int(df["Rank"].notna().sum())
    players = df[["Player", "Roster Position", "%Drafted", "FPTS"]].dropna(subset=["Player"]).copy()
    players["Player"] = players["Player"].str.strip()
    players["pct_drafted"] = players["%Drafted"].str.rstrip("%").astype(float) / 100.0

    grouped = players.groupby("Player", as_index=False).agg(
        pct_drafted=("pct_drafted", "sum"),
        fpts=("FPTS", "first"),
        dk_position=("Roster Position", _pick_position),
    )
    grouped["contest_entries"] = contest_entries
    return grouped.rename(columns={"Player": "player"})[
        ["player", "dk_position", "pct_drafted", "fpts", "contest_entries"]
    ]


def already_logged_contest_ids() -> set[str]:
    """Contest IDs already in the durable ownership log -- lets a caller
    skip a contest it's already logged (e.g. if it ever tries to loop
    over a directory of files)."""
    if not OWNERSHIP_LOG_FILE.exists():
        return set()
    existing = pd.read_csv(OWNERSHIP_LOG_FILE, dtype={"contest_id": str})
    return set(existing["contest_id"])


def append_ownership(rows: pd.DataFrame, *, season: int, week: int, contest_id: str) -> int:
    """Stamps `parse_ownership_export`'s output with (season, week,
    contest_id) and appends it to the durable ownership log -- replacing
    any existing rows for this exact `contest_id` first, so re-running
    against the same contest is idempotent rather than a grower of
    duplicates (the same "replace by key, don't just append" discipline
    `sheet_pool_control`'s own accumulator uses, applied to a local file
    instead of a sheet column)."""
    ensure_data_dirs()
    stamped = rows.copy()
    stamped["season"] = season
    stamped["week"] = week
    stamped["contest_id"] = str(contest_id)
    stamped = stamped[OWNERSHIP_LOG_COLUMNS]

    if OWNERSHIP_LOG_FILE.exists():
        existing = pd.read_csv(OWNERSHIP_LOG_FILE, dtype={"contest_id": str})
        existing = existing[existing["contest_id"] != str(contest_id)]
        combined = pd.concat([existing, stamped], ignore_index=True)
    else:
        combined = stamped
    combined.to_csv(OWNERSHIP_LOG_FILE, index=False)
    return len(stamped)
