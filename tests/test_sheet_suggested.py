"""The Suggested tab (Week 6, slice 4): layout, the parser that reads it back, the writer, the empty state."""

import pandas as pd

from dfs import sheet_suggested as ss
from dfs.late_swap_search import SwapPlayer
from dfs.lineup_suggest import Build, SuggestReport
from dfs.models import ROSTER_SLOTS


def _player(name, pos, team="DET", opp="ARI", salary=5000, value=10.0):
    return SwapPlayer(name, pos, team, opp, "g", salary, value)


def _build(number, kind, title, tag="DET"):
    positions = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "WR", "DST"]  # FLEX holds a WR
    players = tuple(
        _player(f"{tag} {slot}{i}", pos, salary=5000 + i * 100)
        for i, (slot, pos) in enumerate(zip(ROSTER_SLOTS, positions, strict=True))
    )
    return Build(
        number=number,
        kind=kind,
        title=title,
        players=players,
        salary=sum(p.salary for p in players),
        projection=sum(p.value for p in players),
        p_cash=0.512,
        p_gpp=0.0432,
        typical=131.26,
        big_night=166.04,
        stack="DET stack + ARI bring-back" if kind == "gpp" else "",
    )


def _report(*builds, notes=()):
    return SuggestReport(
        builds=list(builds), notes=list(notes), seconds=3.2, candidates={"cash": 47, "gpp": 51}
    )


def _edge(builds):
    names = [p.name for b in builds for p in b.players]
    return pd.DataFrame(
        {
            "Name": names,
            "GameStart": ["2026-10-18T13:00:00Z"] * len(names),
            "Own%": [0.123 if i % 2 else 0.0 for i in range(len(names))],
            "GameID": [f"G{i % 3}" for i in range(len(names))],
        }
    )


def test_the_tab_opens_with_a_title_a_status_line_and_how_to_use_a_build_then_a_blank_row():
    cash, gpp = (
        _build(1, "cash", "Cash build 1"),
        _build(2, "gpp", "GPP build 2  ·  DET stack + ARI bring-back"),
    )
    layout = ss.build_layout(
        _report(cash, gpp), _edge([cash, gpp]), metric="CalPts", cash_line=143.9, gpp_target=190.0
    )
    rows = layout.rows
    assert rows[0][0] == ss.TITLE
    assert rows[1][0] == (
        "CalPts  ·  pool 47 for Cash / 51 for GPP  ·  cash line 143.9  ·  GPP target 190  ·  2 builds in 3 s"
    )
    assert rows[2][0] == ss.HOW_TO and rows[3] == [""] * ss.COLUMN_COUNT
    assert layout.bar_rows[0] == ss.FIRST_BUILD_ROW


def test_each_build_is_a_bar_a_header_nine_player_rows_and_a_total_row_then_a_blank_row():
    cash, gpp = (
        _build(1, "cash", "Cash build 1"),
        _build(2, "gpp", "GPP build 2  ·  DET stack + ARI bring-back"),
    )
    layout = ss.build_layout(
        _report(cash, gpp), _edge([cash, gpp]), metric="CalPts", cash_line=143.9, gpp_target=190.0
    )
    bar, header = layout.bar_rows[0], layout.header_rows[0]
    assert layout.rows[bar - 1][:3] == ["Build 1", "Cash", "Cash build 1"]
    assert header == bar + 1 and layout.rows[header - 1] == [
        "Slot",
        "Name",
        "Team",
        "Opp",
        "Salary",
        "CalPts",
        "Kickoff",
        "Own%",
        "Typical",
        "Big night",
        "Cash %",
        "190+ %",
        "GameID",  # the hidden helper the stack tints group by
    ]
    players = layout.player_rows[:9]
    assert [layout.rows[r - 1][12] for r in players] == [f"G{i % 3}" for i in range(9)]
    assert players == list(range(header + 1, header + 10))
    assert [layout.rows[r - 1][0] for r in players] == list(ROSTER_SLOTS)
    total = layout.total_rows[0]
    assert total == header + 10 and layout.rows[total - 1][0] == "Total"
    assert layout.rows[total - 1][4] == cash.salary and layout.rows[total - 1][5] == cash.projection
    own = [layout.rows[r - 1][7] for r in players]
    assert layout.rows[total - 1][7] == round(sum(o for o in own if o != ""), 4) > 0  # total ownership
    assert layout.rows[total - 1][8:12] == [131.3, 166.0, 0.512, 0.0432]  # Typical, Big night, Cash %, 190+ %
    assert layout.rows[total] == [""] * ss.COLUMN_COUNT  # a blank row between blocks
    assert layout.bar_rows[1] == total + 2
    assert layout.rows[layout.bar_rows[1] - 1][:3] == [
        "Build 2",
        "GPP",
        "GPP build 2  ·  DET stack + ARI bring-back",
    ]


def test_the_player_rows_carry_team_opponent_salary_projection_kickoff_and_ownership():
    cash = _build(1, "cash", "Cash build 1")
    layout = ss.build_layout(_report(cash), _edge([cash]), metric="CalPts", cash_line=143.9, gpp_target=190.0)
    first = layout.rows[layout.player_rows[0] - 1]
    assert first[:8] == [
        "QB",
        "DET QB0",
        "DET",
        "ARI",
        5000,
        10.0,
        "Sun 1:00 pm",
        "",
    ]  # no ownership yet: blank
    second = layout.rows[layout.player_rows[1] - 1]
    assert second[7] == 0.123  # published ownership shows as a fraction (the column formats it as a percent)


def test_the_projection_and_target_headers_follow_the_config():
    gpp = _build(1, "gpp", "GPP build 1  ·  DET stack + ARI bring-back")
    layout = ss.build_layout(_report(gpp), _edge([gpp]), metric="ProjPts", cash_line=140.0, gpp_target=187.5)
    header = layout.rows[layout.header_rows[0] - 1]
    assert header[5] == "ProjPts" and header[11] == "187.5+ %"


def test_the_empty_state_and_the_notes_are_said_on_the_tab():
    layout = ss.build_layout(
        _report(notes=["Only 2 GPP build(s): ..."]),
        pd.DataFrame({"Name": []}),
        metric="CalPts",
        cash_line=145.0,
        gpp_target=190.0,
    )
    texts = [r[0] for r in layout.rows]
    assert ss.EMPTY in texts and "Only 2 GPP build(s): ..." in texts
    assert not layout.bar_rows and not layout.player_rows


def test_parse_builds_reads_back_the_numbered_blocks_and_skips_anything_incomplete():
    cash, gpp = (
        _build(1, "cash", "Cash build 1"),
        _build(2, "gpp", "GPP build 2  ·  DET stack + ARI bring-back", tag="NYG"),
    )
    layout = ss.build_layout(
        _report(cash, gpp), _edge([cash, gpp]), metric="CalPts", cash_line=143.9, gpp_target=190.0
    )
    parsed = ss.parse_builds(layout.rows)
    assert [b["number"] for b in parsed] == [1, 2] and [b["kind"] for b in parsed] == ["Cash", "GPP"]
    assert parsed[0]["names"] == [p.name for p in cash.players] and parsed[1]["names"] == [
        p.name for p in gpp.players
    ]
    broken = [list(r) for r in layout.rows]
    broken[layout.player_rows[3] - 1][1] = ""  # a missing name: that block is not offered
    assert [b["number"] for b in ss.parse_builds(broken)] == [2]


class _Client:
    def __init__(self, exists=False):
        self.exists = exists
        self.written, self.formats, self.rules, self.widths, self.cleared = None, [], [], None, 0

    def tab_exists(self, tab):
        return self.exists

    def write_tab(self, tab, rows):
        self.written = (tab, rows)

    def clear_conditional_formats(self, tab):
        self.cleared += 1

    def format_range(self, tab, rng, fmt):
        self.formats.append((rng, fmt))

    def set_column_widths(self, tab, widths):
        self.widths = widths

    def add_boolean_rule(self, tab, rng, *, condition_type, values, fmt):
        self.rules.append((rng, condition_type, values))

    def hide_columns(self, tab, first, last, *, hidden=True):
        self.hidden = (first, last)

    def read_range(self, tab, rng):
        return [self.written[1][int(rng.split(":")[0][1:]) - 1]]  # the header row, "A<row>:<row>"

    def add_boolean_rules(self, tab, specs):
        self.tints = specs


def test_the_writer_rebuilds_the_tab_and_greens_cash_percent_at_fifty_and_the_gpp_chance_at_five():
    cash = _build(1, "cash", "Cash build 1")
    client = _Client(exists=True)
    message = ss.write_suggested(
        client, _report(cash), _edge([cash]), metric="CalPts", cash_line=143.9, gpp_target=190.0
    )
    assert "1 build(s) written" in message and client.written[0] == "Suggested" and client.cleared == 1
    total = ss.build_layout(
        _report(cash), _edge([cash]), metric="CalPts", cash_line=143.9, gpp_target=190.0
    ).total_rows[0]
    assert (f"K{total}", "NUMBER_GREATER_THAN_EQ", ["0.5"]) in client.rules
    assert (f"L{total}", "NUMBER_GREATER_THAN_EQ", ["0.05"]) in client.rules
    assert client.widths["B"] == 170  # the Name column
    assert client.hidden == ("M", "M")  # the GameID helper
    # Lineups' three correlation tints over the build's Name and Team cells (B:C); Slot is the position
    first_player = total - 9
    assert {spec["a1_range"] for spec in client.tints} == {f"B{first_player}:C{total - 1}"}
    assert len(client.tints) == 3 and "$A" in client.tints[0]["values"][0]  # the Slot column
    assert any(rng == f"K{total}" and fmt["numberFormat"]["pattern"] == "0%" for rng, fmt in client.formats)


def test_build_views_creates_the_tab_empty_and_never_touches_one_that_exists():
    created = _Client(exists=False)
    assert ss.ensure_suggested_tab(created).endswith("created (empty)")
    assert created.written[1][0][0] == ss.TITLE
    present = _Client(exists=True)
    assert "already present" in ss.ensure_suggested_tab(present) and present.written is None


def test_a_tab_nothing_has_written_to_says_it_has_not_been_run_instead_of_showing_zeros():
    layout = ss.build_layout(
        SuggestReport(), pd.DataFrame({"Name": []}), metric="CalPts", cash_line=0.0, gpp_target=0.0
    )
    assert layout.rows[1][0] == "Not run yet: `dfs lineups suggest` fills this tab from your pool."
