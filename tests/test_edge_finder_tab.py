"""The Edge Finder tab layout, its writer, and the Board panel lines."""

import numpy as np
import pandas as pd

from dfs import calibration, usage_r6
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
            "UsageN": 8,
            "GsisId": edge["Id"].astype(str),
        }
    )


def _inputs(edge, **kw):
    base = {
        "edge": edge,
        "players": _players_table(edge),
        "beneficiaries": pd.DataFrame(),
        "matchups": pd.DataFrame(),
        "status": {"week": 5, "calpts_weeks": [1, 2, 3, 4], "calpts_sources": ["ProjPts"]},
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


def _scored_rb_rows(n):
    return pd.DataFrame(
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
            for _ in range(n)
        ]
    )


def test_the_disagreement_reason_quotes_the_applied_shrunk_bias_and_n():
    fitted = calibration.fit(_scored_rb_rows(60), before_week=2, sources=("ProjPts",))
    applied = fitted.bias[("ProjPts", "RB", 0)]  # -3.0 * 60 / 90, shrunk
    row = pd.Series({"Position": "RB", "Salary": 4000, "ProjPts": 10.0, "CalPts": round(10.0 + applied, 1)})
    reason = eft.disagreement_reason(row, fitted)
    assert f"{round(applied, 1):+.1f} from TFFB's measured bias on RBs <$4.5k (n=60)" in reason
    assert "-3.0" not in reason  # the raw miss is not what was applied
    assert eft.disagreement_reason(row, None) == "sources disagree with TFFB"


def test_a_thin_bucket_says_it_was_shrunk():
    fitted = calibration.fit(_scored_rb_rows(11), before_week=2, sources=("ProjPts",))
    applied = fitted.bias[("ProjPts", "RB", 0)]
    assert abs(applied) < 1.0  # -3.0 * 11/41, not the raw -3.0
    row = pd.Series({"Position": "RB", "Salary": 4000, "ProjPts": 10.0, "CalPts": round(10.0 + applied, 1)})
    assert "(n=11; shrunk, small sample)" in eft.disagreement_reason(row, fitted)


def test_the_reason_splits_the_gap_into_bias_and_sources_and_names_a_missing_source():
    scored = _scored_rb_rows(60).assign(SleeperPts=10.0, FantasyProsPts=10.0)
    fitted = calibration.fit(scored, before_week=2)
    row = pd.Series(
        {
            "Position": "RB",
            "Salary": 4000,
            "ProjPts": 10.0,
            "SleeperPts": 14.0,
            "FantasyProsPts": np.nan,
            "CalPts": 12.0,
        }
    )
    why = calibration.explain_gap(row, fitted)
    assert why is not None and abs(why.bias + why.sources - 2.0) < 0.06  # the two parts add up to the gap
    reason = eft.disagreement_reason(row, fitted)
    assert "because Sleeper projects higher" in reason
    assert "no FantasyPros projection for him" in reason


def _col(letter):
    return ord(letter) - ord("A")


NAME = _col(eft.NAME_COL)  # the player's name column
S0 = len(eft.LEAD)  # the first section-specific slot


def _rows_named(layout, name):
    return [r for r in layout.player_rows if layout.rows[r - 1][NAME] == name]


def _why(layout, row):
    """The full reason behind a row's shortened Why cell (it is the cell's note)."""
    return layout.notes[row]


def _row_text(row):
    """A text row as it reads on the sheet: the label in A, then the description from column C."""
    return " ".join(str(c) for c in (row[0], row[eft.DETAIL_INDEX]) if str(c))


def _group(layout, key):
    return [g for g in layout.groups if g.key == key]


def test_the_empty_state_layout_has_every_section_and_says_nothing_yet():
    layout = eft.build_layout(None)
    titles = [layout.rows[r - 1][0] for r in layout.section_rows]  # the label; the description is in C
    assert titles == [
        "CASH CORE",
        "GPP UPSIDE",
        "LEVERAGE PLAYS",
        "CHALK TO FADE OR EAT",
        "PUNT PLAYS",
        "PROJECTION DISAGREEMENTS",
        "INJURY BENEFICIARIES",
        "USAGE TRENDS",
        "MATCHUPS (CONTEXT)",
        "CONTEXT SIGNALS",
    ]
    assert layout.player_rows == [] and layout.groups == []
    assert all(len(r) == eft.COLUMN_COUNT for r in layout.rows)


def test_the_columns_are_pool_name_pos_team_salary_own_numbers_do_why_id_and_a_hidden_key():
    assert eft.LEAD == ["Pool", "Name", "Pos", "Team", "Salary", "Own%"]
    assert eft.TRAILING_HEADERS == ["Do", "Why", "Id"]
    assert [
        eft.POOL_COL,
        eft.NAME_COL,
        eft.OWN_COL,
        eft.DO_COL,
        eft.WHY_COL,
        eft.ID_COL,
        eft.KEY_COL,
    ] == list("ABFMNOP")
    layout = eft.build_layout(_inputs(_edge([_player(1)])))
    header = layout.rows[layout.header_rows[0] - 1]
    assert header[:12] == [
        "Pool",
        "Name",
        "Pos",
        "Team",
        "Salary",
        "Own%",
        "CalPts",
        "Hit3x%",
        "Bust%",
        "ProjPts",
        "",  # the unused slot sits before Edge, which is always the last slot
        "Edge",
    ]
    assert header[12:15] == ["Do", "Why", "Id"]  # Why is last of the visible columns
    assert "Games" not in header and "Set" not in header and "↗" not in header


def test_the_counts_are_the_rosterable_pool_not_every_listing():
    rows = [_player(i, **{"Hit3x%": 30.0 + i}) for i in range(1, 11)]
    edge = _edge(rows)
    saved = eft.rosterable
    eft.rosterable = lambda e: e["Id"] > 4  # players 1-4 are DraftKings scrubs
    try:
        layout = eft.build_layout(_inputs(edge))
    finally:
        eft.rosterable = saved
    subs = [layout.rows[r - 1][0] for r in layout.subheader_rows]
    assert "WR · top 6 of 6" in subs  # 10 listed, 6 rosterable
    names = {layout.rows[r - 1][NAME] for r in layout.player_rows}
    assert not names & {"WR1", "WR2", "WR3", "WR4"}


def test_visible_rows_follow_roster_need_and_the_rest_sit_in_a_collapsed_nested_group():
    edge = _edge([_player(i, **{"Hit3x%": 20.0 + i}) for i in range(1, 31)])  # 30 receivers
    layout = eft.build_layout(_inputs(edge))
    assert eft.VISIBLE_PER_POSITION == {"QB": 6, "RB": 10, "WR": 12, "TE": 6, "DST": 6}
    sub = [layout.rows[r - 1][0] for r in layout.subheader_rows if layout.rows[r - 1][0].startswith("WR")][0]
    assert sub == "WR · top 12 of 30"
    header = [r for r in layout.overflow_rows if "WRs" in layout.rows[r - 1][NAME]][0]
    assert layout.rows[header - 1][NAME] == "▸ 18 more WRs (click + to show)"
    (overflow,) = _group(layout, "CASH CORE|WR|more")
    assert (overflow.first, overflow.last, overflow.depth, overflow.collapsed) == (
        header + 1,
        header + 18,
        2,
        True,
    )
    assert layout.rows[header - 1][_col(eft.KEY_COL)] == "CASH CORE|WR|more"  # the key sits on the row above
    (section,) = _group(layout, "CASH CORE")
    assert section.depth == 1 and section.collapsed is False
    assert section.first <= overflow.first and overflow.last <= section.last  # nested inside the section


def test_every_rosterable_player_is_written_up_to_forty_a_position():
    edge = _edge([_player(i, **{"Hit3x%": 20.0 + i}) for i in range(1, 61)])
    layout = eft.build_layout(_inputs(edge))
    cash_wr = [
        r for r in layout.player_rows if layout.rows[r - 1][S0] != "" and layout.rows[r - 1][S0 + 1] != ""
    ]
    (overflow,) = _group(layout, "CASH CORE|WR|more")
    assert overflow.last - overflow.first + 1 == eft.MAX_PER_POSITION - 12
    assert any("top 12 of 60" in str(r[0]) for r in layout.rows)  # the header counts the whole pool
    assert cash_wr


def test_the_verbs_follow_the_rules_and_there_is_no_rank_column_the_rows_are_in_order():
    edge = _edge(
        [
            _player(1, **{"Hit3x%": 60.0, "Bust%": 10.0}),
            _player(2, **{"Hit3x%": 55.0, "Bust%": 50.0}),  # top 3 Hit3x% but a high bust: only an option
            _player(3, **{"Hit3x%": 50.0, "Bust%": 10.0}),
            _player(4, **{"Hit3x%": 45.0, "Bust%": 30.0}),  # rank 4: an option
        ]
    )
    layout = eft.build_layout(_inputs(edge))
    do = _col(eft.DO_COL)
    by_name = {layout.rows[r - 1][NAME]: layout.rows[r - 1] for r in layout.player_rows[:4]}
    assert not any("Rank" in str(c) or str(c).startswith("#") for row in layout.rows for c in row)
    assert list(by_name) == ["WR1", "WR2", "WR3", "WR4"]  # the order is the rank
    assert [by_name[n][do] for n in ("WR1", "WR2", "WR3", "WR4")] == [
        "Cash add",
        "Cash option",
        "Cash add",
        "Cash option",
    ]


def _gpp_upside_rows(layout):
    """The player rows of GPP UPSIDE alone (the Leverage section repeats some of the same players)."""
    start = [r for r in layout.section_rows if layout.rows[r - 1][0].startswith("GPP UPSIDE")][0]
    end = [r for r in layout.section_rows if layout.rows[r - 1][0].startswith("LEVERAGE PLAYS")][0]
    return [r for r in layout.player_rows if start < r < end]


def test_gpp_verbs_add_for_a_top_quartile_boom_and_leverage_adds_the_star():
    rows = [
        _player(i, **{"Boom%": 10.0 + i, "Own%": 0.20 - i / 100, "OwnStatus": "real"}) for i in range(1, 9)
    ]
    layout = eft.build_layout(_inputs(_edge(rows)))
    verbs = {layout.rows[r - 1][NAME]: layout.rows[r - 1][_col(eft.DO_COL)] for r in _gpp_upside_rows(layout)}
    assert verbs["WR8"] == f"GPP leverage {eft.STAR}"  # highest Boom%, lowest ownership, Lev far above 25
    assert verbs["WR1"] == "GPP option"
    unpublished = eft.build_layout(_inputs(_edge([_player(i, **{"Boom%": 10.0 + i}) for i in range(1, 9)])))
    assert not any(eft.STAR in str(c) for r in unpublished.rows for c in r[S0:])
    assert any("ownership not out yet" in note for note in unpublished.notes.values())
    assert any(eft.OWN_NOT_OUT in _row_text(r) for r in unpublished.rows)  # the status line says so too


def test_a_player_row_carries_the_hidden_id_a_short_why_the_full_note_and_a_do_verb():
    layout = eft.build_layout(_inputs(_edge([_player(7)])))
    row_number = _rows_named(layout, "WR7")[0]
    row = layout.rows[row_number - 1]
    assert row[_col(eft.ID_COL)] == 7 and row[_col(eft.POOL_COL)] == ""  # the writer fills the Pool formula
    assert (
        row[_col(eft.WHY_COL)] == ""
    )  # nothing to add (no calibration): blank rather than repeat CalPts/ProjPts
    assert "to reach 3x salary" in _why(layout, row_number)  # the whole reason is the note
    assert row[_col(eft.DO_COL)] in ("Cash add", "Cash option")


def test_the_shown_why_is_at_most_sixty_characters_and_cash_never_mentions_ownership():
    edge = _edge(
        [_player(i, **{"Own%": 0.1 + i / 100, "OwnStatus": "real", "Hit3x%": 20.0 + i}) for i in range(1, 25)]
    )
    layout = eft.build_layout(_inputs(edge))
    why = _col(eft.WHY_COL)
    assert layout.player_rows
    assert all(len(layout.rows[r - 1][why]) <= eft.SHORT_WHY_CHARS for r in layout.player_rows)
    cash_start = [r for r in layout.section_rows if layout.rows[r - 1][0].startswith("CASH CORE")][0]
    gpp_start = [r for r in layout.section_rows if layout.rows[r - 1][0].startswith("GPP UPSIDE")][0]
    cash_rows = [r for r in layout.player_rows if cash_start < r < gpp_start]
    assert cash_rows and all("own" not in layout.rows[r - 1][why].lower() for r in cash_rows)
    assert all("own" not in _why(layout, r).lower() for r in cash_rows)
    assert eft.short_why("") == "" and eft.short_why("a; b") == "a"
    long = eft.short_why("word " * 30)
    assert len(long) <= eft.SHORT_WHY_CHARS and long.endswith("…")


def test_rows_under_three_games_are_muted_and_the_why_says_how_little_data():
    edge = _edge([_player(1), _player(2)])
    inputs = _inputs(edge)
    inputs.players.loc[inputs.players["Id"] == 2, "Games"] = 2
    layout = eft.build_layout(inputs)
    muted = {layout.rows[r - 1][NAME] for r in layout.muted_rows}
    assert "WR2" in muted and "WR1" not in muted
    row = _rows_named(layout, "WR2")[0]
    assert _why(layout, row).endswith("only 2 games of data")
    assert layout.rows[row - 1][_col(eft.WHY_COL)].endswith("2 games")


def test_a_thin_week_verdict_needs_history_and_a_best_below_the_typical_best():
    edge = _edge([_player(1, **{"Hit3x%": 34.0}), _player(2, **{"Hit3x%": 20.0})])
    none = eft.build_layout(_inputs(edge))
    assert not none.verdict_rows  # no history: no verdict, never invented
    layout = eft.build_layout(_inputs(edge, history={"WR": (41.0, 4)}))
    (row,) = layout.verdict_rows
    # the label fits the frozen pane; the detail starts in column C
    assert layout.rows[row - 1][0] == "Thin week at WR"
    assert layout.rows[row - 1][eft.DETAIL_INDEX] == "best cash odds 34% (typical best ~41%, 4 earlier weeks)"
    strong = eft.build_layout(_inputs(edge, history={"WR": (30.0, 4)}))
    assert not strong.verdict_rows
    assert eft.cash_verdict("RB", 10.0, {"WR": (41.0, 4)}) is None


def test_the_probability_colour_is_per_position_block_with_best_green():
    edge = _edge([_player(i, pos="WR") for i in range(1, 4)] + [_player(i, pos="RB") for i in range(11, 14)])
    layout = eft.build_layout(_inputs(edge))
    cash = [b for b in layout.prob_blocks if b[0] == "Hit3x%"]
    assert len(cash) == 2  # one block per position, not one range for the whole section
    assert all(not lower for _, _, _, lower in cash)
    assert [lower for name, _, _, lower in layout.prob_blocks if name == "Bust%"] == [True, True]


def test_beneficiaries_confirmed_first_then_questionable_muted_with_a_blank_priced_in_when_unknown():
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
                "car_gain": 0.0,
                "xfp_gain": 3.0,
                "Method": "measured table",
                "n": 0,
                "Token": "INJ+",
                "PricedIn": "",
            },
            {
                "GsisId": "2",
                "Name": "WR2",
                "Team": "DEN",
                "Position": "WR",
                "OutStatus": "out",
                "OutPlayers": "Y",
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
    do = _col(eft.DO_COL)
    injury = [r for r in layout.player_rows if layout.rows[r - 1][do] in ("Bump ▲", "Watch")]
    assert [layout.rows[r - 1][NAME] for r in injury] == ["WR2", "WR1"]  # confirmed before questionable
    assert [layout.rows[r - 1][do] for r in injury] == ["Bump ▲", "Watch"]
    assert injury[1] in layout.muted_rows
    confirmed, questionable = (layout.rows[r - 1] for r in injury)
    assert confirmed[S0 + 2] == "w/wo (3 g)" and confirmed[S0 + 3] == "yes"  # Method, Priced?
    assert questionable[S0 + 3] == ""  # unknown reads blank, not the word "unknown"
    assert "Y out: +0.0 carries and +6.0 expected points a game" in _why(layout, injury[0])
    assert layout.rows[injury[0] - 1][_col(eft.WHY_COL)].startswith("Y out")
    assert "Gain Tgt/G" not in eft.BENEFICIARY_COLUMNS  # no target gains, ever


def test_disagreements_show_the_three_sources_the_diff_and_a_look_closer_or_caution_verb():
    edge = _edge(
        [
            _player(1, ProjPts=10.0, CalPts=14.0, SleeperPts=13.0, FantasyProsPts=np.nan),
            _player(2, ProjPts=10.0, CalPts=6.0),
        ]
    )
    layout = eft.build_layout(_inputs(edge))
    do = _col(eft.DO_COL)
    rows = {
        (layout.rows[r - 1][NAME], layout.rows[r - 1][do]): layout.rows[r - 1]
        for r in layout.player_rows
        if layout.rows[r - 1][do] in ("Look closer ▲", "Caution ▼")
    }
    up, down = rows[("WR1", "Look closer ▲")], rows[("WR2", "Caution ▼")]
    assert (
        up[S0 : S0 + 4] == [10.0, 13.0, "", 14.0] and up[S0 + 4] == 4.0
    )  # TFFB, Sleeper, FantasyPros, CalPts, Diff
    assert down[S0] == 10.0 and down[S0 + 3] == 6.0 and down[S0 + 4] == -4.0
    assert (
        layout.rows[[r for r in layout.header_rows if "Sleeper" in layout.rows[r - 1]][0] - 1][S0 : S0 + 5]
        == eft.DISAGREE_COLUMNS[:5]
    )


def test_punt_plays_are_the_best_value_within_a_thousand_of_the_cheapest_salary():
    edge = _edge(
        [
            _player(1, Salary=2500, ValAdj=1.0),
            _player(2, Salary=3400, ValAdj=3.0),
            _player(3, Salary=3600, ValAdj=9.0),  # more than $1,000 above the cheapest: not a punt
            _player(4, Salary=2600, ValAdj=2.0, Avail="OUT"),
        ]
    )
    part, floor = eft.punt_plays(edge, "WR")
    assert floor == 2500 and part["Name"].tolist() == ["WR2", "WR1"]
    layout = eft.build_layout(_inputs(edge))
    punt = [r for r in layout.player_rows if layout.rows[r - 1][_col(eft.DO_COL)] == "Punt option"]
    assert [layout.rows[r - 1][NAME] for r in punt] == ["WR2", "WR1"]


def _trends(**kw):
    base = {
        "GsisId": "7",
        "Position": "WR",
        "Metric": "Tgt%",
        "Recent": 0.27,
        "Prior": 0.18,
        "Change": 0.09,
        "Threshold": 0.0836,
        "Z": 1.08,
        "Direction": "▲",
        "FlagRate": 0.15,
        "EarlierGames": 12,
    }
    return pd.DataFrame([{**base, **kw}])


def _r6(signals=None, trends=None, short_games=0):
    chips = {c.id: c for c in usage_r6.load_chips()}
    return eft.R6View(
        chips=chips,
        signals=signals or {},
        trends=trends if trends is not None else pd.DataFrame(columns=usage_r6.TREND_COLUMNS),
        min_games=9,
        short_games=short_games,
    )


def test_usage_trends_use_r6s_measured_band_with_a_plain_why_and_a_watch_verb():
    edge = _edge([_player(7)])
    layout = eft.build_layout(_inputs(edge, r6=_r6(trends=_trends())))
    row = layout.rows[_rows_named(layout, "WR7")[-1] - 1]
    # last 3, earlier, change: numbers (so they right-align), each row formatted by its metric's unit
    assert row[S0 : S0 + 5] == ["Tgt%", 0.27, 0.18, 9.0, "▲"]
    row_no = _rows_named(layout, "WR7")[-1]
    shown = {c: (p, k) for c, p, k in layout.cell_formats if int(c.lstrip("ABCDEFGHIJKLMNOP")) == row_no}
    assert shown == {
        f"{eft.column_letter(S0 + 1)}{row_no}": ("0%", "PERCENT"),
        f"{eft.column_letter(S0 + 2)}{row_no}": ("0%", "PERCENT"),
        f"{eft.column_letter(S0 + 3)}{row_no}": ('+0" pts";-0" pts";0" pts"', "NUMBER"),
    }
    assert row[_col(eft.DO_COL)] == "Watch"
    assert row[_col(eft.WHY_COL)] == "A bigger jump than 85% of weeks · often fades"
    why = _why(layout, _rows_named(layout, "WR7")[-1])
    assert why.startswith("Tgt% 18% → 27% over the last 3 (▲, a bigger jump than 85% of weeks).")
    assert "Historically projections over-react to jumps like this." in why
    quiet = eft.build_layout(_inputs(edge, r6=_r6(trends=_trends(Direction=""))))
    assert any("moved by more than R6's measured band" in _row_text(r) for r in quiet.rows)


def test_a_player_too_new_for_an_arrow_is_counted_not_guessed():
    edge = _edge([_player(7)])
    layout = eft.build_layout(_inputs(edge, r6=_r6(short_games=4)))
    text = " ".join(_row_text(r) for r in layout.rows)
    assert "Not enough games 4 pool players have fewer than 9 earlier games, so they get no arrow" in text
    missing = eft.build_layout(_inputs(edge))  # no R6 data at all: the tab says how to get it
    assert any("need the R6 data" in _row_text(r) for r in missing.rows)
    assert any("R6 usage signals are not available this sync" in _row_text(r) for r in missing.rows)


def _signal(fade=(), bump=()):
    return usage_r6.PlayerSignal(fade=tuple(fade), bump=tuple(bump))


def test_a_proj_chip_adds_its_reason_and_a_lean_verb_and_the_pool_override_still_applies():
    edge = _edge([_player(1), _player(2), _player(3)])
    inputs = _inputs(
        edge,
        r6=_r6(
            signals={
                "g1": _signal(fade=["tgt_pg|TE|change_up"]),
                "g2": _signal(bump=["tgt_pg|TE|change_down"]),
                "g3": _signal(fade=["hvt|RB|level_hi"], bump=["tgt_pg|TE|change_down"]),
            }
        ),
    )
    inputs.players["GsisId"] = ["g1", "g2", "g3"]
    layout = eft.build_layout(inputs)
    do, why = _col(eft.DO_COL), _col(eft.WHY_COL)
    cash_rows = {layout.rows[r - 1][NAME]: r for r in layout.player_rows[:3]}
    cash = {name: layout.rows[r - 1] for name, r in cash_rows.items()}
    assert cash["WR1"][do] == "Cash option (Proj ▼)"  # a fade signal downgrades "Cash add"
    assert cash["WR2"][do].endswith("(Proj ▲)") and "Cash add" not in cash["WR1"][do]
    assert "Proj" not in cash["WR3"][do] and "Lean" not in cash["WR3"][do]  # points both ways: no chip
    assert all(" · " not in cash[n][do] for n in cash)  # one verb per row
    assert "TE targets up 2.2+/game over the last 3 (usually fades back: −0.8 pts" in _why(
        layout, cash_rows["WR1"]
    )
    assert "Signals point both ways, so no Proj chip" in _why(layout, cash_rows["WR3"])
    assert why  # (the shown Why is the short one)
    # the sheet turns the verb into "In pool (...)" through the same formula as before
    rows = writer.tab_rows(layout, "EdgeRaw")
    formula = rows[_rows_named(layout, "WR1")[0] - 1][_col(eft.DO_COL)]
    assert formula.startswith(f"=IF(${eft.POOL_COL}") and "(Proj ▼)" in formula and "In pool (" in formula


def test_an_older_chip_and_an_r6_signal_on_one_player_share_a_single_why():
    edge = _edge([_player(1, Edge="FADE↓ Proj ▼")])
    inputs = _inputs(edge, r6=_r6(signals={"g1": _signal(fade=["tgt_pg|TE|change_up"])}))
    inputs.players["GsisId"] = ["g1"]
    layout = eft.build_layout(inputs)
    why = _why(layout, layout.player_rows[0])
    assert (
        "TE targets up 2.2+/game" in why and "FADE↓: last-3 DK points a game at least 2 above expected" in why
    )


def test_a_chip_resting_only_on_weaker_evidence_is_muted_and_says_so():
    edge = _edge([_player(1), _player(2)])
    inputs = _inputs(
        edge,
        r6=_r6(
            signals={
                "g1": _signal(fade=["adot|WR|level_hi"]),
                "g2": _signal(fade=["snap_pct|WR|change_down"]),
            }
        ),
    )
    inputs.players["GsisId"] = ["g1", "g2"]
    layout = eft.build_layout(inputs)
    first, second = _rows_named(layout, "WR1")[0], _rows_named(layout, "WR2")[0]
    assert first in layout.muted_rows and "weaker evidence" in _why(layout, first)
    assert second not in layout.muted_rows


def test_context_signals_list_each_of_the_twelve_r6_signals_as_its_own_sub_block():
    edge = _edge([_player(1)])
    inputs = _inputs(edge, r6=_r6(signals={"g1": _signal(fade=["tgt_pg|TE|change_up"])}))
    inputs.players["GsisId"] = ["g1"]
    layout = eft.build_layout(inputs)
    subs = [
        _row_text(layout.rows[r - 1])
        for r in layout.subheader_rows
        if "flagged" in str(layout.rows[r - 1][0])
    ]
    assert len(subs) == 12
    te_up = [s for s in subs if s.startswith("TE · 1 flagged") and "targets up 2.2+/game" in s][0]
    assert "1 flagged" in te_up and "FADE (Proj ▼)" in te_up and "−0.8 pts" in te_up.replace("-", "−")
    assert len(layout.subheader_rows) == len({r for r in layout.subheader_rows})  # no continuation rows here
    assert any("weaker evidence" in s for s in subs) and any(
        "tested on 2014-21 and 2022-25" in s for s in subs
    )
    assert sum("0 flagged" in s for s in subs) == 11


def test_every_confirmed_absence_is_listed_muted_as_context_with_the_historical_line():
    edge = _edge([_player(1), _player(2)])
    layout = eft.build_layout(_inputs(edge, absences=_absences()))
    text = [_row_text(r) for r in layout.rows]
    assert any("Absent regulars" in t and "no points are moved for targets" in t for t in text)
    row = [r for r in layout.player_rows if layout.rows[r - 1][_col(eft.DO_COL)] == "Out"][0]
    assert row in layout.muted_rows  # context is muted, never an edge
    why = _why(layout, row)
    assert "with-or-without (2 g)" in why and "no single teammate gains much" in why and "~27%" in why
    assert layout.rows[row - 1][S0 : S0 + 4] == ["WR1", 8.0, 0.0, 2]  # Role, Tgt/G, Car/G, Games missed


def _matchups():
    return pd.DataFrame(
        [
            {
                "Position": "WR",
                "Team": "DEN",
                "Opp": "KC",
                "Score": 1.0,
                "Group": "top",
                "Reasons": "KC allows many points",
                "Players": "p",
            },
            {
                "Position": "WR",
                "Team": "LV",
                "Opp": "BUF",
                "Score": -1.2,
                "Group": "bottom",
                "Reasons": "BUF is stingy",
                "Players": "p",
            },
        ]
    )


def test_matchups_are_graded_list_the_top_three_players_and_nest_their_player_rows():
    edge = _edge(
        [
            _player(1, **{"CalPts": 18.0, "Salary": 6200}),
            _player(2, **{"CalPts": 15.0, "Salary": 5100}),
            _player(3, **{"CalPts": 12.0, "Salary": 4100}),
            _player(4, **{"CalPts": 9.0, "Salary": 3100}),
        ]
    )
    layout = eft.build_layout(_inputs(edge, matchups=_matchups()))
    titles = [layout.rows[r - 1][0] for r in layout.section_rows]
    assert any(t.startswith("MATCHUPS (CONTEXT)") for t in titles)
    team = [r for r in layout.team_rows if layout.rows[r - 1][NAME] == "DEN vs KC"][0]
    cells = layout.rows[team - 1]
    pos_i, grade_i, players_i = (eft.LEAD.index(h) for h in ("Pos", "Team", "Salary"))
    # Matchup | Pos | Grade | Players: the names are one cell, with the rest of the row's slots empty
    assert (cells[pos_i], cells[grade_i], cells[players_i]) == ("WR", "Soft", "WR1, WR2, WR3")
    assert not any(str(c) for c in cells[S0 : S0 + eft.SLOTS])
    assert "WR4" not in " ".join(str(c) for c in cells)
    assert "score +1.00" in _why(layout, team) and "KC allows many points" in _why(layout, team)
    assert cells[_col(eft.WHY_COL)].startswith("KC allows many points")
    (group,) = _group(layout, "MATCHUPS|WR|DEN")
    assert group.depth == 2 and group.collapsed is True and group.first == team + 1
    # the group opens on its own small header over the standard player columns, then the three players
    inner = layout.rows[group.first - 1]
    assert group.first in layout.inner_header_rows
    assert inner[: len(eft.LEAD)] == eft.LEAD and inner[S0 : S0 + 3] == ["CalPts", "Hit3x%", "Boom%"]
    players = [layout.rows[r - 1][NAME] for r in range(group.first + 1, group.last + 1)]
    assert players == ["WR1", "WR2", "WR3"]
    tough = [r for r in layout.team_rows if layout.rows[r - 1][NAME] == "LV vs BUF"][0]
    assert layout.rows[tough - 1][eft.LEAD.index("Team")] == "Tough" and tough in layout.muted_rows
    assert any("CONTEXT ONLY" in str(c) or "Context only" in str(c) for r in layout.rows for c in r)


def test_matchup_player_rows_get_the_standard_formats_and_the_team_row_gets_none():
    """Sam: "what are these numbers?" -- Salary 6700 and Own% 0.28 showed raw because the matchup header
    had no Salary / Own% columns to take a format from, and a team row sat inside a right-aligned block."""
    edge = _edge([_player(1, **{"CalPts": 18.0, "Salary": 6200}), _player(2, **{"CalPts": 15.0})])
    layout = eft.build_layout(_inputs(edge, matchups=_matchups()))
    (group,) = _group(layout, "MATCHUPS|WR|DEN")
    first, last = group.first + 1, group.last

    def covers(ranges, letter):
        return any(r == f"{letter}{first}:{letter}{last}" for r in ranges)

    assert covers(layout.money_cells, eft.column_letter(eft.LEAD.index("Salary")))
    assert covers(layout.own_cells, eft.OWN_COL)
    assert covers(layout.point_cells, eft.column_letter(S0)) and covers(
        layout.percent_cells, eft.column_letter(S0 + 1)
    )
    # no format range reaches a team row (its Players text must stay left-aligned, never a number format)
    every = [*layout.money_cells, *layout.own_cells, *layout.point_cells, *layout.percent_cells]
    spans = [_range_rows(r) for r in every]
    assert not any(lo <= row <= hi for row in layout.team_rows for lo, hi in spans)


def _range_rows(rng):
    import re

    a, b = re.fullmatch(r"[A-Z]+(\d+):[A-Z]+(\d+)", rng).groups()
    return int(a), int(b)


def test_the_status_lines_are_for_sam_in_eastern_time_with_the_final_sync_reminder():
    info = {"source": "nflverse", "week": 5, "rows": 4, "with_status": 0, "fetched": "2026-10-07T12:21:00Z"}
    status = {
        "week": 5,
        "stats_through_week": 4,
        "projection_snapshot": "20261008T024600Z",
        "injury_report": info,
        "calpts_weeks": [1, 2, 3, 4],
        "calpts_sources": ["ProjPts"],
    }
    edge = _edge([_player(1, GameStart="2026-10-11T13:00:00Z")])
    layout = eft.build_layout(_inputs(edge, status=status))
    assert [layout.rows[r - 1][0] for r in layout.status_rows][:4] == [
        "Data",
        "Injury report",
        "CalPts",
        "Final sync",
    ]
    lines = [layout.rows[r - 1][eft.DETAIL_INDEX] for r in layout.status_rows]
    assert lines[0] == (
        "Stats through Week 4 · Injuries: practice reports only until Friday · "
        "Projections updated Wed 10:46 pm ET"
    )
    assert "fetched Wed 8:21 am ET" in lines[1] and "UTC" not in " ".join(lines)
    assert (
        lines[3]
        == "Run the final `dfs sync --live` about 90 minutes before kickoff. First kickoff Sun 1:00 pm ET."
    )
    assert "Not recorded" in eft._injury_report_line(None)


def _absences():
    return pd.DataFrame(
        [
            {
                "GsisId": "1",
                "Name": "WR1",
                "Team": "DEN",
                "Position": "WR",
                "Role": "WR1",
                "Regular": "28% of team targets",
                "tgt_g": 8.0,
                "car_g": 0.0,
                "GamesMissed": 2,
                "WithWithoutGames": 2,
                "WithWithout": "with-or-without (2 g): Z +1.5 targets/G",
                "History": "historically, no single teammate gains much: WR2 +13% of the vacated targets, "
                "~27% goes nowhere (n=239)",
            }
        ]
    )


def test_board_panel_is_five_lines_one_each_and_says_none_when_empty():
    assert eft.board_panel_lines(None)[0].startswith("Not synced yet")
    edge = _edge([_player(i, **{"Hit3x%": 30.0 + i, "Boom%": 10.0 + i}) for i in range(1, 6)])
    lines = eft.board_panel_lines(_inputs(edge))
    assert len(lines) == eft.BOARD_PANEL_LINES == 5
    assert lines[0].startswith("Cash core: ") and "WR5" in lines[0]
    assert (
        lines[3] == "Injury beneficiaries (carries): none" and lines[4] == "Best offense per position: none"
    )


def test_only_beneficiaries_draftkings_lists_are_shown_in_the_tab_and_the_board_line():
    edge = _edge([_player(1), _player(2)])
    ben = pd.DataFrame(
        [
            {
                "GsisId": g,
                "Name": n,
                "Team": "DEN",
                "Position": "RB",
                "OutStatus": "out",
                "OutPlayers": "X",
                "car_gain": 4.0,
                "xfp_gain": 3.0,
                "Method": "measured table",
                "n": 0,
                "Token": "INJ+",
                "PricedIn": "",
            }
            for g, n in (("1", "On Slate"), ("99", "Not On DraftKings"))
        ]
    )
    inputs = _inputs(edge, beneficiaries=ben)
    layout = eft.build_layout(inputs)
    names = {str(r[NAME]) for r in layout.rows}
    assert "On Slate" in names and "Not On DraftKings" not in names
    assert "Not On DraftKings" not in " ".join(eft.board_panel_lines(inputs))


def test_percent_text_is_kept_as_text_so_sheets_does_not_turn_79_percent_into_0_79():
    assert eft._clean("79%") == "'79%" and eft._clean("12.5%") == "'12.5%"
    assert eft._clean("2.3") == "2.3" and eft._clean("Tgt% up") == "Tgt% up"


# ---- ownership: Lev, Leverage plays, Chalk to fade or eat (actions round, slice 4) -------------------------


def _owned(i, boom, own, pos="WR", **kw):
    return _player(i, pos=pos, **{"Boom%": boom, "Own%": own, "OwnStatus": "real", **kw})


def _section_rows(layout, title):
    """The player rows of one section, found by its title."""
    start = [r for r in layout.section_rows if layout.rows[r - 1][0].startswith(title)][0]
    later = [r for r in layout.section_rows if r > start]
    return [r for r in layout.player_rows if start < r < (later[0] if later else 10**6)]


def test_lev_is_boom_rank_minus_own_rank_within_position_and_blank_until_ownership_is_out():
    rows = [_owned(i, boom=10.0 + i, own=0.30 - i / 100) for i in range(1, 5)]  # boom up, ownership down
    rows += [_owned(i, boom=10.0 + i, own=0.05 + i / 100, pos="RB") for i in range(11, 15)]  # both up
    out = eft.with_leverage(_edge(rows))
    wr = out[out["Position"] == "WR"].set_index("Name")
    assert wr.loc["WR4", "Lev"] == 75 and wr.loc["WR1", "Lev"] == -75  # 100th boom - 25th own; 25th - 100th
    assert (out[out["Position"] == "RB"]["Lev"] == 0).all()  # rank for rank: no leverage
    unpublished = eft.with_leverage(_edge([_player(i) for i in range(1, 5)]))
    assert unpublished["Lev"].isna().all() and not unpublished["BoomHalf"].any()


def test_gpp_leverage_needs_lev_at_least_25_and_a_top_half_boom():
    assert eft.LEVERAGE_MIN_LEV == 25
    assert eft.gpp_verb(20.0, 30.0, False, lev=25, boom_half=True) == "GPP leverage"
    assert eft.gpp_verb(20.0, 30.0, False, lev=24, boom_half=True) == "GPP option"
    assert eft.gpp_verb(20.0, 30.0, False, lev=60, boom_half=False) == "GPP option"  # boom in the bottom half
    assert eft.gpp_verb(35.0, 30.0, False, lev=0, boom_half=True) == "GPP add"
    assert eft.gpp_verb(35.0, 30.0, True, lev=40, boom_half=True) == f"GPP leverage {eft.STAR}"


def test_the_leverage_section_lists_the_top_five_per_position_by_lev_among_top_half_booms():
    rows = [_owned(i, boom=10.0 + i, own=0.40 - i / 50) for i in range(1, 13)]
    layout = eft.build_layout(_inputs(_edge(rows)))
    shown = _section_rows(layout, "LEVERAGE PLAYS")
    assert len(shown) == eft.LEVERAGE_ROWS_PER_POSITION == 5
    names = [layout.rows[r - 1][NAME] for r in shown]
    levs = [layout.rows[r - 1][S0 + 3] for r in shown]
    assert levs == sorted(levs, reverse=True) and names[0] == "WR12"  # best boom, lowest owned
    top_half = {f"WR{i}" for i in range(7, 13)}  # booms at or above the median
    assert set(names) <= top_half
    assert {layout.rows[r - 1][_col(eft.DO_COL)] for r in shown} <= {"GPP leverage", "GPP option"}
    # Lev is coloured within the position block
    assert any(name == "Lev" for name, *_ in layout.prob_blocks)


def test_chalk_lists_the_three_highest_owned_per_position_with_an_eat_or_fade_verb_by_bust_third():
    busts = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
    rows = [_owned(i, boom=15.0, own=0.10 + i / 100, **{"Bust%": busts[i - 1]}) for i in range(1, 7)]
    layout = eft.build_layout(_inputs(_edge(rows)))
    shown = _section_rows(layout, "CHALK TO FADE OR EAT")
    assert [layout.rows[r - 1][NAME] for r in shown] == ["WR6", "WR5", "WR4"]  # the three highest-owned
    verbs = {layout.rows[r - 1][NAME]: layout.rows[r - 1][_col(eft.DO_COL)] for r in shown}
    assert verbs == {"WR6": "Chalk: fade candidate", "WR5": "Chalk: fade candidate", "WR4": "Chalk"}
    assert eft.chalk_verb(0.2) == "Chalk: eat" and eft.chalk_verb(1 / 3) == "Chalk: eat"
    assert eft.chalk_verb(0.5) == "Chalk" and eft.chalk_verb(0.7) == "Chalk: fade candidate"


def test_leverage_and_chalk_say_ownership_is_not_out_and_list_nobody_until_it_is():
    layout = eft.build_layout(_inputs(_edge([_player(i) for i in range(1, 6)])))
    assert (
        _section_rows(layout, "LEVERAGE PLAYS") == [] and _section_rows(layout, "CHALK TO FADE OR EAT") == []
    )
    notes = [str(r[eft.DETAIL_INDEX]) for r in layout.rows]
    assert notes.count(eft.OWN_NOT_OUT) == 4  # the status line plus GPP, Leverage and Chalk
    assert all(layout.rows[r - 1][eft.LEAD.index("Own%")] == "" for r in layout.player_rows)  # blank, not 0%


def test_own_pct_shows_in_every_player_row_once_published_as_a_fraction():
    layout = eft.build_layout(_inputs(_edge([_owned(1, 12.0, 0.255), _owned(2, 14.0, 0.05)])))
    own = {layout.rows[r - 1][NAME]: layout.rows[r - 1][_col(eft.OWN_COL)] for r in layout.player_rows}
    assert own["WR1"] == 0.255 and own["WR2"] == 0.05
    assert layout.own_cells and not any("Own%" in name for name in ("Hit3x%", "Boom%", "Bust%"))


def test_ownership_never_changes_calpts_or_any_probability():
    """Own% is read only to show and to rank; the projection and the model's odds do not depend on it."""
    low = _edge([_owned(i, boom=10.0 + i, own=0.05) for i in range(1, 9)])
    high = _edge([_owned(i, boom=10.0 + i, own=0.45 - i / 100) for i in range(1, 9)])
    numbers = ["CalPts", "Hit3x%", "Boom%", "Bust%", "Floor", "CeilM", "ProjPts"]
    out_low, out_high = eft.with_leverage(low), eft.with_leverage(high)
    pd.testing.assert_frame_equal(out_low[numbers], out_high[numbers])
    layout_low = eft.build_layout(_inputs(low))
    layout_high = eft.build_layout(_inputs(high))

    def first(layout):  # the Cash core's numbers, in order
        return [tuple(layout.rows[r - 1][S0 : S0 + 4]) for r in _section_rows(layout, "CASH CORE")]

    assert first(layout_low) == first(layout_high) and first(layout_low)


def test_no_projection_module_reads_ownership():
    """A source-level guard behind the test above: the modules that compute CalPts and the outcome odds never
    mention ownership."""
    from pathlib import Path

    import dfs

    root = Path(dfs.__file__).parent
    for name in ("probabilities.py", "calibration.py", "edge_finder.py"):
        text = (root / name).read_text()
        assert "Own%" not in text and "ProjOwn" not in text, name


def test_a_proj_signal_never_leaves_two_opposite_instructions_on_one_row():
    """Jonathan Taylor read "Cash add · Lean under in cash" (2026-10-09): the verb and the lean disagreed."""
    down, up = usage_r6.PROJ_DOWN, usage_r6.PROJ_UP
    assert eft.verb_with_proj("Cash add", down) == "Cash option (Proj ▼)"
    assert eft.verb_with_proj("Cash option", down) == "Cash option (Proj ▼)"
    assert eft.verb_with_proj("Cash add", up) == "Cash add (Proj ▲)"  # an up signal never upgrades
    assert eft.verb_with_proj("Cash option", up) == "Cash option (Proj ▲)"
    assert eft.verb_with_proj("GPP add", down) == "GPP add (Proj ▼)"
    assert eft.verb_with_proj("Out", down) == "Out" and eft.verb_with_proj("Cash add", None) == "Cash add"
    assert eft.verb_with_proj("Cash add", "Proj ▼?") == "Cash add"  # weaker evidence only mutes the row


def test_every_text_row_keeps_a_label_that_fits_the_frozen_panes_and_puts_the_description_in_column_c():
    """Pool and Name stay frozen (A:B is ~225 px at the narrowest): a title across that boundary was chopped.
    Sam: a short label in A:B, the longer description from column C where it overflows right freely."""
    from dfs.sheet_clipping import FIT_PADDING_PX, FIT_PX_PER_CHAR, estimate_px

    edge = _edge([_player(i) for i in range(1, 8)])
    layout = eft.build_layout(
        _inputs(edge, history={"WR": (41.0, 4)}, matchups=_matchups(), absences=_absences())
    )
    text_rows = [
        *layout.section_rows,
        *layout.meaning_rows,
        *layout.subheader_rows,
        *layout.note_rows,
        *layout.verdict_rows,
        *layout.status_rows,
    ]
    assert text_rows
    for row in text_rows:
        cells = layout.rows[row - 1]
        label = str(cells[0])
        bold = row in layout.section_rows or row in layout.subheader_rows
        needed = estimate_px(label, per_char=FIT_PX_PER_CHAR, padding=FIT_PADDING_PX, bold=bold)
        assert needed <= eft.FROZEN_PX, (row, label)  # never chopped at the A:B boundary
        assert cells[1] == ""  # B stays empty so the label can overflow into it
        assert len(str(cells[eft.DETAIL_INDEX])) <= eft.DETAIL_CHARS, (row,)
        # nothing between the description and Do, or it could not overflow right (the hidden key is past Id)
        assert not any(str(c) for c in cells[eft.DETAIL_INDEX + 1 : eft.FIRST_TRAILING]), (row, cells)


def test_split_label_keeps_a_short_text_whole_cuts_at_the_dash_and_never_chops_a_word():
    assert eft.split_label("RB  —  top 10 of 32", bold=True) == ("RB · top 10 of 32", "")
    assert eft.split_label("CASH CORE  —  best Hit3x% per position", bold=True, join=False) == (
        "CASH CORE",
        "best Hit3x% per position",
    )
    assert eft.split_label("Thin week at RB: best cash odds 34% (typical best ~41%)", fallback="Verdict") == (
        "Thin week at RB",
        "best cash odds 34% (typical best ~41%)",
    )
    label, detail = eft.split_label(
        "a long sentence with no separator at all that cannot possibly fit A and B"
    )
    assert label.endswith("…") and detail.startswith("a long sentence")
    assert (
        eft.split_label("a long sentence with no separator at all that cannot fit", fallback="Note")[0]
        == "Note"
    )


def test_a_description_longer_than_a_line_continues_on_the_next_row_without_a_stray_separator():
    long = "First sentence about the thing. " + "word " * 40 + "· tail  ·  last bit"
    lines = eft.detail_lines(long, limit=60)
    assert all(len(line) <= 60 for line in lines) and len(lines) > 1
    assert not any(line.startswith("·") for line in lines)


def test_the_shown_why_gives_the_reason_for_the_gap_not_the_numbers_beside_it():
    """Sam: "CalPts 1.8 under TFFB" repeats two visible columns. Use the reason; blank rather than repeat."""
    scored = _scored_rb_rows(60).assign(SleeperPts=10.0, FantasyProsPts=10.0)
    fitted = calibration.fit(scored, before_week=2)
    row = pd.Series(
        {
            "Position": "RB",
            "Salary": 4000,
            "ProjPts": 10.0,
            "SleeperPts": 14.0,
            "FantasyProsPts": 14.0,
            "CalPts": 12.0,
        }
    )
    reason = eft.gap_short(row, fitted)
    assert "CalPts" not in reason and "TFFB" in reason or "higher" in reason
    assert eft.gap_short(row, None) == ""  # no calibration: nothing to say
    close = row.copy()
    close["CalPts"] = 10.2
    assert eft.gap_short(close, fitted) == ""  # a gap under half a point is not worth a reason
    both = pd.Series(
        {
            "Position": "RB",
            "Salary": 4000,
            "ProjPts": 10.0,
            "SleeperPts": 14.0,
            "FantasyProsPts": 14.0,
            "CalPts": 14.0,
        }
    )
    assert "Sleeper and FantasyPros both higher" in eft.gap_short(both, fitted)


def test_r6_signal_rows_do_not_repeat_the_subheader_in_their_why():
    edge = _edge([_player(1)])
    inputs = _inputs(edge, r6=_r6(signals={"g1": _signal(fade=["tgt_pg|TE|change_up"])}))
    inputs.players["GsisId"] = ["g1"]
    layout = eft.build_layout(inputs)
    why = _col(eft.WHY_COL)
    block_rows = [
        r for r in layout.player_rows if layout.rows[r - 1][_col(eft.DO_COL)].startswith("Context only")
    ]
    assert block_rows and all(layout.rows[r - 1][why] in ("", "1 game") for r in block_rows)
    first = layout.player_rows[0]  # the signal shows in a few words on his ordinary rows
    assert "targets up 2.2+/game (usually fades back)" in layout.rows[first - 1][why]
