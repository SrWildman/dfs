"""Contests tab: the tables the engine writes, the pure layout, then the writer against a recording fake."""

from pathlib import Path

import pandas as pd
import pytest

from dfs import sheet_contests as sc
from dfs.contests import analysis

FIXTURES = Path(__file__).parent / "contests" / "fixtures"
SEASON = 2025


@pytest.fixture(scope="module")
def tables(tmp_path_factory):
    out = tmp_path_factory.mktemp("contests")
    result = analysis.run(
        [FIXTURES / "history_export.csv"],
        out,
        standings_dir=FIXTURES / "standings",
        players=FIXTURES / "players_week.csv",
        me="test_me",
        fetch_season=None,
        index_candidate="none",
    )
    analysis.write(result)
    return sc.load_tables(out)


def _text(layout):
    return [str(c) for row in layout.rows for c in row if c != ""]


def test_every_row_is_padded_to_the_tab_width_and_the_last_column_matches():
    layout = sc.build_layout(sc.Tables(*[pd.DataFrame()] * 6), SEASON)
    assert all(len(row) == sc.COLUMN_COUNT for row in layout.rows)
    assert sc.LAST_COLUMN == chr(ord("A") + sc.COLUMN_COUNT - 1)


def test_an_empty_log_still_builds_every_section_with_a_plain_sentence():
    layout = sc.build_layout(sc.Tables(*[pd.DataFrame()] * 6), SEASON)
    titles = [layout.rows[r - 1][0].split("  ")[0] for r in layout.section_rows]
    assert [t.split()[0] for t in titles] == ["SERIES", "CASH", "HOW", "WHAT", "HEAD", "LEFT"]
    assert "No Classic entries logged this season yet." in _text(layout)


def test_sections_come_in_the_decided_order(tables):
    layout = sc.build_layout(tables, SEASON)
    titles = [layout.rows[r - 1][0] for r in layout.section_rows]
    assert [t.split("  ")[0] for t in titles] == [
        "THIS WEEK, YOUR DECISIONS",
        "SERIES YOU PLAY, 2025",
        "CASH LINES FROM YOUR STANDINGS FILES",
        "HOW THE FIELD BUILDS",
        "WHAT WINS IN YOUR CONTESTS",
        "HEAD TO HEAD",
        "LEFT OUT OF THE ROI ABOVE",
    ]
    assert layout.rows[0][0] == "Contests"


def test_the_series_table_has_one_row_per_series_with_roi_and_cash_rate(tables):
    series = sc.series_rows(tables, SEASON).set_index("series")
    fair = series.loc["NFL $8K Fair Catch [Single Entry]"]
    mine = tables.typed[
        (tables.typed["series"] == "NFL $8K Fair Catch [Single Entry]") & (tables.typed["season"] == SEASON)
    ]
    assert fair["format"] == "SE GPP" and fair["played"] == len(mine) >= 1
    assert fair["roi"] == pytest.approx((mine["winnings"].sum() - mine["fee"].sum()) / mine["fee"].sum())
    assert 0.0 <= fair["cash_rate"] <= 1.0
    assert not series["format"].isin(["Freeroll", "Showdown", "Private", "Other"]).any()


def test_a_series_toughness_note_explains_why_gaps_replace_the_engines_number(tables):
    text = " ".join(_text(sc.build_layout(tables, SEASON)))
    assert "not separable yet" in text and "same weeks" in text


def test_the_cash_line_gap_is_against_the_other_series_of_the_same_format_in_the_same_week():
    profiles = pd.DataFrame(
        [
            {
                "contest_id": "1",
                "season": 2026,
                "week": 5,
                "format": "Double Up",
                "series": "A",
                "kind": "Classic",
                "cash_line": 153.0,
            },
            {
                "contest_id": "2",
                "season": 2026,
                "week": 5,
                "format": "Double Up",
                "series": "B",
                "kind": "Classic",
                "cash_line": 147.0,
            },
            {
                "contest_id": "3",
                "season": 2026,
                "week": 5,
                "format": "Double Up",
                "series": "C",
                "kind": "Classic",
                "cash_line": 141.0,
            },
            {
                "contest_id": "4",
                "season": 2026,
                "week": 4,
                "format": "Double Up",
                "series": "A",
                "kind": "Classic",
                "cash_line": 106.0,
            },
            {
                "contest_id": "5",
                "season": 2026,
                "week": 4,
                "format": "SE GPP",
                "series": "S",
                "kind": "Classic",
                "cash_line": 130.0,
            },
        ]
    )
    gaps = sc.peer_gaps(profiles)
    assert gaps[("Double Up", "A")] == (
        pytest.approx(9.0),
        1,
    )  # 153 against the mean of 147 and 141; week 4 has no peer
    assert gaps[("Double Up", "C")][0] == pytest.approx(-9.0)
    assert ("SE GPP", "S") not in gaps  # alone in its format that week
    assert sc.latest_cash_lines(profiles)[("Double Up", "A")] == "153.0 (Wk 5)"


def test_how_the_field_builds_has_field_top_ten_and_you_rows_per_format(tables):
    fields = sc.field_by_format(tables)
    assert fields, "the fixtures hold a Classic contest with a players frame"
    for frame in fields.values():
        assert list(frame["label"]) == ["field", "top 10%", "you"][: len(frame)]


def test_head_to_head_bands_and_the_how_to_line_are_on_the_tab(tables):
    text = " ".join(_text(sc.build_layout(tables, SEASON)))
    assert "Win rate" in text and sc.H2H_USE in text


def test_no_cell_can_be_read_by_sheets_as_a_formula(tables):
    cells = [c for row in sc.build_layout(tables, SEASON).rows for c in row if isinstance(c, str)]
    assert not [c for c in cells if c.startswith(("=", "+", "@"))]
    assert sc._clean("=SUM(A1)") == "'=SUM(A1)"


def test_every_header_on_the_tab_has_a_note(tables):
    from dfs.sheet_column_notes import CONTESTS_NOTES

    layout = sc.build_layout(tables, SEASON)
    headers = {str(c).strip() for r in layout.header_rows for c in layout.rows[r - 1] if c != ""}
    assert not [h for h in headers if h not in CONTESTS_NOTES], "a header with no hover note"


class _Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return record


def test_the_writer_resets_the_tab_then_styles_it_and_sets_header_notes(tables):
    layout = sc.build_layout(tables, SEASON)
    client = _Recorder()
    summary = sc.write_contests(client, layout)
    names = [c[0] for c in client.calls]
    assert names[0] == "write_tab" and client.calls[0][1][0] == sc.CONTESTS_TAB
    reset = next(c for c in client.calls if c[0] == "format_range")
    assert reset[1][2]["numberFormat"] is None and reset[1][2]["horizontalAlignment"] is None
    assert "clear_notes" in names and "clear_conditional_formats" in names and "set_row_notes" in names
    assert "built" in summary


def _winners_table():
    return pd.DataFrame(
        {
            "measure": [
                "Stack + a bring-back",
                "QB + 1 or more teammates (any position)",
                "Ownership sum of the nine",
                "Ownership product of the nine (median)",
                "Salary left ($)",
            ],
            "kind": ["share", "share", "mean", "median", "mean"],
            "top1": [0.68, 0.96, 1.21, 2.0e-8, 125.0],
            "field": [0.52, 0.89, 1.40, 4.0e-8, 120.0],
            "diff": [0.16, 0.07, -0.19, -2.0e-8, 5.0],
            "n_top1": [333] * 5,
            "n_field": [33041] * 5,
            "contests": [9] * 5,
        }
    )


def test_what_wins_lists_each_measure_with_the_gap_and_calls_out_the_biggest_ones():
    layout = sc.build_layout(sc.Tables(*[pd.DataFrame()] * 6, winners=_winners_table()), SEASON)
    text = _text(layout)
    rows = {str(r[0]): r for r in layout.rows}
    assert rows["Stack + a bring-back"][1:4] == [0.68, 0.52, 0.16]
    assert rows["Ownership product of the nine (median)"][1:4] == ["2.0e-08", "4.0e-08", "0.50x"]
    assert any(
        t.startswith("Biggest gaps (top 1% minus the field): stack + a bring-back +16 pts") for t in text
    )
    assert "directional, not proof" in " ".join(text)
    from dfs.sheet_column_notes import CONTESTS_NOTES

    assert set(sc.WINNERS_HEADERS) <= set(CONTESTS_NOTES)


def test_what_wins_says_so_plainly_with_no_large_gpp_standings():
    layout = sc.build_layout(sc.Tables(*[pd.DataFrame()] * 6), SEASON)
    assert "No large GPP standings with lineups yet (700+ entries)." in _text(layout)


def _entries(**kw):
    base = {
        "contest_id": ["a", "b", "c", "d"],
        "season": [2026] * 4,
        "week": [5] * 4,
        "series": [
            "NFL GIANT $25 Double Up [Single Entry]",
            "NFL GIANT $10 Double Up [Single Entry]",
            "NFL $8K Fair Catch [Single Entry]",
            "NFL $4K Singleback [5 Entry Max]",
        ],
        "format": ["Double Up", "Double Up", "SE GPP", "N-max GPP"],
        "cls": ["cash", "cash", "gpp", "gpp"],
        "points": [152.6, 152.6, 181.5, 110.3],
        "pct": [0.44, 0.38, 0.02, 0.82],
        "cashed": [False, True, True, False],
        "margin": [-0.5, 5.1, 27.0, -42.0],
        "copies": [4, 5, 0, 0],
        "sib_total": [0, 0, 6, 6],
        "sib_cashed": [0, 0, 6, 0],
    }
    return pd.DataFrame({**base, **kw})


def _post_mortem(**extra):
    swings = pd.DataFrame(
        {
            "season": [2026],
            "week": [5],
            "contest_id": ["c"],
            "series": ["NFL $8K Fair Catch [Single Entry]"],
            "player": ["Christian Watson"],
            "player_own": [0.096],
            "player_pts": [35.3],
            "alt": ["Michael Wilson"],
            "alt_own": [0.545],
            "alt_pts": [7.28],
            "swing": [28.02],
        }
    )
    pool = pd.DataFrame(
        {"season": [2026], "week": [5], "pooled": [40], "pooled_hits": [18], "likes": [12], "likes_hits": [4]}
    )
    return sc.Tables(*[pd.DataFrame()] * 6, decisions=_entries(), swings=swings, pool_hits=pool, **extra)


def test_the_monday_post_mortem_is_five_lines_numbers_first_at_the_top_of_the_tab():
    SEASON = 2026
    layout = sc.build_layout(_post_mortem(), SEASON)
    assert layout.rows[layout.section_rows[0] - 1][0] == "THIS WEEK, YOUR DECISIONS  -  Week 5"
    week, lines = sc.decision_lines(_post_mortem(), SEASON)
    assert week == 5 and [line.split(":")[0] for line in lines] == [
        "Cash",
        "GPP",
        "Biggest swing",
        "Pool hit rate",
        "Contest choice",
    ]
    cash, gpp, swing, pool, choice = lines
    assert "GIANT $10 Double Up cashed by 5.1" in cash and "GIANT $25 Double Up missed by 0.5" in cash
    assert "6 identical entries (including yours)" in cash  # the most copies is 5 others
    assert "2 entries, average finish top 42%, 1 cashed, best top 2% in $8K Fair Catch" in gpp
    assert "Christian Watson (10% owned, 35.3 pts) instead of Michael Wilson (55%, 7.3 pts)" in swing
    assert "won 28.0 points" in swing
    assert "18 of 40 pooled players beat their CalPts (45%), against 4 of 12 model favourites" in pool
    assert "cashed in 6 of 12 (lineup, other contest) pairs" in choice


def test_the_post_mortem_shows_a_dash_for_what_it_has_no_data_for():
    tables = sc.Tables(*[pd.DataFrame()] * 6, decisions=_entries().iloc[:2])  # cash only, no swing, no pool
    _, lines = sc.decision_lines(tables, 2026)
    assert lines[1].startswith("GPP: - (no GPP entries in Week 5)")
    assert lines[2] == "Biggest swing: -." and lines[3].startswith("Pool hit rate: - (not logged for Week 5")
    assert lines[4].startswith("Contest choice: - (no other contest")
    assert sc.decision_lines(sc.Tables(*[pd.DataFrame()] * 6), 2026) == (None, [])


def test_the_season_block_adds_the_same_measures_up():
    rows = {r[0]: r[1:] for r in sc.decision_season_rows(_post_mortem(), 2026)}
    assert rows["Cash entries cashed"] == ["1 of 2 (50%)", "1 week(s)"]
    assert rows["GPP: average finish"] == ["top 42%", "2 entries"]
    assert rows["Biggest weekly swing: won / cost"] == ["1 won, 0 cost", "1 week(s)"]
    assert rows["Pool hit rate (pooled / model favourites left out)"] == ["45% / 33%", "1 week(s) logged"]
    assert rows["Same lineups cashing in other contests"] == ["6 of 12 pairs", "2 entries"]
    assert set(sc.DECISIONS_HEADERS) <= set(
        __import__("dfs.sheet_column_notes", fromlist=["x"]).CONTESTS_NOTES
    )
