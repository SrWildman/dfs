"""R1: who inherits a missing starter's volume?

Unit of analysis is the TEAM-GAME, so a bye week can never produce an absence (there is no game for the
player to miss) and the "previous 3 games" are the team's last three games played, byes skipped.

Definitions (all from the games BEFORE the one being explained, nothing from the game itself):

- prior share      a player's share of his team's targets (carries) over the team's previous 3 games in the
                   same season, as sum(player) / sum(team). A game he missed counts as 0 for him, so a player
                   who has already been out is no longer a "regular" -- absences measured here are mostly
                   the FIRST missed game, against a baseline in which he was playing.
- regular          prior target share >= 15% or prior carry share >= 30%.
- absence          a regular with no stats row for the team in a game the team played. (A stats row exists
                   for any player who recorded a stat, so a player on the field with no box-score line is the
                   one case not seen; snap counts would catch it but are keyed by a different id and add
                   nothing else.) Needs 3 prior games in the same season, so weeks 1-3 are out.
- clean event      a team-game with exactly one regular absent. Games with two or more are counted and
                   excluded from every table (their gains cannot be attributed to either player).
- vacated volume   V = absent prior share x the team's mean volume per game over those 3 games, in
                   targets or in carries. The target channel is analysed for target-share regulars, the carry
                   channel for carry-share regulars.
- gain             for a teammate who played: his actual count minus (his prior share x the same team
                   baseline volume). As a fraction of V.

Conservation: with T the team's actual volume and TB its baseline,
    gain(rotation teammates who played) + gain(players outside the prior rotation)
        = V + (volume of other rotation players who also missed) + (T - TB).
So the vacated volume reaches a prior-rotation teammate, reaches a newcomer, or "leaks" through the team
simply running fewer plays than usual. `nowhere = 1 - rotation_share`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from dfs.research import data
from dfs.research.common import FIT_SEASONS, SEASONS, TEST_SEASONS, metadata, write_csv, write_json
from dfs.research.stats import Z90, percentile_interval

WINDOW = 3
TARGET_REGULAR = 0.15
CARRY_REGULAR = 0.30
RANK_CAP = {"QB": 2, "RB": 3, "WR": 4, "TE": 2}
MIN_CELL = 8  # a cell needs this many fit events before the learned table trusts it
OFFENSE = ("QB", "RB", "WR", "TE")
CHANNELS = ("targets", "carries")
THRESHOLD = {"targets": TARGET_REGULAR, "carries": CARRY_REGULAR}

# The hand-set rule, as the research task states it (the local round's code is not in this repo, so this is
# the task's wording made precise -- see docs/RESEARCH.md): the next player at the same position gets
# NEXT_UP_SHARE of the vacated volume; for a WR or TE, SPILL_SHARE of the vacated TARGETS goes to the other
# pass-catching group (a WR out -> the TEs, a TE out -> the WRs), pro rata to their prior shares.
NEXT_UP_SHARE = 0.60
SPILL_SHARE = 0.25
OTHER_GROUP = {"WR": "TE", "TE": "WR"}


# --------------------------------------------------------------------------------------------------------
# Building blocks (pure functions over frames; the tests drive these with synthetic data)
# --------------------------------------------------------------------------------------------------------


def team_game_index(games: pd.DataFrame) -> pd.DataFrame:
    """One row per (team, game actually played): `gidx` counts the team's games in the season, 1-based, in
    order. A bye week has no row, so `gidx` runs 1..N through it."""
    cols = ["season", "week", "game_id"]
    home = games[[*cols, "home_team"]].rename(columns={"home_team": "team"})
    away = games[[*cols, "away_team"]].rename(columns={"away_team": "team"})
    tg = pd.concat([home, away], ignore_index=True).sort_values(["team", "season", "week"])
    tg["gidx"] = tg.groupby(["team", "season"]).cumcount() + 1
    return tg.reset_index(drop=True)


def offense_rows(sp: pd.DataFrame, team_games: pd.DataFrame) -> pd.DataFrame:
    """Player-game rows for QB/RB/WR/TE (FB read as RB) with the team's game index attached."""
    cols = ["player_id", "player_display_name", "position", "team", "season", "week"]
    rows = sp[sp["position"].isin(OFFENSE)][[*cols, "targets", "carries", "attempts"]].copy()
    rows = rows.rename(columns={"player_id": "gsis_id", "player_display_name": "name"})
    for c in ("targets", "carries", "attempts"):
        rows[c] = rows[c].fillna(0.0)
    rows = rows.merge(team_games[["team", "season", "week", "gidx"]], on=["team", "season", "week"])
    return rows.reset_index(drop=True)


def prior_windows(
    rows: pd.DataFrame, window: int = WINDOW, team_games: pd.DataFrame | None = None
) -> pd.DataFrame:
    """For every (team, season, target game `tidx`) with `window` earlier games that season, each player's
    summed targets / carries / pass attempts over those earlier games, his share of the team's, and the
    team's mean volume per game. Only games with index < tidx contribute: the target game is never in its
    own window (the leakage test adds it and asserts nothing changes). `team_games` (the schedule's
    `team_game_index`) says how many games each team played; without it the last game in `rows` is used."""
    keep = ["gsis_id", "name", "position", "team", "season", "week", "gidx", "targets", "carries", "attempts"]
    parts = []
    for lag in range(1, window + 1):
        part = rows[keep].copy()
        part["tidx"] = part["gidx"] + lag
        parts.append(part)
    lagged = pd.concat(parts, ignore_index=True)
    source = rows if team_games is None else team_games
    last_game = (
        source.groupby(["team", "season"], as_index=False)["gidx"].max().rename(columns={"gidx": "last_game"})
    )
    lagged = lagged.merge(last_game, on=["team", "season"])
    # A full window only, and only for games the team actually played (never "game 11" of a 9-game sample).
    lagged = lagged[(lagged["tidx"] > window) & (lagged["tidx"] <= lagged["last_game"])].sort_values("week")
    grp = lagged.groupby(["team", "season", "tidx", "gsis_id"], as_index=False).agg(
        name=("name", "last"),
        position=("position", "last"),
        p_targets=("targets", "sum"),
        p_carries=("carries", "sum"),
        p_attempts=("attempts", "sum"),
    )
    key = ["team", "season", "tidx"]
    grp["team_targets"] = grp.groupby(key)["p_targets"].transform("sum")
    grp["team_carries"] = grp.groupby(key)["p_carries"].transform("sum")
    grp["share_t"] = np.where(grp["team_targets"] > 0, grp["p_targets"] / grp["team_targets"], 0.0)
    grp["share_c"] = np.where(grp["team_carries"] > 0, grp["p_carries"] / grp["team_carries"], 0.0)
    grp["tb_t"] = grp["team_targets"] / window
    grp["tb_c"] = grp["team_carries"] / window
    grp["touches"] = grp["p_targets"] + grp["p_carries"] + grp["p_attempts"]
    return grp


def rank_rotation(prior: pd.DataFrame) -> pd.DataFrame:
    """The rotation (anyone with a touch in the window) with a within-position rank by prior usage and its
    label (WR1, WR2, ... capped: WR4+, RB3+, TE2+, QB2+). Usage: targets for WR/TE, carries + targets for
    RB, pass attempts for QB."""
    rot = prior[prior["touches"] > 0].copy()
    usage = np.select(
        [rot["position"] == "RB", rot["position"] == "QB"],
        [rot["p_carries"] + rot["p_targets"], rot["p_attempts"]],
        default=rot["p_targets"],
    )
    rot["usage"] = usage + 1e-6 * rot["p_carries"]  # carries break a targets tie
    rot = rot.sort_values(
        ["team", "season", "tidx", "position", "usage", "gsis_id"],
        ascending=[True, True, True, True, False, True],
    )
    rot["rank"] = rot.groupby(["team", "season", "tidx", "position"]).cumcount() + 1
    rot["label"] = [rank_label(p, r) for p, r in zip(rot["position"], rot["rank"], strict=True)]
    return rot


def rank_label(position: str, rank: int) -> str:
    cap = RANK_CAP.get(position, 1)
    return f"{position}{rank}" if rank < cap else f"{position}{cap}+"


def find_absences(prior: pd.DataFrame, rows: pd.DataFrame, team_games: pd.DataFrame) -> pd.DataFrame:
    """Every regular who has no row for his team in a game the team played. The inner merge with the
    team-game index is what keeps byes out: only (team, season, tidx) that exist as games survive."""
    reg = prior[(prior["share_t"] >= TARGET_REGULAR) | (prior["share_c"] >= CARRY_REGULAR)].copy()
    played = rows[["gsis_id", "team", "season", "gidx"]].rename(columns={"gidx": "tidx"})
    played = played.assign(played=True)
    reg = reg.merge(played, on=["gsis_id", "team", "season", "tidx"], how="left")
    reg = reg.merge(
        team_games.rename(columns={"gidx": "tidx"})[["team", "season", "tidx", "week", "game_id"]],
        on=["team", "season", "tidx"],
        how="inner",
    )
    out = reg[reg["played"].isna()].drop(columns="played")
    out["n_regulars_out"] = out.groupby(["team", "season", "tidx"])["gsis_id"].transform("size")
    return out.reset_index(drop=True)


def tag_reasons(absences: pd.DataFrame, injuries: pd.DataFrame) -> pd.DataFrame:
    """`reason` (injury / suspension / other) from the weekly injury report, and `reason_detail`:

    - suspension: the report's own text says suspension or discipline. Most suspensions are never on an
      injury report, so they fall under unlisted.
    - injury: on that week's report with an injury listed.
    - listed_non_injury: on the report with a non-injury reason (personal, rest, coach's decision).
    - unlisted: not on that week's report at all (released, traded, retired, suspended, rested, or simply
      unreported)."""
    inj = injuries.copy()
    text = inj["report_primary_injury"].fillna("").str.lower()
    inj["detail"] = np.select(
        [
            text.str.contains("suspen|discipline"),
            text.str.contains(r"not injury|coach|personal|\brest|did not travel|team decision|\btravel"),
        ],
        ["suspension", "listed_non_injury"],
        default="injury",
    )
    inj["pri"] = inj["detail"].map({"suspension": 0, "injury": 1, "listed_non_injury": 2})
    inj = inj.sort_values("pri").drop_duplicates(["gsis_id", "season", "week"])
    out = absences.merge(
        inj[["gsis_id", "season", "week", "detail"]], on=["gsis_id", "season", "week"], how="left"
    )
    out["reason_detail"] = out["detail"].fillna("unlisted")
    out["reason"] = out["reason_detail"].map(
        {"injury": "injury", "suspension": "suspension", "listed_non_injury": "other", "unlisted": "other"}
    )
    return out.drop(columns="detail")


def team_volume(rows: pd.DataFrame) -> pd.DataFrame:
    """Actual team targets and carries in each team-game."""
    return rows.groupby(["team", "season", "gidx"], as_index=False).agg(
        t_targets=("targets", "sum"), t_carries=("carries", "sum")
    )


def clean_events(absences: pd.DataFrame, volume: pd.DataFrame) -> pd.DataFrame:
    """Team-games with exactly one regular out, with the team's actual volume attached. `elig_targets` /
    `elig_carries` say which channels the absent player qualifies a regular in."""
    ev = absences[absences["n_regulars_out"] == 1].copy()
    ev = ev.rename(
        columns={"gsis_id": "abs_id", "name": "abs_name", "position": "abs_pos", "label": "abs_label"}
    )
    ev["v_t"] = ev["share_t"] * ev["tb_t"]
    ev["v_c"] = ev["share_c"] * ev["tb_c"]
    ev["elig_targets"] = ev["share_t"] >= TARGET_REGULAR
    ev["elig_carries"] = ev["share_c"] >= CARRY_REGULAR
    ev = ev.merge(volume.rename(columns={"gidx": "tidx"}), on=["team", "season", "tidx"], how="left")
    ev["event_id"] = np.arange(len(ev))
    return ev.reset_index(drop=True)


def control_events(prior: pd.DataFrame, volume: pd.DataFrame, absences: pd.DataFrame) -> pd.DataFrame:
    """Team-games with a full prior window and NO regular out, shaped like `clean_events` (nothing is
    vacated, `v_*` is 0). Run through the same machinery they give the churn every game has: fringe players
    who do not play, newcomers who do, a team running more or fewer plays than its last three games, and
    the drift of a played-or-not 3-game baseline. An absence game is measured against this."""
    base = prior.groupby(["team", "season", "tidx"], as_index=False).agg(
        tb_t=("tb_t", "first"), tb_c=("tb_c", "first")
    )
    base = base.merge(volume.rename(columns={"gidx": "tidx"}), on=["team", "season", "tidx"])
    out = absences[["team", "season", "tidx"]].drop_duplicates().assign(has_absence=True)
    base = base.merge(out, on=["team", "season", "tidx"], how="left")
    base = base[base["has_absence"].isna()].drop(columns="has_absence").reset_index(drop=True)
    base["v_t"] = 0.0
    base["v_c"] = 0.0
    base["elig_targets"] = True
    base["elig_carries"] = True
    base["abs_id"] = None
    base["abs_pos"] = None
    base["abs_label"] = None
    base["reason"] = None
    base["event_id"] = CONTROL_ID_OFFSET + np.arange(len(base))
    return base


CONTROL_ID_OFFSET = 1_000_000


def beneficiaries(events: pd.DataFrame, rot: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    """One row per (event, teammate): everyone in the prior rotation plus everyone who played. Columns:
    `p_*` / `share_*` (the teammate's prior window), `g_*` (his line in the game), `label` / `rank` (his
    place in the rotation), and `kind`: rot_played / rot_missing / new (played, outside the prior rotation)
    / absent (the regular himself). The event's own columns (`abs_*`, `tb_*`, `v_*`) ride along."""
    key = ["team", "season", "tidx"]
    ev_keys = events[[*key, "event_id"]]
    rot_e = rot.merge(ev_keys, on=key)
    played = rows.rename(columns={"gidx": "tidx", "targets": "g_targets", "carries": "g_carries"})
    played = played[["gsis_id", "name", "position", "team", "season", "tidx", "g_targets", "g_carries"]]
    played = played.merge(ev_keys, on=key)
    rot_cols = [
        "event_id",
        "gsis_id",
        "name",
        "position",
        "p_targets",
        "p_carries",
        "share_t",
        "share_c",
        "label",
        "rank",
    ]
    ben = rot_e[rot_cols].merge(
        played[["event_id", "gsis_id", "name", "position", "g_targets", "g_carries"]],
        on=["event_id", "gsis_id"],
        how="outer",
        suffixes=("", "_g"),
    )
    ben["in_rot"] = ben["label"].notna()
    ben["did_play"] = ben["g_targets"].notna()
    ben["name"] = ben["name"].fillna(ben["name_g"])
    ben["position"] = ben["position"].fillna(ben["position_g"])
    ben = ben.drop(columns=["name_g", "position_g"])
    for c in ("g_targets", "g_carries", "p_targets", "p_carries", "share_t", "share_c"):
        ben[c] = ben[c].fillna(0.0)
    ev_cols = ["event_id", "abs_id", "abs_pos", "abs_label", "tb_t", "tb_c", "v_t", "v_c"]
    ben = ben.merge(events[ev_cols], on="event_id")
    ben["kind"] = np.select(
        [ben["gsis_id"] == ben["abs_id"], ben["in_rot"] & ben["did_play"], ben["in_rot"]],
        ["absent", "rot_played", "rot_missing"],
        default="new",
    )
    return ben.sort_values(["event_id", "position", "rank"]).reset_index(drop=True)


def baseline_count(ben: pd.DataFrame, channel: str) -> pd.Series:
    """A teammate's expected volume in the game if nothing changed: prior share x team baseline."""
    if channel == "targets":
        return ben["share_t"] * ben["tb_t"]
    return ben["share_c"] * ben["tb_c"]


def actual_count(ben: pd.DataFrame, channel: str) -> pd.Series:
    return ben["g_targets" if channel == "targets" else "g_carries"]


# Every non-player item `event_gains` emits, and what it is. Identity (per event, in counts):
#   ROTATION + NEW = V + OTHER_MISSING + TEAM_VOLUME        NOWHERE = V - ROTATION
LEAK_ITEMS = ("ROTATION", "NEW", "OTHER_MISSING", "TEAM_VOLUME", "NOWHERE")


def event_gains(events: pd.DataFrame, ben: pd.DataFrame) -> pd.DataFrame:
    """Long table of gains, one row per (event, channel, item), where item is a single rotation teammate
    (his label), a position group (`GRP_WR`), outsiders by position (`NEW_WR`), or a total / leak term
    (`LEAK_ITEMS`). `gain` is in targets / carries, `v` the vacated volume and `frac` = gain / v (NaN for
    control games, which vacate nothing)."""
    out = []
    ev = events.set_index("event_id")
    for channel in CHANNELS:
        v_col = "v_t" if channel == "targets" else "v_c"
        eligible = ev[ev["elig_" + channel]]
        b = ben[ben["event_id"].isin(eligible.index)].copy()
        b["base"] = baseline_count(b, channel)
        b["actual"] = actual_count(b, channel)
        b["gain"] = np.where(b["did_play"], b["actual"] - b["base"], 0.0)
        b["v"] = b[v_col]
        rp = b[b["kind"] == "rot_played"]
        out.append(
            rp.assign(item=rp["label"], item_kind="player", channel=channel)[
                ["event_id", "item", "item_kind", "gain", "v", "channel"]
            ]
        )
        grp = rp.groupby(["event_id", "position"], as_index=False).agg(gain=("gain", "sum"), v=("v", "first"))
        out.append(
            grp.assign(item="GRP_" + grp["position"], item_kind="group", channel=channel).drop(
                columns="position"
            )
        )
        new = b[b["kind"] == "new"]
        new_pos = new.groupby(["event_id", "position"], as_index=False).agg(
            gain=("g_" + channel, "sum"), v=("v", "first")
        )
        out.append(
            new_pos.assign(item="NEW_" + new_pos["position"], item_kind="new", channel=channel).drop(
                columns="position"
            )
        )
        per_event = pd.DataFrame(index=eligible.index)
        per_event["v"] = eligible[v_col]
        per_event["rot_gain"] = rp.groupby("event_id")["gain"].sum()
        per_event["new_gain"] = new.groupby("event_id")["g_" + channel].sum()
        missing = b[b["kind"] == "rot_missing"]
        per_event["other_missing"] = missing.groupby("event_id")["base"].sum()
        team_col = "t_targets" if channel == "targets" else "t_carries"
        tb_col = "tb_t" if channel == "targets" else "tb_c"
        per_event["team_delta"] = eligible[team_col] - eligible[tb_col]
        per_event = per_event.fillna(0.0).reset_index()
        for item, gain in (
            ("ROTATION", per_event["rot_gain"]),
            ("NEW", per_event["new_gain"]),
            ("OTHER_MISSING", per_event["other_missing"]),
            ("TEAM_VOLUME", per_event["team_delta"]),
            ("NOWHERE", per_event["v"] - per_event["rot_gain"]),
        ):
            out.append(
                pd.DataFrame(
                    {
                        "event_id": per_event["event_id"],
                        "item": item,
                        "item_kind": "total" if item in ("ROTATION", "NEW") else "leak",
                        "gain": gain,
                        "v": per_event["v"],
                        "channel": channel,
                    }
                )
            )
    gains = pd.concat(out, ignore_index=True)
    gains = gains.merge(
        events[["event_id", "season", "abs_pos", "abs_label", "reason"]], on="event_id", how="left"
    )
    gains["frac"] = np.where(gains["v"] > 0, gains["gain"] / gains["v"].where(gains["v"] > 0, 1.0), np.nan)
    gains["split"] = np.where(gains["season"].isin(FIT_SEASONS), "fit", "test")
    return gains


def control_means(
    control_gains: pd.DataFrame, seasons: list[int] | None = None
) -> dict[tuple[str, str], float]:
    """Mean gain per (channel, item), in targets / carries, over the control games of `seasons`: what a
    team-game with nothing vacated does by itself. Subtracted from absence games to leave the absence's
    own effect. NOWHERE is `V - ROTATION`, so its control mean is minus the rotation's, with V = 0."""
    g = control_gains if seasons is None else control_gains[control_gains["season"].isin(seasons)]
    means = g.groupby(["channel", "item"])["gain"].mean().to_dict()
    for channel in CHANNELS:
        means[(channel, "NOWHERE")] = means.get((channel, "NOWHERE"), 0.0)
    return means


def net_gains(gains: pd.DataFrame, ctrl: dict[tuple[str, str], float]) -> pd.DataFrame:
    """`gains` with the control mean removed from each item (`net_gain`, `net_frac`). Items the control
    games never show (a position group that never has a newcomer) get a control mean of 0."""
    out = gains.copy()
    keys = list(zip(out["channel"], out["item"], strict=True))
    out["ctrl_gain"] = [ctrl.get(k, 0.0) for k in keys]
    out["net_gain"] = out["gain"] - out["ctrl_gain"]
    out["net_frac"] = np.where(out["v"] > 0, out["net_gain"] / out["v"].where(out["v"] > 0, 1.0), np.nan)
    return out


# --------------------------------------------------------------------------------------------------------
# The learned table
# --------------------------------------------------------------------------------------------------------


def _summarise(frame: pd.DataFrame) -> dict:
    """Mean, median, pooled (sum of gains over sum of vacated volume) and a 90% interval of the mean, for
    both the raw gain fraction and the fraction net of control-game churn; plus the central 90% spread."""
    out = {"n": len(frame)}
    for col, prefix in (("frac", ""), ("net_frac", "net_")):
        x = frame[col].to_numpy(dtype=float)
        n = len(x)
        mean = float(np.mean(x))
        se = float(np.std(x, ddof=1) / np.sqrt(n)) if n > 1 else np.nan
        gain_col = "gain" if col == "frac" else "net_gain"
        out |= {
            f"{prefix}mean_frac": mean,
            f"{prefix}median_frac": float(np.median(x)),
            f"{prefix}pooled_frac": float(frame[gain_col].sum() / frame["v"].sum()),
            f"{prefix}ci90_lo": mean - Z90 * se if n > 1 else np.nan,
            f"{prefix}ci90_hi": mean + Z90 * se if n > 1 else np.nan,
        }
    lo, hi = percentile_interval(frame["frac"].to_numpy(), 0.90)
    out |= {"spread90_lo": lo, "spread90_hi": hi}
    return out


def learned_table(gains: pd.DataFrame) -> pd.DataFrame:
    """For every (channel, absent group, item) over the fit seasons, the test seasons and all seasons: n and
    the gain as a fraction of the vacated volume -- raw (`mean_frac` ...) and net of control-game churn
    (`net_mean_frac` ...). Absent groups: his label (WR1, RB1, ...) and his bare position (`WR`)."""
    rows = []
    for absent_kind, absent_col in (("label", "abs_label"), ("position", "abs_pos")):
        g = gains.assign(absent=gains[absent_col], absent_kind=absent_kind)
        for split, sel in (
            ("fit", g["split"] == "fit"),
            ("test", g["split"] == "test"),
            ("all", g["split"].notna()),
        ):
            sub = g[sel]
            for (channel, absent, item, kind), chunk in sub.groupby(
                ["channel", "absent", "item", "item_kind"]
            ):
                rows.append(
                    {
                        "channel": channel,
                        "absent": absent,
                        "absent_kind": absent_kind,
                        "item": item,
                        "item_kind": kind,
                        "seasons": split,
                        **_summarise(chunk),
                    }
                )
    table = pd.DataFrame(rows)
    return table.sort_values(["channel", "absent_kind", "absent", "seasons", "item"]).reset_index(drop=True)


@dataclass
class Lookup:
    """Learned fractions of the vacated volume with a fallback chain for thin cells: the exact (channel,
    absent label, item), then (channel, absent position, item), then (channel, item), then 0."""

    exact: dict
    by_pos: dict
    by_item: dict

    def frac(self, channel: str, abs_label: str, abs_pos: str, item: str) -> float:
        for table, key in (
            (self.exact, (channel, abs_label, item)),
            (self.by_pos, (channel, abs_pos, item)),
            (self.by_item, (channel, item)),
        ):
            hit = table.get(key)
            if hit is not None:
                return hit
        return 0.0


def make_lookup(gains: pd.DataFrame, seasons: list[int], column: str = "net_gain") -> Lookup:
    """Pooled fractions (sum of `column` over sum of vacated volume) from `seasons` only, for single-player
    items. `column` is `net_gain` (the absence's own effect) or `gain` (raw)."""
    g = gains[gains["season"].isin(seasons) & (gains["item_kind"] == "player")]

    def agg(keys: list[str]) -> dict:
        out = {}
        for k, chunk in g.groupby(keys):
            if len(chunk) >= MIN_CELL:
                out[k if isinstance(k, tuple) else (k,)] = chunk[column].sum() / chunk["v"].sum()
        return out

    return Lookup(
        agg(["channel", "abs_label", "item"]), agg(["channel", "abs_pos", "item"]), agg(["channel", "item"])
    )


# --------------------------------------------------------------------------------------------------------
# The hand-set rule and the out-of-sample prediction test
# --------------------------------------------------------------------------------------------------------


def _rank_of(label: str) -> int:
    digits = "".join(ch for ch in label if ch.isdigit())
    return int(digits) if digits else 1


def rule_allocation(
    abs_pos: str,
    abs_rank: int,
    channel: str,
    played: pd.DataFrame,
    next_up: float = NEXT_UP_SHARE,
    spill: float = SPILL_SHARE,
    variant: str = "next_lower",
) -> dict[str, float]:
    """The hand-set rule: fractions of the vacated volume by teammate id. `played` holds the event's
    rotation players who played (columns gsis_id, position, rank, share_t).

    next_up: `next_lower` gives the player ranked just behind the absent one (the next man up the depth
    chart; the best remaining player at the position if the absent was the last); `top_remaining` gives the
    best-ranked remaining player at the position. spill: WR out -> TEs, TE out -> WRs, targets only,
    pro rata to prior target share (equal if none has any)."""
    alloc: dict[str, float] = {}
    same = played[played["position"] == abs_pos].sort_values("rank")
    if abs_pos in ("WR", "TE", "RB") and len(same):
        if variant == "top_remaining":
            pick = same.iloc[0]
        else:
            below = same[same["rank"] > abs_rank]
            pick = below.iloc[0] if len(below) else same.iloc[0]
        alloc[pick["gsis_id"]] = next_up
    other = OTHER_GROUP.get(abs_pos)
    if channel == "targets" and other:
        grp = played[played["position"] == other]
        if len(grp):
            weights = grp["share_t"].to_numpy(dtype=float)
            weights = weights / weights.sum() if weights.sum() > 0 else np.full(len(grp), 1 / len(grp))
            for pid, w in zip(grp["gsis_id"], weights, strict=True):
                alloc[pid] = alloc.get(pid, 0.0) + spill * float(w)
    return alloc


def _next_up_id(abs_pos: str, abs_rank: int, played: pd.DataFrame) -> str | None:
    """The teammate the hand-set rule calls the next man up: the player ranked just behind the absent one at
    his position (the best remaining one if the absent was the last)."""
    same = played[played["position"] == abs_pos].sort_values("rank")
    if abs_pos not in ("WR", "TE", "RB") or not len(same):
        return None
    below = same[same["rank"] > abs_rank]
    return (below.iloc[0] if len(below) else same.iloc[0])["gsis_id"]


METHODS = {
    "a_prior_share": "pred_a",
    "a2_prior_share_plus_churn": "pred_a_churn",
    "b_hand_rule": "pred_b",
    "b2_hand_rule_top_remaining": "pred_b_top",
    "b3_hand_rule_plus_churn": "pred_b_churn",
    "c_learned_table": "pred_c",
    "c2_learned_raw_no_churn_split": "pred_c_raw",
    "d_rule_refit_plus_churn": "pred_d",
}


def prediction_frame(
    events: pd.DataFrame,
    ben: pd.DataFrame,
    lookup: Lookup,
    raw_lookup: Lookup,
    structure: dict,
    churn: dict[tuple[str, str], float],
) -> pd.DataFrame:
    """One row per (channel, event, rotation teammate who played), with the actual count and the
    predictions. `base` = prior share x the team's baseline volume.

    (a)  base                            (a2) base + control-game gain for his label
    (b)  base + hand-set rule x V        (b3) (b) + the control-game gain
    (c)  base + control gain + learned net fraction x V  -- the learned table
    (c2) base + learned RAW fraction x V (no separate churn term)
    (d)  the hand-set rule's structure with its two constants refit on the fit seasons, + control gain."""
    ev = events.set_index("event_id")
    rp = ben[ben["kind"] == "rot_played"]
    rows = []
    for event_id, chunk in rp.groupby("event_id"):
        e = ev.loc[event_id]
        for channel in CHANNELS:
            if not e["elig_" + channel]:
                continue
            share_col = "share_t" if channel == "targets" else "share_c"
            v = e["v_t" if channel == "targets" else "v_c"]
            tb = e["tb_t" if channel == "targets" else "tb_c"]
            rank = _rank_of(e["abs_label"])
            rule = rule_allocation(e["abs_pos"], rank, channel, chunk)
            rule_top = rule_allocation(e["abs_pos"], rank, channel, chunk, variant="top_remaining")
            fit = structure.get((channel, e["abs_pos"]), {"next_up": 0.0, "spill": 0.0})
            refit = rule_allocation(e["abs_pos"], rank, channel, chunk, fit["next_up"], fit["spill"])
            next_up_id = _next_up_id(e["abs_pos"], rank, chunk)
            other_pos = OTHER_GROUP.get(e["abs_pos"]) if channel == "targets" else None
            for r in chunk.itertuples():
                base = getattr(r, share_col) * tb
                actual = r.g_targets if channel == "targets" else r.g_carries
                c = churn.get((channel, r.label), 0.0)
                rows.append(
                    {
                        "event_id": event_id,
                        "channel": channel,
                        "season": e["season"],
                        "abs_label": e["abs_label"],
                        "abs_pos": e["abs_pos"],
                        "gsis_id": r.gsis_id,
                        "label": r.label,
                        "is_next_up": r.gsis_id == next_up_id,
                        "in_other_group": other_pos is not None and r.position == other_pos,
                        "actual": actual,
                        "pred_a": base,
                        "pred_a_churn": base + c,
                        "pred_b": base + rule.get(r.gsis_id, 0.0) * v,
                        "pred_b_top": base + rule_top.get(r.gsis_id, 0.0) * v,
                        "pred_b_churn": base + c + rule.get(r.gsis_id, 0.0) * v,
                        "pred_c": base + c + lookup.frac(channel, e["abs_label"], e["abs_pos"], r.label) * v,
                        "pred_c_raw": base
                        + raw_lookup.frac(channel, e["abs_label"], e["abs_pos"], r.label) * v,
                        "pred_d": base + c + refit.get(r.gsis_id, 0.0) * v,
                    }
                )
    return pd.DataFrame(rows)


def fit_structure(events: pd.DataFrame, ben: pd.DataFrame, ctrl_player: dict, seasons: list[int]) -> dict:
    """Refit the hand-set rule's two constants on `seasons`: for each (channel, absent position), the pooled
    share of the vacated volume (net of control-game churn) that the next-lower same-position player gained,
    and, for targets with a WR or TE out, what the other group gained in total."""
    ev = events[events["season"].isin(seasons)].set_index("event_id")
    rp = ben[(ben["kind"] == "rot_played") & ben["event_id"].isin(ev.index)]
    acc: dict[tuple, dict[str, float]] = {}
    for event_id, chunk in rp.groupby("event_id"):
        e = ev.loc[event_id]
        for channel in CHANNELS:
            if not e["elig_" + channel] or e["abs_pos"] not in ("WR", "TE", "RB"):
                continue
            share_col = "share_t" if channel == "targets" else "share_c"
            tb = e["tb_t" if channel == "targets" else "tb_c"]
            v = e["v_t" if channel == "targets" else "v_c"]
            same = chunk[chunk["position"] == e["abs_pos"]].sort_values("rank")
            if not len(same):
                continue
            below = same[same["rank"] > _rank_of(e["abs_label"])]
            pick = below.iloc[0] if len(below) else same.iloc[0]
            count = pick["g_targets" if channel == "targets" else "g_carries"]
            gain_next = count - pick[share_col] * tb - ctrl_player.get((channel, pick["label"]), 0.0)
            other = OTHER_GROUP.get(e["abs_pos"])
            gain_other = 0.0
            if channel == "targets" and other:
                grp = chunk[chunk["position"] == other]
                churn = sum(ctrl_player.get((channel, lab), 0.0) for lab in grp["label"])
                gain_other = float((grp["g_targets"] - grp["share_t"] * tb).sum()) - churn
            a = acc.setdefault((channel, e["abs_pos"]), {"v": 0.0, "next": 0.0, "other": 0.0})
            a["v"] += v
            a["next"] += gain_next
            a["other"] += gain_other
    return {
        k: {"next_up": a["next"] / a["v"], "spill": a["other"] / a["v"] if k[0] == "targets" else 0.0}
        for k, a in acc.items()
        if a["v"] > 0
    }


def _paired(diff: np.ndarray, codes: np.ndarray) -> tuple[float, float, float]:
    """Mean of a paired difference with a 90% interval clustered by event."""
    gain = float(diff.mean())
    per = np.bincount(codes, weights=diff - gain)
    se = float(np.sqrt(per.size / max(per.size - 1, 1) * np.sum(per**2)) / len(diff))
    return gain, gain - Z90 * se, gain + Z90 * se


SUBSETS = ("all_rotation", "next_up_player", "other_pass_catching_group")


def mae_table(pred: pd.DataFrame) -> pd.DataFrame:
    """MAE and RMSE of every method by channel, season group and subset, with each method's paired
    improvement over (a) and over the hand-set rule (b) (positive = better), 90% intervals clustered by
    event. Subsets: every rotation teammate who played; just the player the rule calls the next man up;
    just the other pass-catching group (targets with a WR or TE out)."""
    out = []
    pred = pred.assign(split=np.where(pred["season"].isin(FIT_SEASONS), "fit", "test"))
    masks = {
        "all_rotation": pd.Series(True, index=pred.index),
        "next_up_player": pred["is_next_up"],
        "other_pass_catching_group": pred["in_other_group"],
    }
    for subset, mask in masks.items():
        sub = pred[mask]
        for (channel, split), chunk in sub.groupby(["channel", "split"]):
            codes = pd.factorize(chunk["event_id"])[0]
            err_a = (chunk["pred_a"] - chunk["actual"]).abs()
            err_b = (chunk["pred_b"] - chunk["actual"]).abs()
            for name, col in METHODS.items():
                err = (chunk[col] - chunk["actual"]).abs()
                g_a = _paired((err_a - err).to_numpy(), codes)
                g_b = _paired((err_b - err).to_numpy(), codes)
                out.append(
                    {
                        "channel": channel,
                        "seasons": split,
                        "subset": subset,
                        "method": name,
                        "n": len(chunk),
                        "events": int(chunk["event_id"].nunique()),
                        "mae": float(err.mean()),
                        "rmse": float(np.sqrt(((chunk[col] - chunk["actual"]) ** 2).mean())),
                        "gain_vs_a": g_a[0],
                        "gain_vs_a_lo90": g_a[1],
                        "gain_vs_a_hi90": g_a[2],
                        "gain_vs_b": g_b[0],
                        "gain_vs_b_lo90": g_b[1],
                        "gain_vs_b_hi90": g_b[2],
                    }
                )
    return pd.DataFrame(out)


# --------------------------------------------------------------------------------------------------------
# The study
# --------------------------------------------------------------------------------------------------------


def run_study(sp: pd.DataFrame, games: pd.DataFrame, injuries: pd.DataFrame) -> dict:
    """Everything R1 produces, in memory: absence counts, the learned table, the prediction test."""
    tg = team_game_index(games)
    rows = offense_rows(sp, tg)
    prior = prior_windows(rows, team_games=tg)
    rot = rank_rotation(prior)
    labelled = find_absences(prior, rows, tg).merge(
        rot[["team", "season", "tidx", "gsis_id", "label", "rank"]],
        on=["team", "season", "tidx", "gsis_id"],
        how="left",
    )
    absences = tag_reasons(labelled, injuries)
    volume = team_volume(rows)
    events = clean_events(absences, volume)
    controls = control_events(prior, volume, absences)

    ben = beneficiaries(events, rot, rows)
    ben_c = beneficiaries(controls, rot, rows)
    gains_c = event_gains(controls, ben_c)
    ctrl_all = control_means(gains_c)
    ctrl_fit = control_means(gains_c, FIT_SEASONS)
    gains = net_gains(event_gains(events, ben), ctrl_all)
    gains_fit_net = net_gains(gains.drop(columns=["ctrl_gain", "net_gain", "net_frac"]), ctrl_fit)
    table = learned_table(gains)

    churn_fit = {k: v for k, v in ctrl_fit.items()}
    lookup = make_lookup(gains_fit_net, FIT_SEASONS, "net_gain")
    raw_lookup = make_lookup(gains_fit_net, FIT_SEASONS, "gain")
    structure = fit_structure(events, ben, churn_fit, FIT_SEASONS)
    pred = prediction_frame(events, ben, lookup, raw_lookup, structure, churn_fit)
    mae = mae_table(pred)

    counts = {
        "regular_absences_all": int(len(absences)),
        "team_games_with_regular_out": int(absences[["team", "season", "tidx"]].drop_duplicates().shape[0]),
        "clean_events": int(len(events)),
        "multi_absence_team_games": int(
            absences[absences["n_regulars_out"] > 1][["team", "season", "tidx"]].drop_duplicates().shape[0]
        ),
        "control_team_games": int(len(controls)),
        "by_reason": events["reason"].value_counts().to_dict(),
        "by_reason_detail": events["reason_detail"].value_counts().to_dict(),
        "by_absent_label": events["abs_label"].value_counts().to_dict(),
        "by_season": {int(k): int(v) for k, v in events["season"].value_counts().sort_index().items()},
    }
    return {
        "tg": tg,
        "rows": rows,
        "prior": prior,
        "absences": absences,
        "events": events,
        "controls": controls,
        "beneficiaries": ben,
        "gains": gains,
        "control_gains": gains_c,
        "control_means": ctrl_all,
        "table": table,
        "prediction": pred,
        "mae": mae,
        "structure": structure,
        "counts": counts,
    }


def recommended_constants(result: dict) -> dict:
    """The learned equivalents of the hand-set constants, from ALL seasons, per (channel, absent label):
    what the next-lower same-position player, the same-position group, the other pass-catching group and
    outsiders gained, and how much went nowhere -- each as a fraction of the vacated volume, net of
    control-game churn (`net_*`) and raw."""
    table = result["table"]
    t = table[(table["seasons"] == "all") & (table["absent_kind"] == "label")]
    keys = ("n", "net_mean_frac", "net_median_frac", "net_pooled_frac", "net_ci90_lo", "net_ci90_hi")
    keys += ("mean_frac", "median_frac", "pooled_frac")

    def pick(channel: str, absent: str, item: str) -> dict | None:
        hit = t[(t["channel"] == channel) & (t["absent"] == absent) & (t["item"] == item)]
        if hit.empty:
            return None
        r = hit.iloc[0]
        return {k: (int(r[k]) if k == "n" else float(r[k])) for k in keys}

    out: dict[str, dict] = {}
    for channel in CHANNELS:
        for absent in sorted(t[t["channel"] == channel]["absent"].unique()):
            pos = absent.rstrip("0123456789+")
            nxt = rank_label(pos, _rank_of(absent) + 1)
            out[f"{channel}:{absent}"] = {
                "next_lower_same_position": {"item": nxt, **(pick(channel, absent, nxt) or {})},
                "same_position_group": pick(channel, absent, f"GRP_{pos}"),
                "other_pass_catching_group": (
                    pick(channel, absent, f"GRP_{OTHER_GROUP[pos]}") if pos in OTHER_GROUP else None
                ),
                "outside_prior_rotation": pick(channel, absent, "NEW"),
                "rotation_total": pick(channel, absent, "ROTATION"),
                "team_volume_change": pick(channel, absent, "TEAM_VOLUME"),
                "nowhere": pick(channel, absent, "NOWHERE"),
            }
    return out


STABILITY_ITEMS = ("ROTATION", "NOWHERE", "NEW", "TEAM_VOLUME", "GRP_WR", "GRP_TE", "GRP_RB")


def period_stability(gains: pd.DataFrame) -> list[dict]:
    """For the headline items, the net gain fraction in the fit seasons against the test seasons and whether
    the two differ by more than chance (`z` = difference / its standard error). A learned constant is only as
    good as its stability: a |z| above about 2 says the 2014-2021 behaviour is not the 2022-2025 behaviour."""
    rows = []
    g = gains[gains["item"].isin(STABILITY_ITEMS) & gains["abs_pos"].isin(("WR", "RB", "TE"))]
    for (channel, abs_pos, item), chunk in g.groupby(["channel", "abs_pos", "item"]):
        a = chunk.loc[chunk["split"] == "fit", "net_frac"].to_numpy(dtype=float)
        b = chunk.loc[chunk["split"] == "test", "net_frac"].to_numpy(dtype=float)
        if len(a) < 5 or len(b) < 5:
            continue
        se = float(np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b)))
        diff = float(b.mean() - a.mean())
        rows.append(
            {
                "channel": channel,
                "absent_pos": abs_pos,
                "item": item,
                "n_fit": len(a),
                "n_test": len(b),
                "fit_mean": float(a.mean()),
                "test_mean": float(b.mean()),
                "diff_test_minus_fit": diff,
                "z": diff / se if se > 0 else float("nan"),
            }
        )
    return rows


def reason_breakdown(gains: pd.DataFrame) -> list[dict]:
    """The same headline items by why the player was out (injury report / suspension / other)."""
    rows = []
    g = gains[gains["item"].isin(STABILITY_ITEMS) & gains["abs_pos"].isin(("WR", "RB", "TE"))]
    for (channel, abs_pos, reason, item), chunk in g.groupby(["channel", "abs_pos", "reason", "item"]):
        if len(chunk) < 5:
            continue
        rows.append(
            {
                "channel": channel,
                "absent_pos": abs_pos,
                "reason": reason,
                "item": item,
                "n": len(chunk),
                "net_mean_frac": float(chunk["net_frac"].mean()),
                "net_pooled_frac": float(chunk["net_gain"].sum() / chunk["v"].sum()),
            }
        )
    return rows


def write_outputs(result: dict, directory=None) -> None:
    n = len(result["events"])
    meta = metadata("r1", SEASONS, n, unit="clean single-regular absences", min_cell=MIN_CELL)
    write_csv("redistribution.csv", result["table"], meta, directory)
    ctrl = result["control_means"]
    payload = {
        "definitions": {
            "regular": (
                f"prior-{WINDOW}-game target share >= {TARGET_REGULAR} or carry share >= {CARRY_REGULAR}"
            ),
            "hand_set_rule": {"next_up_share": NEXT_UP_SHARE, "spill_share": SPILL_SHARE},
        },
        "counts": result["counts"],
        "control_game_mean_gain": {f"{c}:{i}": float(v) for (c, i), v in sorted(ctrl.items())},
        "refit_rule_constants_fit_seasons": {f"{k[0]}:{k[1]}": v for k, v in result["structure"].items()},
        "recommended": recommended_constants(result),
        "period_stability": period_stability(result["gains"]),
        "by_reason": reason_breakdown(result["gains"]),
        "prediction_mae": result["mae"].to_dict(orient="records"),
    }
    write_json("redistribution_constants.json", payload, meta, directory)


def run(log=lambda msg: None) -> dict:
    """Read the cache, run R1, write `redistribution.csv` and `redistribution_constants.json`."""
    log("R1: reading player stats, schedule and injuries")
    cols = ["player_id", "player_display_name", "position", "team", "season", "week", "season_type"]
    sp = data.read_stats_player(SEASONS, [*cols, "targets", "carries", "attempts"])
    result = run_study(sp, data.read_games(), data.read_injuries())
    write_outputs(result)
    return result


__all__ = ["run", "run_study", "FIT_SEASONS", "TEST_SEASONS"]
