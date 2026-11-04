"""A sync never wipes data: the merge every sync applies before it saves a source or writes its tab.

Found in Week 7's own data (Sun Oct 25, the 3:18 pm ET `dfs sync --live`): once the 1 pm games kicked off,
DraftKings stopped listing the main slate as upcoming, so the salary source picked the 4 pm-only slate
(156 players, new player IDs). Nothing joined: every DK `Status` vanished from EdgeRaw (102 OUT / IR / Q
players lost their `Avail`, 98 their flags), `DKSalRaw` (and so DkSalClean / PlayerPoolRaw, which read it row
by row) held the wrong slate, and the odds feed dropped the seven started games (`TotMove` / `SpdMove`
blank for all 359 started players). TFFB kept revising started players' projections after kickoff (98 of
them moved). Sam stopped running the Sunday sync because of it.

The rules, applied by `run_sync` to every source that has a `Policy` below, before anything is saved:

1. **Started players are frozen.** A row whose game has kicked off (and is in this NFL week) keeps every
   value from the last sync, so the last pre-kickoff values survive any number of later syncs. Only
   players whose game has not started update.
2. **A row the feed no longer has is kept** (this week's rows only), with its last values.
3. **A populated column is never wiped.** When more than `BLANK_GUARD_WIPE_SHARE` (20%) of the rows that had
   a value in a column come back blank at once, those rows keep their previous value. A smaller loss passes
   through (mid-week a source normally drops a few players: Week 9's FantasyPros blanked 9 of 374, Xavier
   Legette on IR and Jacoby Brissett a backup, and the old guard kept their numbers) and the sync lists who
   lost a value. A player DraftKings marks OUT or IR is never held, wipe or not. Columns where blank is a
   real state (DK's `Status`: a cleared Questionable; EdgeRaw's flags) are exempt.
4. **DraftKings switching slates after lock** (rule 1's cause above): when the feed's IDs are mostly new
   and the slate on file has started, the slate on file is kept and only the unstarted players' `Status`
   is refreshed from the new feed, matched by name and team.

Each rule that fires returns a plain line for the sync to print. Nothing here touches a sheet or the disk.
`late-swap` never syncs (it reads `data/current/edge.csv` and ESPN), so this is what keeps the Sunday
`dfs sync --live` safe to run at any time.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from dfs import nfl_calendar
from dfs.kickoff import KICKOFF_TIMEZONE, kickoff_utc

# A wipe (rule 3): more than this share of a column's populated rows come back blank in one sync. Anything
# smaller is a source dropping a few players, which is normal and passes through.
BLANK_GUARD_WIPE_SHARE = 0.20

# DraftKings statuses that mean "not playing": such a player's values are never held (rule 3).
NOT_PLAYING = frozenset({"OUT", "IR"})

# A feed is "a different slate" when fewer than this share of its keys were on file.
DIFFERENT_SLATE_OVERLAP = 0.5

# How many names a note lists before "and N more".
_NOTE_NAMES = 5


@dataclass(frozen=True)
class Policy:
    """How one source's rows are matched and timed. `key` names the column that identifies a row;
    `kickoffs(frame)` gives each row's kickoff as a UTC instant (NaT when unknown); `label` is the column a
    note names rows by; `blank_exempt` the columns where blank is a real value; `sort_by` re-sorts the
    result (descending) when kept rows were appended."""

    key: str
    kickoffs: Callable[[pd.DataFrame], pd.Series]
    label: str
    blank_exempt: frozenset[str] = frozenset()
    sort_by: str | None = None
    slate_switch: bool = False  # rule 4 (DraftKings only)
    status_column: str | None = None  # DK's status for the row (rule 3 never holds an OUT / IR player)


@dataclass
class GuardResult:
    frame: pd.DataFrame
    notes: list[str] = field(default_factory=list)


def _game_start_kickoffs(frame: pd.DataFrame) -> pd.Series:
    if "GameStart" not in frame.columns:
        return pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]")
    return kickoff_utc(frame["GameStart"])


_DK_GAME_INFO = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)", re.IGNORECASE)


def _dk_kickoffs(frame: pd.DataFrame) -> pd.Series:
    """DK's `Game Info` ("GB@DET 10/25/2026 04:25PM ET") as UTC instants; the time is Eastern."""
    if "Game Info" not in frame.columns:
        return pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]")
    parts = frame["Game Info"].astype(str).str.extract(_DK_GAME_INFO)
    naive = pd.to_datetime(
        parts[0] + " " + parts[1].str.replace(" ", ""), format="%m/%d/%Y %I:%M%p", errors="coerce"
    )
    return naive.dt.tz_localize(KICKOFF_TIMEZONE, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")


def _odds_kickoffs(frame: pd.DataFrame) -> pd.Series:
    """The odds feed's `date` (Eastern wall clock, "2026-10-25 16:05:00") as UTC instants."""
    if "date" not in frame.columns:
        return pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]")
    return kickoff_utc(frame["date"])


# EdgeRaw's token columns: blank there means "nothing fired" / "active", a real value.
EDGE_BLANK_EXEMPT = frozenset({"Avail", "Flags", "Flag", "Edge"})

POLICIES: dict[str, Policy] = {
    "projections": Policy(key="Id", kickoffs=_game_start_kickoffs, label="Name"),
    "draftkings": Policy(
        key="ID",
        kickoffs=_dk_kickoffs,
        label="Name",
        blank_exempt=frozenset({"Status"}),
        slate_switch=True,
        status_column="Status",
    ),
    "nfl_odds": Policy(key="abbr", kickoffs=_odds_kickoffs, label="abbr"),
    "edge": Policy(
        key="Id",
        kickoffs=_game_start_kickoffs,
        label="Name",
        blank_exempt=EDGE_BLANK_EXEMPT,
        sort_by="ValAdj",
        status_column="Avail",
    ),
}


def _keys(series: pd.Series) -> pd.Series:
    """Row keys as clean strings (an Id read back from a CSV as 4.4557804e7 or "44557804.0" is 44557804)."""

    def one(value: object) -> str:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
        text = str(value).strip()
        return text[:-2] if text.endswith(".0") else text

    return series.map(one)


def _blank(series: pd.Series) -> pd.Series:
    return series.isna() | series.astype(str).str.strip().eq("")


def _in_week(kickoffs: pd.Series, week: int, season: int) -> pd.Series:
    local_dates = kickoffs.dt.tz_convert(KICKOFF_TIMEZONE).dt.date
    return local_dates.map(
        lambda d: d is not None and not pd.isna(d) and nfl_calendar.week_for_date(d, season) == week
    )


def _names(labels: pd.Series) -> str:
    names = [str(x) for x in labels.tolist()]
    shown = ", ".join(names[:_NOTE_NAMES])
    return shown + (f" and {len(names) - _NOTE_NAMES} more" if len(names) > _NOTE_NAMES else "")


def guard(
    name: str, new: pd.DataFrame, old: pd.DataFrame | None, *, week: int, season: int, now: datetime
) -> GuardResult:
    """`new` merged with the last saved copy `old` under source `name`'s policy (see the module docstring).
    A source without a policy, a first sync (no `old`) or an `old` from another week passes through."""
    policy = POLICIES.get(name)
    if (
        policy is None
        or old is None
        or old.empty
        or policy.key not in new.columns
        or policy.key not in old.columns
    ):
        return GuardResult(new)
    stamp = pd.Timestamp(now)
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")

    old = old.reset_index(drop=True)
    old_kick = policy.kickoffs(old)
    old_this_week = _in_week(old_kick, week, season)
    if not old_this_week.any():
        return GuardResult(new)  # last week's file (or one with no kickoffs): nothing to protect
    old = old[old_this_week.to_numpy()].reset_index(drop=True)
    old_kick = old_kick[old_this_week.to_numpy()].reset_index(drop=True)
    old_keys = _keys(old[policy.key])

    new = new.reset_index(drop=True)
    new_keys = _keys(new[policy.key])

    if policy.slate_switch:
        switched = _slate_switch(name, new, old, new_keys, old_keys, old_kick, policy, stamp)
        if switched is not None:
            return switched

    notes: list[str] = []
    columns = [c for c in new.columns if c in old.columns and c != policy.key]
    old_by_key = old.assign(_k=old_keys).drop_duplicates("_k").set_index("_k")
    aligned = old_by_key.reindex(new_keys.to_numpy())
    aligned.index = new.index
    present = new_keys.isin(set(old_keys)).to_numpy()

    # Rule 1: started players keep their last values. A row's kickoff is the feed's own (a postponed game
    # moves it later), else the one on file.
    new_kick = policy.kickoffs(new)
    file_kick = pd.Series(old_kick.to_numpy(), index=old_keys.to_numpy())
    file_kick = file_kick[~file_kick.index.duplicated()]
    kick = new_kick.where(new_kick.notna(), new_keys.map(file_kick))
    started = (kick.notna() & (kick <= stamp)).to_numpy() & present
    out = new.copy()
    changed_started = 0
    if started.any():
        mask = pd.Series(started, index=new.index)
        for column in columns:
            before = out[column]
            kept = before.where(~mask, aligned[column])
            changed_started += int((mask & ~_same(before, kept)).sum())
            out[column] = kept
        n_started = int(started.sum())
        notes.append(
            f"{name}: {n_started} row(s) whose game has started keep their values from the last sync"
            + (
                f" ({changed_started} value(s) the feed changed after kickoff were not taken)"
                if changed_started
                else ""
            )
            + "."
        )

    # Rule 3: no column is wiped (unstarted rows; started ones are frozen above). A small loss passes through
    # and is listed; an OUT / IR player is never held.
    not_playing = _not_playing(policy, new, aligned)
    for column in columns:
        if column in policy.blank_exempt:
            continue
        had = present & ~started & ~_blank(aligned[column]).to_numpy()
        lost = had & _blank(out[column]).to_numpy()
        n_lost = int(lost.sum())
        if not n_lost:
            continue
        lost_mask = pd.Series(lost, index=new.index)
        labels = out.loc[lost_mask, policy.label] if policy.label in out.columns else new_keys[lost_mask]
        if n_lost > BLANK_GUARD_WIPE_SHARE * int(had.sum()):
            hold = lost & ~not_playing
            if hold.any():
                mask = pd.Series(hold, index=new.index)
                out[column] = out[column].where(~mask, aligned[column])
            n_out = n_lost - int(hold.sum())
            notes.append(
                f"{name}: the feed blanked {column} for {n_lost} of {int(had.sum())} row(s) that had a value "
                f"(e.g. {_names(labels)}); kept the previous values"
                + (f" except for {n_out} player(s) DraftKings has OUT / IR." if n_out else ".")
            )
        else:
            notes.append(
                f"{name}: the feed dropped {column} for {n_lost} player(s) ({_names(labels)}); "
                "took the blanks."
            )

    # Rule 2: rows the feed dropped are kept.
    missing = ~old_keys.isin(set(new_keys)).to_numpy()
    if missing.any():
        kept_rows = old[missing][[c for c in new.columns if c in old.columns]]
        out = pd.concat([out, kept_rows], ignore_index=True)
        labels = kept_rows[policy.label] if policy.label in kept_rows.columns else old_keys[missing]
        started_missing = int(((old_kick[missing].notna()) & (old_kick[missing] <= stamp)).sum())
        why = f", {started_missing} of them already kicked off" if started_missing else ""
        notes.append(
            f"{name}: the feed no longer lists {int(missing.sum())} row(s) from the last sync{why} "
            f"({_names(labels)}); kept them with their last values."
        )
        if policy.sort_by and policy.sort_by in out.columns:
            out = out.sort_values(
                policy.sort_by, ascending=False, na_position="last", kind="stable"
            ).reset_index(drop=True)
    return GuardResult(out, notes)


def _not_playing(policy: Policy, new: pd.DataFrame, aligned: pd.DataFrame) -> np.ndarray:
    """Rows DraftKings marks OUT / IR, in the new feed or in the copy on file: never held by rule 3."""
    flags = np.zeros(len(new), dtype=bool)
    column = policy.status_column
    if column is None:
        return flags
    for frame in (new, aligned):
        if column in frame.columns:
            flags |= frame[column].astype(str).str.strip().str.upper().isin(NOT_PLAYING).to_numpy()
    return flags


def _same(a: pd.Series, b: pd.Series) -> pd.Series:
    both_blank = a.isna() & b.isna()
    an, bn = pd.to_numeric(a, errors="coerce"), pd.to_numeric(b, errors="coerce")
    numeric = an.notna() & bn.notna()
    close = numeric & ((an - bn).abs() < 1e-9)
    text = ~numeric & (a.astype(str) == b.astype(str))
    return both_blank | close | text


def _slate_switch(
    name: str,
    new: pd.DataFrame,
    old: pd.DataFrame,
    new_keys: pd.Series,
    old_keys: pd.Series,
    old_kick: pd.Series,
    policy: Policy,
    stamp: pd.Timestamp,
) -> GuardResult | None:
    """Rule 4: DraftKings now lists another slate (most IDs new). When the slate on file has started, keeps
    it and refreshes `Status` for its unstarted players by name and team; when it has not, DK really changed
    the main slate and the new list is taken whole. None when the IDs mostly match (rules 1-3 apply)."""
    if new.empty:
        return None
    overlap = new_keys.isin(set(old_keys)).mean()
    if overlap >= DIFFERENT_SLATE_OVERLAP:
        return None
    if not ((old_kick.notna()) & (old_kick <= stamp)).any():  # not started: DK really changed the main slate
        return GuardResult(
            new,
            [
                f"{name}: DraftKings lists a different main slate than the last sync ({overlap:.0%} of its "
                f"{len(new)} players were on file) and none of its games has started: took the new one."
            ],
        )
    out = old.copy()
    refreshed = 0
    if "Status" in out.columns and "Status" in new.columns and {"Name", "TeamAbbrev"} <= set(new.columns):
        lookup = (
            new.assign(_n=new["Name"].astype(str).str.strip(), _t=new["TeamAbbrev"].astype(str).str.strip())
            .drop_duplicates(["_n", "_t"])
            .set_index(["_n", "_t"])["Status"]
        )
        unstarted = (old_kick.isna() | (old_kick > stamp)).to_numpy()
        keys = list(
            zip(out["Name"].astype(str).str.strip(), out["TeamAbbrev"].astype(str).str.strip(), strict=True)
        )
        status = out["Status"].astype(object).copy()
        for i, k in enumerate(keys):
            if unstarted[i] and k in lookup.index:
                status.iloc[i] = lookup.loc[k]
                refreshed += 1
        out["Status"] = status
    n_unstarted = int((old_kick.isna() | (old_kick > stamp)).sum())
    return GuardResult(
        out,
        [
            f"{name}: DraftKings now lists a different slate ({len(new)} players, {overlap:.0%} of them on "
            f"the slate on file, which has started). Kept the slate on file ({len(old)} players); refreshed "
            f"Status for {refreshed} of its {n_unstarted} unstarted player(s) from the new list, by name and "
            "team."
        ],
    )
