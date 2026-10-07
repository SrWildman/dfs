"""Roles within a team-game, and the frame the correlations are fitted on.

A role is a player's slot on his team *as of the game's kickoff*: QB1 is the quarterback with the most pass
attempts, RB1-RB3 the running backs by carries + targets, WR1-WR4 and TE1-TE2 the pass catchers by targets,
each over the player's own **prior three games** (games played, continuing across the season boundary --
the same convention as `dfs.model.features`). The game itself is never used.

Ranking is among the players who appeared in the game: nobody knows from this data who was inactive, so "WR1"
means the top prior-usage receiver *who played*. A player with no prior game has no role. Rank beyond the last
named role collapses into it (a fourth running back is RB3, a fifth receiver WR4, a third tight end TE2), so
a lineup's FLEX spot always has a role; a second quarterback has none.

DST is its own role: one row per team-game.
"""

from __future__ import annotations

import pandas as pd

from dfs.model.history import History

ROLE_WINDOW = 3

# Position -> (usage column the ranking uses, role names by rank).
ROLE_NAMES = {
    "QB": ("QB1",),
    "RB": ("RB1", "RB2", "RB3"),
    "WR": ("WR1", "WR2", "WR3", "WR4"),
    "TE": ("TE1", "TE2"),
}
# Positions where only the named ranks have a role: a team's second quarterback is not "QB1".
NO_COLLAPSE = frozenset({"QB"})
# Every role the simulator knows, in the order pairs are written (a pair is stored as (earlier, later)).
ROLES = ("QB1", "RB1", "RB2", "RB3", "WR1", "WR2", "WR3", "WR4", "TE1", "TE2", "DST")
ROLE_POSITION = {role: pos for pos, names in ROLE_NAMES.items() for role in names} | {"DST": "DST"}
# Roles that only exist from a given rank on and absorb every lower rank.
_ROLE_ALIASES = {"QB": "QB1", "RB": "RB1", "WR": "WR1", "TE": "TE1"}


def normalize_role(role: str) -> str:
    """`role` as one of `ROLES` ("QB" reads as "QB1", "TE" as "TE1"; WR5 and RB4 fold into the last role)."""
    r = role.strip().upper()
    r = _ROLE_ALIASES.get(r, r)
    if r in ROLES:
        return r
    pos, digits = r[:2], r[2:]
    if (
        pos in ROLE_NAMES
        and pos not in NO_COLLAPSE
        and digits.isdigit()
        and int(digits) >= len(ROLE_NAMES[pos])
    ):
        return ROLE_NAMES[pos][-1]
    raise ValueError(f"unknown role {role!r}; expected one of {', '.join(ROLES)}")


def _usage(player_games: pd.DataFrame) -> pd.Series:
    """The per-game usage number each position is ranked on."""
    pg = player_games
    return (
        pg["attempts"].where(pg["position"] == "QB", 0.0)
        + pg["carries"].where(pg["position"] == "RB", 0.0)
        + pg["targets"].where(pg["position"] != "QB", 0.0)
    )


def prior_usage(player_games: pd.DataFrame, window: int = ROLE_WINDOW) -> pd.Series:
    """Mean usage over each player's `window` games BEFORE the row's game (NaN with no prior game), indexed
    like `player_games`."""
    pg = player_games.sort_values(["gsis_id", "t"])
    use = _usage(pg)
    prior = use.groupby(pg["gsis_id"], sort=False).transform(
        lambda s: s.shift(1).rolling(window, min_periods=1).mean()
    )
    return prior.reindex(player_games.index)


def assign_roles(player_games: pd.DataFrame) -> pd.Series:
    """Role per player-game ('' where the player has none), indexed like `player_games`."""
    pg = player_games.assign(_prior=prior_usage(player_games))
    ranked = pg[pg["_prior"].notna()].sort_values(
        ["game_id", "team", "position", "_prior", "gsis_id"], ascending=[True, True, True, False, True]
    )
    rank = ranked.groupby(["game_id", "team", "position"], sort=False).cumcount()
    roles = pd.Series("", index=player_games.index, dtype=object)
    for pos, names in ROLE_NAMES.items():
        sel = ranked["position"] == pos
        idx = rank[sel] if pos in NO_COLLAPSE else rank[sel].clip(upper=len(names) - 1)
        roles.loc[ranked.index[sel]] = [names[i] if i < len(names) else "" for i in idx]
    return roles


def build_frame(history: History, oof: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per out-of-fold player-game (every position, DST included) with the context the correlations
    need: `season, week, game_id, team, opp, position, gsis_id, name, role, pred, dk, total`.

    Rows whose role is empty (no prior game, or a position without roles) are dropped, as are rows whose
    projection is not positive (the outcome ratio is undefined)."""
    pg = history.player_games
    roles = assign_roles(pg)
    keyed = pg.assign(role=roles)[
        ["gsis_id", "name", "season", "week", "game_id", "team", "opp", "position", "role", "total"]
    ]
    parts = []
    for position, frame in oof.items():
        if position == "DST":
            tg = history.team_games[
                ["gsis_id", "season", "week", "game_id", "team", "opp", "position", "total"]
            ]
            joined = frame.merge(tg, on=["gsis_id", "season", "week"], how="left", validate="one_to_one")
            joined["role"] = "DST"
            joined["name"] = joined["team"] + " DST"
        else:
            joined = frame.merge(
                keyed[keyed["position"] == position],
                on=["gsis_id", "season", "week"],
                how="left",
                validate="one_to_one",
            )
        parts.append(joined)
    out = pd.concat(parts, ignore_index=True)
    out = out[(out["role"] != "") & (out["pred"] > 0) & out["game_id"].notna()]
    cols = [
        "season",
        "week",
        "game_id",
        "team",
        "opp",
        "position",
        "gsis_id",
        "name",
        "role",
        "pred",
        "dk",
        "total",
    ]
    return out[cols].sort_values(["season", "week", "game_id", "team", "role"]).reset_index(drop=True)
