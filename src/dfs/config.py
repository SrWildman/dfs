"""Typed config, loaded from config.toml.

Replaces utils/config.py + config.json. The old config.json was ~70% dead:
file_management, advanced, workflows, and google_sheets.update_behavior had
zero readers anywhere in the codebase, and scrapers.nfl_odds used key names
(base_url, min_week, max_week) that the code never actually looked up (it
read its own hardcoded SCRAPER_SETTINGS instead) -- the two had silently
drifted apart. Pydantic's `extra="forbid"` here means a typo'd or dead key
is a load-time error instead of a silent no-op, and a missing required key
is a clear validation error instead of a KeyError three calls deep.
"""

from __future__ import annotations

import sys
import tomllib
from functools import lru_cache

from pydantic import BaseModel, ConfigDict, ValidationError

from dfs.paths import CONFIG_EXAMPLE_FILE, CONFIG_FILE


class ConfigError(Exception):
    """Raised for any problem loading or validating config.toml."""


class GoogleSheetsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sheet_id: str
    credentials_file: str
    tab_mappings: dict[str, str]

    # Set automatically by `dfs week new` when it rewrites sheet_id above --
    # the sheet that was current *before* that rewrite, so bankroll carryover
    # and any other "diff against last week" logic doesn't need the user to
    # paste last week's URL a second time. Not meant to be hand-edited.
    previous_sheet_id: str | None = None


class NflOddsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_week: int | None = None
    default_season: int | None = None


class LineupsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The tab where you pair finished lineups to DK contest entries (DK's
    # own "bulk edit entries" layout: Entry ID, Contest Name, Contest ID,
    # Entry Fee, then the 9 roster slot columns). dfs export reads this
    # tab as-is -- pairing lineups to entries stays a manual step in the
    # sheet, per your workflow.
    upload_tab: str = "DK Upload"
    salary_cap: int = 50000

    # Tabs `dfs lineups clear` resets at the start of a new week -- typed
    # values (names/picks), not formulas, so they don't reset on their own
    # when the sheet is duplicated from the template. See weekly_reset.py.
    builder_tab: str = "Lineups"
    player_pool_tab: str = "Player Pool"


class EntryTableConfig(BaseModel):
    """One append-only entry ledger inside the bankroll tab: a header row,
    a fixed range of data rows with pre-built per-row formulas already in
    place (e.g. "% Paid"/"Place %"), and a spare column for our dedupe key.
    Row numbers are sheet-specific -- set these to match your own tab."""

    model_config = ConfigDict(extra="forbid")

    header_row: int
    first_row: int
    last_row: int
    entry_key_column: str = "L"


class BankrollConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tab: str = "Bankroll"
    cash: EntryTableConfig | None = None
    gpp: EntryTableConfig | None = None
    # Round 5, item 7: the hand-entered Betting ledger, above Cash on the
    # tab (see sheet_bankroll_view.py's module docstring for why). Reuses
    # EntryTableConfig for its header/first/last row shape even though
    # there's no CSV sync or dedupe key for hand-typed bets --
    # `entry_key_column` is simply unused for this bucket.
    bets: EntryTableConfig | None = None


class ResultsConfig(BaseModel):
    """The season-level Results log: a header row plus a fixed range of
    data rows with two formula columns already built into every row
    (`Cash Results`, `H2H %` -- columns D and G, see
    `week.RESULTS_VALUE_COLUMN_RANGES`). Unlike Bankroll's per-week
    entries, this tab isn't reset by a new weekly sheet copy at all --
    `dfs week new` copies its typed-value columns from the outgoing sheet
    to the new one so the season log keeps accumulating across copies
    instead of resetting to empty every week."""

    model_config = ConfigDict(extra="forbid")

    tab: str = "Results"
    header_row: int = 1
    first_row: int = 2
    last_row: int = 20


class SeasonConfig(BaseModel):
    """Round 5, item 7d: the season-level Betting/Cash/GPP net rollup --
    one pre-built row per NFL week (1-`nfl_calendar.MAX_WEEK`), found by
    matching column A against the week number, same convention as
    `ResultsConfig`/`results_autofill.py`. Also not reset by a new weekly
    sheet copy -- see `sheet_season_view.py`'s module docstring for the
    full column layout."""

    model_config = ConfigDict(extra="forbid")

    tab: str = "Season"
    header_row: int = 1
    first_row: int = 2
    last_row: int = 19


# The GPP target the simulator's P(GPP) column uses until config.toml says otherwise. A tournament-winning
# neighbourhood for a small field, not advice: Sam sets his own with `[sim] gpp_target`.
SIM_GPP_TARGET_DEFAULT = 190.0


class SimConfig(BaseModel):
    """`[sim]`: the lineup simulator's one setting. The cash line is NOT here: it is Sam's typed `Cash Line`
    in Results (the median of the last three weeks), read from the sheet each time."""

    model_config = ConfigDict(extra="forbid")

    gpp_target: float = SIM_GPP_TARGET_DEFAULT


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    google_sheets: GoogleSheetsConfig
    nfl_odds: NflOddsConfig = NflOddsConfig()
    lineups: LineupsConfig = LineupsConfig()
    bankroll: BankrollConfig = BankrollConfig()
    results: ResultsConfig = ResultsConfig()
    season: SeasonConfig = SeasonConfig()
    sim: SimConfig = SimConfig()


def _missing_config_message() -> str:
    example = CONFIG_EXAMPLE_FILE.name if CONFIG_EXAMPLE_FILE.exists() else "config.example.toml"
    return (
        f"No config.toml found at {CONFIG_FILE}.\n"
        f"Copy {example} to config.toml and fill in your sheet_id and "
        f"credentials_file."
    )


@lru_cache(maxsize=1)
def load_config() -> Config:
    if not CONFIG_FILE.exists():
        raise ConfigError(_missing_config_message())

    try:
        with CONFIG_FILE.open("rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"config.toml is not valid TOML: {e}") from e

    try:
        return Config.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(f"config.toml is invalid:\n{e}") from e


if __name__ == "__main__":  # pragma: no cover
    try:
        cfg = load_config()
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    print(cfg.model_dump_json(indent=2))
