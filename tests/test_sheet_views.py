from dfs.derived import EDGE_COLUMNS
from dfs.sheet_views import (
    DEFAULT_LINEUP_COUNT,
    EXPOSURE_TAB,
    LINEUP_COUNT_CELL,
    MOVEMENT_TAB,
    _col,
    _rng,
    build_board,
    build_exposure,
    build_movement,
    build_slate_grid,
)


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


def test_build_exposure_adds_portfolio_headline_when_lineups_pos_and_gameid_linked():
    client = FakeSheetsClient(
        existing_rows=[], post_write_names=[], lineups_header=["Name", "Pos.", "GameID"]
    )

    result = build_exposure(client, edge_tab="EdgeRaw", lineups_tab="Lineups", lineup_count=20)

    header = client.write_tab_calls[0][1][0]
    assert header[10:16] == [
        "Distinct QBs",
        '=COUNTA(UNIQUE(FILTER(Lineups!$A$1:$A,Lineups!$B$1:$B="QB")))',
        "Shared QB?",
        '=IF(COUNTIF(Lineups!$B$1:$B,"QB")>COUNTA(UNIQUE(FILTER(Lineups!$A$1:$A,Lineups!$B$1:$B="QB"))),"Yes","No")',
        "Distinct games",
        '=COUNTA(UNIQUE(FILTER(Lineups!$C$1:$C,Lineups!$C$1:$C<>"")))',
    ]
    assert "portfolio headline (Distinct QBs" in result


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


def test_build_movement_uses_edge_columns_positions_for_impmove_and_gamestart():
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
    body = rows[-1][0]
    assert f"${_col('ImpliedMove')}$2:${_col('ImpliedMove')}" in body
    assert f"${_col('GameStart')}$2:${_col('GameStart')}" in body


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


def test_board_header_row_matches_style_boards_column_assumptions():
    client = _CapturingClient()
    build_board(client, edge_tab="EdgeRaw", games_tab="GamesRaw", weather_tab="WeatherRaw")
    header = client.rows[5]  # row 6: style_board formats this as each panel's sub-header
    # Panel 1 (A-D): Player, Pos, Lev, CeilVal -- style_board colour-scales C (Lev) and D (CeilVal).
    assert header[2] == "Lev"
    assert header[3] == "CeilVal"
    # Panel 2 (F-I): Player, Pos, Salary, CeilVal -- style_board currency-formats H (Salary).
    assert header[7] == "Salary"
    assert header[8] == "CeilVal"
    # Panel 3 (K-N): Player, Pos, Avail, Flags -- style_board chips M (Avail) and N (Flags).
    assert header[12] == "Avail"
    assert header[13] == "Flags"


def test_best_ceiling_value_ranks_within_position_not_across_the_whole_slate():
    # Phase 6, Part 1.3: BEST CEILING VALUE used to be one flat SORT by
    # CeilVal across the whole slate, which reads as ~11 QBs of 12 rows
    # live (CeilVal is points-per-$1,000 and QBs mechanically dominate it
    # cross-position). Fixed as 5 independent per-position blocks.
    client = _CapturingClient()
    build_board(client, edge_tab="EdgeRaw", games_tab="GamesRaw", weather_tab="WeatherRaw")
    best_value = client.rows[6][5]  # row 7, panel 2's formula cell (F)

    assert best_value.startswith("={")
    for position in ("QB", "RB", "WR", "TE", "DST"):
        assert f'{_rng("EdgeRaw", "Position")}="{position}"' in best_value
    # Each position's own block is independently constrained -- not one
    # combined constrain that could still cut a whole position off.
    assert best_value.count("ARRAY_CONSTRAIN") == 5
    assert best_value.count(",2,4)") == 5  # 2 rows x 4 cols per position block


def test_top_leverage_and_landmines_reference_current_edge_columns():
    # Phase 6, Part 1.3: found live that Board's formulas go stale the
    # moment EdgeRaw's own column order changes underneath them (the Sept
    # 16 CeilPct/OwnPct reorder), since a written formula string doesn't
    # follow a later column move. This test only pins that build_board
    # generates against EDGE_COLUMNS at call time -- it can't catch a
    # regenerate never having been re-run after a real reorder; that's a
    # process discipline (re-run build-views after any EdgeRaw reorder),
    # not something a unit test can enforce.
    client = _CapturingClient()
    build_board(client, edge_tab="EdgeRaw", games_tab="GamesRaw", weather_tab="WeatherRaw")
    top_leverage = client.rows[6][0]
    landmines = client.rows[6][10]

    assert _rng("EdgeRaw", "Leverage") in top_leverage
    assert _rng("EdgeRaw", "Avail") in landmines
    # Part 7.9: LANDMINES reads "Flags" (every matching condition), not
    # the hidden, top-priority-only "Flag".
    assert _rng("EdgeRaw", "Flags") in landmines
    # The exact stale-reference bug: LANDMINES must never read Leverage's
    # own column as a stand-in for Avail/Flags. (OwnPct, the other half of
    # the original stale-reference incident, no longer exists at all --
    # dropped entirely from EDGE_COLUMNS in Part 7.9.)
    assert _rng("EdgeRaw", "Leverage") not in landmines


def test_slate_grid_header_row_matches_style_slate_grids_column_assumptions():
    client = _CapturingClient()
    build_slate_grid(client, games_tab="GamesRaw", weather_tab="WeatherRaw")
    header = client.rows[0]
    assert header[2] == "Total"  # style_slate_grid colour-scales C
    assert header[3] == "Spread"  # style_slate_grid number-formats D
    assert header[5] == "Wind"  # style_slate_grid flags F/G over 15
    assert header[6] == "Gust"
    assert header[8] == "Div"  # style_slate_grid chips I on "DIV"


def test_exposure_header_row_matches_style_exposures_column_assumptions():
    client = FakeSheetsClient(existing_rows=[], post_write_names=[])
    build_exposure(client, edge_tab="EdgeRaw", lineups_tab="Lineups", lineup_count=20)
    tab_name, rows = client.write_tab_calls[0]
    header = rows[0]
    assert tab_name == EXPOSURE_TAB
    assert header[5] == "Target"  # style_exposure marks F as the one typed input column
    assert header[6] == "vs Target"  # style_exposure chips G on over/under


def test_movement_header_row_has_the_names_style_movement_looks_up():
    # style_movement (sheet_style.py) is header-NAME-driven now (Section
    # F), not a hardcoded A:E range -- this just confirms build_movement
    # still produces the names it looks for, wherever they land.
    client = _CapturingClient()
    build_movement(client, edge_tab="EdgeRaw")
    header = client.rows[2]  # row 3
    assert "Implied move" in header
    assert "Total move" in header
    assert "Spread move" in header
    assert "Flags" in header
    assert "Line Move" not in header  # Section F: renamed away from the ambiguous old label
