"""The Suggested tab (Week 6, slice 4): layout, the parser that reads it back, the writer, the empty state."""

from contextlib import contextmanager
from dataclasses import replace

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
        "CalPts  ·  pool 47 for Cash / 51 for GPP, plus any DST on the slate (pooled or not)  ·  "
        "cash line 143.9  ·  GPP target 190  ·  2 builds in 3 s"
    )
    assert rows[2][0].startswith(ss.HOW_TO) and rows[3] == [""] * ss.COLUMN_COUNT
    assert ss.RANK_NOTES["top1"] in rows[2][0] and ss.TITLE_KEY in rows[2][0]
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
    # the kind over the lock column, the decision cell over Name (blank: Rebuild), the title after it
    assert layout.rows[bar - 1][:4] == ["Build 1", "Cash", "", "Cash build 1"]
    assert header == bar + 1 and layout.rows[header - 1] == [
        "Slot",
        "🔒",
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
        "Top 1%",
        "GameID",  # the hidden helper the stack tints group by
        "Yours",  # the hidden helper behind the "your pick" tint
    ]
    players = layout.player_rows[:9]
    assert [layout.rows[r - 1][ss.COLUMN_COUNT - 2] for r in players] == [f"G{i % 3}" for i in range(9)]
    assert players == list(range(header + 1, header + 10))
    assert [layout.rows[r - 1][0] for r in players] == list(ROSTER_SLOTS)
    total = layout.total_rows[0]
    assert total == header + 10 and layout.rows[total - 1][0] == "Total"
    col = ss.COLUMNS.index
    assert layout.rows[total - 1][col("Salary")] == cash.salary
    assert layout.rows[total - 1][col("CalPts")] == cash.projection
    own = [layout.rows[r - 1][col("Own%")] for r in players]
    assert (
        layout.rows[total - 1][col("Own%")] == round(sum(o for o in own if o != ""), 4) > 0
    )  # total ownership
    assert layout.rows[total - 1][col("Typical") : col("Top 1%") + 1] == [131.3, 166.0, 0.512, 0.0432]
    assert layout.rows[total] == [""] * ss.COLUMN_COUNT  # a blank row between blocks
    assert layout.bar_rows[1] == total + 2
    assert layout.rows[layout.bar_rows[1] - 1][:4] == [
        "Build 2",
        "GPP",
        "",
        "GPP build 2  ·  DET stack + ARI bring-back",
    ]


def test_the_player_rows_carry_team_opponent_salary_projection_kickoff_and_ownership():
    cash = _build(1, "cash", "Cash build 1")
    layout = ss.build_layout(_report(cash), _edge([cash]), metric="CalPts", cash_line=143.9, gpp_target=190.0)
    first = layout.rows[layout.player_rows[0] - 1]
    assert first[:9] == [
        "QB",
        False,  # the lock tick
        "DET QB0",
        "DET",
        "ARI",
        5000,
        10.0,
        "Sun 1:00 pm",
        "",
    ]  # no ownership yet: blank
    second = layout.rows[layout.player_rows[1] - 1]
    assert (
        second[ss.COLUMNS.index("Own%")] == 0.123
    )  # published ownership shows as a fraction (the column formats it as a percent)


def test_the_projection_header_follows_the_config_and_the_top_header_is_fixed():
    gpp = _build(1, "gpp", "GPP build 1  ·  DET stack + ARI bring-back")
    layout = ss.build_layout(_report(gpp), _edge([gpp]), metric="ProjPts", cash_line=140.0, gpp_target=187.5)
    header = layout.rows[layout.header_rows[0] - 1]
    assert header[ss.COLUMNS.index("CalPts")] == "ProjPts" and header[ss.COLUMNS.index("Top 1%")] == "Top 1%"


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
    broken[layout.player_rows[3] - 1][ss.NAME_INDEX] = ""  # a missing name: that block is not offered
    assert [b["number"] for b in ss.parse_builds(broken)] == [2]


class _Client:
    def __init__(self, exists=False):
        self.exists = exists
        self.batches, self.in_batch, self.formats_in_batch = 0, False, 0
        self.written, self.formats, self.rules, self.widths, self.cleared = None, [], [], None, 0

    def tab_exists(self, tab):
        return self.exists

    def write_tab(self, tab, rows):
        self.written = (tab, rows)

    def clear_conditional_formats(self, tab):
        self.cleared += 1

    def clear_data_validation(self, tab, rng):
        self.cleared_validation = rng

    def set_checkbox_validation(self, tab, rng):
        self.checkboxes = [*getattr(self, "checkboxes", []), rng]

    def set_dropdown_validation(self, tab, rng, options, *, strict=True):
        self.dropdowns = [*getattr(self, "dropdowns", []), (rng, options, strict)]

    def add_boolean_rule_plain(self):  # pragma: no cover
        pass

    @contextmanager
    def batched(self):
        self.batches += 1
        self.in_batch = True
        try:
            yield self
        finally:
            self.in_batch = False

    def format_range(self, tab, rng, fmt):
        self.formats.append((rng, fmt))
        self.formats_in_batch += self.in_batch

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


def test_the_writer_rebuilds_the_tab_and_greens_cash_percent_at_fifty_and_top_one_percent_at_two():
    cash = _build(1, "cash", "Cash build 1")
    client = _Client(exists=True)
    message = ss.write_suggested(
        client, _report(cash), _edge([cash]), metric="CalPts", cash_line=143.9, gpp_target=190.0
    )
    assert "1 build(s) written" in message and client.written[0] == "Suggested" and client.cleared == 1
    total = ss.build_layout(
        _report(cash), _edge([cash]), metric="CalPts", cash_line=143.9, gpp_target=190.0
    ).total_rows[0]
    assert (f"L{total}", "NUMBER_GREATER_THAN_EQ", ["0.5"]) in client.rules
    assert (f"M{total}", "NUMBER_GREATER_THAN_EQ", ["0.02"]) in client.rules
    assert client.widths["C"] == 170  # the Name column (the lock column is B)
    assert client.hidden == ("N", "O")  # the GameID and Yours helpers
    # Lineups' three correlation tints over the build's Name and Team cells (C:D); Slot is the position
    first_player = total - 9
    assert {spec["a1_range"] for spec in client.tints} == {f"C{first_player}:D{total - 1}"}
    assert len(client.tints) == 3 and "$A" in client.tints[0]["values"][0]  # the Slot column
    assert any(rng == f"L{total}" and fmt["numberFormat"]["pattern"] == "0%" for rng, fmt in client.formats)
    # the controls: a checkbox per player run, a decision dropdown on the bar, the "your pick" rule on top
    assert client.checkboxes == [f"B{first_player}:B{total - 1}"]
    bar = first_player - 2
    assert client.dropdowns == [(f"C{bar}", ss.DECISION_OPTIONS, False)]  # Vary N is typed, so non-strict
    assert (f"C{first_player}:C{total - 1}", "CUSTOM_FORMULA", [f"=$O{first_player}=TRUE"]) in client.rules
    assert client.cleared_validation.startswith("A1:O")  # last run's ticks and dropdowns go first


def test_the_typed_columns_are_the_lock_column_and_the_name_column_and_the_rules_box_sits_above_the_builds():
    assert ss.typed_ranges() == ["B:B", "C:C"]
    cash = _build(1, "cash", "Cash build 1")
    report = _report(cash)
    report.rules = {"Always use": "Bijan", "Never use": "Kyren"}
    layout = ss.build_layout(report, _edge([cash]), metric="CalPts", cash_line=1.0, gpp_target=2.0)
    labels = [layout.rows[r - 1][0] for r in layout.rules_rows]
    assert labels == list(ss.RULE_LABELS) and layout.bar_rows[0] == ss.FIRST_BUILD_ROW
    assert layout.rows[layout.rules_rows[0] - 1][ss.NAME_INDEX] == "Bijan"


def test_the_state_the_writer_puts_on_the_tab_is_the_state_the_reader_gets_back():
    """Sheet <-> state: decisions, ticks, the rules box and the round survive a write and a parse."""
    cash = _build(1, "cash", "Cash build 1")
    gpp = _build(2, "gpp", "GPP build 2  ·  DET stack + ARI bring-back")
    cash = replace(cash, decision="Keep", locked=(cash.players[0].name, cash.players[4].name), note="kept")
    gpp = replace(gpp, yours=(gpp.players[1].name,), locked=(gpp.players[1].name,))
    report = _report(cash, gpp)
    report.round, report.rules = 4, {"Max exposure": "Stafford: 4", "Builds wanted": "cash 1, gpp 1"}
    layout = ss.build_layout(report, _edge([cash, gpp]), metric="CalPts", cash_line=1.0, gpp_target=2.0)
    state = ss.parse_state(layout.rows)
    assert (
        state.round == 4 and state.rules.exposure == "Stafford: 4" and state.rules.wanted == "cash 1, gpp 1"
    )
    assert [(b.number, b.kind, b.decision) for b in state.builds] == [(1, "cash", "Keep"), (2, "gpp", "")]
    ticked = [n for n, tick in zip(state.builds[0].names, state.builds[0].locked, strict=True) if tick]
    assert ticked == [cash.players[0].name, cash.players[4].name]
    assert state.builds[1].names == [p.name for p in gpp.players]
    # a "yours" name carries its flag in the hidden helper column the tint rule reads
    flags = [layout.rows[r - 1][ss.COLUMN_COUNT - 1] for r in layout.player_rows[9:]]
    assert flags.count(True) == 1 and flags[1] is True
    # a blank name is kept blank (the rerun reports it)
    typed = [list(r) for r in layout.rows]
    typed[layout.player_rows[3] - 1][ss.NAME_INDEX] = ""
    assert ss.parse_state(typed).builds[0].names[3] == ""


def _protectable(client, grid):
    client.read_tab = lambda tab: grid
    client.protected, client.protection_cleared = [], 0
    client.clear_protected_ranges = lambda tab: setattr(
        client, "protection_cleared", client.protection_cleared + 1
    )
    client.protect_sheet = lambda tab, **kw: client.protected.append((tab, kw["unprotected_ranges"]))
    return client


def test_setup_controls_rewrites_an_empty_tab_keeping_typed_rules_and_protects_all_but_the_typed_columns():
    typed = ss.build_layout(
        SuggestReport(rules={"Never use": "Kyren"}),
        pd.DataFrame({"Name": []}),
        metric="CalPts",
        cash_line=0.0,
        gpp_target=0.0,
    ).rows
    client = _protectable(_Client(exists=True), typed)
    lines = ss.setup_controls(client)
    assert ss.parse_state(client.written[1]).rules.never == "Kyren" and "rewritten" in lines[0]
    assert client.protected == [("Suggested", ["B:B", "C:C"])] and client.protection_cleared == 1
    again = ss.setup_controls(client)  # idempotent: the same writes, one protection (cleared first)
    assert again == lines and client.protection_cleared == 2 and len(client.protected) == 2


def test_setup_controls_leaves_a_tab_that_holds_builds_alone():
    cash = _build(1, "cash", "Cash build 1")
    grid = ss.build_layout(_report(cash), _edge([cash]), metric="CalPts", cash_line=1.0, gpp_target=2.0).rows
    client = _protectable(_Client(exists=True), grid)
    lines = ss.setup_controls(client)
    assert client.written is None and "holds 1 build(s)" in lines[0]


def test_every_format_is_sent_in_one_batch_and_the_two_phases_are_timed():
    """The run used to sit idle after printing the builds: one round trip per format range. Now the styling is
    one queued batch, and the write and the format phase report their seconds and request counts."""
    cash, gpp = (
        _build(1, "cash", "Cash build 1"),
        _build(2, "gpp", "GPP build 2  ·  DET stack + ARI bring-back"),
    )
    client, timings = _Client(exists=True), {}
    ss.write_suggested(
        client, _report(cash, gpp), _edge([cash, gpp]), metric="CalPts", cash_line=143.9, gpp_target=190.0,
        timings=timings,
    )  # fmt: skip
    assert client.batches == 1  # one block, not one per build
    assert client.formats and client.formats_in_batch == len(client.formats)  # every format is inside it
    assert set(timings) == {"write tab", "format tab"}
    assert all(seconds >= 0 and requests >= 0 for seconds, requests in timings.values())


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


def test_a_bar_adds_the_ownership_sum_and_product_and_the_share_when_the_build_has_them():
    from dataclasses import replace

    plain = _build(2, "gpp", "GPP build 2  ·  DET stack")
    assert ss.bar_text(plain) == plain.title  # no ownership published: the bar is the title
    owned = replace(plain, own_sum=1.16, own_product=1.2e-8, e_dups=0.1, objective=0.0031)
    assert (
        ss.bar_text(owned) == "GPP build 2  ·  DET stack  ·  own 116%, product 1.2e-08  ·  Top 1% share 0.31%"
    )
    assert "Top 1% share" not in ss.bar_text(replace(owned, objective=None))  # a cash build has none
