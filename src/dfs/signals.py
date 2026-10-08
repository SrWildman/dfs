"""The per-week signal table: `CalPts`, the context tokens, injury beneficiaries and matchups for one
slate, as of one moment.

Pure and offline-testable. `build_signals` takes the week's DraftKings-level frame and a `SeasonData`
bundle (the free nflverse / ffopportunity files, already loaded) and returns a `SignalOutput`. It is the
single piece of code that the live sync and the results backfill both call, so a backfilled week and a
live week are computed identically.

**No lookahead.** Everything is cut at the slate's week: xFP windows, usage jumps, with-or-without
history and the matchup tables use games BEFORE `week` only; the injury report and depth chart are
those in force at `as_of` (`depth_dt`). Adding week-`week` actuals to the inputs changes nothing here
(`tests/test_signals.py` asserts it).

The tokens are context, not proven edges (`xfp.CONTEXT_NOTE`); Model Check scores every one of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from dfs import injury_beneficiaries as ib
from dfs import matchups, probabilities, xfp
from dfs.derived import OUT_STATUSES
from dfs.player_join import JoinResult, join_key, join_source_to_dk, normalize_team

SIGNAL_COLUMNS = [
    "Id",
    "GsisId",
    "Name",
    "Team",
    "Position",
    "Salary",
    "GameStart",
    "ProjPts",
    "UmPts",
    "CalPts",
    *probabilities.PROB_COLUMNS,
    "LowConf",
    "Games",
    "xFP/G",
    "DkG",
    "TdExcess",
    "UsageN",
    "InjFrom",
    "InjGain",
    "InjMethod",
    "PricedIn",
    "MatchupGroup",
    "MatchupRank",
    "Edge",
]
TOKEN_ORDER = [ib.TOKEN_INJ, xfp.TOKEN_BUY, xfp.TOKEN_FADE, xfp.TOKEN_USAGE_UP, xfp.TOKEN_USAGE_DOWN]
SOURCE_NAMES = ("ffopportunity", "injuries", "depth")


@dataclass
class SeasonData:
    """Every free input a slate's signals need, already loaded. Any field may be None or empty (that
    source failed, or does not exist yet): the signals that depend on it blank, nothing else changes."""

    season: int
    ffo_weeks: pd.DataFrame | None = (
        None  # xfp.player_weeks, this AND last season, `dk_actual` on this season
    )
    rz_by_week: pd.DataFrame | None = None  # GsisId, week, rz (red-zone targets + carries), this season
    injuries: pd.DataFrame | None = None  # nflverse_injuries.OUTPUT_COLUMNS
    depth: pd.DataFrame | None = None  # nflverse_depth.OUTPUT_COLUMNS (one or many snapshots)
    offense_actual: pd.DataFrame | None = None  # results_actual.score_offense_actual, this season
    offense_actual_prior: pd.DataFrame | None = None
    dst_actual: pd.DataFrame | None = None  # results_actual.score_dst_actual, this season
    dst_actual_prior: pd.DataFrame | None = None
    schedule: pd.DataFrame | None = None  # week, away_team, home_team, this season
    schedule_prior: pd.DataFrame | None = None


@dataclass
class SignalOutput:
    """One slate's signals: the player table (`SIGNAL_COLUMNS`), the outs, the beneficiaries and the matchup
    rows (top and bottom per position), plus the join coverage for the report."""

    players: pd.DataFrame
    outs: pd.DataFrame
    beneficiaries: pd.DataFrame
    matchups: pd.DataFrame
    coverage: dict[str, tuple[int, int]] = field(default_factory=dict)  # source -> (matched, rosterable pool)


def _has(frame: pd.DataFrame | None) -> bool:
    return frame is not None and not frame.empty


def identity_frame(*sources: pd.DataFrame | None) -> pd.DataFrame:
    """`GsisId`, `Name`, `Team`, `Position` of every player the given frames know (first frame first)."""
    parts = [
        s[["GsisId", "Name", "Team", "Position"]]
        for s in sources
        if _has(s) and {"GsisId", "Name", "Team", "Position"} <= set(s.columns)
    ]
    if not parts:
        return pd.DataFrame(columns=["GsisId", "Name", "Team", "Position"])
    # One row per join key (normalised name, team, position): the join assumes a key appears once, but a
    # player the depth chart and ffopportunity spell differently ("Marvin Mims Jr." / "Marvin Mims") is
    # still offered under both spellings, since they normalise to different keys only when they differ.
    out = pd.concat(parts, ignore_index=True).dropna(subset=["GsisId"])
    key = [join_key(n, t, p) for n, t, p in zip(out["Name"], out["Team"], out["Position"], strict=True)]
    return out.assign(_k=key).drop_duplicates(subset="_k", keep="first").drop(columns="_k")


def identity_for(data: SeasonData, depth_dt: str | None = None) -> pd.DataFrame:
    """Every player the free files can name, for the gsis join: the depth chart at `depth_dt` first (it holds
    each player's CURRENT team and covers players with no games yet), then this and last season's
    ffopportunity players (newest game first), then the injury report."""
    weeks = data.ffo_weeks if _has(data.ffo_weeks) else None
    newest = weeks.sort_values(["season", "week"], ascending=False) if weeks is not None else None
    return identity_frame(ib.latest_depth(data.depth, depth_dt), newest, data.injuries)


def attach_gsis(frame: pd.DataFrame, identity: pd.DataFrame) -> tuple[pd.DataFrame, JoinResult | None]:
    """Add `GsisId` to the DraftKings-level frame by the project's one join (name, team, position). A
    `GsisId` already on the frame is kept; DSTs never get one."""
    out = frame.copy()
    if "GsisId" not in out.columns:
        out["GsisId"] = np.nan
    if identity.empty:
        return out, None
    players = out[out["Position"] != "DST"]
    if players.empty:
        return out, None
    pool = out["RosterablePool"].astype(bool) if "RosterablePool" in out.columns else None
    result = join_source_to_dk(
        players[["Id", "Name", "Team", "Position"]],
        identity.rename(columns={"Name": "SrcName", "Team": "SrcTeam", "Position": "SrcPos"}),
        source_name_col="SrcName",
        source_team_col="SrcTeam",
        source_position_col="SrcPos",
        source="gsis",
        pool_mask=pool.reindex(players.index) if pool is not None else None,
        expect_dst=False,
    )
    found = result.matched[["Id", "GsisId"]].drop_duplicates(subset="Id")
    mapped = out[["Id"]].merge(found.rename(columns={"GsisId": "_g"}), on="Id", how="left")["_g"].to_numpy()
    out["GsisId"] = out["GsisId"].where(
        out["GsisId"].notna() & (out["GsisId"] != ""), pd.Series(mapped, index=out.index)
    )
    return out, result


def season_weeks(data: SeasonData) -> pd.DataFrame:
    """This season's player-weeks from `data.ffo_weeks` (empty frame when there are none)."""
    if not _has(data.ffo_weeks):
        return pd.DataFrame(columns=[*xfp.PLAYER_WEEK_COLUMNS, "dk_actual"])
    weeks = data.ffo_weeks
    return weeks[pd.to_numeric(weeks["season"], errors="coerce") == data.season]


def player_tokens(frame: pd.DataFrame, data: SeasonData, *, week: int) -> pd.DataFrame:
    """Per frame row (index-aligned): `Games`, `xFP/G`, `DkG`, `TdExcess`, `UsageN`, and the BUY/FADE and
    USAGE tokens. Blank (NaN / "") where the player has no ffopportunity history."""
    cols = ["Games", "xFP/G", "DkG", "TdExcess", "UsageN", "TokenBuyFade", "TokenUsage", "XfpTopHalf"]
    out = pd.DataFrame(index=frame.index, columns=cols, dtype=object)
    out[["TokenBuyFade", "TokenUsage"]] = ""
    weeks = season_weeks(data)
    if weeks.empty or "GsisId" not in frame.columns:
        return out
    windows = xfp.xfp_windows(weeks, before_week=week)
    mapped = frame["GsisId"].map(lambda g: g if g in windows.index else np.nan)
    have = mapped.notna()
    values = windows.reindex(mapped[have])
    for column in ("Games", "xFP/G", "DkG", "TdExcess"):
        out.loc[have, column] = values[column].to_numpy()
    pool = frame["RosterablePool"].astype(bool) if "RosterablePool" in frame.columns else None
    xfp_g = pd.to_numeric(out["xFP/G"], errors="coerce")
    percentile = xfp.position_percentile(xfp_g, frame["Position"], pool)
    top_half = percentile >= xfp.XFP_TOP_HALF_PCT
    out["XfpTopHalf"] = top_half
    if have.any():
        by_player = pd.DataFrame(
            {
                "xFP/G": xfp_g[have].to_numpy(),
                "DkG": pd.to_numeric(out.loc[have, "DkG"], errors="coerce").to_numpy(),
                "TdExcess": pd.to_numeric(out.loc[have, "TdExcess"], errors="coerce").to_numpy(),
                "Games": pd.to_numeric(out.loc[have, "Games"], errors="coerce").to_numpy(),
            },
            index=mapped[have].to_numpy(),
        )
        tokens = xfp.buy_fade_tokens(by_player, pd.Series(top_half[have].to_numpy(), index=by_player.index))
        out.loc[have, "TokenBuyFade"] = tokens.to_numpy()
    jumps = xfp.usage_jumps(weeks, data.rz_by_week, before_week=week)
    if not jumps.empty:
        ids = frame["GsisId"].map(lambda g: g if g in jumps.index else np.nan)
        j = ids.notna()
        out.loc[j, "UsageN"] = jumps.loc[ids[j], "n"].to_numpy()
        out.loc[j, "TokenUsage"] = jumps.loc[ids[j], "token"].to_numpy()
    return out


def find_outs(frame: pd.DataFrame, data: SeasonData, *, week: int) -> pd.DataFrame:
    """Everyone out or questionable this week: DraftKings' `Avail` on the frame (it wins), else the nflverse
    report for `week`. Includes report-only players who are not on the frame (DraftKings may have dropped
    them). Columns `GsisId`, `Name`, `Team`, `Position`, `Status`, `Source`."""
    rows = []
    seen: set[str] = set()
    if "Avail" in frame.columns and "GsisId" in frame.columns:
        report = _report_lookup(data, week)
        for _, r in frame[frame["Position"].isin(xfp.SKILL_POSITIONS)].iterrows():
            gsis = r["GsisId"]
            if not isinstance(gsis, str) or not gsis:
                continue
            status = ib.classify_status(r.get("Avail"), report.get(gsis))
            if status:
                avail = r.get("Avail")
                source = "DraftKings Avail" if isinstance(avail, str) and avail.strip() else "injury report"
                rows.append(_out_row(gsis, r["Name"], r["Team"], r["Position"], status, source))
                seen.add(gsis)
    if _has(data.injuries):
        inj = data.injuries
        inj = inj[
            (pd.to_numeric(inj["season"], errors="coerce") == data.season)
            & (pd.to_numeric(inj["week"], errors="coerce") == week)
        ]
        for _, r in inj.iterrows():
            if r["GsisId"] in seen:
                continue
            status = ib.classify_status(None, r.get("report_status"))
            if status:
                rows.append(
                    _out_row(r["GsisId"], r["Name"], r["Team"], r["Position"], status, "injury report")
                )
                seen.add(r["GsisId"])
    return pd.DataFrame(rows, columns=["GsisId", "Name", "Team", "Position", "Status", "Source"])


def _out_row(gsis, name, team, position, status, source) -> dict:  # noqa: ANN001
    return {
        "GsisId": gsis,
        "Name": name,
        "Team": normalize_team(team),
        "Position": position,
        "Status": status,
        "Source": source,
    }


def _report_lookup(data: SeasonData, week: int) -> dict[str, str]:
    if not _has(data.injuries):
        return {}
    inj = data.injuries
    inj = inj[
        (pd.to_numeric(inj["season"], errors="coerce") == data.season)
        & (pd.to_numeric(inj["week"], errors="coerce") == week)
    ]
    return dict(zip(inj["GsisId"], inj["report_status"], strict=True))


def designation_baseline(name: str, team: str, dk_snapshots: list[tuple[str, pd.DataFrame]]) -> str | None:
    """The stamp of the last DraftKings snapshot at which `name` was listed WITHOUT an out status, provided a
    LATER snapshot lists him OUT or IR (so the designation appeared in between). None when he was out in the
    first snapshot we have, never out, or absent: the answer is then "unknown"."""
    baseline = None
    for stamp, snap in dk_snapshots:
        row = snap[(snap["Name"] == name) & (snap["TeamAbbrev"].map(normalize_team) == normalize_team(team))]
        if row.empty:
            continue
        status = row.iloc[0].get("Status")
        is_out = isinstance(status, str) and status.strip().upper() in OUT_STATUSES
        if is_out:
            return baseline
        baseline = stamp
    return None


def projection_at(
    name: str, team: str, stamp: str | None, snapshots: list[tuple[str, pd.DataFrame]]
) -> float | None:
    """TFFB's `ProjPts` for the player in the latest projection snapshot taken at or before `stamp`."""
    if stamp is None:
        return None
    best = None
    for s, snap in snapshots:
        if s <= stamp:
            best = snap
    if best is None:
        return None
    row = best[(best["Name"] == name) & (best["Team"].map(normalize_team) == normalize_team(team))]
    if row.empty:
        return None
    value = pd.to_numeric(row.iloc[0].get("ProjPts"), errors="coerce")
    return None if pd.isna(value) else float(value)


def attach_priced_in(
    benef: pd.DataFrame,
    outs: pd.DataFrame,
    frame: pd.DataFrame,
    dk_snapshots: list[tuple[str, pd.DataFrame]] | None,
    projection_snapshots: list[tuple[str, pd.DataFrame]] | None,
) -> pd.DataFrame:
    """Add `PricedIn` (`yes` / `no` / `unknown`) to the beneficiary table. A beneficiary of several outs is
    judged against the earliest designation among the outs that fed him."""
    out = benef.copy()
    out["PricedIn"] = ib.PRICED_UNKNOWN
    if out.empty or not dk_snapshots or not projection_snapshots:
        return out
    current = frame.set_index("GsisId")["ProjPts"] if "GsisId" in frame.columns else pd.Series(dtype=float)
    out_info = outs.set_index("Name")[["Team"]]
    for idx, row in out.iterrows():
        stamps = []
        for name in str(row["OutPlayers"]).split(", "):
            if name in out_info.index:
                stamp = designation_baseline(name, out_info.loc[name, "Team"], dk_snapshots)
                if stamp:
                    stamps.append(stamp)
        if not stamps:
            continue
        before = projection_at(row["Name"], row["Team"], min(stamps), projection_snapshots)
        now = current.get(row["GsisId"])
        out.at[idx, "PricedIn"] = ib.priced_in(
            float(now) if now is not None and not pd.isna(now) else np.nan, before, float(row["xfp_gain"])
        )
    return out


def build_matchups(
    frame: pd.DataFrame,
    data: SeasonData,
    *,
    week: int,
    team_metrics: pd.DataFrame | None,
    implied: pd.Series,
) -> pd.DataFrame:
    """The top-8 / bottom-4 matchup rows per position for the slate (`matchups.MATCHUP_COLUMNS`). Empty when
    the schedule or results are missing."""
    if not (_has(data.offense_actual) and _has(data.schedule)):
        return pd.DataFrame(columns=matchups.MATCHUP_COLUMNS)
    tp = matchups.team_position_points(data.offense_actual, data.schedule)
    allowed = matchups.adjusted_points_allowed(tp, before_week=week)
    if _has(data.offense_actual_prior) and _has(data.schedule_prior):
        prior_tp = matchups.team_position_points(data.offense_actual_prior, data.schedule_prior)
        prior = matchups.adjusted_points_allowed(prior_tp, before_week=99)
        allowed = matchups.blend_allowed(allowed, prior, ["Team", "Position"])
    dst = pd.DataFrame(columns=["Team", "adj", "games"])
    if _has(data.dst_actual):
        dst = matchups.adjusted_dst_allowed(data.dst_actual, data.schedule, before_week=week)
        if _has(data.dst_actual_prior) and _has(data.schedule_prior):
            prior_dst = matchups.adjusted_dst_allowed(
                data.dst_actual_prior, data.schedule_prior, before_week=99
            )
            dst = matchups.blend_allowed(dst, prior_dst, ["Team"])
    pairs = matchups.week_pairs(data.schedule, week, set(frame["Team"].map(normalize_team)))
    scores = matchups.matchup_scores(pairs, allowed, dst, team_metrics, implied)
    groups = matchups.select_groups(scores)
    return matchups.attach_players(groups, frame)


def implied_by_team(projections: pd.DataFrame) -> pd.Series:
    """Implied points per team from the TFFB projections' `ImpPts` column (one value per team)."""
    if projections is None or projections.empty or "ImpPts" not in projections.columns:
        return pd.Series(dtype=float)
    teams = projections.assign(_t=projections["Team"].map(normalize_team))
    return pd.to_numeric(teams["ImpPts"], errors="coerce").groupby(teams["_t"]).mean()


def build_signals(
    frame: pd.DataFrame,
    data: SeasonData,
    *,
    week: int,
    depth_dt: str | None = None,
    team_metrics: pd.DataFrame | None = None,
    implied: pd.Series | None = None,
    dk_snapshots: list[tuple[str, pd.DataFrame]] | None = None,
    projection_snapshots: list[tuple[str, pd.DataFrame]] | None = None,
) -> SignalOutput:
    """All of a slate's signals.

    `frame` is the week's DraftKings-level table: `Id`, `Name`, `Team`, `Opp`, `Position`, `Salary`,
    `ProjPts`, `Avail`, `RosterablePool`, optionally `CalPts`, `GameStart` and a known `GsisId`.
    `depth_dt` is the ISO stamp the depth chart is cut at (the slate's first kickoff / the as-of moment);
    None takes the newest snapshot. Snapshots for "priced in" are `[(stamp, frame)]` as stored under
    `data/raw/`."""
    frame = frame.reset_index(drop=True)
    identity = identity_for(data, depth_dt)
    frame, join = attach_gsis(frame, identity)
    coverage = {}
    if join is not None:
        coverage["gsis (depth chart + ffopportunity + injuries)"] = (join.pool_matched, join.pool_total)

    tokens = player_tokens(frame, data, week=week)
    outs = find_outs(frame, data, week=week)
    weeks = season_weeks(data)
    windows = xfp.xfp_windows(weeks, before_week=week)
    people = identity_frame(weeks.sort_values("week", ascending=False), data.injuries)
    benef = ib.beneficiaries(
        outs,
        windows,
        people,
        data.ffo_weeks if _has(data.ffo_weeks) else pd.DataFrame(columns=xfp.PLAYER_WEEK_COLUMNS),
        ib.latest_depth(data.depth, depth_dt),
        before=(data.season, week),
    )
    benef = attach_priced_in(benef, outs, frame, dk_snapshots, projection_snapshots)

    players = frame.copy()
    for column in ("GameStart", "CalPts"):
        if column not in players.columns:
            players[column] = np.nan
    if "UmPts" not in players.columns:
        players["UmPts"] = np.nan
    for column, value in probabilities.outcome_columns(players).items():
        players[column] = value
    players["Games"], players["xFP/G"] = tokens["Games"], tokens["xFP/G"]
    players["DkG"], players["TdExcess"], players["UsageN"] = (
        tokens["DkG"],
        tokens["TdExcess"],
        tokens["UsageN"],
    )

    players["InjFrom"], players["InjGain"], players["InjMethod"], players["PricedIn"] = "", np.nan, "", ""
    if not benef.empty:
        by_id = benef.sort_values("xfp_gain", ascending=False).drop_duplicates("GsisId").set_index("GsisId")
        have = players["GsisId"].isin(by_id.index)
        pulled = by_id.reindex(players.loc[have, "GsisId"])
        players.loc[have, "InjFrom"] = pulled["OutStatus"].to_numpy()
        players.loc[have, "InjGain"] = pulled["xfp_gain"].to_numpy()
        players.loc[have, "InjMethod"] = pulled["Method"].to_numpy()
        players.loc[have, "PricedIn"] = pulled["PricedIn"].to_numpy()
        inj_token = pd.Series(
            np.where(
                have & (players["InjFrom"] == ib.STATUS_OUT) & (players["InjGain"] >= ib.INJ_MIN_GAINED_XFP),
                ib.TOKEN_INJ,
                "",
            ),
            index=players.index,
        )
    else:
        inj_token = pd.Series("", index=players.index)

    matchup_rows = build_matchups(
        frame,
        data,
        week=week,
        team_metrics=team_metrics,
        implied=implied if implied is not None else pd.Series(dtype=float),
    )
    players["MatchupGroup"], players["MatchupRank"] = "", np.nan
    if not matchup_rows.empty:
        key = matchup_rows.set_index(["Team", "Position"])
        for idx, row in players.iterrows():
            k = (normalize_team(row["Team"]), row["Position"])
            if k in key.index:
                players.at[idx, "MatchupGroup"] = key.loc[k, "Group"]
                players.at[idx, "MatchupRank"] = key.loc[k, "Rank"]

    players["Edge"] = _join_tokens(inj_token, tokens["TokenBuyFade"], tokens["TokenUsage"])
    return SignalOutput(
        players=players[[c for c in SIGNAL_COLUMNS if c in players.columns]],
        outs=outs,
        beneficiaries=benef,
        matchups=matchup_rows,
        coverage=coverage,
    )


def _join_tokens(*series: pd.Series) -> pd.Series:
    """Space-joined tokens, in `TOKEN_ORDER`, from parallel series of single tokens (or "")."""
    frame = pd.concat(series, axis=1).fillna("").astype(str)
    return frame.apply(
        lambda row: " ".join(
            sorted((t for t in row if t), key=lambda t: TOKEN_ORDER.index(t) if t in TOKEN_ORDER else 99)
        ),
        axis=1,
    )
