"""The Stacks tab layout: what it shows, what it hides before ownership is published, and how it sorts."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from dfs import stacks, stacks_tab
from dfs.stacks_tab import StackTabInputs

NOW = datetime(2026, 10, 21, 15, 0, tzinfo=UTC)


def table(n: int = 25) -> pd.DataFrame:
    rows = []
    for i in range(n):
        rows.append(
            {
                "qb": f"QB {i}",
                "mates": f"WR {i}, TE {i}",
                "team": "AAA",
                "opp": "BBB",
                "k": 2,
                "salary": 15_000 + 100 * i,
                "proj": 40.0 - i / 10,
                "top1": 0.02 - 0.0004 * i,
                "top1_naked": 0.019,
                "top1_bb": 0.021,
                "bb_edge": 0.002,
                "lift": (0.02 - 0.0004 * i) / 0.01,
                "field": 0.01 + 0.0005 * i,
                "field_src": "gen",
                "boom_share": 0.03 - 0.001 * i,
                "lev": 0.02 - 0.0015 * i,
                "qb_i": i,
                "mates_i": (i + 100, i + 200),
            }
        )
    return pd.DataFrame(rows)


def games() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "game": ["AAA v BBB", "CCC v DDD"],
            "teams": [("AAA", "BBB"), ("CCC", "DDD")],
            "stack_rate": [0.31, 0.22],
            "game_stack_rate": [0.12, 0.08],
            "best_lev": [0.01, -0.002],
            "total": [51.5, 44.5],
            "pace": [34.0, 31.5],
            "spread": [3.0, 1.0],
        }
    )


def inputs(**kw) -> StackTabInputs:
    result = stacks.StackResult(table=table(), games=games(), meta={"n_sims": 4000})
    base = dict(
        result=result,
        sheet_title="Week 7",
        now=NOW,
        week=7,
        ownership_published=True,
        field_weeks=(2, 4, 5, 6),
        retro_line="Track record (weeks 4, 5, 6): fine.",
        verdict="tested",
    )
    base.update(kw)
    return StackTabInputs(**base)


def col(layout, letter, first, last):
    c = ord(letter) - ord("A")
    return [layout.rows[r - 1][c] for r in range(first, last + 1)]


def stack_rows(layout):
    header = layout.header_rows[0]
    return header + 1, header + stacks.STACK_ROWS


def test_the_tab_has_the_title_status_track_record_and_both_blocks():
    lay = stacks_tab.build_layout(inputs())
    assert lay.rows[0][0].startswith("STACKS  Week 7")
    assert "ownership: published" in lay.rows[1][0] and "weeks 2, 4, 5, 6" in lay.rows[1][0]
    assert "4,000 simulated draws" in lay.rows[1][0]
    assert any("Track record (weeks 4, 5, 6)" in str(r[1]) for r in lay.rows)
    assert len(lay.section_rows) == 2 and len(lay.header_rows) == 2
    first, last = stack_rows(lay)
    assert last - first + 1 == 20
    assert col(lay, "B", first, first)[0] == "QB 0"
    gfirst = lay.header_rows[1] + 1
    assert col(lay, "B", gfirst, gfirst + 1) == ["AAA v BBB", "CCC v DDD"]


def test_the_cue_is_build_only_for_positive_leverage_and_lift_when_the_signal_is_tested_or_weak():
    for verdict in ("tested", "weak"):
        lay = stacks_tab.build_layout(inputs(verdict=verdict))
        first, last = stack_rows(lay)
        cues = col(lay, "A", first, last)
        levs = col(lay, "K", first, last)
        assert [c == "BUILD" for c in cues] == [lev > 0 for lev in levs]
        assert set(lay.cue_rows.values()) == {"BUILD"}
    for verdict in ("untested", None):
        lay = stacks_tab.build_layout(inputs(verdict=verdict))
        assert not lay.cue_rows


def test_the_last_cell_is_the_line_to_type_into_the_rules_box():
    lay = stacks_tab.build_layout(inputs())
    first, _ = stack_rows(lay)
    assert col(lay, "M", first, first) == ["Always use: QB 0, WR 0, TE 0"]
    assert (
        stacks_tab.rule_text("Matthew Stafford", "Puka Nacua") == "Always use: Matthew Stafford, Puka Nacua"
    )


def test_the_header_names_how_far_the_leverage_signal_can_be_trusted():
    def lev_header(verdict):
        lay = stacks_tab.build_layout(inputs(verdict=verdict))
        return lay.rows[lay.header_rows[0] - 1][stacks_tab.STACK_HEADERS.index("Lev") + 1 + 0]

    assert lev_header("tested") == "Lev"
    assert lev_header("weak") == "Lev (weak signal)"
    assert lev_header("untested") == "Lev (untested)"
    assert lev_header(None) == "Lev (untested)"


def test_an_untested_signal_sorts_by_boom_share_and_a_tested_one_by_leverage():
    t = table().sample(frac=1, random_state=1)
    tested, label = stacks_tab.sorted_table(t, published=True, verdict="tested")
    assert label == "leverage" and tested["lev"].is_monotonic_decreasing
    weak, label = stacks_tab.sorted_table(t, published=True, verdict="weak")
    assert label == "leverage"
    untested, label = stacks_tab.sorted_table(t, published=True, verdict="untested")
    assert label == "boom share" and untested["boom_share"].is_monotonic_decreasing
    unpublished, label = stacks_tab.sorted_table(t, published=False, verdict="tested")
    assert "no ownership yet" in label and unpublished["top1"].is_monotonic_decreasing


def test_before_ownership_is_published_the_field_columns_are_dashes_and_nothing_is_cued():
    lay = stacks_tab.build_layout(inputs(ownership_published=False))
    first, last = stack_rows(lay)
    for letter in "IJK":
        assert set(col(lay, letter, first, last)) == {"-"}
    assert not lay.cue_rows
    assert "NOT published" in lay.rows[1][0]
    assert any("appear once TFFB publishes ownership" in str(r[1]) for r in lay.rows)
    gfirst = lay.header_rows[1] + 1
    assert set(col(lay, "F", gfirst, gfirst + 1)) == {"-"}
    assert col(lay, "C", gfirst, gfirst)[0] == 51.5  # the totals still show


def test_a_missing_track_record_says_so_and_a_failed_build_says_why():
    lay = stacks_tab.build_layout(inputs(retro_line="", verdict=None))
    assert any("retro has not been run" in str(r[1]) for r in lay.rows)
    failed = stacks_tab.build_layout(inputs(unavailable="no fitted field model"))
    assert any("Not built: no fitted field model" in str(r[1]) for r in failed.rows)
    assert not failed.header_rows


def test_number_formats_cover_the_stack_and_game_columns():
    lay = stacks_tab.build_layout(inputs())
    ranges = {r for r, _p, _k in lay.formats}
    first, last = stack_rows(lay)
    assert f"G{first}:G{last}" in ranges and f"K{first}:K{last}" in ranges


def test_the_terminal_render_has_one_line_per_row_and_formats_percentages():
    lay = stacks_tab.build_layout(inputs())
    text = stacks_tab.render_text(lay)
    assert "QB 0" in text and "2.0%" in text and "$15,000" in text and "2.0x" in text
    assert text.count("\n") >= len(lay.rows) - 1
