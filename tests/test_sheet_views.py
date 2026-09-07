from dfs.derived import EDGE_COLUMNS
from dfs.sheet_views import (
    EXPOSURE_TAB,
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

    def __init__(self, *, existing_rows: list[list[str]], post_write_names: list[str]):
        self.existing_rows = existing_rows
        self.post_write_names = post_write_names
        self.write_tab_calls: list[tuple[str, list[list]]] = []
        self.update_calls: list[tuple[str, str, list[list]]] = []

    def tab_exists(self, tab_name: str) -> bool:
        return True

    def read_range(self, tab_name: str, a1_range: str):
        if a1_range.startswith("A2:F"):
            return self.existing_rows
        if a1_range.startswith("A2:A"):
            return [[n] for n in self.post_write_names]
        raise AssertionError(f"unexpected read_range call: {a1_range!r}")

    def write_tab(self, tab_name: str, rows: list[list], **_kwargs) -> int:
        self.write_tab_calls.append((tab_name, rows))
        return len(rows)

    def update_range(self, tab_name: str, a1_range: str, rows: list[list]) -> None:
        self.update_calls.append((tab_name, a1_range, rows))


def test_col_and_rng_use_edge_columns_positions():
    assert _col("Name") == "C"  # index 1 + EDGE_DATA_OFFSET (Pool occupies column A)
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


def test_build_exposure_skips_restore_when_nothing_was_typed_before():
    client = FakeSheetsClient(existing_rows=[], post_write_names=["Someone"])

    result = build_exposure(client, edge_tab="EdgeRaw", lineups_tab="Lineups", lineup_count=20)

    assert "target(s) preserved" not in result
    assert client.update_calls == []  # nothing to restore, so no F-column write at all


def test_build_exposure_slots_filled_excludes_rows_above_lineups_data_start_row():
    # Once the pool deck sits above Lineups' header (sheet_pool_deck.py),
    # column A rows 1-10 hold the deck's controls and real player names
    # pulled from the pool for browsing -- counting the whole column would
    # miscount those as filled roster slots (or worse, as rostered
    # players) via the "?*" wildcard. lineups_data_start_row cuts them out.
    client = FakeSheetsClient(existing_rows=[], post_write_names=[])

    build_exposure(
        client,
        edge_tab="EdgeRaw",
        lineups_tab="Lineups",
        lineup_count=20,
        lineups_data_start_row=11,
    )

    tab_name, rows = client.write_tab_calls[0]
    header = rows[0]
    assert header[8] == "Slots filled"
    assert "Lineups!$A$11:$A" in header[9]
    assert "Lineups!$A$11:$A" in rows[1][3]  # per-row COUNTIF also respects it


def test_build_exposure_defaults_to_whole_column_when_no_pool_deck_present():
    client = FakeSheetsClient(existing_rows=[], post_write_names=[])

    build_exposure(client, edge_tab="EdgeRaw", lineups_tab="Lineups", lineup_count=20)

    tab_name, rows = client.write_tab_calls[0]
    assert "Lineups!$A$1:$A" in rows[0][9]


def test_build_movement_uses_edge_columns_positions_for_linemove_and_gamestart():
    assert "LineMove" in EDGE_COLUMNS and "GameStart" in EDGE_COLUMNS

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
    assert f"${_col('LineMove')}$2:${_col('LineMove')}" in body
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
    # Panel 3 (K-N): Player, Pos, Avail, Flag -- style_board chips M (Avail) and N (Flag).
    assert header[12] == "Avail"
    assert header[13] == "Flag"


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


def test_movement_header_row_matches_style_movements_column_assumptions():
    client = _CapturingClient()
    build_movement(client, edge_tab="EdgeRaw")
    header = client.rows[2]  # row 3: style_movement's _HEADER_FMT range
    assert header[2] == "Line Move"  # style_movement colour-scales C
    assert header[4] == "Flag"  # style_movement chips E
