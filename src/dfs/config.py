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
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

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


# Where each contest line sits relative to that contest's FIELD MEAN (the mean entry score, which is exactly
# the ownership-weighted sum of the players' points), measured on Sam's 20 real standings files (Weeks 2, 4
# and 5, 2026); see docs/SIM.md "Relative lines". Points above the field mean.
SIM_DOUBLE_UP_OFFSET = 0.6  # the double-up cash line (7 contests, sd 4.9)
SIM_GPP_CASH_OFFSET = 17.9  # a GPP's min-cash line, 21-26% paid (13 contests, sd 1.9)
SIM_GPP_TOP10_OFFSET = 31.9  # the GPP top-10% line (9 contests, sd 1.2)
SIM_GPP_TOP1_OFFSET = 63.2  # the GPP top-1% line, fields of 700+ (9 contests, sd 5.7)
SIM_OWN_COVERAGE = 0.95  # the simulated field players hold at least this share of the total ownership


class SimLinesConfig(BaseModel):
    """`[sim.lines]`: the lines the lineup simulator scores against, as offsets from the field mean.

    Cash % is P(lineup >= field mean + `double_up_offset`) and Top 1% is P(lineup >= field mean +
    `gpp_top1_offset`); the other two offsets are the measured min-cash and top-10% lines, kept here for
    reference (the simulator does not report them yet). Before ownership publishes (Own% is blank) there is no
    field to measure against, and the simulator falls back to FIXED lines: `fallback_cash_line` /
    `fallback_top1_line`, which left unset mean the old fixed numbers (the season median of the Cash Lines Sam
    typed on Results, and `[sim] gpp_target`).

    `double_up_own_curve` / `gpp_own_curve` map PROJECTED Own% to the Own% a contest type is expected to have:
    a list of `[projected, expected]` points (fractions), piecewise-linear, anchored at (0, 0) and (1, 1);
    empty (the default) is the identity. Double-ups are chalkier than the large-field projection (players
    projected over 15% were owned about 39% in Sam's files), so the local session fits that curve."""

    model_config = ConfigDict(extra="forbid")

    double_up_offset: float = SIM_DOUBLE_UP_OFFSET
    gpp_cash_offset: float = SIM_GPP_CASH_OFFSET
    gpp_top10_offset: float = SIM_GPP_TOP10_OFFSET
    gpp_top1_offset: float = SIM_GPP_TOP1_OFFSET
    fallback_cash_line: float | None = None
    fallback_top1_line: float | None = None
    double_up_own_curve: list[tuple[float, float]] = []
    gpp_own_curve: list[tuple[float, float]] = []
    own_coverage: float = Field(SIM_OWN_COVERAGE, gt=0.0, le=1.0)

    @field_validator("double_up_own_curve", "gpp_own_curve")
    @classmethod
    def _curve_is_monotone(cls, curve: list[tuple[float, float]]) -> list[tuple[float, float]]:
        points = sorted(curve)
        if any(not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0) for x, y in points):
            raise ValueError("ownership curve points are fractions between 0 and 1")
        if any(b[0] <= a[0] for a, b in zip(points, points[1:], strict=False)):
            raise ValueError("ownership curve: the projected values must be strictly increasing")
        if any(b[1] < a[1] for a, b in zip(points, points[1:], strict=False)):
            raise ValueError("ownership curve: the expected values must not decrease")
        return curve


class SimConfig(BaseModel):
    """`[sim]`: the lineup simulator's settings. `gpp_target` is the FIXED top-end line (the old `190+ %`;
    used with `--fixed-lines` and as the fallback before ownership publishes). The fixed cash line is NOT
    here: it is Sam's typed `Cash Line` in Results (the median of every typed week this season), read from
    the sheet each time. The lines that move with the slate are `[sim.lines]`."""

    model_config = ConfigDict(extra="forbid")

    gpp_target: float = SIM_GPP_TARGET_DEFAULT
    lines: SimLinesConfig = SimLinesConfig()


PROJECTION_CHOICES = ("CalPts", "ProjPts", "AggPts")

# Shadow projection sources (`calibration.py`): scored by Model Check from their first week, but kept out
# of the CalPts blend until they have `shadow_min_weeks` scored weeks. A source named here that has no
# column yet (ESPN's is still being wired up) is simply never active, so listing it early is harmless.
SHADOW_SOURCES_DEFAULT = ("EspnPts", "PropPts")
SHADOW_MIN_WEEKS_DEFAULT = 3


class ProjectionConfig(BaseModel):
    """`[projection]`: which projection the model-driven tools act on (late swap, the Lineups simulator, the
    Suggested builds, the Board's portfolio line). `CalPts` is our corrected projection (Sam, Week 6: after
    five weeks it beats TFFB clearly at RB and WR and ties at QB and TE); wherever CalPts is blank the tools
    fall back to TFFB's `ProjPts`. TFFB stays visible on every tab for comparison."""

    model_config = ConfigDict(extra="forbid")

    default: Literal["CalPts", "ProjPts", "AggPts"] = "CalPts"
    # Columns of the scored table that are tracked but not yet blended into CalPts. A shadow source joins
    # the blend (through the same inverse-error weighting and shrinkage as the others) once it has
    # `shadow_min_weeks` scored weeks; see `calibration.ShadowPolicy`.
    shadow_sources: list[str] = list(SHADOW_SOURCES_DEFAULT)
    shadow_min_weeks: int = Field(SHADOW_MIN_WEEKS_DEFAULT, ge=1)


class OddsApiConfig(BaseModel):
    """`[odds_api]`: Sam's key for the-odds-api.com (player props). The key lives only in config.toml
    (gitignored) or the environment and is never printed or committed. The pull itself is built in the sources
    round; this section is here so a config that already holds the key still loads."""

    model_config = ConfigDict(extra="forbid")

    key: str | None = None
    # The day of the month the monthly quota renews (your account page on the-odds-api.com). The budget guard
    # spreads the credits left over the NFL weekends before it (`odds_props.weekends_left`).
    reset_day: int = Field(1, ge=1, le=28)


# "The" double-up whose cash line Sam types into Results each week (his typed values match its standings).
CASH_LINE_SERIES_DEFAULT = "NFL GIANT $25 Double Up [Single Entry]"


class ContestsConfig(BaseModel):
    """`[contests]`: where `dfs week close` looks for the two DraftKings downloads, and which double-up's
    standings fill Results' blank `Cash Line`."""

    model_config = ConfigDict(extra="forbid")

    downloads_dir: str = "~/Downloads"
    cash_line_series: str = CASH_LINE_SERIES_DEFAULT
    tab: str = "Contests"


class StakesSplitConfig(BaseModel):
    """`[stakes] split`: your own cash/GPP split of the weekly spend, in percent (overrides the default)."""

    model_config = ConfigDict(extra="forbid")

    cash: float = Field(ge=0, le=100)
    gpp: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _adds_to_100(self) -> StakesSplitConfig:
        if abs(self.cash + self.gpp - 100) > 0.01:
            raise ValueError(f"split must add up to 100 (cash {self.cash:g} + gpp {self.gpp:g})")
        return self


class StakesConfig(BaseModel):
    """`[stakes]`: the staking rules you set for the Bankroll tab's STAKES block (`stakes.py`). Every rule is
    optional and OFF until set; the block then says so and shows the arithmetic without it. The weekly budget
    is not here: it already lives on the Bankroll tab (its Weekly Budget cell, carried week to week), and the
    block reads it from there. Percentages are percent (5 means 5%), of the week's Starting Bankroll."""

    model_config = ConfigDict(extra="forbid")

    max_pct_bankroll_per_week: float | None = Field(default=None, gt=0, le=100)
    max_entry_fee_pct: float | None = Field(default=None, gt=0, le=100)
    split: StakesSplitConfig | None = None
    # Share of the weekly budget set aside for betting, as its own budget line (percent; Sam, 2026-10-20: 20).
    # Only a line in the plan: nothing here tracks a bet or talks to a sportsbook. The rest is split between
    # Double Up and single-entry GPP as before.
    betting_pct: float = Field(default=20.0, ge=0, le=100)


class SuggestConfig(BaseModel):
    """`[suggest]`: how `dfs lineups suggest --iterate` treats what Sam decided."""

    model_config = ConfigDict(extra="forbid")

    # When Sam removed a build's bring-back, a rebuild of it keeps none unless the best build WITH one beats
    # the best without by more than this share (relative: 0.25 = 25% better on the probability that build's
    # kind is ranked by, Cash % or Top 1%).
    bring_back_gain: float = Field(0.25, ge=0.0)


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    google_sheets: GoogleSheetsConfig
    nfl_odds: NflOddsConfig = NflOddsConfig()
    lineups: LineupsConfig = LineupsConfig()
    bankroll: BankrollConfig = BankrollConfig()
    results: ResultsConfig = ResultsConfig()
    season: SeasonConfig = SeasonConfig()
    sim: SimConfig = SimConfig()
    projection: ProjectionConfig = ProjectionConfig()
    contests: ContestsConfig = ContestsConfig()
    odds_api: OddsApiConfig = OddsApiConfig()
    stakes: StakesConfig = StakesConfig()
    suggest: SuggestConfig = SuggestConfig()


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
