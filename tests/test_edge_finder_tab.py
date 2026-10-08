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


def _rows_named(layout, name):
    return [r for r in layout.player_rows if layout.rows[r - 1][0] == name]


def _col(letter):
    return ord(letter) - ord("A")


def _group(layout, key):
    return [g for g in layout.groups if g.key == key]


def test_the_empty_state_layout_has_every_section_and_says_nothing_yet():
    layout = eft.build_layout(None)
    titles = [layout.rows[r - 1][0].split("  ")[0] for r in layout.section_rows]
    assert titles == [
        "CASH CORE",
        "GPP UPSIDE",
        "PUNT PLAYS",
        "PROJECTION DISAGREEMENTS",
        "INJURY BENEFICIARIES",
        "USAGE TRENDS",
        "MATCHUPS (CONTEXT)",
        "CONTEXT SIGNALS",
    ]
    assert layout.player_rows == [] and layout.groups == []
    assert all(len(r) == eft.COLUMN_COUNT for r in layout.rows)


def test_the_trailing_columns_are_why_do_pool_set_link_id_and_a_hidden_key():
    assert eft.TRAILING_HEADERS == ["Why", "Do", "Pool", "Set", "↗", "Id"]
    assert [
        eft.WHY_COL,
        eft.DO_COL,
        eft.POOL_COL,
        eft.SET_COL,
        eft.LINK_COL,
        eft.ID_COL,
        eft.KEY_COL,
    ] == list("KLMNOPQ")
    layout = eft.build_layout(_inputs(_edge([_player(1)])))
    header = layout.rows[layout.header_rows[0] - 1]
    assert header[:10] == [
        "Name",
        "Pos",
        "Team",
        "Salary",
        "CalPts",
        "Hit3x%",
        "Bust%",
        "ProjPts",
        "Rank",
        "Edge",
    ]
    assert header[10:16] == ["Why", "Do", "Pool", "Set", "↗", "Id"]
    assert "Games" not in header


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
    assert "WR  —  top 6 of 6" in subs  # 10 listed, 6 rosterable
    names = {layout.rows[r - 1][0] for r in layout.player_rows}
    assert not names & {"WR1", "WR2", "WR3", "WR4"}


def test_visible_rows_follow_roster_need_and_the_rest_sit_in_a_collapsed_nested_group():
    edge = _edge([_player(i, **{"Hit3x%": 20.0 + i}) for i in range(1, 31)])  # 30 receivers
    layout = eft.build_layout(_inputs(edge))
    assert eft.VISIBLE_PER_POSITION == {"QB": 6, "RB": 10, "WR": 12, "TE": 6, "DST": 6}
    sub = [layout.rows[r - 1][0] for r in layout.subheader_rows if layout.rows[r - 1][0].startswith("WR")][0]
    assert sub == "WR  —  top 12 of 30"
    header = [r for r in layout.overflow_rows if "WRs" in layout.rows[r - 1][0]][0]
    assert layout.rows[header - 1][0] == "▸ 18 more WRs (click + to show)"
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
    cash_wr = [r for r in layout.player_rows if layout.rows[r - 1][4] != "" and layout.rows[r - 1][5] != ""]
    (overflow,) = _group(layout, "CASH CORE|WR|more")
    assert overflow.last - overflow.first + 1 == eft.MAX_PER_POSITION - 12
    assert any("top 12 of 60" in str(r[0]) for r in layout.rows)  # the header counts the whole pool
    assert cash_wr


def test_rank_is_against_the_position_pool_and_the_verbs_follow_the_rules():
    edge = _edge(
        [
            _player(1, **{"Hit3x%": 60.0, "Bust%": 10.0}),
            _player(2, **{"Hit3x%": 55.0, "Bust%": 50.0}),  # top 3 Hit3x% but a high bust: only an option
            _player(3, **{"Hit3x%": 50.0, "Bust%": 10.0}),
            _player(4, **{"Hit3x%": 45.0, "Bust%": 30.0}),  # rank 4: an option
        ]
    )
    layout = eft.build_layout(_inputs(edge))
    do, rank = _col(eft.DO_COL), _col("I")
    by_name = {layout.rows[r - 1][0]: layout.rows[r - 1] for r in layout.player_rows[:4]}
    assert by_name["WR1"][rank] == "#1 of 4" and by_name["WR4"][rank] == "#4 of 4"
    assert [by_name[n][do] for n in ("WR1", "WR2", "WR3", "WR4")] == [
        "Cash add",
        "Cash option",
        "Cash add",
        "Cash option",
    ]


def test_gpp_verbs_add_for_a_top_quartile_boom_and_leverage_adds_the_star():
    rows = [
        _player(i, **{"Boom%": 10.0 + i, "Own%": 0.20 - i / 100, "OwnStatus": "real"}) for i in range(1, 9)
    ]
    layout = eft.build_layout(_inputs(_edge(rows)))
    verbs = {
        layout.rows[r - 1][0]: layout.rows[r - 1][_col(eft.DO_COL)]
        for r in layout.player_rows
        if str(layout.rows[r - 1][_col(eft.DO_COL)]).startswith("GPP")
    }
    assert verbs["WR8"] == f"GPP leverage {eft.STAR}"  # highest Boom%, lowest ownership
    assert verbs["WR1"] == "GPP option"
    unpublished = eft.build_layout(_inputs(_edge([_player(i, **{"Boom%": 10.0 + i}) for i in range(1, 9)])))
    assert not any(eft.STAR in str(c) for r in unpublished.rows for c in r[10:13])
    assert any("ownership not out yet" in str(r[10]) for r in unpublished.rows)


def test_a_player_row_carries_the_hidden_id_the_why_and_a_do_verb():
    layout = eft.build_layout(_inputs(_edge([_player(7)])))
    row = layout.rows[_rows_named(layout, "WR7")[0] - 1]
    assert row[_col(eft.ID_COL)] == 7
    assert "to reach 3x salary" in row[_col(eft.WHY_COL)] and row[_col(eft.DO_COL)] in (
        "Cash add",
        "Cash option",
    )
    assert row[_col(eft.SET_COL)] == ""


def test_rows_under_three_games_are_muted_and_the_why_says_how_little_data():
    edge = _edge([_player(1), _player(2)])
    inputs = _inputs(edge)
    inputs.players.loc[inputs.players["Id"] == 2, "Games"] = 2
    layout = eft.build_layout(inputs)
    muted = {layout.rows[r - 1][0] for r in layout.muted_rows}
    assert "WR2" in muted and "WR1" not in muted
    why = layout.rows[_rows_named(layout, "WR2")[0] - 1][_col(eft.WHY_COL)]
    assert why.endswith("only 2 games of data")


def test_a_thin_week_verdict_needs_history_and_a_best_below_the_typical_best():
    edge = _edge([_player(1, **{"Hit3x%": 34.0}), _player(2, **{"Hit3x%": 20.0})])
    none = eft.build_layout(_inputs(edge))
    assert not none.verdict_rows  # no history: no verdict, never invented
    layout = eft.build_layout(_inputs(edge, history={"WR": (41.0, 4)}))
    (row,) = layout.verdict_rows
    assert (
        layout.rows[row - 1][0] == "Thin week at WR: best cash odds 34% (typical best ~41%, 4 earlier weeks)"
    )
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
    assert [layout.rows[r - 1][0] for r in injury] == ["WR2", "WR1"]  # confirmed before questionable
    assert [layout.rows[r - 1][do] for r in injury] == ["Bump ▲", "Watch"]
    assert injury[1] in layout.muted_rows
    confirmed, questionable = (layout.rows[r - 1] for r in injury)
    assert confirmed[6] == "with-or-without (3 g)" and confirmed[7] == "yes"  # Method, Priced in?
    assert questionable[7] == ""  # unknown reads blank, not the word "unknown"
    assert "Y out: +0.0 carries and +6.0 expected points a game" in confirmed[_col(eft.WHY_COL)]
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
        (layout.rows[r - 1][0], layout.rows[r - 1][do]): layout.rows[r - 1]
        for r in layout.player_rows
        if layout.rows[r - 1][do] in ("Look closer ▲", "Caution ▼")
    }
    up, down = rows[("WR1", "Look closer ▲")], rows[("WR2", "Caution ▼")]
    assert up[4:8] == [10.0, 13.0, "", 14.0] and up[8] == 4.0  # TFFB, Sleeper, FantasyPros, CalPts, Diff
    assert down[4] == 10.0 and down[7] == 6.0 and down[8] == -4.0
    assert (
        layout.rows[[r for r in layout.header_rows if "Sleeper" in layout.rows[r - 1]][0] - 1][4:9]
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
    assert [layout.rows[r - 1][0] for r in punt] == ["WR2", "WR1"]


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
    assert row[4:9] == ["Tgt%", "27%", "18%", "'+9 pts", "▲"]  # last 3, earlier, change ('= literal text)
    assert row[_col(eft.DO_COL)] == "Watch"
    why = row[_col(eft.WHY_COL)]
    assert why.startswith("Tgt% 18% → 27% over the last 3 (▲, a bigger jump than 85% of weeks).")
    assert "Historically projections over-react to jumps like this." in why
    quiet = eft.build_layout(_inputs(edge, r6=_r6(trends=_trends(Direction=""))))
    assert any("moved by more than R6's measured band" in str(r[0]) for r in quiet.rows)


def test_a_player_too_new_for_an_arrow_is_counted_not_guessed():
    edge = _edge([_player(7)])
    layout = eft.build_layout(_inputs(edge, r6=_r6(short_games=4)))
    text = " ".join(str(r[0]) for r in layout.rows)
    assert "Not enough games: 4 pool players have fewer than 9 earlier games, so they get no arrow" in text
    missing = eft.build_layout(_inputs(edge))  # no R6 data at all: the tab says how to get it
    assert any("need the R6 data" in str(r[0]) for r in missing.rows)
    assert any("R6 usage signals are not available this sync" in str(r[0]) for r in missing.rows)


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
    cash = {layout.rows[r - 1][0]: layout.rows[r - 1] for r in layout.player_rows[:3]}
    assert cash["WR1"][do].endswith(" · Lean under in cash") and cash["WR1"][do].startswith("Cash")
    assert cash["WR2"][do].endswith(" · Lean over")
    assert "Lean" not in cash["WR3"][do]  # signals point both ways: no chip, no lean
    assert "TE targets up 2.2+/game over the last 3 (usually fades back: −0.8 pts" in cash["WR1"][why]
    assert "Signals point both ways, so no Proj chip" in cash["WR3"][why]
    # the sheet turns the verb into "In pool (...)" through the same formula as before
    rows = writer.tab_rows(layout, "EdgeRaw", 5)
    formula = rows[_rows_named(layout, "WR1")[0] - 1][_col(eft.DO_COL)]
    assert formula.startswith("=IF($M") and "Lean under in cash" in formula and "In pool (" in formula


def test_an_older_chip_and_an_r6_signal_on_one_player_share_a_single_why():
    edge = _edge([_player(1, Edge="FADE↓ Proj ▼")])
    inputs = _inputs(edge, r6=_r6(signals={"g1": _signal(fade=["tgt_pg|TE|change_up"])}))
    inputs.players["GsisId"] = ["g1"]
    layout = eft.build_layout(inputs)
    why = layout.rows[layout.player_rows[0] - 1][_col(eft.WHY_COL)]
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
    assert first in layout.muted_rows and "weaker evidence" in layout.rows[first - 1][_col(eft.WHY_COL)]
    assert second not in layout.muted_rows


def test_context_signals_list_each_of_the_twelve_r6_signals_as_its_own_sub_block():
    edge = _edge([_player(1)])
    inputs = _inputs(edge, r6=_r6(signals={"g1": _signal(fade=["tgt_pg|TE|change_up"])}))
    inputs.players["GsisId"] = ["g1"]
    layout = eft.build_layout(inputs)
    subs = [layout.rows[r - 1][0] for r in layout.subheader_rows if "flagged" in str(layout.rows[r - 1][0])]
    assert len(subs) == 12
    te_up = [s for s in subs if s.startswith("TE targets up 2.2+/game")][0]
    assert "1 flagged" in te_up and "FADE (Proj ▼)" in te_up and "−0.8 pts" in te_up.replace("-", "−")
    assert any("weaker evidence" in s for s in subs) and any(
        "tested on 2014-21 and 2022-25" in s for s in subs
    )
    assert sum("0 flagged" in s for s in subs) == 11


def test_every_confirmed_absence_is_listed_muted_as_context_with_the_historical_line():
    edge = _edge([_player(1), _player(2)])
    layout = eft.build_layout(_inputs(edge, absences=_absences()))
    text = [str(c) for r in layout.rows for c in r if c != ""]
    assert any("Absent regulars" in t and "no points are moved for targets" in t for t in text)
    row = [r for r in layout.player_rows if layout.rows[r - 1][_col(eft.DO_COL)] == "Out"][0]
    assert row in layout.muted_rows  # context is muted, never an edge
    why = layout.rows[row - 1][_col(eft.WHY_COL)]
    assert "with-or-without (2 g)" in why and "no single teammate gains much" in why and "~27%" in why
    assert layout.rows[row - 1][4:8] == ["WR1", 8.0, 0.0, 2]  # Role, Tgt/G, Car/G, Games missed


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
    team = [r for r in layout.team_rows if layout.rows[r - 1][0] == "DEN vs KC"][0]
    cells = layout.rows[team - 1]
    assert cells[3] == "Soft" and cells[4:7] == ["WR1 $6.2k", "WR2 $5.1k", "WR3 $4.1k"]
    assert "WR4" not in " ".join(str(c) for c in cells)
    assert "score +1.00" in cells[_col(eft.WHY_COL)] and "KC allows many points" in cells[_col(eft.WHY_COL)]
    (group,) = _group(layout, "MATCHUPS|WR|DEN")
    assert group.depth == 2 and group.collapsed is True and group.first == team + 1
    players = [layout.rows[r - 1][0] for r in range(group.first, group.last + 1)]
    assert players == ["WR1", "WR2", "WR3"]
    tough = [r for r in layout.team_rows if layout.rows[r - 1][0] == "LV vs BUF"][0]
    assert layout.rows[tough - 1][3] == "Tough" and tough in layout.muted_rows
    assert any("CONTEXT ONLY" in str(c) or "Context only" in str(c) for r in layout.rows for c in r)


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
    lines = [layout.rows[r - 1][0] for r in layout.status_rows]
    assert lines[0] == (
        "Stats through Week 4 · Injuries: practice reports only until Friday · "
        "Projections updated Wed 10:46 pm ET"
    )
    assert "fetched Wed 8:21 am ET" in lines[1] and "UTC" not in " ".join(lines)
    assert (
        lines[3]
        == "Run the final `dfs sync --live` about 90 minutes before kickoff. First kickoff Sun 1:00 pm ET."
    )
    assert "not recorded" in eft._injury_report_line(None)


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
    names = {str(r[0]) for r in layout.rows}
    assert "On Slate" in names and "Not On DraftKings" not in names
    assert "Not On DraftKings" not in " ".join(eft.board_panel_lines(inputs))
