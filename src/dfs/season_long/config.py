"""`season.toml`: Sam's season-long leagues and the few numbers he can override.

A file of its own rather than a table in config.toml: `[season]` there is the DFS sheet's Season tab, and the
weekly CLI's `Config` forbids unknown keys, so a new table would break every worktree sharing config.toml
until this code merged (Sam, 2026-10-28).
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from dfs.paths import REPO_ROOT

SEASON_CONFIG_FILE = REPO_ROOT / "season.toml"


class SeasonConfigError(Exception):
    pass


class LeagueConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    platform: Literal["sleeper", "espn", "yahoo"] = "sleeper"
    league_id: str
    username: str
    # When Sleeper's settings have no playoff start (Mega's say 0), the week its playoffs start.
    playoff_week_start: int | None = Field(None, ge=1, le=18)
    # What one FAAB dollar is worth in this league, in team-value points; overrides the one fitted from the
    # league's own winning bids (`faab.py`). Not used in a chopped league, where FAAB is simulated.
    faab_point_value: float | None = Field(None, ge=0.0)


class SeasonLongConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sheet_id: str | None = None
    leagues: list[LeagueConfig] = Field(default_factory=list)
    # A playoff week's lineup counts this many regular weeks in team value.
    playoff_weight: float = Field(1.5, ge=0.0)
    # Bench as cover: each starter misses a given week with this chance (injury, a late scratch); team value
    # takes the expected loss, so a bench that covers it is worth that much and no more.
    starter_miss_rate: float = Field(0.06, ge=0.0, le=1.0)
    # A player on IR / PUP / NFI / suspended counts as out this many weeks from now.
    ir_weeks_out: int = Field(4, ge=0)
    # Chopped leagues: week w ahead weighs `survival_decay ** w` (this week 1.0).
    survival_decay: float = Field(0.8, gt=0.0, le=1.0)
    survival_draws: int = Field(4000, ge=200)
    # Sam's buy list, across leagues: the trade finder's targets (`finder.Filters.targets`), with each
    # league's weakest position. A name he already owns in a league is skipped there.
    buy_list: list[str] = Field(default_factory=list)


def load_season_config(path: Path | None = None) -> SeasonLongConfig:
    path = path or SEASON_CONFIG_FILE
    if not path.exists():
        raise SeasonConfigError(
            f"No season.toml at {path}. It holds sheet_id and one [[leagues]] table per league "
            "(name, platform, league_id, username); see docs/SEASON.md."
        )
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
        return SeasonLongConfig.model_validate(raw)
    except (tomllib.TOMLDecodeError, ValidationError) as e:
        raise SeasonConfigError(f"season.toml is invalid:\n{e}") from e


def select_leagues(cfg: SeasonLongConfig, name: str | None, all_leagues: bool) -> list[LeagueConfig]:
    """`--league <name>` (case-insensitive) or `--all`; with neither and a single league, that league."""
    if all_leagues:
        return list(cfg.leagues)
    if name:
        hits = [lg for lg in cfg.leagues if lg.name.lower() == name.strip().lower()]
        if not hits:
            names = ", ".join(lg.name for lg in cfg.leagues)
            raise SeasonConfigError(f"No league named {name!r} in season.toml (leagues: {names}).")
        return hits
    if len(cfg.leagues) == 1:
        return list(cfg.leagues)
    raise SeasonConfigError("Name a league with --league <name>, or pass --all.")
