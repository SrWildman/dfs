import re
from dataclasses import dataclass

from dfs.config import Config
from dfs.derived import ALL_PCT_COLUMNS, EDGE_COLUMNS, EDGE_SHEET_ORDER
from dfs.doctor import run_doctor
from dfs.sheet_columns import INTERNAL
from dfs.sheet_formula_ranges import DKSALCLEAN_TAB, RESULTS_FORMULA_HEADERS, formula_ranges
from dfs.sheet_instructions import INSTRUCTIONS_LAST_ROW, INSTRUCTIONS_TAB, render_instructions_grid
from dfs.sheet_lineup_keys import LINEUP_KEY_HEADER
from dfs.sheet_links import LINKED_EDGE_COLUMNS, PLAYER_POOL_RAW_TAB
from dfs.sheet_season_view import HEADER as SEASON_HEADER
from dfs.sheet_style import EDGE_HIDDEN_HELPERS, edge_group_spans
from dfs.sheet_views import EXPOSURE_TAB, LINEUP_COUNT_CELL
from dfs.sheets import column_letter
from dfs.sources.edge import POOL_HEADER
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS, PLAYER_POOL_HEADER_ROW


def column_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


@dataclass
class _FakeTab:
    title: str
    header: list[str]


class FakeDoctorClient:
    """Fakes the SheetsClient methods doctor.py calls -- list_tabs() for tab
    existence + header rows, read_range() for the row-specific checks
    (Lineups header repeats, Bankroll header rows, Exposure's lineup-count
    cell)."""

    def __init__(
        self,
        tabs: dict[str, list[str]],
        rows: dict[tuple[str, str], list[list[str]]] | None = None,
        formulas: dict[tuple[str, str], list[list[str]]] | None = None,
        hidden_names: dict[str, set[str]] | None = None,
        groups: dict[str, list[tuple[int, int, bool]]] | None = None,
    ):
        self._tabs = tabs
        self._rows = rows or {}
        self._formulas = formulas or {}
        self._hidden_names = hidden_names or {}
        self._groups = groups or {}

    def get_column_widths(self, tab_name: str, last_col_a1: str) -> list[dict]:
        """Default: a healthy layout -- exactly the tab's helper columns hidden (by header name), plus the
        columns inside its groups. A test that wants a fault passes its own `hidden_names` / `groups`."""
        header = self._tabs[tab_name]
        if tab_name in self._hidden_names:
            hidden = self._hidden_names[tab_name]
        else:
            hidden = set(EDGE_HIDDEN_HELPERS if tab_name == "EdgeRaw" else INTERNAL)
            if tab_name == "Lineups":
                hidden.add(LINEUP_KEY_HEADER)
        in_group = {i for a, b, _ in self.get_column_groups(tab_name) for i in range(a, b + 1)}
        return [{"hiddenByUser": name in hidden or i in in_group} for i, name in enumerate(header)]

    def get_column_groups(self, tab_name: str) -> list[tuple[int, int, bool]]:
        if tab_name in self._groups:
            return self._groups[tab_name]
        return [(a, b, True) for a, b in edge_group_spans()] if tab_name == "EdgeRaw" else []

    def list_tabs(self):
        return [_FakeTab(title=title, header=header) for title, header in self._tabs.items()]

    def read_range(self, tab_name: str, a1_range: str) -> list[list[str]]:
        return self._rows.get((tab_name, a1_range), [])

    def row_count(self, tab_name: str) -> int:
        return 30

    def read_formula(self, tab_name: str, a1_range: str) -> list[list[str]]:
        """Default: a healthy grid (row N's every cell is `=X{N}`), so existing tests see
        no formula-range findings; a test that wants a gap passes its own grid."""
        if (tab_name, a1_range) in self._formulas:
            return self._formulas[(tab_name, a1_range)]
        m = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", a1_range)
        first, last = int(m.group(2)), int(m.group(4))
        width = column_index(m.group(3)) + 1
        return [[f"=X{r}"] * width for r in range(first, last + 1)]


def _base_config(**overrides) -> Config:
    data = {
        "google_sheets": {
            "sheet_id": "abc",
            "credentials_file": "creds.json",
            "tab_mappings": {"edge": "EdgeRaw"},
        },
        "lineups": {
            "upload_tab": "DK Upload",
            "builder_tab": "Lineups",
            "player_pool_tab": "Player Pool",
        },
        "bankroll": {"tab": "Bankroll"},
    }
    data.update(overrides)
    return Config.model_validate(data)


_ALL_GOOD_TABS = {
    "EdgeRaw": [POOL_HEADER, *EDGE_SHEET_ORDER],
    "DK Upload": ["Entry ID"],
    "Lineups": ["Name", "Pos.", *LINKED_EDGE_COLUMNS],
    "Player Pool": ["Name", "Pos.", *LINKED_EDGE_COLUMNS],
    PLAYER_POOL_RAW_TAB: ["Name", "Pos.", *LINKED_EDGE_COLUMNS],
    "Bankroll": [],
    "Results": [
        "Week",
        "Cash Pts",
        "Cash Line",
        RESULTS_FORMULA_HEADERS[0],
        "H2H Entered",
        "H2H Win",
        RESULTS_FORMULA_HEADERS[1],
    ],
    "Season": list(SEASON_HEADER),
    DKSALCLEAN_TAB: ["Position", "Team", "ID", "Name", "Salary", "Team", "OPP"],
    "NameAlias": [],
    "Edge Finder": [],
    EXPOSURE_TAB: [],
}

# Lineups' header sits at row 1 (the pool deck that used to sit above it,
# shifting it to row 11, was removed entirely -- Phase 5, 2026-09-16). No
# override is needed any more for list_tabs()'s own header read; the
# per-row column-A check below (_check_lineups_header_repeats) still needs
# every LINEUPS_NAME_BLOCKS row spelled out, including row 1 itself.
_LINEUPS_HEADER_ROWS = [start - 1 for start, _ in LINEUPS_NAME_BLOCKS]
_LINEUPS_HEADER_LAST_ROW = max(_LINEUPS_HEADER_ROWS)


def _good_lineups_rows() -> list[list[str]]:
    rows = [[""] for _ in range(_LINEUPS_HEADER_LAST_ROW)]
    for row_num in _LINEUPS_HEADER_ROWS:
        rows[row_num - 1] = ["Name"]
    return rows


_PLAYER_POOL_HEADER_RANGE = f"A{PLAYER_POOL_HEADER_ROW}:{PLAYER_POOL_HEADER_ROW}"


def _lineups_rows(player_pool_header: list[str] | None = None) -> dict[tuple[str, str], list[list[str]]]:
    # run_doctor re-reads Player Pool's header from PLAYER_POOL_HEADER_ROW
    # (A3: row 1 became the add-a-player control) -- defaults to
    # _ALL_GOOD_TABS' own Player Pool header so every existing call site
    # keeps working without having to know about this override; a test
    # that deliberately varies Player Pool's header passes its own.
    rows = {("Lineups", f"A1:A{_LINEUPS_HEADER_LAST_ROW}"): _good_lineups_rows()}
    rows[("Player Pool", _PLAYER_POOL_HEADER_RANGE)] = [
        player_pool_header if player_pool_header is not None else _ALL_GOOD_TABS["Player Pool"]
    ]
    # Exposure!H1, the lineup-count cell _check_lineup_count_cell reads --
    # default to a valid in-range value so every existing call site stays
    # "all good" without knowing about this check; a test that
    # deliberately wants a bad H1 overrides this entry itself.
    rows[(EXPOSURE_TAB, LINEUP_COUNT_CELL)] = [["6"]]
    return rows


def _instructions_grid_rows() -> list[list[str]]:
    """The exact [[A, B], ...] shape client.read_range would return for
    an Instructions tab generated by sheet_instructions.py (one entry per
    ACTUAL sheet row, 1..INSTRUCTIONS_LAST_ROW, including the untouched
    divider row -- a real read_range doesn't skip rows the way iterating
    render_instructions_grid()'s own dict would), straight from
    render_instructions_grid so this can never drift from the real
    generator's own idea of "correct"."""
    grid = render_instructions_grid()
    return [list(grid.get(row, ("", ""))) for row in range(1, INSTRUCTIONS_LAST_ROW + 1)]


def test_run_doctor_reports_no_drift_on_a_freshly_generated_instructions_tab():
    tabs = {**_ALL_GOOD_TABS, INSTRUCTIONS_TAB: []}
    rows = _lineups_rows()
    rows[(INSTRUCTIONS_TAB, f"A1:B{INSTRUCTIONS_LAST_ROW}")] = _instructions_grid_rows()
    client = FakeDoctorClient(tabs=tabs, rows=rows)

    issues = run_doctor(client, cfg=_base_config(), title="Week 1")
    assert not any(i.check == "instructions-drift" for i in issues)


def test_run_doctor_flags_instructions_drift_after_one_cell_is_hand_edited():
    # Week 3 follow-ups, Item 2: the whole point -- a hand edit (or a
    # generator change nobody re-ran against the sheet) must not go
    # unnoticed the way the original hand-typed tab did.
    tabs = {**_ALL_GOOD_TABS, INSTRUCTIONS_TAB: []}
    grid_rows = _instructions_grid_rows()
    grid_rows[7][1] = "Someone edited this by hand"  # row 8 ("Board")'s body
    rows = _lineups_rows()
    rows[(INSTRUCTIONS_TAB, f"A1:B{INSTRUCTIONS_LAST_ROW}")] = grid_rows
    client = FakeDoctorClient(tabs=tabs, rows=rows)

    issues = run_doctor(client, cfg=_base_config(), title="Week 1")
    drift_issues = [i for i in issues if i.check == "instructions-drift"]
    assert len(drift_issues) == 1
    assert "8" in drift_issues[0].detail


def test_run_doctor_skips_instructions_drift_when_the_tab_is_absent():
    client = FakeDoctorClient(tabs=_ALL_GOOD_TABS, rows=_lineups_rows())
    issues = run_doctor(client, cfg=_base_config(), title="Week 1")
    assert not any(i.check == "instructions-drift" for i in issues)


def test_run_doctor_reports_nothing_wrong_on_a_correct_sheet():
    cfg = _base_config()
    client = FakeDoctorClient(tabs=_ALL_GOOD_TABS, rows=_lineups_rows())
    assert run_doctor(client, cfg, title="Week 1") == []


def test_run_doctor_flags_a_title_that_does_not_parse_as_week_n():
    # Week 3 follow-ups, Item 1: catches Week4/Copy of Template/etc. at
    # doctor time, not at Tuesday's week close (Fix 1).
    cfg = _base_config()
    client = FakeDoctorClient(tabs=_ALL_GOOD_TABS, rows=_lineups_rows())

    issues = run_doctor(client, cfg, title="Copy of Template")
    assert any(i.check == "sheet-title" for i in issues)


def test_run_doctor_exempts_the_templates_own_title():
    cfg = _base_config()
    client = FakeDoctorClient(tabs=_ALL_GOOD_TABS, rows=_lineups_rows())

    issues = run_doctor(client, cfg, title="Template")
    assert not any(i.check == "sheet-title" for i in issues)


def test_run_doctor_skips_the_title_check_when_asked():
    # dfs week new's own pre-flight call: the fresh copy isn't renamed
    # yet, so a not-yet-"Week N" title must not be a doctor failure there.
    cfg = _base_config()
    client = FakeDoctorClient(tabs=_ALL_GOOD_TABS, rows=_lineups_rows())

    issues = run_doctor(client, cfg, title="Copy of Template", check_title=False)
    assert not any(i.check == "sheet-title" for i in issues)


def test_run_doctor_flags_missing_tab():
    tabs = dict(_ALL_GOOD_TABS)
    del tabs["Bankroll"]
    cfg = _base_config()
    client = FakeDoctorClient(tabs=tabs, rows=_lineups_rows())

    issues = run_doctor(client, cfg, title="Week 1")
    assert any(i.check == "tab-exists" and "Bankroll" in i.detail for i in issues)


def test_run_doctor_flags_edgeraw_header_mismatch():
    tabs = dict(_ALL_GOOD_TABS)
    tabs["EdgeRaw"] = [*EDGE_COLUMNS[:-1], "SomethingElse"]
    cfg = _base_config()
    client = FakeDoctorClient(tabs=tabs, rows=_lineups_rows())

    issues = run_doctor(client, cfg, title="Week 1")
    assert any(i.check == "edgeraw-header" for i in issues)


def test_run_doctor_passes_when_edgeraw_pool_column_is_correct():
    tabs = dict(_ALL_GOOD_TABS)
    tabs["EdgeRaw"] = [POOL_HEADER, *EDGE_SHEET_ORDER]
    cfg = _base_config()
    client = FakeDoctorClient(tabs=tabs, rows=_lineups_rows())

    assert run_doctor(client, cfg, title="Week 1") == []


def test_run_doctor_flags_a_wrong_label_in_edgeraw_pool_column():
    tabs = dict(_ALL_GOOD_TABS)
    tabs["EdgeRaw"] = ["SomethingElse", *EDGE_COLUMNS]
    cfg = _base_config()
    client = FakeDoctorClient(tabs=tabs, rows=_lineups_rows())

    issues = run_doctor(client, cfg, title="Week 1")
    assert any(i.check == "edgeraw-header" for i in issues)


def test_run_doctor_flags_edgeraw_still_on_the_old_pool_appended_at_the_end_layout():
    # Pool used to be appended after EDGE_COLUMNS, not prepended before it
    # -- a sheet still in that old shape must fail loudly, not be silently
    # tolerated as "close enough".
    tabs = dict(_ALL_GOOD_TABS)
    tabs["EdgeRaw"] = [*EDGE_COLUMNS, POOL_HEADER]
    cfg = _base_config()
    client = FakeDoctorClient(tabs=tabs, rows=_lineups_rows())

    issues = run_doctor(client, cfg, title="Week 1")
    assert any(i.check == "edgeraw-header" for i in issues)


def test_run_doctor_flags_missing_linked_edge_columns():
    tabs = dict(_ALL_GOOD_TABS)
    tabs["Lineups"] = ["Name", "Pos."]  # never linked
    cfg = _base_config()
    client = FakeDoctorClient(tabs=tabs, rows=_lineups_rows())

    issues = run_doctor(client, cfg, title="Week 1")
    assert any(i.check == "linked-edge-columns" and "'Lineups'" in i.detail for i in issues)


def test_run_doctor_flags_duplicated_linked_edge_columns():
    # The exact bug class the idempotency-guard fix targets: two copies of
    # LINKED_EDGE_COLUMNS in the same header.
    tabs = dict(_ALL_GOOD_TABS)
    tabs["Player Pool"] = ["Name", *LINKED_EDGE_COLUMNS, "Venue", "Ceil", *LINKED_EDGE_COLUMNS]
    cfg = _base_config()
    client = FakeDoctorClient(tabs=tabs, rows=_lineups_rows(player_pool_header=tabs["Player Pool"]))

    issues = run_doctor(client, cfg, title="Week 1")
    assert any(
        i.check == "linked-edge-columns" and "'Player Pool'" in i.detail and "2 times" in i.detail
        for i in issues
    )


def test_run_doctor_flags_lineups_header_repeats_drift():
    cfg = _base_config()
    bad_rows = _good_lineups_rows()
    bad_rows[_LINEUPS_HEADER_ROWS[1] - 1] = ["Aaron Rodgers"]  # a stale pick sitting where a header should be
    rows = _lineups_rows()
    rows[("Lineups", f"A1:A{_LINEUPS_HEADER_LAST_ROW}")] = bad_rows
    client = FakeDoctorClient(tabs=_ALL_GOOD_TABS, rows=rows)

    issues = run_doctor(client, cfg, title="Week 1")
    assert any(i.check == "lineups-header-repeats" for i in issues)


def test_run_doctor_flags_lineups_header_wrong_at_row_one():
    # Row 1 is LINEUPS_NAME_BLOCKS' own first "header row" entry now that
    # the pool deck (which used to sit above it) is gone -- the same
    # header-repeats check catches a wrong row 1 as it would any other.
    cfg = _base_config()
    bad_rows = _good_lineups_rows()
    bad_rows[0] = ["Aaron Rodgers"]  # row 1 should read "Name"
    rows = _lineups_rows()
    rows[("Lineups", f"A1:A{_LINEUPS_HEADER_LAST_ROW}")] = bad_rows
    client = FakeDoctorClient(tabs=_ALL_GOOD_TABS, rows=rows)

    issues = run_doctor(client, cfg, title="Week 1")
    matches = [i for i in issues if i.check == "lineups-header-repeats"]
    assert any("1" in i.detail for i in matches)


def test_run_doctor_flags_blank_bankroll_header_row():
    cfg = _base_config(
        bankroll={
            "tab": "Bankroll",
            "cash": {"header_row": 16, "first_row": 17, "last_row": 59},
        }
    )
    rows = _lineups_rows()
    rows[("Bankroll", "A16:16")] = []
    client = FakeDoctorClient(tabs=_ALL_GOOD_TABS, rows=rows)

    issues = run_doctor(client, cfg, title="Week 1")
    assert any(i.check == "bankroll-header-row" and "cash" in i.detail for i in issues)


def test_run_doctor_passes_bankroll_header_row_when_present():
    cfg = _base_config(
        bankroll={
            "tab": "Bankroll",
            "cash": {"header_row": 16, "first_row": 17, "last_row": 59},
        }
    )
    rows = _lineups_rows()
    rows[("Bankroll", "A16:16")] = [["Entry name"]]
    client = FakeDoctorClient(tabs=_ALL_GOOD_TABS, rows=rows)

    issues = run_doctor(client, cfg, title="Week 1")
    assert not any(i.check == "bankroll-header-row" for i in issues)


def test_run_doctor_flags_lineup_count_cell_when_non_numeric():
    cfg = _base_config()
    rows = _lineups_rows()
    rows[(EXPOSURE_TAB, LINEUP_COUNT_CELL)] = [[""]]
    client = FakeDoctorClient(tabs=_ALL_GOOD_TABS, rows=rows)

    issues = run_doctor(client, cfg, title="Week 1")
    matches = [i for i in issues if i.check == "lineup-count-cell"]
    assert any("not a number" in i.detail for i in matches)


def test_run_doctor_flags_lineup_count_cell_when_out_of_range():
    cfg = _base_config()
    rows = _lineups_rows()
    rows[(EXPOSURE_TAB, LINEUP_COUNT_CELL)] = [[str(len(LINEUPS_NAME_BLOCKS) + 1)]]
    client = FakeDoctorClient(tabs=_ALL_GOOD_TABS, rows=rows)

    issues = run_doctor(client, cfg, title="Week 1")
    matches = [i for i in issues if i.check == "lineup-count-cell"]
    assert any("expected 1.." in i.detail for i in matches)


def test_run_doctor_skips_lineup_count_cell_when_exposure_tab_absent():
    tabs = dict(_ALL_GOOD_TABS)
    del tabs[EXPOSURE_TAB]
    cfg = _base_config()
    client = FakeDoctorClient(tabs=tabs, rows=_lineups_rows())

    issues = run_doctor(client, cfg, title="Week 1")
    assert not any(i.check == "lineup-count-cell" for i in issues)


def test_run_doctor_skips_dependent_checks_for_a_missing_tab():
    # A missing Lineups tab should report tab-exists once, not also crash
    # or double-report from the header-repeats check.
    tabs = dict(_ALL_GOOD_TABS)
    del tabs["Lineups"]
    cfg = _base_config()
    client = FakeDoctorClient(tabs=tabs)

    issues = run_doctor(client, cfg, title="Week 1")
    assert any(i.check == "tab-exists" and "Lineups" in i.detail for i in issues)
    assert not any(i.check == "lineups-header-repeats" for i in issues)


# --- Round 5 follow-up item 2: per-row formula ranges -------------------------------------


def _formula_check_client(formulas):
    return FakeDoctorClient(_ALL_GOOD_TABS, {**_lineups_rows()}, formulas=formulas)


def _range_for(tab: str):
    (spec,) = [s for s in formula_ranges(_base_config(), dict(_ALL_GOOD_TABS)) if s.tab == tab]
    return spec, f"A{spec.first_row}:{column_letter(max(spec.columns))}{spec.last_row}"


def _good_grid(spec):
    width = max(spec.columns) + 1
    return [[f"=X{r}"] * width for r in range(spec.first_row, spec.last_row + 1)]


def test_run_doctor_flags_a_results_formula_column_with_a_blank_row():
    spec, rng = _range_for("Results")
    grid = _good_grid(spec)
    h2h = [i for i, h in spec.columns.items() if h == RESULTS_FORMULA_HEADERS[1]][0]
    grid[5][h2h] = ""  # row 7
    issues = run_doctor(_formula_check_client({("Results", rng): grid}), _base_config(), title="Week 4")
    (issue,) = [i for i in issues if i.check == "formula-ranges"]
    assert "'Results'" in issue.detail and "row(s) 7 have no formula" in issue.detail


def test_run_doctor_flags_an_unguarded_h2h_percent_but_not_the_guarded_one():
    spec, rng = _range_for("Results")
    h2h = [i for i, h in spec.columns.items() if h == RESULTS_FORMULA_HEADERS[1]][0]
    grid = _good_grid(spec)
    for r in range(spec.first_row, spec.last_row + 1):
        grid[r - spec.first_row][h2h] = f'=IF(E{r}="","",IFERROR(F{r}/E{r},""))'
    clean = run_doctor(_formula_check_client({("Results", rng): grid}), _base_config(), title="Week 4")
    assert [i for i in clean if i.check == "formula-ranges"] == []
    grid[3][h2h] = "=F5/E5"  # row 5 lost its guard
    issues = run_doctor(_formula_check_client({("Results", rng): grid}), _base_config(), title="Week 4")
    (issue,) = [i for i in issues if i.check == "formula-ranges"]
    assert "row(s) 5 divide with no empty-state guard" in issue.detail


def test_run_doctor_flags_unguarded_divisions_on_bankroll_and_season_but_not_results_h2h_twice():
    cfg = _base_config()
    tab = "Bankroll"
    grid = [[f"=X{r}"] for r in range(1, 31)]
    grid[11] = ["=D12/B7"]  # Weekly Cash %: the classic empty-week #DIV/0!
    client = FakeDoctorClient(_ALL_GOOD_TABS, {**_lineups_rows()}, formulas={(tab, "A1:Z30"): grid})
    issues = [i for i in run_doctor(client, cfg, title="Week 4") if i.check == "empty-guards"]
    (issue,) = issues
    assert "'Bankroll' column A: row(s) 12" in issue.detail and "guard-empty-states" in issue.detail


def test_run_doctor_flags_dksalclean_rows_that_are_missing_or_read_another_row():
    spec, rng = _range_for(DKSALCLEAN_TAB)
    grid = _good_grid(spec)
    for r in range(746, 988):
        grid[r - spec.first_row][6] = ""  # OPP missing from 746 on
    for r in range(802, 988):
        grid[r - spec.first_row][0] = f"=DKSalRaw!A{r + 2083}"  # the old row-deletion artifact
    issues = run_doctor(_formula_check_client({(DKSALCLEAN_TAB, rng): grid}), _base_config(), title="Week 4")
    details = [i.detail for i in issues if i.check == "formula-ranges"]
    assert any("column G (OPP): row(s) 746-987 have no formula" in d for d in details)
    assert any(
        "column A (Position): row(s) 802-987 hold a formula pointing at another row" in d for d in details
    )


def test_run_doctor_does_not_flag_playerpoolraws_permuted_identity_columns():
    spec, rng = _range_for(PLAYER_POOL_RAW_TAB)
    grid = _good_grid(spec)
    grid[14][0] = "=DkSalClean!D17"  # row 16 reads DkSalClean row 17 -- deliberate
    issues = run_doctor(
        _formula_check_client({(PLAYER_POOL_RAW_TAB, rng): grid}), _base_config(), title="Week 4"
    )
    assert [i for i in issues if i.check == "formula-ranges"] == []


def test_run_doctor_flags_a_missing_dksalclean_tab():
    tabs = {k: v for k, v in _ALL_GOOD_TABS.items() if k != DKSALCLEAN_TAB}
    issues = run_doctor(FakeDoctorClient(tabs, {**_lineups_rows()}), _base_config(), title="Week 4")
    assert any(i.check == "tab-exists" and DKSALCLEAN_TAB in i.detail for i in issues)


# ---- pct-helpers: the colour steps read hidden within-position percentile columns -------------


def _edge_rows(*, with_helper_values: bool, n: int = 40) -> dict[tuple[str, str], list[list[str]]]:
    """EdgeRaw rows with `n` scored players; the ProjPts%ile column is filled or left blank."""
    header = _ALL_GOOD_TABS["EdgeRaw"]
    proj, pct = header.index("ProjPts"), header.index("ProjPts%ile")
    rows = []
    for i in range(n):
        row = [""] * len(header)
        row[proj] = f"{10 + i / 10:.1f}"
        row[pct] = f"{(i * 100) // n}" if with_helper_values else ""
        rows.append(row)
    # the check reads out to the furthest metric/helper column it needs
    furthest = max(
        header.index(c) for m, h in ALL_PCT_COLUMNS.items() if m in header and h in header for c in (m, h)
    )
    return {("EdgeRaw", f"A2:{column_letter(furthest)}30"): rows}


def _pct_issues(rows):
    client = FakeDoctorClient(tabs=_ALL_GOOD_TABS, rows={**_lineups_rows(), **rows})
    return [i for i in run_doctor(client, _base_config(), title="Week 4") if i.check == "pct-helpers"]


def test_run_doctor_passes_when_the_percentile_helpers_are_filled():
    assert _pct_issues(_edge_rows(with_helper_values=True)) == []


def test_run_doctor_flags_a_blank_percentile_helper_next_to_real_metric_values():
    (issue,) = _pct_issues(_edge_rows(with_helper_values=False))
    assert "ProjPts%ile" in issue.detail and "no colour" in issue.detail


def test_run_doctor_ignores_the_percentile_helpers_on_a_tab_with_no_real_data_yet():
    # a fresh template/week: nothing synced, so a blank helper is expected, not a failure
    assert _pct_issues(_edge_rows(with_helper_values=False, n=3)) == []
    assert _pct_issues({}) == []


def test_run_doctor_flags_a_pool_cell_that_holds_a_value_instead_of_the_formula():
    """The Pool cell is a dropdown over a formula; a pick the script did not restore leaves a plain value."""
    from dfs.edge_finder_tab import ID_COL, POOL_COL

    cfg = _base_config()
    tabs = _ALL_GOOD_TABS
    last = 30
    rows = {(("Edge Finder"), f"{ID_COL}1:{ID_COL}{last}"): [["Id"], [""], ["1001"], ["1002"]]}
    formulas = {
        ("Edge Finder", f"{POOL_COL}1:{POOL_COL}{last}"): [
            ["Pool"],
            [""],
            ['=IF($O3="","")'],
            ["Cash"],
        ]
    }
    issues = run_doctor(
        FakeDoctorClient(tabs, rows={**_lineups_rows(), **rows}, formulas=formulas), cfg, title="Week 5"
    )
    plain = [i for i in issues if i.check == "pool-cell-plain-value"]
    assert len(plain) == 1 and "Edge Finder" in plain[0].detail and f"{POOL_COL}4" in plain[0].detail
    # a title in the Pool column has no Id beside it: not a player row, not flagged
    assert not [
        i
        for i in run_doctor(FakeDoctorClient(tabs, rows=_lineups_rows()), cfg, title="Week 5")
        if i.check == "pool-cell-plain-value"
    ]


def test_run_doctor_flags_a_player_rows_empty_pool_cell_as_a_failed_restore():
    """Edge Finder A741 (2026-10-09): the strict dropdown made the script's restore throw, which left the
    cell empty. An empty Pool cell beside a player's Id is as wrong as a typed value, and was not caught."""
    from dfs.edge_finder_tab import ID_COL, POOL_COL

    rows = {("Edge Finder", f"{ID_COL}1:{ID_COL}30"): [["Id"], [""], ["1001"], ["1002"]]}
    formulas = {("Edge Finder", f"{POOL_COL}1:{POOL_COL}30"): [["Pool"], [""], ['=IF($O3="","")'], [""]]}
    issues = run_doctor(
        FakeDoctorClient(_ALL_GOOD_TABS, rows={**_lineups_rows(), **rows}, formulas=formulas),
        _base_config(),
        title="Week 5",
    )
    (found,) = [i for i in issues if i.check == "pool-cell-plain-value"]
    assert f"{POOL_COL}4" in found.detail and "empty" in found.detail


# ---------------------------------------------------------------------------
# column visibility (slice 5 follow-up, 2026-10-09)
# ---------------------------------------------------------------------------


def _visibility_issues(**kw):
    cfg = _base_config()
    client = FakeDoctorClient(tabs=_ALL_GOOD_TABS, rows=_lineups_rows(), **kw)
    return [i for i in run_doctor(client, cfg, title="Week 1") if i.check == "column-visibility"]


def test_doctor_accepts_edgeraw_with_exactly_its_helpers_hidden_and_its_groups_in_place():
    assert _visibility_issues() == []


def test_doctor_flags_edgeraw_spine_columns_hidden_outside_any_group_by_name():
    helpers = set(EDGE_HIDDEN_HELPERS)
    issues = _visibility_issues(hidden_names={"EdgeRaw": helpers | {"Avail", "Flags", "Edge"}})
    assert len(issues) == 1
    for name in ("Avail", "Flags", "Edge"):
        assert name in issues[0].detail
    assert "Name" not in issues[0].detail


def test_doctor_flags_a_zone_label_that_is_hidden():
    header = _ALL_GOOD_TABS["EdgeRaw"]
    label = header[edge_group_spans()[1][0] - 1]  # the column just before the second group
    issues = _visibility_issues(hidden_names={"EdgeRaw": set(EDGE_HIDDEN_HELPERS) | {label}})
    assert len(issues) == 1 and label in issues[0].detail


def test_doctor_flags_a_helper_column_that_is_showing():
    issues = _visibility_issues(hidden_names={"EdgeRaw": set(EDGE_HIDDEN_HELPERS) - {"NameKey", "Id"}})
    assert len(issues) == 1 and "NameKey" in issues[0].detail and "Id" in issues[0].detail


def test_doctor_flags_edgeraw_groups_at_the_old_letters():
    stale = [(a - 3, b - 3, True) for a, b in edge_group_spans()]
    issues = _visibility_issues(groups={"EdgeRaw": stale})
    assert any("belong at" in i.detail for i in issues)


def test_doctor_leaves_an_expanded_group_alone():
    expanded = [(a, b, False) for a, b in edge_group_spans()]
    assert (
        _visibility_issues(groups={"EdgeRaw": expanded}, hidden_names={"EdgeRaw": set(EDGE_HIDDEN_HELPERS)})
        == []
    )


def test_run_doctor_does_not_flag_a_section_header_row_whose_id_cell_says_id():
    """Week 5 (2026-10-09): the matchup section's header row has an empty Pool cell and "Id" in the Id
    column."""
    from dfs.edge_finder_tab import ID_COL, POOL_COL

    rows = {("Edge Finder", f"{ID_COL}1:{ID_COL}30"): [["Id"], [""], ["1001"]]}
    formulas = {("Edge Finder", f"{POOL_COL}1:{POOL_COL}30"): [[""], [""], ['=IF($O3="","")']]}
    issues = run_doctor(
        FakeDoctorClient(_ALL_GOOD_TABS, rows={**_lineups_rows(), **rows}, formulas=formulas),
        _base_config(),
        title="Week 5",
    )
    assert not [i for i in issues if i.check == "pool-cell-plain-value"]


def test_run_doctor_survives_a_blank_row_in_the_id_column():
    from dfs.edge_finder_tab import ID_COL, POOL_COL

    rows = {("Edge Finder", f"{ID_COL}1:{ID_COL}30"): [[], ["Id"], [], ["1001"]]}  # a blank row reads as []
    formulas = {("Edge Finder", f"{POOL_COL}1:{POOL_COL}30"): [[""], [""], [""], ['=IF($O4="","")']]}
    issues = run_doctor(
        FakeDoctorClient(_ALL_GOOD_TABS, rows={**_lineups_rows(), **rows}, formulas=formulas),
        _base_config(),
        title="Week 5",
    )
    assert not [i for i in issues if i.check == "pool-cell-plain-value"]
