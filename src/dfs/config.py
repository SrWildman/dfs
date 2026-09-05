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
    scratch_tab: str = "Scratch"


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


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    google_sheets: GoogleSheetsConfig
    nfl_odds: NflOddsConfig = NflOddsConfig()
    lineups: LineupsConfig = LineupsConfig()
    bankroll: BankrollConfig = BankrollConfig()


def _missing_config_message() -> str:
    example = (
        CONFIG_EXAMPLE_FILE.name
        if CONFIG_EXAMPLE_FILE.exists()
        else "config.example.toml"
    )
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
