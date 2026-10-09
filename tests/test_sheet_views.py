import re

import pandas as pd

from dfs.derived import EDGE_COLUMNS, EDGE_DATA_OFFSET, VAL_ADJ_ROSTERABLE_TOP_N
from dfs.gps_check import GPS_IMPLIED_MISMATCH_PTS
from dfs.sheet_lineup_keys import LINEUP_KEY_HEADER
from dfs.sheet_views import (
    BANNER_START_COL,
    BOARD_BANNER_ROW,
    BOARD_BUSTCUT_COL_INDEX,
    BOARD_CHALK_COLHEADER,
    BOARD_CHALK_EMPTY,
    BOARD_CHALK_FIRST_ROW,
    BOARD_CHALK_HEADER_ROW,
    BOARD_CHALK_LAST_ROW,
    BOARD_CHALK_POSITION_ROWS,
    BOARD_CHECK_FIRST_ROW,
    BOARD_CHECK_HEADER_ROW,
    BOARD_FRESHNESS_ROW,
    BOARD_ID_COL,
    BOARD_ID_COL_INDEX,
    BOARD_LIST_COLHEADER,
    BOARD_LIST_NAME_COL,
    BOARD_LIST_REASON_INDEX,
    BOARD_POOL_COL,
    BOARD_POOL_COLHEADER,
    BOARD_POOL_FIRST_ROW,
    BOARD_POOL_GAP_ROW,
    BOARD_POOL_HEADER_ROW,
    BOARD_PORTFOLIO_PLACEHOLDER,
    BOARD_PORTFOLIO_ROW,
    BOARD_QUEUE_COLHEADER,
    BOARD_QUEUE_COLHEADER_LEGACY,
    BOARD_QUEUE_COLHEADER_ROW,
    BOARD_QUEUE_EMPTY,
    BOARD_QUEUE_FIRST_ROW,
    BOARD_QUEUE_HEADER_ROW,
    BOARD_QUEUE_LAST_ROW,
    BOARD_QUEUE_ROWS,
    BOARD_SLATE_AWAY_COL,
    BOARD_SLATE_AWAY_COL_INDEX,
    BOARD_SLATE_COL,
    BOARD_SLATE_COLHEADER,
    BOARD_SLATE_COLHEADER_ROW,
    BOARD_SLATE_FIRST_ROW,
    BOARD_SLATE_GAMEID_COL,
    BOARD_SLATE_GAMEID_COL_INDEX,
    BOARD_SLATE_GPSCHK_COL_INDEX,
    BOARD_SLATE_HEADER_ROW,
    BOARD_SLATE_HOME_COL,
    BOARD_SLATE_HOME_COL_INDEX,
    BOARD_STACK_COLHEADER,
    BOARD_STACK_FIRST_ROW,
    BOARD_STACK_HEADER_ROW,
    BOARD_STACKS_COL,
    BOARD_STACKS_COLHEADER,
    BOARD_STACKS_FIRST_ROW,
    BOARD_STACKS_HEADER_ROW,
    DEFAULT_LINEUP_COUNT,
    EXPOSURE_TAB,
    LINEUP_COUNT_CELL,
    MOVEMENT_EMPTY,
    MOVEMENT_FIRST_ROW,
    MOVEMENT_HEADER,
    MOVEMENT_HEADER_ROW,
    MOVEMENT_ROWS,
    MOVEMENT_TAB,
    MOVEMENT_TOP_PLAYERS,
    ROSTER_MIN,
    SLATE_GAME_FIRST_ROW,
    SLATE_GPS_CHECK_COL_INDEX,
    SLATE_GPS_CHECK_HEADER,
    SLATE_HEADER,
    SLATE_ON_SLATE_COL_INDEX,
    SLATE_ON_SLATE_HEADER,
    SLATE_TEAM_ROWS,
    SLATE_TEAMS_COLHEADER,
    SLATE_TEAMS_COLHEADER_ROW,
    SLATE_TEAMS_FIRST_ROW,
    SLATE_TEAMS_HEADER_ROW,
    SLATE_TEAMS_LAST_ROW,
    SLATE_TEAMS_LOOKUPS,
    _col,
    _edge_team_pair_mean,
    _rng,
    board_portfolio_text,
    build_board,
    build_exposure,
    build_movement,
    build_slate_grid,
    queue_body,
    used_queue_rows,
    write_board_portfolio,
    write_queue_section,
)
from dfs.sheets import column_letter


class _CapturingClient:
    """Just enough of SheetsClient for a build_* function to run against:
    write_tab captures whatever it's given, everything else this module's
    builders call is a no-op."""

    def __init__(self):
        self.rows: list[list] = []

    def write_tab(self, tab_name: str, rows: list[list], **_kwargs) -> int:
        self.rows = rows
        return len(rows)

    def tab_exists(self, tab_name: str) -> bool:
        return False

    def read_range(self, tab_name: str, a1_range: str):
        return []


class FakeSheetsClient:
    """Records write_tab/update_range calls and fakes read_range for the
    two distinct ranges build_exposure reads: the pre-rebuild "A2:F..."
    (existing typed Targets) and the post-rebuild "A2:A..." (the roster
    names the just-written array formula resolved to). A real sheet
    resolves the second read against live formula output; this fake just
    returns whatever `post_write_names` says, since build_exposure's own
    logic -- not Sheets' recalculation -- is what's under test here.
    """

    def __init__(
        self,
        *,
        existing_rows: list[list[str]],
        post_write_names: list[str],
        existing_lineup_count: str = "",
        lineups_header: list[str] | None = None,
    ):
        self.existing_rows = existing_rows
        self.post_write_names = post_write_names
        self.existing_lineup_count = existing_lineup_count
        # Part 7.5: Lineups' own header row, for the portfolio headline's
        # Pos./GameID column lookup -- empty by default (most existing
        # tests predate Part 7.4/7.5 and don't care about it).
        self.lineups_header = lineups_header or []
        self.write_tab_calls: list[tuple[str, list[list]]] = []
        self.update_calls: list[tuple[str, str, list[list]]] = []
        self.note_calls: list[tuple[str, str]] = []
        self.number_range_validation_calls: list[tuple[str, int, int]] = []

    def tab_exists(self, tab_name: str) -> bool:
        return True

    def read_range(self, tab_name: str, a1_range: str):
        if a1_range == LINEUP_COUNT_CELL:
            return [[self.existing_lineup_count]] if self.existing_lineup_count else []
        if a1_range.startswith("A2:F"):
            return self.existing_rows
        if a1_range.startswith("A2:A"):
            return [[n] for n in self.post_write_names]
        if a1_range.split(":")[0][1:] == a1_range.split(":")[1] and a1_range[0] == "A":
            # A full header-row read, e.g. "A1:1" -- Lineups' own header.
            return [self.lineups_header] if self.lineups_header else []
        raise AssertionError(f"unexpected read_range call: {a1_range!r}")

    def write_tab(self, tab_name: str, rows: list[list], **_kwargs) -> int:
        self.write_tab_calls.append((tab_name, rows))
        return len(rows)

    def update_range(self, tab_name: str, a1_range: str, rows: list[list]) -> None:
        self.update_calls.append((tab_name, a1_range, rows))

    def set_note(self, tab_name: str, cell_a1: str, note: str) -> None:
        self.note_calls.append((cell_a1, note))

    def set_number_range_validation(
        self, tab_name: str, a1_range: str, *, minimum: int, maximum: int, strict: bool = False
    ) -> None:
        self.number_range_validation_calls.append((a1_range, minimum, maximum))


def test_col_and_rng_use_edge_columns_positions():
    assert _col("Name") == "B"  # index 0 + EDGE_DATA_OFFSET (Pool occupies column A)
    assert _col("NotAColumn") is None


def test_rng_raises_for_a_column_not_in_edge_columns():
    try:
        _rng("EdgeRaw", "NotAColumn")
    except KeyError as e:
        assert "NotAColumn" in str(e)
    else:
        raise AssertionError("expected KeyError for a column absent from EDGE_COLUMNS")


def test_build_exposure_preserves_typed_targets_keyed_by_name_across_rebuild():
    # Targets were typed against last run's row order (McCaffrey then
    # Jefferson); this run's rebuilt roster comes back in a different
    # order (an exposure/leverage-sort change, or new players entering
    # Lineups) plus one brand-new name with no prior target. Preservation
    # must follow the NAME, not the row position, and a name that no
    # longer has a target must come back blank, not stale or shifted.
    existing_rows = [
        ["Justin Jefferson", "WR", "8200", "5", "0.42", "0.35"],
        ["Christian McCaffrey", "RB", "9500", "3", "0.25", "0.20"],
    ]
    post_write_names = ["Christian McCaffrey", "Justin Jefferson", "Brand New Guy"]
    client = FakeSheetsClient(existing_rows=existing_rows, post_write_names=post_write_names)

    result = build_exposure(client, edge_tab="EdgeRaw", lineups_tab="Lineups", lineup_count=20)

    assert "2 target(s) preserved" in result
    assert len(client.write_tab_calls) == 1
    tab_name, rows = client.write_tab_calls[0]
    assert tab_name == EXPOSURE_TAB
    # The roster formula in row 2 must reference EdgeRaw's Name/Position/
    # Salary columns by their real EDGE_COLUMNS letters, not guessed ones.
    roster_formula = rows[1][0]
    for name in ("Name", "Position", "Salary"):
        letter = _col(name)
        assert f"${letter}$2:${letter}" in roster_formula

    assert len(client.update_calls) == 1
    updated_tab, a1_range, restored = client.update_calls[0]
    assert updated_tab == EXPOSURE_TAB
    assert a1_range == "F2:F180"
    assert restored[0] == ["0.20"]  # McCaffrey, now first
    assert restored[1] == ["0.35"]  # Jefferson, now second
    assert restored[2] == [""]  # Brand New Guy never had a target


def test_build_exposure_divides_by_lineup_count_cell_not_capacity():
    # Phase 5A: the old bug divided by lineup_count (capacity, 20) even
    # though Sam builds far fewer lineups than that -- the live divisor
    # must be Exposure's own H1 (LINEUP_COUNT_CELL), with lineup_count
    # only as the fallback for a blank/zero H1.
    client = FakeSheetsClient(existing_rows=[], post_write_names=["Someone"])

    build_exposure(client, edge_tab="EdgeRaw", lineups_tab="Lineups", lineup_count=20)

    tab_name, rows = client.write_tab_calls[0]
    row2_exposure = rows[1][4]
    assert "$H$1" in row2_exposure
    assert "20" in row2_exposure  # capacity fallback still present
    row3_exposure = rows[2][4]
    assert "$H$1" in row3_exposure


def test_build_exposure_defaults_lineup_count_cell_when_blank():
    client = FakeSheetsClient(existing_rows=[], post_write_names=["Someone"])

    build_exposure(client, edge_tab="EdgeRaw", lineups_tab="Lineups", lineup_count=20)

    tab_name, rows = client.write_tab_calls[0]
    assert rows[0][7] == str(DEFAULT_LINEUP_COUNT)


def test_build_exposure_preserves_typed_lineup_count_across_rebuild():
    client = FakeSheetsClient(existing_rows=[], post_write_names=["Someone"], existing_lineup_count="4")

    build_exposure(client, edge_tab="EdgeRaw", lineups_tab="Lineups", lineup_count=20)

    tab_name, rows = client.write_tab_calls[0]
    assert rows[0][7] == "4"


def test_build_exposure_sets_note_and_validation_on_lineup_count_cell():
    client = FakeSheetsClient(existing_rows=[], post_write_names=["Someone"])

    build_exposure(client, edge_tab="EdgeRaw", lineups_tab="Lineups", lineup_count=20)

    assert client.note_calls and client.note_calls[0][0] == LINEUP_COUNT_CELL
    assert client.number_range_validation_calls == [(LINEUP_COUNT_CELL, 1, 20)]


def test_build_exposure_skips_restore_when_nothing_was_typed_before():
    client = FakeSheetsClient(existing_rows=[], post_write_names=["Someone"])

    result = build_exposure(client, edge_tab="EdgeRaw", lineups_tab="Lineups", lineup_count=20)

    assert "target(s) preserved" not in result
    assert client.update_calls == []  # nothing to restore, so no F-column write at all


def test_build_exposure_counts_the_whole_lineups_column():
    # No pool deck sits above Lineups any more (removed entirely) -- the
    # whole column is always the right range, no start-row parameter needed.
    client = FakeSheetsClient(existing_rows=[], post_write_names=[])

    build_exposure(client, edge_tab="EdgeRaw", lineups_tab="Lineups", lineup_count=20)

    tab_name, rows = client.write_tab_calls[0]
    assert "Lineups!$A$1:$A" in rows[0][9]


def test_build_exposure_counts_resolved_names_when_lineups_has_a_player_key_column():
    """Round 5 follow-up item 3: two spellings of one player must count as one, so every
    'same player?' count reads the hidden Player Key column, not the typed column A --
    while 'is this slot filled?' still reads the genuinely-blank typed column."""
    client = FakeSheetsClient(
        existing_rows=[],
        post_write_names=[],
        lineups_header=["Name", "Pos.", "GameID", LINEUP_KEY_HEADER],
    )

    build_exposure(client, edge_tab="EdgeRaw", lineups_tab="Lineups", lineup_count=20)

    header, roster_row = client.write_tab_calls[0][1][0], client.write_tab_calls[0][1][1]
    key = "Lineups!$D$1:$D"
    assert f"COUNTIF({key},EdgeRaw!" in roster_row[0]  # who is in a lineup: canonical names
    assert roster_row[3] == f'=IF($A2="","",COUNTIF({key},$A2))'  # the # Lineups count
    assert "Lineups!$A$1:$A" not in roster_row[0]
    typed_slots = '=COUNTIF(Lineups!$A$1:$A,"?*")-COUNTIF(Lineups!$A$1:$A,"Name")'
    assert header[9] == typed_slots  # "is this slot filled?" stays on the genuinely-blank typed column
    assert f'FILTER({key},Lineups!$B$1:$B="QB",{key}<>"")' in header[11]
    assert 'COUNTIFS(Lineups!$B$1:$B,"QB",Lineups!$A$1:$A,"<>")' in header[13]


def test_build_exposure_adds_portfolio_headline_when_lineups_pos_and_gameid_linked():
    client = FakeSheetsClient(
        existing_rows=[], post_write_names=[], lineups_header=["Name", "Pos.", "GameID"]
    )

    result = build_exposure(client, edge_tab="EdgeRaw", lineups_tab="Lineups", lineup_count=20)

    header = client.write_tab_calls[0][1][0]
    assert header[10:16] == [
        "Distinct QBs",
        '=IFERROR(ROWS(UNIQUE(FILTER(Lineups!$A$1:$A,Lineups!$B$1:$B="QB",Lineups!$A$1:$A<>""))),0)',
        "Shared QB?",
        '=IF(COUNTIFS(Lineups!$B$1:$B,"QB",Lineups!$A$1:$A,"<>")'
        '>IFERROR(ROWS(UNIQUE(FILTER(Lineups!$A$1:$A,Lineups!$B$1:$B="QB",Lineups!$A$1:$A<>""))),0),"Yes","No")',
        "Distinct games",
        '=IFERROR(ROWS(UNIQUE(FILTER(Lineups!$C$1:$C,Lineups!$C$1:$C<>"",Lineups!$C$1:$C<>"GameID"))),0)',
    ]
    assert "portfolio headline (Distinct QBs" in result


def test_build_exposure_shared_qb_is_no_when_zero_real_qbs_are_rostered():
    """Found live (2026-09-19): `Pos.` is Lineups' fixed slot label, one
    "QB" row per block regardless of whether a name is typed there, so the
    old `COUNTIF(lu_pos,"QB")` was always the block count (e.g. 20) --
    `shared_qb` read "Yes" even with zero real QBs anywhere. Fixed by also
    requiring the Name cell non-blank (`lu,"<>"`), matching a filled slot
    rather than a labeled one."""
    client = FakeSheetsClient(
        existing_rows=[], post_write_names=[], lineups_header=["Name", "Pos.", "GameID"]
    )

    build_exposure(client, edge_tab="EdgeRaw", lineups_tab="Lineups", lineup_count=20)

    header = client.write_tab_calls[0][1][0]
    shared_qb_formula = header[13]
    assert 'COUNTIFS(Lineups!$B$1:$B,"QB",Lineups!$A$1:$A,"<>")' in shared_qb_formula


def test_build_exposure_distinct_games_excludes_the_gameid_header_repeat_text():
    """Found live (2026-09-19): every lineup block repeats its own header
    row, so Lineups' GameID column literally contains the text "GameID"
    once per block -- a real, non-blank string that the old `<>""` filter
    let through, always counting one phantom "distinct game" even with
    zero real lineups built. Same header-repeat-exclusion idiom "Slots
    filled" already uses (`COUNTIF(lu,"?*")-COUNTIF(lu,"Name")`), applied
    here as an extra FILTER criterion instead."""
    client = FakeSheetsClient(
        existing_rows=[], post_write_names=[], lineups_header=["Name", "Pos.", "GameID"]
    )

    build_exposure(client, edge_tab="EdgeRaw", lineups_tab="Lineups", lineup_count=20)

    header = client.write_tab_calls[0][1][0]
    distinct_games_formula = header[15]
    assert 'Lineups!$C$1:$C<>"GameID"' in distinct_games_formula


def test_build_exposure_distinct_games_degrades_to_zero_not_an_na_error():
    """Found live (2026-09-19), right after fixing the header-repeat
    false-match above: with that excluded, zero real games in progress
    makes FILTER's own result set genuinely empty, and FILTER errors
    (`#N/A`) on an empty result rather than returning nothing. A first
    fix attempt, `IFERROR(COUNTA(...),0)`, did NOT work: `COUNTA` absorbs
    the error into a valid count of 1 (an error value still "counts" as
    present) *before* IFERROR ever sees an error to catch -- confirmed
    empirically live, and that the already-shipped per-lineup
    `sheet_lineup_metrics.distinct_games_formula` had the identical bug.
    `ROWS` does not absorb the error -- it propagates it, so
    `IFERROR(ROWS(...),0)` genuinely degrades to 0."""
    client = FakeSheetsClient(
        existing_rows=[], post_write_names=[], lineups_header=["Name", "Pos.", "GameID"]
    )

    build_exposure(client, edge_tab="EdgeRaw", lineups_tab="Lineups", lineup_count=20)

    header = client.write_tab_calls[0][1][0]
    distinct_games_formula = header[15]
    assert distinct_games_formula.startswith("=IFERROR(ROWS(")
    assert distinct_games_formula.endswith(",0)")
    assert "COUNTA(" not in distinct_games_formula


def test_build_exposure_skips_portfolio_headline_when_gameid_not_linked_yet():
    client = FakeSheetsClient(existing_rows=[], post_write_names=[], lineups_header=["Name", "Pos."])

    result = build_exposure(client, edge_tab="EdgeRaw", lineups_tab="Lineups", lineup_count=20)

    header = client.write_tab_calls[0][1][0]
    assert header[10:16] == ["Distinct QBs", "", "Shared QB?", "", "Distinct games", ""]
    assert "portfolio headline skipped" in result


def test_build_exposure_reads_lineups_own_header_row_not_row_1():
    client = FakeSheetsClient(
        existing_rows=[], post_write_names=[], lineups_header=["Name", "Pos.", "GameID"]
    )

    build_exposure(client, edge_tab="EdgeRaw", lineups_tab="Lineups", lineup_count=20, lineups_header_row=8)

    header = client.write_tab_calls[0][1][0]
    assert "QB" in header[11]  # formula still built -- read succeeded against row 8, not row 1


def test_build_movement_is_one_row_per_team_and_derives_columns_from_edge_columns():
    assert "ImpliedMove" in EDGE_COLUMNS and "GameStart" in EDGE_COLUMNS

    class NoopClient:
        def write_tab(self, tab_name, rows, **_kwargs):
            self.written = (tab_name, rows)
            return len(rows)

    client = NoopClient()
    result = build_movement(client, edge_tab="EdgeRaw")
    assert result.startswith(f"{MOVEMENT_TAB}: built")
    tab_name, rows = client.written
    assert tab_name == MOVEMENT_TAB
    body = rows[MOVEMENT_FIRST_ROW - 1][0]
    for name in ("Team", "Opp", "ImpliedMove", "TotMove", "SpdMove", "GameStart", "OverUnder", "Spread"):
        assert f"${_col(name)}$2:${_col(name)}" in body
    assert "UNIQUE(" in body  # a team once, not once per player
    assert "ABS(INDEX(UNIQUE(" in body and ",0,4)))" in body  # sorted by |Implied move|, the fourth column
    assert MOVEMENT_EMPTY in body  # the empty state is kept
    assert len(rows) == MOVEMENT_FIRST_ROW - 1 + MOVEMENT_ROWS


def test_movement_times_are_eastern_and_never_say_utc():
    class NoopClient:
        def write_tab(self, tab_name, rows, **_kwargs):
            self.written = rows
            return len(rows)

    client = NoopClient()
    build_movement(client, edge_tab="EdgeRaw")
    flat = " ".join(str(c) for row in client.written for c in row)
    assert "UTC" not in flat and "Kickoff (ET)" in flat and '&" ET"' in flat
    # TFFB's "Z" is Eastern wall-clock time: the text is formatted as written, never converted
    assert "TIMEVALUE(MID(" in flat and "TIMEZONE" not in flat.upper()


def test_movement_what_it_means_names_the_team_and_which_way_its_players_moved():
    class NoopClient:
        def write_tab(self, tab_name, rows, **_kwargs):
            self.written = rows
            return len(rows)

    client = NoopClient()
    build_movement(client, edge_tab="EdgeRaw")
    first = client.written[MOVEMENT_FIRST_ROW - 1]
    means = first[MOVEMENT_HEADER.index("What it means")]
    assert '" implied "' in means and "project lower than when the week opened" in means
    assert "project higher than when the week opened" in means
    top = first[MOVEMENT_HEADER.index("Top players")]
    assert "TEXTJOIN" in top and f",{MOVEMENT_TOP_PLAYERS},1)" in top


# ---------------------------------------------------------------------------
# sheet_style.py styles these four tabs by LITERAL column letter, on the
# stated assumption that it, not the sheet, defines their layout (see the
# comment above sheet_style.style_board). That assumption is exactly the
# "position moved, formula/format didn't" hazard CONTRIBUTING.md warns
# about elsewhere (EDGE_COLUMNS/link-edge) -- so it gets the same
# treatment: a test pinning today's agreed layout, not a shared-constants
# refactor. If one of these ever fails, the fix is to update the matching
# style_* function in sheet_style.py, not this test.
# ---------------------------------------------------------------------------


def _ix(header, name):
    """The column index of a header name in one of the Board's tables (column A is the Pool gutter)."""
    return header.index(name)


SL = _ix(BOARD_SLATE_COLHEADER, "Matchup")  # Slate shape spill start
PL = _ix(BOARD_CHALK_COLHEADER, "Player")  # list / chalk spill start
ST = _ix(BOARD_STACK_COLHEADER, "Team")  # Stack candidates spill start
YS = _ix(BOARD_STACKS_COLHEADER, "QB")  # Your stacks spill start


def _build_board(client=None, **overrides):
    client = client or _CapturingClient()
    kwargs = {
        "edge_tab": "EdgeRaw",
        "games_tab": "GamesRaw",
        "weather_tab": "WeatherRaw",
        "gps_tab": "GPSRaw",
        "player_pool_tab": "Player Pool",
        **overrides,
    }
    build_board(client, **kwargs)
    return client


def test_board_writes_every_section_header_at_its_own_row():
    client = _build_board()
    # Row numbers come from sheet_views' own BOARD_* constants (1-indexed; client.rows is 0-indexed) so a
    # change to those constants that silently detaches a header from its section fails a test.
    headers = [
        (BOARD_SLATE_HEADER_ROW, "SLATE SHAPE"),
        (BOARD_QUEUE_HEADER_ROW, "QUEUE"),
        (BOARD_CHECK_HEADER_ROW, "POOL CHECK"),
        (BOARD_POOL_HEADER_ROW, "POOL SUMMARY"),
        (BOARD_STACKS_HEADER_ROW, "YOUR STACKS"),
        (BOARD_CHALK_HEADER_ROW, "CHALK MAP"),
        (BOARD_STACK_HEADER_ROW, "STACK CANDIDATES"),
    ]
    for row, title in headers:
        assert client.rows[row - 1][0].startswith(title)
    # Sam's order: Slate shape, then the pool's state (Queue, Pool check, Pool summary), then the stacks.
    rows = [row for row, _ in headers]
    assert rows == sorted(rows)


def test_the_sections_the_edge_finder_took_over_are_gone_from_the_board():
    client = _build_board()
    flat = " ".join(str(c) for row in client.rows for c in row)
    for gone in ("PER-POSITION LEADERS", "PUNT FINDER", "THIS WEEK'S EDGES", "POOL DIAGNOSTICS"):
        assert gone not in flat
    assert "docs/planning" not in flat  # no link to a developer file


def test_chalk_map_is_one_spill_per_position_sorted_by_ownership_with_ids_beside_it():
    client = _build_board()
    assert client.rows[BOARD_CHALK_HEADER_ROW][: len(BOARD_CHALK_COLHEADER)] == BOARD_CHALK_COLHEADER
    first = BOARD_CHALK_FIRST_ROW
    row = first
    for position, count in BOARD_CHALK_POSITION_ROWS.items():
        main = client.rows[row - 1][PL]
        assert f'="{position}"' in main
        assert "SORT(FILTER(" in main and ",5,FALSE)" in main  # Own% is the fifth column
        assert f",{count},7)" in main  # the block's own size, seven columns wide
        assert "REGEXMATCH(" in main and "OUT|IR" in main  # nobody listed out
        ids = client.rows[row - 1][BOARD_ID_COL_INDEX]
        assert "INDEX(SORT(FILTER(" in ids and f",{count},1)" in ids
        row += count
    assert row - 1 == BOARD_CHALK_LAST_ROW
    # a Pool cell per row keyed on the hidden Id, so Set and the Pool tick read the same player
    pool = _ix(BOARD_CHALK_COLHEADER, "Pool")
    assert pool == 0
    assert all(client.rows[r - 1][pool].startswith("=") for r in range(first, BOARD_CHALK_LAST_ROW + 1))


def test_chalk_map_says_so_until_ownership_publishes_and_only_once():
    client = _build_board()
    qb_main = client.rows[BOARD_CHALK_FIRST_ROW - 1][PL]
    assert BOARD_CHALK_EMPTY in qb_main and "COUNTIF(EdgeRaw!" in qb_main and ',"real")=0' in qb_main
    other = client.rows[BOARD_CHALK_FIRST_ROW - 1 + BOARD_CHALK_POSITION_ROWS["QB"]][PL]
    assert BOARD_CHALK_EMPTY not in other  # one message, not five


def test_slate_shape_sorts_by_total_not_gamesraws_own_row_order():
    # Found live (2026-09-22, live sheet): the first cut of this section
    # was a straight per-row passthrough of GamesRaw's own (unsorted) row
    # order -- "games ranked by total" was never actually true. Must be a
    # SORT, not a plain per-row VLOOKUP loop.
    client = _build_board()
    slate_row = client.rows[BOARD_SLATE_FIRST_ROW - 1]

    assert slate_row[SL].startswith("=")
    assert "SORT(" in slate_row[SL]
    assert "GamesRaw!$M$2:$M$40" in slate_row[SL]  # Total is the sort key


def test_slate_shape_fav_and_spread_columns_derive_from_games_columns():
    # PROMPT_BOARD_FIXES.md item 1: Fav/Spread inserted right after Total
    # (columns C/D), sourced from the same GamesRaw range Total already
    # is, with every letter derived from GAMES_COLUMNS rather than
    # hardcoded -- Spread shows the ABSOLUTE line; Fav's sign check picks
    # the favoured team code (or "PK").
    client = _build_board()
    slate_formula = client.rows[BOARD_SLATE_FIRST_ROW - 1][SL]

    assert "GamesRaw!$L$2:$L$40" in slate_formula  # Spread is GAMES_COLUMNS' own column L
    assert 'IF(GamesRaw!$L$2:$L$40=0,"PK"' in slate_formula
    assert "ABS(GamesRaw!$L$2:$L$40)" in slate_formula


def test_slate_shape_wind_lookup_joins_on_a_parallel_gameid_column_not_matchup_text():
    client = _build_board()
    slate_row = client.rows[BOARD_SLATE_FIRST_ROW - 1]
    wind_formula = slate_row[BOARD_SLATE_COLHEADER.index("Wind")]

    # The GameId column (a second, independent SORT on the same key) is
    # what Wind's VLOOKUP joins against -- not the human-readable Matchup
    # text in column A, which WeatherRaw has no way to match against.
    assert f"${BOARD_SLATE_GAMEID_COL}" in wind_formula
    assert "SORT(" in slate_row[BOARD_SLATE_GAMEID_COL_INDEX]


def test_slate_shape_pace_averages_away_and_home_team_lookups():
    # Part C, C7: Pace is a per-TEAM EdgeRaw column, so both the away and
    # home team codes (two more parallel hidden SORT columns) are looked
    # up and averaged -- unlike Wind, which is one game-level value keyed
    # by GameId alone.
    client = _build_board()
    slate_row = client.rows[BOARD_SLATE_FIRST_ROW - 1]
    pace_formula = slate_row[BOARD_SLATE_COLHEADER.index("Pace")]

    assert pace_formula.startswith(f"=IF(${BOARD_SLATE_COL['Matchup']}")
    assert "AVERAGE(" in pace_formula
    assert f"${BOARD_SLATE_AWAY_COL}" in pace_formula
    assert f"${BOARD_SLATE_HOME_COL}" in pace_formula
    assert "EdgeRaw!" in pace_formula
    assert "SORT(" in slate_row[BOARD_SLATE_AWAY_COL_INDEX]
    assert "SORT(" in slate_row[BOARD_SLATE_HOME_COL_INDEX]


def test_stack_candidates_reference_current_edge_columns():
    # Same discipline the pre-rebuild leverage/landmines panels had:
    # found live that a written formula string doesn't follow a later
    # EdgeRaw column reorder, so this only pins that build_board
    # generates against EDGE_COLUMNS at call time.
    client = _build_board()
    stack_row = client.rows[BOARD_STACK_FIRST_ROW - 1]
    team_list = stack_row[ST]
    # Total spills beside the team, so the QB name formula starts two columns after it.
    qb_formula = stack_row[_ix(BOARD_STACK_COLHEADER, "QB")]

    assert _rng("EdgeRaw", "OverUnder") in team_list
    assert _rng("EdgeRaw", "Team") in qb_formula
    assert _rng("EdgeRaw", "TmRank") in stack_row[_ix(BOARD_STACK_COLHEADER, "WR1")]


def test_pool_summary_counts_cash_and_gpp_from_player_pool_with_both_counting_for_each():
    client = _build_board()
    qb = client.rows[BOARD_POOL_FIRST_ROW - 1]
    pos, pooled, cash, gpp = (qb[_ix(BOARD_POOL_COLHEADER, n)] for n in ("Pos", "Pooled", "Cash", "GPP"))
    assert pos == "QB" and qb[0] == ""  # the gutter stays blank on a row that is not a player
    assert "Player Pool" in pooled and "EdgeRaw" not in pooled
    assert "COUNTIF" in cash and '"Cash"' in cash and '"Both"' in cash
    assert '"GPP"' in gpp and '"Both"' in gpp
    assert BOARD_POOL_COL["Cash"] == "D" and BOARD_POOL_COL["GPP"] == "E"


def test_pool_summary_has_no_per_position_targets_only_what_is_missing_to_fill_a_lineup():
    client = _build_board()
    gap = client.rows[BOARD_POOL_GAP_ROW - 1][0]
    assert gap.startswith('="Cash: "&') and 'GPP: "&' in gap
    for position, need in ROSTER_MIN.items():
        assert f"<{need}" in gap and f'" more {position}"' in gap
    assert "target" not in gap.lower()
    assert "FLEX" in gap  # the seventh RB/WR/TE


def test_pool_check_triggers_are_the_four_the_prompt_names_and_it_ends_in_the_set_cell():
    client = _build_board()
    spill = client.rows[BOARD_CHECK_FIRST_ROW - 1][PL]
    ids = client.rows[BOARD_CHECK_FIRST_ROW - 1][BOARD_ID_COL_INDEX]
    assert "OUT|D|Q|IR" in spill  # listed out / doubtful / questionable
    assert "Bust odds" in spill and "below TFFB" in spill and "FADE↓" in spill
    assert "-2" in spill  # CalPts at least 2.0 below ProjPts
    assert "Nothing in your pool looks worse than when you added it." in spill
    assert "Tick players into your pool to see this." in spill
    assert spill.startswith("=IF(SUMPRODUCT(")  # empty-safe: never a COUNTA of a FILTER
    assert ids.startswith("=IF(SUMPRODUCT(")
    for row in range(BOARD_CHECK_FIRST_ROW, BOARD_CHECK_FIRST_ROW + 3):
        pool = client.rows[row - 1][BOARD_LIST_COLHEADER.index("Pool")]
        assert f"${BOARD_ID_COL}{row}" in pool  # found by the hidden Id, never by name
        assert pool.startswith("=IF(") and f"MATCH(${BOARD_LIST_NAME_COL}" not in pool


def test_pool_check_compares_bust_to_the_positions_own_rosterable_quartile_cut():
    client = _build_board()
    for i, position in enumerate(("QB", "RB", "WR", "TE", "DST")):
        cut = client.rows[BOARD_POOL_FIRST_ROW - 1 + i][BOARD_BUSTCUT_COL_INDEX]
        assert "PERCENTILE(" in cut and f'="{position}"' in cut and "0.75" in cut
        assert f",{VAL_ADJ_ROSTERABLE_TOP_N[position]})" in cut  # top-N by ProjPts is the rosterable pool


def test_your_stacks_counts_each_pooled_qbs_pass_catchers_and_a_bring_back():
    client = _build_board()
    spill = client.rows[BOARD_STACKS_FIRST_ROW - 1][YS]
    assert "No QB in your pool yet." in spill and "ARRAY_CONSTRAIN" in spill
    catchers, bring_back = (
        client.rows[BOARD_STACKS_FIRST_ROW - 1][_ix(BOARD_STACKS_COLHEADER, "Pass catchers")],
        client.rows[BOARD_STACKS_FIRST_ROW - 1][_ix(BOARD_STACKS_COLHEADER, "Bring-back")],
    )
    team, opp = BOARD_STACKS_COL["Team"], BOARD_STACKS_COL["Opp"]
    assert "COUNTIF(" in catchers and f"${team}" in catchers  # his team's WR + TE in the pool
    assert f"${opp}" in bring_back and '"Yes ("' in bring_back and '"No"' in bring_back  # the opponent's


def test_the_banner_values_do_not_run_into_each_other():
    client = _build_board()
    row = client.rows[BOARD_BANNER_ROW - 1]
    b = BANNER_START_COL
    assert row[b] == "Games" and row[b + 2] == "Highest total"
    assert row[b + 6] == "Max wind" and row[b + 8] == "Injuries"
    assert row[0] == ""  # the gutter stays blank
    # Highest total's value (E) overflows across the empty F-G; Max wind's label starts at H.
    assert row[b + 4] == "" and row[b + 5] == ""


def test_the_portfolio_line_is_one_sentence_and_the_placeholder_until_lineups_exist():
    summary = {"expected_cashes": 2.14, "p_any_cash": 0.913, "p_any_gpp": 0.124}
    text = board_portfolio_text(summary, 6, 190.0)
    assert text == (
        "Portfolio: 6 lineups  ·  2.1 expected cashes  ·  "
        "P(at least one cash) 91%  ·  P(at least one 190+) 12%"
    )
    assert board_portfolio_text(summary, 1, 190.0).startswith("Portfolio: 1 lineup  ·")
    assert board_portfolio_text(summary, 6, 190.0, "cash line 141.3 (season median, 4 weeks)").endswith(
        "P(at least one 190+) 12%  ·  cash line 141.3 (season median, 4 weeks)"
    )
    assert board_portfolio_text(None, 0, 190.0) == BOARD_PORTFOLIO_PLACEHOLDER
    client = _build_board()
    assert client.rows[BOARD_PORTFOLIO_ROW - 1][0] == BOARD_PORTFOLIO_PLACEHOLDER

    class _C:
        calls = []

        def tab_exists(self, tab):
            return True

        def update_range(self, tab, rng, rows):
            self.calls.append((tab, rng, rows))

    c = _C()
    write_board_portfolio(c, summary, 6, 190.0)
    assert c.calls == [("Board", f"A{BOARD_PORTFOLIO_ROW}", [[text]])]


def test_games_banner_uses_sumproduct_not_counta_of_filter():
    # Same COUNTA/COUNTIF-of-an-erroring-FILTER trap as the pool notice
    # above -- found live via this exact formula (an empty GamesRaw read
    # "1" instead of the intended IFERROR(...,0) fallback).
    client = _build_board()
    games_formula = client.rows[BOARD_BANNER_ROW - 1][BANNER_START_COL + 1]

    assert "SUMPRODUCT" in games_formula
    assert "COUNTA(" not in games_formula


def test_freshness_banner_no_longer_describes_the_removed_ranked_leverage_panel():
    # Fix 6.1 (Week 3 fixes, 2026-09-23): the old text described a ranked
    # ceiling-percentile panel Part 7.6's Board rebuild removed entirely.
    # Replaced with Part 7.1's ownership caveat, present either way.
    client = _build_board()
    banner = client.rows[BOARD_FRESHNESS_ROW - 1][0]

    assert "ceiling percentile" not in banner
    assert "ranked" not in banner.lower()
    assert banner.count("large-field projection used in small-field contests") == 2
    assert banner.count("directional") == 2


def _queue_client(header, body_row):
    colheader_range = f"A{BOARD_QUEUE_COLHEADER_ROW}:{BOARD_ID_COL}{BOARD_QUEUE_COLHEADER_ROW}"

    class _ClientWithQueue(_CapturingClient):
        def tab_exists(self, tab_name):
            return True

        def read_range(self, tab_name, a1_range):
            if a1_range == colheader_range:
                return [header]
            if a1_range.startswith(f"A{BOARD_QUEUE_FIRST_ROW}:"):
                return [body_row]
            return []

    return _ClientWithQueue()


def test_build_board_preserves_existing_queue_rows_on_rebuild():
    # A routine dfs setup build-views re-run (e.g. after an EdgeRaw reorder) must not wipe whatever the last
    # `dfs sync --live` wrote into Queue; the rows come back with fresh live Pool formulas and their Ids.
    old_row = ["", "Player A", "RB", "KC", "Avail -> Q"] + [""] * (BOARD_ID_COL_INDEX - 5) + ["44391845"]
    client = _build_board(client=_queue_client(BOARD_QUEUE_COLHEADER, old_row))
    row = client.rows[BOARD_QUEUE_FIRST_ROW - 1]
    first = BOARD_LIST_COLHEADER.index("Player")
    assert row[first : first + 3] == ["Player A", "RB", "KC"] and row[4] == "Avail -> Q"
    assert row[BOARD_ID_COL_INDEX] == "44391845"
    assert row[BOARD_LIST_COLHEADER.index("Pool")].startswith(
        f'=IF(${BOARD_ID_COL}{BOARD_QUEUE_FIRST_ROW}="","",'
    )


def test_build_board_carries_a_queue_from_the_layout_before_the_pool_gutter_forward():
    # The Board before the gutter read Player..reason in A:D (Pool and Set at H:I) with the Id one column
    # further left. A rebuild re-emits those rows in the new order rather than blanking the Queue.
    legacy_id = BOARD_ID_COL_INDEX - 1
    old_row = ["Player A", "RB", "KC", "Avail -> Q"] + [""] * (legacy_id - 4) + ["44391845"]
    client = _build_board(client=_queue_client(BOARD_QUEUE_COLHEADER_LEGACY, old_row))
    row = client.rows[BOARD_QUEUE_FIRST_ROW - 1]
    first = BOARD_LIST_COLHEADER.index("Player")
    assert row[first : first + 3] == ["Player A", "RB", "KC"] and row[4] == "Avail -> Q"
    assert row[BOARD_ID_COL_INDEX] == "44391845" and row[0].startswith("=IF(")


def test_build_board_discards_stale_pre_rebuild_data_sitting_in_queues_rows():
    # A Board still on the pre-rebuild 3-panel design has TOP LEVERAGE's
    # own data occupying Queue's row range by coincidence -- reading that
    # forward as if it were real Queue data (found live, 2026-09-22)
    # produced garbage. The column-header check must reject it.
    stale_leverage_panel_data = [["Some Player", "RB KC", "12.3", "4.5"]]

    class _ClientWithStaleBoard(_CapturingClient):
        def tab_exists(self, tab_name):
            return True

        def read_range(self, tab_name, a1_range):
            return stale_leverage_panel_data  # wrong header AND wrong body

    client = _build_board(client=_ClientWithStaleBoard())
    # No stale data copied forward: the Queue reads as empty.
    row = client.rows[BOARD_QUEUE_FIRST_ROW - 1]
    assert row[BOARD_LIST_COLHEADER.index("Player")] == BOARD_QUEUE_EMPTY
    assert row[0] == ""  # the line is not a player, so the gutter stays blank


def test_old_top_leverage_and_landmines_panels_are_gone():
    # Part 3 replaced the old leverage/landmines panels; guard against a
    # careless partial revert leaving old text behind.
    client = _build_board()
    flat = [str(cell) for row in client.rows for cell in row]
    assert not any("LANDMINES" in cell for cell in flat)
    assert not any("TOP LEVERAGE" in cell for cell in flat)


def _build_slate(client):
    build_slate_grid(
        client,
        games_tab="GamesRaw",
        weather_tab="WeatherRaw",
        edge_tab="EdgeRaw",
        gps_tab="GPSRaw",
        team_metrics_tab="TeamMetricsRaw",
    )


def test_slate_grid_header_row_matches_style_slate_grids_column_assumptions():
    client = _CapturingClient()
    _build_slate(client)
    header = client.rows[0]
    assert header[2] == "Total"  # style_slate_grid colour-scales C
    assert header[3] == "Spread"  # style_slate_grid number-formats D
    assert header[5] == "Wind"  # style_slate_grid flags F/G over 15
    assert header[6] == "Gust"
    assert header[8] == "Div"  # style_slate_grid chips I on "DIV"
    assert header[10] == "Total move"  # style_slate_grid colour-scales K
    assert header[11] == "Spread move"  # style_slate_grid colour-scales L


def test_slate_grid_movement_columns_read_the_home_teams_edgeraw_row():
    # A9 (2026-09-22): TotMove/SpdMove are team-level joins on EdgeRaw
    # (derived._attach_line_movement) -- SpdMove is directional, so the
    # HOME team's row is used consistently (matching GamesRaw!$L's own
    # home-team-perspective convention for the static Spread column),
    # keyed off GamesRaw's own Home column (C), not Away (B).
    client = _CapturingClient()
    _build_slate(client)
    row = client.rows[1]
    assert "VLOOKUP(GamesRaw!$C2," in row[10]
    assert "VLOOKUP(GamesRaw!$C2," in row[11]
    total_move_end = column_letter(EDGE_COLUMNS.index("TotMove") + EDGE_DATA_OFFSET)
    spread_move_end = column_letter(EDGE_COLUMNS.index("SpdMove") + EDGE_DATA_OFFSET)
    assert f"EdgeRaw!$D:${total_move_end}" in row[10]  # Team through TotMove
    assert f"EdgeRaw!$D:${spread_move_end}" in row[11]  # Team through SpdMove


def test_slate_grid_wind_gust_vlookups_derive_from_weather_columns_not_hardcoded():
    # A9 (2026-09-22): the last surviving hardcoded-VLOOKUP-index instance
    # (CLAUDE.md's central hazard) -- both the range end letter and the
    # result index must track sources.weather.WEATHER_COLUMNS, not a
    # literal "6"/"7" that would silently break if that list ever
    # reorders.
    from dfs.sources.weather import WEATHER_COLUMNS

    client = _CapturingClient()
    _build_slate(client)
    wind_idx = WEATHER_COLUMNS.index("Wind") + 1
    gust_idx = WEATHER_COLUMNS.index("Gust") + 1
    row = client.rows[1]
    assert f"WeatherRaw!$A:$F,{wind_idx},FALSE" in row[5]
    assert f"WeatherRaw!$A:$G,{gust_idx},FALSE" in row[6]


def test_slate_grid_has_gps_and_a_hidden_check_but_no_model_columns():
    """Round 5 item 5c: GPS's "Implied Total" is Vegas, not a model, so Model
    Tot/Tot Δ/Model Spd/Spd Δ are gone; only GPS and a hidden sanity check stay."""
    client = _CapturingClient()
    _build_slate(client)
    header = client.rows[0]
    assert header[12:] == [
        "GPS",
        "GameEnv",
        "Pace",
        "PROE",
        "Expl%",
        SLATE_GPS_CHECK_HEADER,
        SLATE_ON_SLATE_HEADER,
    ]
    for gone in ("Model Tot", "Tot Δ", "Model Spd", "Spd Δ"):
        assert gone not in header
    row = client.rows[1]
    assert "GPSRaw!$A:$C" in row[12]  # GPS reads GPSRaw directly, keyed off the home team
    assert "VLOOKUP(GamesRaw!$C2," in row[12]


def test_slate_grid_keeps_every_game_and_flags_the_ones_with_no_players():
    """Round 5 follow-up item 1: Slate Grid lists the whole week; a hidden helper says
    which games have a player on EdgeRaw (either team), for the dimming rule."""
    client = _CapturingClient()
    _build_slate(client)
    assert all(client.rows[r][0] for r in range(1, 19))  # header + 18 game rows, none filtered out
    helper = client.rows[1][SLATE_ON_SLATE_COL_INDEX]
    assert helper.count("COUNTIF(EdgeRaw!") == 2  # away + home
    assert "GamesRaw!$B2" in helper and "GamesRaw!$C2" in helper and ")>0" in helper
    assert helper.startswith('=IF(GamesRaw!$A2="","",')  # blank rows stay blank


def test_slate_grid_game_columns_use_the_same_helper_as_the_board_slate_shape():
    # Usage work (2026-10-02): GameEnv/Pace/PROE/Expl% on every game row are both teams' EdgeRaw
    # values averaged -- the Board's own helper, not a second copy of the logic.
    client = _CapturingClient()
    _build_slate(client)
    row = client.rows[SLATE_GAME_FIRST_ROW - 1]
    for metric in ("GameEnv", "Pace", "PROE", "Expl%"):
        expected = _edge_team_pair_mean("EdgeRaw", metric, "GamesRaw!$B2", "GamesRaw!$C2")
        assert expected in row[SLATE_HEADER.index(metric)], metric
    away, home = (
        f"${BOARD_SLATE_AWAY_COL}{BOARD_SLATE_FIRST_ROW}",
        f"${BOARD_SLATE_HOME_COL}{BOARD_SLATE_FIRST_ROW}",
    )
    board_row = _build_board().rows[BOARD_SLATE_FIRST_ROW - 1]
    assert (
        _edge_team_pair_mean("EdgeRaw", "Pace", away, home) in board_row[BOARD_SLATE_COLHEADER.index("Pace")]
    )


def test_slate_grid_teams_section_layout_and_spill():
    client = _CapturingClient()
    _build_slate(client)
    rows = client.rows
    assert rows[SLATE_TEAMS_HEADER_ROW - 1][0].startswith("TEAMS")
    assert rows[SLATE_TEAMS_COLHEADER_ROW - 1][: len(SLATE_TEAMS_COLHEADER)] == SLATE_TEAMS_COLHEADER
    assert len(rows) == SLATE_TEAMS_LAST_ROW  # one slot per possible team, no more
    first = rows[SLATE_TEAMS_FIRST_ROW - 1]
    spill = first[0]
    # One SORT by implied total (3rd column) descending, spilling Team | Opp | Implied.
    assert "SORT(FILTER(" in spill and ",3,FALSE)" in spill and f",{SLATE_TEAM_ROWS},3)" in spill
    # Vegas implied from the home-perspective spread: away (total - spread)/2, home (total + spread)/2.
    assert "-GamesRaw!$L$2:$L$40)/2" in spill and "+GamesRaw!$L$2:$L$40)/2" in spill
    # The spill owns A:C of every team row below the first: nothing may sit in them.
    for r in range(SLATE_TEAMS_FIRST_ROW, SLATE_TEAMS_LAST_ROW):
        assert rows[r][:3] == ["", "", ""]


def test_slate_grid_teams_metrics_read_team_metrics_raw_by_name_not_position():
    from dfs.sources.nflverse_pbp import TEAM_METRIC_COLUMNS

    client = _CapturingClient()
    _build_slate(client)
    row = client.rows[SLATE_TEAMS_FIRST_ROW - 1]
    end = column_letter(len(TEAM_METRIC_COLUMNS) - 1)
    for header, (field, whose) in SLATE_TEAMS_LOOKUPS.items():
        formula = row[SLATE_TEAMS_COLHEADER.index(header)]
        idx = TEAM_METRIC_COLUMNS.index(field) + 1
        key = f"$B{SLATE_TEAMS_FIRST_ROW}" if whose == "opp" else f"$A{SLATE_TEAMS_FIRST_ROW}"
        assert f"VLOOKUP({key},TeamMetricsRaw!$A:${end},{idx},FALSE)" in formula, header
        assert formula.startswith(f'=IF($A{SLATE_TEAMS_FIRST_ROW}="","",')  # blank rows stay blank
    # What the opponent's defense ALLOWS is the opponent's own row, never the team's.
    assert SLATE_TEAMS_LOOKUPS["Opp Def EPA/pass"] == ("DefEPA/Pass", "opp")
    assert SLATE_TEAMS_LOOKUPS["Off EPA/pass"] == ("OffEPA/Pass", "own")


def test_slate_grid_teams_rows_carry_the_on_dk_slate_helper_for_dimming():
    client = _CapturingClient()
    _build_slate(client)
    helper = client.rows[SLATE_TEAMS_FIRST_ROW - 1][SLATE_ON_SLATE_COL_INDEX]
    assert helper.startswith(f'=IF($A{SLATE_TEAMS_FIRST_ROW}="","",COUNTIF(EdgeRaw!')
    assert helper.endswith(f"$A{SLATE_TEAMS_FIRST_ROW})>0)")


def test_board_slate_shape_drops_games_with_no_players_through_one_shared_filter():
    """All four spills (Matchup, GameId, Away, Home) must use the identical condition
    or the rows the per-row Wind/Pace lookups join on would misalign."""
    client = _build_board()
    slate_row = client.rows[BOARD_SLATE_FIRST_ROW - 1]
    spills = [
        slate_row[SL],
        slate_row[BOARD_SLATE_GAMEID_COL_INDEX],
        slate_row[BOARD_SLATE_GAMEID_COL_INDEX + 1],
        slate_row[BOARD_SLATE_GAMEID_COL_INDEX + 2],
    ]
    team = _rng("EdgeRaw", "Team")
    conditions = set()
    for formula in spills:
        assert "MATCH(GamesRaw!" in formula and f",{team},0)" in formula
        conditions.add(re.search(r'\(GamesRaw![^)]*<>""\)\*.*>0\)', formula).group(0))
    assert len(conditions) == 1


def test_slate_grid_gps_check_is_blank_without_gps_and_uses_the_1_5_point_threshold():
    client = _CapturingClient()
    _build_slate(client)
    check = client.rows[1][SLATE_GPS_CHECK_COL_INDEX]
    assert "GPSRaw!$A:$C" in check
    assert f">{GPS_IMPLIED_MISMATCH_PTS}" in check
    assert check.count('=""') >= 2  # away/home implied blank-checks: never a fabricated 0
    # Vegas implied: away = (total - spread)/2, home = (total + spread)/2 (positive spread = home favoured).
    assert "-GamesRaw!$L2)/2" in check.replace(" ", "") or "GamesRaw!$L2)/2" in check


def test_board_slate_shape_carries_game_env_proe_expl_and_gps_but_not_tot_delta():
    client = _build_board()
    header = client.rows[BOARD_SLATE_COLHEADER_ROW - 1]
    assert header[: len(BOARD_SLATE_COLHEADER)] == BOARD_SLATE_COLHEADER
    for wanted in ("Pace", "PROE", "Expl%", "GameEnv", "GPS"):
        assert wanted in BOARD_SLATE_COLHEADER
    assert "Tot Δ" not in BOARD_SLATE_COLHEADER
    row = client.rows[BOARD_SLATE_FIRST_ROW - 1]
    gps = row[BOARD_SLATE_COLHEADER.index("GPS")]
    assert "GPSRaw!$A:$C" in gps
    assert f"${BOARD_SLATE_HOME_COL}" in gps  # GPS read off the home team's row


def test_board_slate_team_metrics_average_both_teams_and_derive_letters_from_edge_columns():
    client = _build_board()
    row = client.rows[BOARD_SLATE_FIRST_ROW - 1]
    for metric in ("Pace", "PROE", "Expl%", "GameEnv"):
        formula = row[BOARD_SLATE_COLHEADER.index(metric)]
        letter = column_letter(EDGE_COLUMNS.index(metric) + EDGE_DATA_OFFSET)
        idx = EDGE_COLUMNS.index(metric) - EDGE_COLUMNS.index("Team") + 1
        assert "AVERAGE(" in formula
        assert f"${letter}," in formula and f",{idx},FALSE" in formula
        assert f"${BOARD_SLATE_AWAY_COL}" in formula and f"${BOARD_SLATE_HOME_COL}" in formula


def test_board_slate_gps_check_helper_sits_in_the_hidden_join_key_block():
    client = _build_board()
    row = client.rows[BOARD_SLATE_FIRST_ROW - 1]
    check = row[BOARD_SLATE_GPSCHK_COL_INDEX]
    assert "GPSRaw!$A:$C" in check
    assert f">{GPS_IMPLIED_MISMATCH_PTS}" in check
    assert BOARD_SLATE_GPSCHK_COL_INDEX > BOARD_SLATE_HOME_COL_INDEX


def test_exposure_header_row_matches_style_exposures_column_assumptions():
    client = FakeSheetsClient(existing_rows=[], post_write_names=[])
    build_exposure(client, edge_tab="EdgeRaw", lineups_tab="Lineups", lineup_count=20)
    tab_name, rows = client.write_tab_calls[0]
    header = rows[0]
    assert tab_name == EXPOSURE_TAB
    assert header[5] == "Target"  # style_exposure marks F as the one typed input column
    assert header[6] == "vs Target"  # style_exposure chips G on over/under


def test_movement_header_row_has_the_names_style_movement_looks_up():
    client = _CapturingClient()
    build_movement(client, edge_tab="EdgeRaw")
    header = client.rows[MOVEMENT_HEADER_ROW - 1]
    assert header == MOVEMENT_HEADER
    assert "Kickoff (UTC)" not in header and "Player" not in header  # per team, not per player


class _QueueSectionClient:
    """Just enough of SheetsClient for write_queue_section: a real
    EdgeRaw header/Id column/Pool column, and a Board tab it can write
    the Queue body into."""

    def __init__(self, header, ids, pool_ticks, *, board_present=True, edge_present=True):
        self._header = header
        self._ids = ids
        self._pool_ticks = pool_ticks
        self._board_present = board_present
        self._edge_present = edge_present
        self.update_calls: list[tuple[str, list[list]]] = []
        self.hidden: list[tuple[int, int]] = []
        self.unhidden: list[tuple[int, int]] = []

    def tab_exists(self, tab_name):
        if tab_name == "Player Pool":
            return False
        return self._board_present if tab_name == "Board" else self._edge_present

    def hide_rows(self, tab_name, first, last):
        self.hidden.append((first, last))

    def unhide_rows(self, tab_name, first, last):
        self.unhidden.append((first, last))

    def read_range(self, tab_name, a1_range):
        if a1_range == "A1:1":
            return [self._header]
        if a1_range == "A2:A1000":
            return [[t] for t in self._pool_ticks]
        return []

    def read_range_unformatted(self, tab_name, a1_range):
        return [[i] for i in self._ids]

    def update_range(self, tab_name, a1_range, rows):
        self.update_calls.append((a1_range, rows))


def _queue_changes_df(*rows):
    return pd.DataFrame(rows, columns=["Id", "Name", "Position", "Team", "Reason"])


def test_write_queue_section_filters_to_pooled_players_and_writes_player_rows_with_ids():
    client = _QueueSectionClient(
        header=["Pool", "Name", "Position", "Team", "Id"],
        ids=["1", "2"],
        pool_ticks=["Both", ""],  # player 1 pooled, player 2 not
    )
    changes = _queue_changes_df(
        ("1", "Player A", "RB", "KC", "Avail -> Q"),
        ("2", "Player B", "WR", "SF", "Salary 6000 -> 5800"),
    )

    result = write_queue_section(client, changes, "EdgeRaw")

    assert "1 pooled change" in result
    a1_range, body = client.update_calls[0]
    assert a1_range == f"A{BOARD_QUEUE_FIRST_ROW}:{BOARD_ID_COL}{BOARD_QUEUE_LAST_ROW}"
    assert (
        body[0][PL : PL + 4] == ["Player A", "RB", "KC", "Avail -> Q"] and body[0][BOARD_ID_COL_INDEX] == "1"
    )
    assert body[0][BOARD_LIST_COLHEADER.index("Pool")].startswith("=IF($")  # live Pool cell, keyed on the Id
    assert all(not any(c for c in row) for row in body[1:])
    # the rows it did not use are hidden: one visible line, not twenty
    assert client.hidden == [(BOARD_QUEUE_FIRST_ROW + 1, BOARD_QUEUE_LAST_ROW)]
    assert client.unhidden == [(BOARD_QUEUE_FIRST_ROW, BOARD_QUEUE_FIRST_ROW)]


def test_write_queue_section_shows_one_line_when_nothing_pooled_changed():
    client = _QueueSectionClient(
        header=["Pool", "Name", "Position", "Team", "Id"],
        ids=["1"],
        pool_ticks=["Both"],
    )
    changes = _queue_changes_df(("2", "Player B", "WR", "SF", "Salary 6000 -> 5800"))

    write_queue_section(client, changes, "EdgeRaw")

    _, body = client.update_calls[0]
    assert body[0][PL] == BOARD_QUEUE_EMPTY and body[0][0] == ""
    assert client.hidden == [(BOARD_QUEUE_FIRST_ROW + 1, BOARD_QUEUE_LAST_ROW)]  # one visible line


def test_write_queue_section_notes_overflow_past_the_row_cap_and_hides_nothing():
    header = ["Pool", "Name", "Position", "Team", "Id"]
    ids = [str(i) for i in range(1, 25)]
    pool_ticks = ["Both"] * len(ids)
    changes = _queue_changes_df(
        *[(str(i), f"Player {i}", "RB", "KC", "Salary changed") for i in range(1, 25)]
    )
    client = _QueueSectionClient(header=header, ids=ids, pool_ticks=pool_ticks)

    write_queue_section(client, changes, "EdgeRaw")

    _, body = client.update_calls[0]
    assert len(body) == BOARD_QUEUE_ROWS
    assert "more not shown" in body[-1][BOARD_LIST_REASON_INDEX]
    assert client.hidden == []


def test_queue_body_and_used_rows_are_pure_helpers():
    body = queue_body(_queue_changes_df(), set(), edge_tab="EdgeRaw")
    assert len(body) == BOARD_QUEUE_ROWS and body[0][PL] == BOARD_QUEUE_EMPTY
    assert used_queue_rows(body) == 1
    assert used_queue_rows([[""] * 3] * 4) == 1  # never fewer than one visible row
    assert used_queue_rows([["x"], ["y"], [""]]) == 2


def test_write_queue_section_skips_cleanly_when_board_absent():
    client = _QueueSectionClient(header=["Pool", "Name", "Id"], ids=[], pool_ticks=[], board_present=False)
    result = write_queue_section(client, _queue_changes_df(), "EdgeRaw")
    assert "not present" in result
    assert client.update_calls == []


def test_write_queue_section_skips_cleanly_when_edgeraw_has_no_id_column():
    client = _QueueSectionClient(header=["Pool", "Name"], ids=[], pool_ticks=[])
    result = write_queue_section(client, _queue_changes_df(), "EdgeRaw")
    assert "no Id column" in result
    assert client.update_calls == []


def test_banner_counts_the_same_main_slate_games_as_slate_shape():
    """Round 5 follow-up: Games / Highest total / Max wind describe the games Slate shape
    lists, not the whole week -- one shared condition, or the header contradicts the table."""
    client = _build_board()
    row = client.rows[BOARD_BANNER_ROW - 1]
    slate_row = client.rows[BOARD_SLATE_FIRST_ROW - 1]
    team = _rng("EdgeRaw", "Team")
    b = BANNER_START_COL
    games, top_total, max_wind = row[b + 1], row[b + 3], row[b + 7]
    for formula in (games, top_total, max_wind):
        assert f",{team},0)" in formula  # gated on a team having players on EdgeRaw
    shared = re.search(r'\(GamesRaw![^)]*<>""\)\*.*>0\)', slate_row[SL]).group(0)
    assert shared in games and shared in top_total
    assert "MATCH(WeatherRaw!$A$2:$A$40" in max_wind  # wind is per GameId, restricted to slate games
