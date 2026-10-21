"""The Cockpit tab's pure builder: rows from frames and records, no sheet, no network."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from dfs import cockpit
from dfs import late_swap_store as lss
from dfs.cockpit import CockpitInputs, DkEntry, PlanNumbers
from dfs.entries import SheetLineup

NOW = datetime(2026, 10, 25, 15, 30, tzinfo=UTC)  # 11:30 ET, ahead of the 1:00 PM games
EARLY, LATE = "2026-10-25T13:00:00Z", "2026-10-25T16:25:00Z"  # TFFB writes Eastern wall time with a "Z"

# slot order: QB RB RB WR WR WR TE FLEX DST
NAMES = ["Qb One", "Rb One", "Rb Two", "Wr One", "Wr Two", "Wr Three", "Te One", "Flex Wr", "Home DST"]
POSITIONS = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "WR", "DST"]


def edge(overrides: dict | None = None) -> pd.DataFrame:
    rows = []
    for i, (name, pos) in enumerate(zip(NAMES, POSITIONS, strict=True)):
        late = i in (3, 8)  # Wr One and the DST play the 4:25 game
        rows.append(
            {
                "Name": name,
                "Position": pos,
                "Team": "GB" if late else "ATL",
                "GameID": "2026_07_GB_DET" if late else "2026_07_SF_ATL",
                "GameStart": LATE if late else EARLY,
                "Avail": "",
                "Wind": 4.0 if late else 20.0,
                "Roof": "dome" if late else "outdoors",
                "WX": "",
                "Own%": 0.10,
                "CalPts": 12.0 - i,
            }
        )
    frame = pd.DataFrame(rows)
    for (name, column), value in (overrides or {}).items():
        frame.loc[frame["Name"] == name, column] = value
    return frame


def lineup(number=1, names=None, marker="", own=None) -> SheetLineup:
    return SheetLineup(
        number=number, names=tuple(names if names is not None else NAMES), marker=marker, own=own
    )


def inputs(**kw) -> CockpitInputs:
    base = {
        "now": NOW,
        "sheet_title": "Week 7",
        "edge": edge(),
        "lineups": [lineup(1), lineup(2)],
        "week": 7,
    }
    base.update(kw)
    return CockpitInputs(**base)


def rows_with(layout: cockpit.Layout, cue: str) -> list[list]:
    return [layout.rows[r - 1] for r, c in layout.cue_rows.items() if c == cue]


def text(layout: cockpit.Layout) -> str:
    return " | ".join(str(c) for row in layout.rows for c in row if c != "")


def section(layout: cockpit.Layout, number: int) -> list[list]:
    """The rows of block `number` (from its section row to the next)."""
    marks = layout.section_rows
    start = next(r for r in marks if layout.rows[r - 1][0].startswith(f"{number}."))
    later = [r for r in marks if r > start]
    end = later[0] if later else len(layout.rows) + 1
    return layout.rows[start : end - 1]


# ---- shape -------------------------------------------------------------------------------------------


def test_every_row_is_the_tab_width_and_cues_stay_in_the_vocabulary():
    layout = cockpit.build_layout(inputs())
    assert {len(r) for r in layout.rows} == {cockpit.COLUMN_COUNT}
    assert set(layout.cue_rows.values()) <= set(cockpit.CUES)
    assert all(layout.rows[r - 1][0] == c for r, c in layout.cue_rows.items())


def test_text_that_would_read_as_a_formula_is_neutralised():
    layout = cockpit.build_layout(inputs(sheet_title="=SUM(A1)"))
    assert layout.rows[1][0].startswith("'=SUM(A1)")


def test_header_says_week_and_the_eastern_clock():
    layout = cockpit.build_layout(inputs())
    assert layout.rows[0][0] == "COCKPIT  Week 7  —  as of 11:30 AM ET"
    assert "2 lineups built" in layout.rows[1][0]


def test_next_lock_counts_the_clock_the_games_and_your_slots():
    layout = cockpit.build_layout(inputs())
    line = layout.rows[layout.headline_rows[0] - 1][1]
    assert line.startswith("NEXT LOCK  1:00 PM ET  in 1h 30m  (1 game; 14 of your slots)")
    assert line.endswith("then 4:25 PM")


def test_next_lock_after_every_kickoff():
    layout = cockpit.build_layout(inputs(now=datetime(2026, 10, 26, 6, 0, tzinfo=UTC)))
    assert "every game has kicked off" in layout.rows[layout.headline_rows[0] - 1][1]


def test_no_built_lineups_say_so_in_every_lineup_block():
    layout = cockpit.build_layout(inputs(lineups=[lineup(1, names=[""] * 9)]))
    for number in (1, 2, 3, 4, 5):
        assert any("No lineups built yet" in str(c) for row in section(layout, number) for c in row)


def test_lineup_ranges_collapse_runs():
    assert cockpit.lineup_ranges([5, 1, 2, 3, 9, 10]) == "L1-L3 L5 L9-L10"


# ---- 1. news -----------------------------------------------------------------------------------------


def test_a_new_out_in_two_lineups_is_a_swap_row_with_what_it_was():
    now_edge = edge({("Rb One", "Avail"): "OUT"})
    before = edge({("Rb One", "Avail"): "Q"})
    layout = cockpit.build_layout(inputs(edge=now_edge, previous_edge=before))
    swaps = [r for r in rows_with(layout, cockpit.CUE_SWAP) if r[1] == "Rb One"]
    assert len(swaps) == 1
    assert swaps[0][3] == "OUT (was Q)" and swaps[0][4] == "L1-L2" and swaps[0][5] == "1:00 PM"


def test_a_standing_question_mark_is_a_look_and_a_clear_player_is_not_listed():
    layout = cockpit.build_layout(inputs(edge=edge({("Wr Two", "Avail"): "Q"})))
    looks = [r for r in rows_with(layout, cockpit.CUE_LOOK) if r[1] == "Wr Two"]
    assert looks and looks[0][3] == "Q (standing)"
    assert not any(r[1] == "Qb One" for r in section(layout, 1))


def test_a_started_game_is_never_news():
    later = datetime(2026, 10, 25, 17, 30, tzinfo=UTC)  # 1:30 ET: the early game has kicked off
    layout = cockpit.build_layout(inputs(now=later, edge=edge({("Rb One", "Avail"): "OUT"})))
    assert not any(r[1] == "Rb One" for r in section(layout, 1))
    assert any(
        "No new or standing Q / D / OUT among the 2 players still to play" in str(c)
        for r in section(layout, 1)
        for c in r
    )


def test_a_player_who_came_back_is_news_too():
    layout = cockpit.build_layout(inputs(edge=edge(), previous_edge=edge({("Rb One", "Avail"): "Q"})))
    row = next(r for r in section(layout, 1) if r[1] == "Rb One")
    assert row[3] == "clear (was Q)"


def test_quiet_slate_says_so():
    layout = cockpit.build_layout(inputs())
    assert any("No new or standing Q / D / OUT" in str(c) for r in section(layout, 1) for c in r)


def test_espn_news_from_the_late_swap_run_escalates_and_is_marked():
    saved = lss.Saved(
        saved_at=NOW,
        refreshed_injuries=True,
        news=(lss.NewsItem("Wr Two", "", "OUT", "ruled out"),),
    )
    layout = cockpit.build_layout(inputs(saved_swap=saved))
    row = next(r for r in rows_with(layout, cockpit.CUE_SWAP) if r[1] == "Wr Two")
    assert row[3].endswith("· ESPN") and row[3].startswith("OUT")
    assert any("ESPN's game-day feed" in str(c) for r in section(layout, 1) for c in r)


# ---- 2. swappable ------------------------------------------------------------------------------------


def result(number, names=None, best=None, open_slots=("RB", "WR")):
    return lss.LineupResult(number, "cash", tuple(names or NAMES), tuple(open_slots), best)


ALL_OPEN = ("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST")


def test_best_change_from_the_saved_run_is_a_swap_row_with_point_changes():
    best = lss.BestChange("single", ("Rb One",), ("Rb Nine",), 1.5, 0.031, -0.004)
    saved = lss.Saved(NOW, False, (result(1, best=best, open_slots=ALL_OPEN), result(2, open_slots=ALL_OPEN)))
    layout = cockpit.build_layout(inputs(saved_swap=saved))
    rows = section(layout, 2)
    one = next(r for r in rows if r[1] == "L1 cash")
    assert one[0] == "SWAP" and one[3] == "Rb One -> Rb Nine" and one[4] == "'+3.1" and one[5] == "'-0.4"
    two = next(r for r in rows if r[1] == "L2 cash")
    assert two[0] == "ok" and two[3] == "nothing beats it"


def test_the_swappable_note_says_which_field_the_run_was_scored_against():
    best = lss.BestChange("single", ("Rb One",), ("Rb Nine",), 1.5, 0.031, -0.004)
    for source, shown in (
        (lss.FIELD_PROJECTED, "Field: projected."),
        (
            "field: real (NFL $5 Double Up, exported Sun 1:32 pm ET)",
            "Field: real (NFL $5 Double Up, exported",
        ),
    ):
        saved = lss.Saved(NOW, False, (result(1, best=best, open_slots=ALL_OPEN),), field_source=source)
        layout = cockpit.build_layout(inputs(saved_swap=saved))
        assert any(shown in str(c) for r in section(layout, 2) for c in r)


def test_a_lineup_edited_since_the_run_is_not_shown_as_current():
    changed = [*NAMES[:-1], "Other DST"]
    saved = lss.Saved(NOW, False, (result(1, names=changed, open_slots=ALL_OPEN),))
    layout = cockpit.build_layout(inputs(saved_swap=saved, lineups=[lineup(1)]))
    row = next(r for r in section(layout, 2) if r[1] == "L1 cash")
    assert row[0] == "LOOK" and "changed since the late-swap run" in row[3]


def test_no_saved_run_or_one_from_yesterday_asks_for_late_swap():
    layout = cockpit.build_layout(inputs(lineups=[lineup(1)]))
    row = next(r for r in section(layout, 2) if r[1] == "L1 cash")
    assert row[0] == "LOOK" and "no late-swap result" in row[3]
    old = lss.Saved(datetime(2026, 10, 24, 12, 0, tzinfo=UTC), False, (result(1, open_slots=ALL_OPEN),))
    layout = cockpit.build_layout(inputs(lineups=[lineup(1)], saved_swap=old))
    assert any("earlier day" in str(c) for r in section(layout, 2) for c in r)


def test_a_game_that_locked_after_the_run_is_flagged():
    later = datetime(2026, 10, 25, 17, 30, tzinfo=UTC)  # the early game has kicked off, the late one not
    saved = lss.Saved(NOW, False, (result(1, open_slots=ALL_OPEN),))
    layout = cockpit.build_layout(inputs(now=later, saved_swap=saved, lineups=[lineup(1)]))
    row = next(r for r in section(layout, 2) if r[1] == "L1 cash")
    assert row[2] == "2: WR, DST" and "a game locked" in row[3]


def test_fully_locked_lineups_are_grouped_into_one_line():
    later = datetime(2026, 10, 26, 6, 0, tzinfo=UTC)
    layout = cockpit.build_layout(inputs(now=later))
    row = next(r for r in section(layout, 2) if r[2] == "all slots locked")
    assert row[1] == "L1-L2"


def test_best_change_without_probabilities_shows_points():
    best = lss.BestChange("pair", ("Rb One", "Rb Two"), ("A", "B"), 2.25)
    saved = lss.Saved(NOW, False, (result(1, best=best, open_slots=ALL_OPEN),))
    layout = cockpit.build_layout(inputs(saved_swap=saved, lineups=[lineup(1)]))
    row = next(r for r in section(layout, 2) if r[1] == "L1 cash")
    assert row[3] == "Rb One + Rb Two -> A + B  (+2.2 pts)" or row[3].endswith("(+2.3 pts)")


# ---- 3. weather --------------------------------------------------------------------------------------


def test_windy_games_lead_and_are_flagged():
    layout = cockpit.build_layout(inputs(lineups=[lineup(1)]))
    rows = [r for r in section(layout, 3) if r[0] in ("LOOK", "ok")]
    assert rows[0][0] == "LOOK" and rows[0][1] == "SF @ ATL" and rows[0][2] == "20 mph"
    assert rows[1][0] == "ok" and rows[1][3] == "dome"


def test_started_games_drop_out_of_the_weather_list():
    later = datetime(2026, 10, 25, 17, 30, tzinfo=UTC)
    layout = cockpit.build_layout(inputs(now=later, lineups=[lineup(1)]))
    labels = [r[1] for r in section(layout, 3)]
    assert "SF @ ATL" not in labels and "GB @ DET" in labels


# ---- 4. warnings -------------------------------------------------------------------------------------


def test_overuse_meter_and_chalk_are_look_rows():
    lineups = [lineup(n, marker="GPP", own=1.6 if n == 1 else 1.3) for n in (1, 2, 3)]
    chalky = edge()
    chalky.loc[len(chalky)] = {
        **chalky.iloc[0].to_dict(),
        "Name": "Chalk Guy",
        "Position": "RB",
        "Own%": 0.60,
    }
    layout = cockpit.build_layout(inputs(edge=chalky, lineups=lineups))
    kinds = {r[1] for r in section(layout, 4) if r[0] == "LOOK"}
    assert {"Overused", "GPP meter", "Chalk out"} <= kinds
    meter = next(r for r in section(layout, 4) if r[1] == "GPP meter")
    assert meter[2].startswith("L1: Own sum 160%")


def test_nothing_flagged_is_one_ok_line():
    layout = cockpit.build_layout(inputs(lineups=[lineup(1)]))
    assert [r for r in section(layout, 4) if r[0]] == [["ok", "Nothing flagged.", "", "", "", "", "", ""]]


# ---- 5. exposure -------------------------------------------------------------------------------------


def test_exposure_lists_the_most_held_first_with_share_and_target():
    other = list(NAMES)
    other[1] = "Rb Two"  # lineup 2 swaps Rb One out (RB, RB -> Rb Two twice is fine for counting)
    layout = cockpit.build_layout(
        inputs(lineups=[lineup(1), lineup(2, names=other), lineup(3, names=other)], targets={"Qb One": 0.5})
    )
    rows = [r for r in section(layout, 5) if r[1] and isinstance(r[3], int)]
    assert len(rows) == 9  # nine distinct players, fewer than the cap of TOP_EXPOSURE
    two = next(r for r in rows if r[1] == "Rb Two")  # typed twice in lineups 2 and 3: still 3 lineups, 100%
    assert two[3] == 3 and two[4] == 1.0
    assert rows[-1][1] == "Rb One" and rows[-1][3] == 1  # most held first, least held last
    qb = next(r for r in rows if r[1] == "Qb One")
    assert qb[3] == 3 and qb[5] == 0.5
    assert layout.percent_cells and layout.number_cells


# ---- 6/7. DK Upload and budget -----------------------------------------------------------------------


def entries():
    return [
        DkEntry("1", 5.0, "GPP", False, "L1"),
        DkEntry("2", 5.0, "GPP", False, ""),
        DkEntry("3", 5.0, "GPP", False, ""),
        DkEntry("4", 25.0, "Double Up", True, ""),
        DkEntry("5", 10.0, "GPP", False, "L9"),
    ]


def test_unpicked_entries_are_a_fill_row_with_the_format_mix():
    layout = cockpit.build_layout(inputs(dk_entries=entries()))
    rows = section(layout, 6)
    fill = next(r for r in rows if r[0] == "FILL")
    assert fill[1] == "3 of 5 entries have no Lineup picked"
    assert fill[2] == "GPP $5 x2, Double Up $25 x1"
    assert any(r[0] == "LOOK" and "L9" in r[1] for r in rows)


def test_all_picked_is_ok_and_an_unreadable_tab_names_the_reason():
    done = [DkEntry("1", 5.0, "GPP", False, "L1")]
    layout = cockpit.build_layout(inputs(dk_entries=done))
    assert any(r[0] == "ok" and r[1] == "All 1 entries have a Lineup picked." for r in section(layout, 6))
    layout = cockpit.build_layout(inputs(dk_entries=None, unavailable={"dk": "tab missing"}))
    assert any("could not be read: tab missing" in str(c) for r in section(layout, 6) for c in r)


def test_budget_compares_picked_and_all_entries_with_the_plan():
    plan = PlanNumbers(200.0, 200.0, 90.0, 110.0, entry_cap=20.0, warnings=("a rule is broken",))
    layout = cockpit.build_layout(inputs(dk_entries=entries(), plan=plan))
    rows = section(layout, 7)
    assert rows[0][1] == "Picked entries cost $15" and rows[0][3] == "of $200 planned"
    over = next(r for r in rows if r[1].startswith("Entering all 5"))
    assert over[0] == "ok" and over[1] == "Entering all 5 would cost $50"
    assert any(r[0] == "LOOK" and "Largest entry fee $25" in r[1] for r in rows)
    assert any(r[0] == "LOOK" and r[1] == "a rule is broken" for r in rows)


def test_entering_everything_above_the_plan_is_a_look():
    plan = PlanNumbers(30.0, 30.0, 10.0, 20.0, None)
    layout = cockpit.build_layout(inputs(dk_entries=entries(), plan=plan))
    over = next(r for r in section(layout, 7) if r[1].startswith("Entering all 5"))
    assert over[0] == "LOOK" and over[2] == "above the plan"


def test_no_plan_names_why():
    layout = cockpit.build_layout(inputs(plan=None, unavailable={"plan": "Bankroll tab missing"}))
    assert any(
        "Budget could not be read: Bankroll tab missing" in str(c) for r in section(layout, 7) for c in r
    )


def test_a_long_overuse_list_is_cut_with_a_count_of_the_rest():
    many = [lineup(n) for n in (1, 2, 3)]  # nine players, all in every lineup
    layout = cockpit.build_layout(inputs(lineups=many))
    rows = [r for r in section(layout, 4) if r[1] == "Overused"]
    assert len(rows) == cockpit.OVERUSE_LISTED + 1
    assert rows[-1][2] == "and 4 more players in over half of them"


def test_exposure_divides_by_the_built_lineups_so_nine_of_fifteen_is_sixty_percent_not_150():
    # Week 6: Exposure's typed divisor stayed at 6 while 15 lineups were built, so a player in 9 read 150%.
    other = [f"Other {i}" for i in range(9)]
    held = [lineup(n) if n <= 9 else lineup(n, names=other) for n in range(1, 16)]
    layout = cockpit.build_layout(inputs(lineups=held))
    rows = {r[1]: r for r in section(layout, 5) if r[1] and isinstance(r[3], int)}
    assert rows["Qb One"][3] == 9 and rows["Qb One"][4] == 0.6
    assert all(r[4] <= 1.0 for r in rows.values())


def test_the_exposure_divisor_counts_lineups_that_hold_a_player_and_is_never_zero():
    built = [lineup(1), lineup(2)]
    assert cockpit.exposure_divisor(built) == 2
    assert cockpit.exposure_divisor([]) == 1
    empty = lineup(3, names=[""] * 9)
    assert not empty.built  # an all-blank block does not count, as in H1's COUNTIF(.., "?*") > 0
