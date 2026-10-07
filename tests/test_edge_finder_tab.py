"""The Edge Finder tab layout, its writer, and the Board panel lines."""

import numpy as np
import pandas as pd

from dfs import calibration
from dfs import edge_finder_tab as eft
from dfs import sheet_edge_finder as writer
from dfs.derived import EDGE_COLUMNS


def _edge(rows):
    frame = pd.DataFrame(rows)
    for column in EDGE_COLUMNS:
        if column not in frame.columns:
            frame[column] = "" if column in ("Edge", "Avail") else np.nan
    return frame


def _player(i, pos="WR", **kw):
    base = {
        "Id": i,
        "Name": f"{pos}{i}",
        "Position": pos,
        "Team": "DEN",
        "Salary": 5000 + 100 * i,
        "ProjPts": 10.0 + i / 10,
        "CalPts": 11.0 + i / 10,
        "Hit3x%": 30.0 + i,
        "Boom%": 10.0 + i,
        "Bust%": 40.0,
        "Floor": 4.0,
        "CeilM": 20.0,
        "Own%": 0.0,
        "CalPts%ile": 80.0,
        "Avail": "",
        "Edge": "",
        "OwnStatus": "unpublished",
    }
    return {**base, **kw}


def _players_table(edge):
    return pd.DataFrame(
        {
            "Id": edge["Id"],
            "Games": 3,
            "DkG": 10.0,
            "TdExcess": 0.0,
            "UsageN": 2,
            "GsisId": edge["Id"].astype(str),
        }
    )


def _inputs(edge, **kw):
    base = {
        "edge": edge,
        "players": _players_table(edge),
        "beneficiaries": pd.DataFrame(),
        "matchups": pd.DataFrame(),
        "status": {"week": 5, "calpts_weeks": [1, 2, 3, 4], "calpts_sources": ["ProjPts"], "um_players": 9},
    }
    return eft.Inputs(**{**base, **kw})


def test_cash_core_excludes_unavailable_players_and_those_below_the_position_median():
    edge = _edge(
        [
            _player(1, **{"Hit3x%": 60.0}),
            _player(2, **{"Hit3x%": 55.0, "Avail": "OUT"}),  # out: never listed
            _player(3, **{"Hit3x%": 50.0, "CalPts%ile": 49.9}),  # below the median CalPts
            _player(4, **{"Hit3x%": 45.0}),
            _player(5, **{"Hit3x%": np.nan}),  # no probability: not listed
        ]
    )
    names = eft.cash_core(edge, "WR")["Name"].tolist()
    assert names == ["WR1", "WR4"]


def test_gpp_upside_sorts_by_boom_and_skips_players_with_no_probability():
    edge = _edge([_player(i, **{"Boom%": float(i)}) for i in range(1, 4)] + [_player(9, **{"Boom%": np.nan})])
    assert eft.gpp_upside(edge, "WR")["Name"].tolist() == ["WR3", "WR2", "WR1"]


def test_disagreements_report_the_direction_and_skip_zero_projections():
    edge = _edge(
        [
            _player(1, ProjPts=10.0, CalPts=14.0),
            _player(2, ProjPts=10.0, CalPts=6.0),
            _player(3, ProjPts=0.0, CalPts=3.0),  # TFFB has nothing on him: no disagreement to report
        ]
    )
    # rosterable mask needs enough players; patch it to everyone for this tiny frame
    eft_rosterable = eft.rosterable
    eft.rosterable = lambda e: pd.Series(True, index=e.index)
    try:
        up = eft.disagreements(edge, "WR", "up")
        down = eft.disagreements(edge, "WR", "down")
    finally:
        eft.rosterable = eft_rosterable
    assert up["Name"].tolist()[0] == "WR1" and down["Name"].tolist()[0] == "WR2"
    assert "WR3" not in set(up["Name"]) | set(down["Name"])


def test_the_disagreement_reason_quotes_the_raw_measured_bias_and_n():
    scored = pd.DataFrame(
        [
            {
                "season": 2026,
                "week": 1,
                "Position": "RB",
                "Salary": 4000,
                "ProjPts": 10.0,
                "DkActual": 7.0,
                "RosterablePool": True,
                "Status": "scored",
                "SleeperPts": np.nan,
                "FantasyProsPts": np.nan,
                "AggPts": 10.0,
            }
            for _ in range(60)
        ]
    )
    fitted = calibration.fit(scored, before_week=2, sources=("ProjPts",))
    row = pd.Series({"Position": "RB", "Salary": 4000, "ProjPts": 10.0})
    reason = eft.disagreement_reason(row, fitted)
    assert "TFFB runs -3.0 on RBs <$4.5k (n=60)" in reason  # the raw mean miss, shrinkage undone
    assert eft.disagreement_reason(row, None) == "sources disagree with TFFB"


def test_the_empty_state_layout_has_every_section_and_says_nothing_yet():
    layout = eft.build_layout(None)
    titles = [layout.rows[r - 1][0].split("  ")[0] for r in layout.section_rows]
    assert titles == [
        "CASH CORE",
        "GPP UPSIDE",
        "PROJECTION DISAGREEMENTS",
        "INJURY BENEFICIARIES",
        "MATCHUPS BY POSITION",
        "CONTEXT SIGNALS",
    ]
    assert layout.pool_rows == []
    assert all(len(r) == eft.COLUMN_COUNT for r in layout.rows)


def test_a_full_layout_is_fourteen_wide_caps_sections_and_says_how_many_more():
    edge = _edge([_player(i, **{"Hit3x%": 30.0 + i, "Boom%": 5.0 + i}) for i in range(1, 9)])
    layout = eft.build_layout(_inputs(edge))
    assert all(len(r) == eft.COLUMN_COUNT for r in layout.rows)
    text = [str(c) for r in layout.rows for c in r if c != ""]
    assert any("top 5 of 8 (+3 more)" in t for t in text)
    assert layout.rows[layout.header_rows[0] - 1][:9] == [
        "Name",
        "Pos",
        "Team",
        "Salary",
        "CalPts",
        "ProjPts",
        "Hit3x%",
        "Bust%",
        "Edge",
    ]
    # player rows carry the games column and are all pool-formula rows
    assert len(layout.pool_rows) >= 10


def test_stars_need_published_ownership_a_top_quartile_boom_and_a_bottom_half_own():
    rows = [
        _player(i, **{"Boom%": 10.0 + i, "Own%": 0.20 - i / 100, "OwnStatus": "real"}) for i in range(1, 9)
    ]
    layout = eft.build_layout(_inputs(_edge(rows)))
    gpp_rows = [r for r in layout.rows if r[1] == "WR" and r[0] != "Name" and r[9] == eft.STAR]
    assert gpp_rows, "the highest-Boom, lowest-ownership player should be starred"
    unpublished = eft.build_layout(_inputs(_edge([_player(i, **{"Boom%": 10.0 + i}) for i in range(1, 9)])))
    assert not [r for r in unpublished.rows if r[0] != "Name" and r[9] == eft.STAR]
    assert any("ownership has not published yet" in str(c) for r in unpublished.rows for c in r)


def test_rows_under_three_games_are_marked_muted_by_the_games_column():
    edge = _edge([_player(1), _player(2)])
    inputs = _inputs(edge)
    inputs.players.loc[inputs.players["Id"] == 2, "Games"] = 2
    layout = eft.build_layout(inputs)
    games_col = ord(eft.GAMES_COL) - ord("A")
    muted_names = {layout.rows[r - 1][0] for r in layout.muted_rows}
    assert "WR2" in muted_names and "WR1" not in muted_names
    assert layout.rows[[r for r in layout.pool_rows if layout.rows[r - 1][0] == "WR2"][0] - 1][games_col] == 2


def test_beneficiaries_confirmed_first_then_questionable_muted_with_priced_in():
    edge = _edge([_player(1), _player(2)])
    ben = pd.DataFrame(
        [
            {
                "GsisId": "1",
                "Name": "WR1",
                "Team": "DEN",
                "Position": "WR",
                "OutStatus": "questionable",
                "OutPlayers": "X",
                "tgt_gain": 2.0,
                "car_gain": 0.0,
                "xfp_gain": 3.0,
                "Method": "depth chart",
                "n": 0,
                "Token": "INJ+",
                "PricedIn": "unknown",
            },
            {
                "GsisId": "2",
                "Name": "WR2",
                "Team": "DEN",
                "Position": "WR",
                "OutStatus": "out",
                "OutPlayers": "Y",
                "tgt_gain": 4.0,
                "car_gain": 0.0,
                "xfp_gain": 6.0,
                "Method": "with-or-without",
                "n": 3,
                "Token": "INJ+",
                "PricedIn": "yes",
            },
        ]
    )
    layout = eft.build_layout(_inputs(edge, beneficiaries=ben))
    order = [
        layout.rows[r - 1][0]
        for r in layout.pool_rows
        if layout.rows[r - 1][12] != "" and "out:" in str(layout.rows[r - 1][13])
    ]
    assert order == ["WR2", "WR1"]  # confirmed before questionable
    questionable_row = [
        r
        for r in layout.pool_rows
        if layout.rows[r - 1][0] == "WR1" and "out:" in str(layout.rows[r - 1][13])
    ][0]
    assert questionable_row in layout.muted_rows
    confirmed = [
        layout.rows[r - 1]
        for r in layout.pool_rows
        if layout.rows[r - 1][0] == "WR2" and "out:" in str(layout.rows[r - 1][13])
    ][0]
    assert confirmed[7] == "with-or-without (3 g)" and confirmed[8] == "yes"


def test_the_pool_and_link_formulas_point_at_edgerawand_reference_their_own_row():
    layout = eft.build_layout(_inputs(_edge([_player(1)])))
    rows = writer.tab_rows(layout, "EdgeRaw", 777)
    row = layout.pool_rows[0]
    pool, link = rows[row - 1][10], rows[row - 1][11]
    name = writer._name_letter()
    assert pool == f'=IFERROR(INDEX(EdgeRaw!$A:$A,MATCH($A{row},EdgeRaw!${name}:${name},0)),"")'
    assert "#gid=777" in link and f"MATCH($A{row}," in link and link.startswith("=IFERROR(HYPERLINK(")
    assert rows[row - 1][0] == "WR1"  # other cells untouched


class _Recorder:
    def __init__(self):
        self.tab_written = None
        self.rules = []
        self.scales = []
        self.formats = []
        self.freeze_args = None

    def tab_exists(self, tab):
        return True

    def tab_gid(self, tab):
        return 5

    def write_tab(self, tab, rows):
        self.tab_written = (tab, rows)
        return len(rows)

    def clear_conditional_formats(self, tab):
        self.cleared = True

    def format_range(self, tab, rng, fmt):
        self.formats.append((rng, fmt))

    def set_column_widths(self, tab, widths):
        self.widths = widths

    def add_boolean_rule(self, tab, rng, *, condition_type, values, fmt):
        self.rules.append((rng, condition_type, values))

    def add_color_scale(self, tab, rng, **kw):
        self.scales.append((rng, kw))

    def freeze(self, tab, **kw):
        self.freeze_args = kw


def test_the_writer_adds_relative_row_rules_so_colour_survives_a_sort_or_filter():
    client = _Recorder()
    edge = _edge([_player(i) for i in range(1, 4)])
    message = writer.write_tab(client, _inputs(edge), edge_tab="EdgeRaw")
    assert "player rows" in message and client.tab_written[0] == "Edge Finder"
    muting = [r for r in client.rules if r[1] == "CUSTOM_FORMULA"]
    assert len(muting) == 1 and muting[0][2][0].startswith("=AND(ISNUMBER($M") and "$M" in muting[0][2][0]
    chip_texts = {v[0] for _rng, kind, v in client.rules if kind == "TEXT_CONTAINS"}
    assert chip_texts == {"INJ+", "BUY↑", "FADE↓", "USAGE↑", "USAGE↓"}
    assert client.scales, "Hit3x% / Boom% / Bust% get a gradient"
    assert client.freeze_args == {"rows": 0, "cols": 1}  # no frozen header row: sections are separate blocks


def test_the_empty_state_writes_without_error_and_ensure_tab_leaves_an_existing_tab_alone():
    client = _Recorder()
    assert "written" in writer.write_tab(client, None)
    assert "already present" in writer.ensure_tab(client)
    assert client.tab_written[0] == "Edge Finder"


def test_board_panel_is_five_lines_one_each_and_says_none_when_empty():
    assert eft.board_panel_lines(None)[0].startswith("Not synced yet")
    edge = _edge([_player(i, **{"Hit3x%": 30.0 + i, "Boom%": 10.0 + i}) for i in range(1, 6)])
    lines = eft.board_panel_lines(_inputs(edge))
    assert len(lines) == eft.BOARD_PANEL_LINES == 5
    assert lines[0].startswith("Cash core: ") and "WR5" in lines[0]
    assert lines[3] == "Injury beneficiaries: none" and lines[4] == "Best offense per position: none"
