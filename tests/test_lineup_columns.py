"""The Lineups column fold: the pure plan (`lineup_columns`), applying it (`sheet_lineup_columns`) and the
`dfs setup lineup-columns` command."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from dfs import cli, sheet_lineup_columns
from dfs.lineup_columns import (
    AUDIT_VISIBLE,
    VISIBLE,
    LineupColumnsError,
    fold_runs,
    group_plan,
    span_text,
)
from dfs.sheet_lineup_sim import SIM_SLOT_HEADERS
from dfs.sheet_links import zone_column_ranges
from dfs.sheets import column_letter

runner = CliRunner()

# The template's Lineups header, read by API on 2026-11-23 (77 columns, A..BY).
TEMPLATE_HEADER = [
    "Name", "Pos.", "Team", "Opp.", "DK Sal", "Pts", "AggPts", "CalPts", "EspnPts", "PropPts", "Val",
    "ValAdj", "Hit3x%", "Boom%", "Bust%", "Ceil", "CeilVal", "Own%", "Avail", "Flags", "Edge",
    "% of Cap", "Issues", "Games", "Min Unique", "Edge ↗", "GAME", "O/U", "Spread", "Team Implied",
    "GameEnv", "Pace", "PROE", "Expl%", "OppPosRank", "OppEPA", "GameID", "TmRank", "CEIL", "CeilPct",
    "Leverage", "OwnStatus", "Floor", "CeilM", "MOVE", "ImpliedMove", "TotMove", "SpdMove", "GameStart",
    "WX", "Venue", "Stadium", "Roof", "Wind", "USAGE", "Snap%", "Tgt%", "WOPR", "Rush%", "RZ/G",
    "HVT/G", "xFP/G", "Id", "Flag", "ProjPts%ile", "AggPts%ile", "Ceiling%ile", "Val%ile",
    "CeilVal%ile", "Tgt%ile", "WOPR%ile", "Rush%ile", "RZ/G%ile", "HVT/G%ile", "CalPts%ile",
    "xFP/G%ile", "Player Key",
]  # fmt: skip


def letters(runs):
    return [
        column_letter(first) if first == last else f"{column_letter(first)}-{column_letter(last)}"
        for first, last, *_ in runs
    ]


def plan_for(header):
    return group_plan(header, zone_column_ranges(header))


# --- the plan --------------------------------------------------------------------------------------------


def test_visible_is_the_audits_13_plus_the_scoreboard_and_issues():
    assert len(AUDIT_VISIBLE) == 13
    assert set(VISIBLE) == {*AUDIT_VISIBLE, *SIM_SLOT_HEADERS, "Issues"}
    assert len(VISIBLE) == 17  # ValAdj is in both the audit's 13 and the scoreboard


def test_on_todays_template_it_folds_these_runs_and_shows_the_17():
    runs = fold_runs(TEMPLATE_HEADER)
    # The audit's G, I-K, M-O, Q, S, V-AN, AP-BY, less K, M, N (the scoreboard) and W (Issues).
    assert letters(runs) == ["G", "I-J", "O", "Q", "S", "V", "X-AN", "AP-BY"]
    folded = {i for first, last in runs for i in range(first, last + 1)}
    shown = [name for i, name in enumerate(TEMPLATE_HEADER) if i not in folded]
    assert shown == [name for name in TEMPLATE_HEADER if name in VISIBLE]
    assert len(shown) == 17


def test_the_zones_stay_groups_one_level_deeper_and_ceil_splits_around_leverage():
    plan = plan_for(TEMPLATE_HEADER)
    outer = [g for g in plan if g[2] == 1]
    inner = [g for g in plan if g[2] == 2]
    assert letters(outer) == ["G", "I-J", "O", "Q", "S", "V", "X-AN", "AP-BY"]
    # GAME, CEIL (CeilPct | Leverage | OwnStatus..CeilM), MOVE, WX, USAGE
    assert letters(inner) == ["AB-AL", "AN", "AP-AR", "AT-AW", "AY-BB", "BD-BJ"]
    assert all(collapsed for *_, collapsed in plan)
    # every inner group sits inside one outer group
    for first, last, _, _ in inner:
        assert any(o_first <= first and last <= o_last for o_first, o_last, _, _ in outer)


def test_no_two_groups_of_one_depth_touch_so_sheets_keeps_them_apart():
    plan = plan_for(TEMPLATE_HEADER)
    for depth in (1, 2):
        spans = sorted((first, last) for first, last, d, _ in plan if d == depth)
        for (_, prev_last), (next_first, _) in zip(spans, spans[1:], strict=False):
            assert next_first > prev_last + 1


def test_a_column_inserted_later_folds_with_its_neighbours_not_onto_the_wrong_ones():
    header = list(TEMPLATE_HEADER)
    header.insert(header.index("AggPts"), "NewSignal")  # a future insert between Pts and AggPts
    runs = fold_runs(header)
    assert [span_text(header, first, last) for first, last in runs][0] == "G-H NewSignal..AggPts (2)"
    folded = {i for first, last in runs for i in range(first, last + 1)}
    assert [name for i, name in enumerate(header) if i not in folded] == [n for n in header if n in VISIBLE]


def test_a_zone_part_that_would_cover_its_whole_fold_is_left_out():
    header = ["Name", "Pos.", "Team", "Opp.", "DK Sal", "Pts", "CalPts", "Val", "ValAdj", "Hit3x%", "Boom%",
              "Ceil", "Own%", "Flags", "Edge", "Issues", "Leverage", "O/U"]  # fmt: skip
    plan = group_plan(header, [(17, 17)])
    assert plan == [(17, 17, 1, True)]


def test_a_missing_visible_column_raises_before_anything_is_planned():
    header = [name for name in TEMPLATE_HEADER if name != "Leverage"]
    with pytest.raises(LineupColumnsError, match="Leverage"):
        fold_runs(header)


# --- applying it -----------------------------------------------------------------------------------------


class FakeClient:
    """Lineups' header, column groups and hidden flags. A column is hidden when it was hidden by hand or sits
    in a collapsed group, as the API reports it."""

    def __init__(self, header, groups=(), hidden=()):
        self.header = list(header)
        self.groups = list(groups)
        self.hand_hidden = set(hidden)
        self.writes: list[str] = []
        self.drop_apply = False

    def read_range(self, tab, a1):
        assert a1 == "A1:1"
        return [self.header]

    def read_column_groups(self, tab):
        return sorted(self.groups, key=lambda g: (g[2], g[0]))

    def get_column_widths(self, tab, last_letter):
        assert last_letter == column_letter(len(self.header) - 1)
        collapsed = {i for first, last, _, c in self.groups if c for i in range(first, last + 1)}
        return [{"hiddenByUser": i in self.hand_hidden or i in collapsed} for i in range(len(self.header))]

    def clear_column_groups(self, tab):
        self.writes.append("clear")
        self.groups = []

    def hide_columns(self, tab, first, last, *, hidden=True):
        self.writes.append(f"{'hide' if hidden else 'unhide'} {first}:{last}")
        idx = {column_letter(i): i for i in range(len(self.header))}
        span = set(range(idx[first], idx[last] + 1))
        self.hand_hidden = self.hand_hidden | span if hidden else self.hand_hidden - span

    def set_column_group_control_before(self, tab):
        self.writes.append("control-before")

    def apply_column_groups(self, tab, groups):
        self.writes.append(f"apply {len(groups)}")
        if not self.drop_apply:
            self.groups = list(groups)


def today_on_the_template():
    """Today's groups (the five zones, CEIL covering Leverage) and the helpers hidden by hand."""
    zones = [(first, last, 1, True) for first, last in zone_column_ranges(TEMPLATE_HEADER)]
    helpers = range(TEMPLATE_HEADER.index("Id"), len(TEMPLATE_HEADER))
    return FakeClient(TEMPLATE_HEADER, groups=zones, hidden=helpers)


def test_fold_writes_the_plan_reads_it_back_and_shows_exactly_the_17():
    client = today_on_the_template()
    result = sheet_lineup_columns.fold(client, "Lineups")
    assert result.changed
    assert client.groups == plan_for(TEMPLATE_HEADER)
    assert result.visible == [name for name in TEMPLATE_HEADER if name in VISIBLE]
    assert "Leverage" in result.visible  # was inside the collapsed CEIL zone
    assert client.writes[0] == "clear" and client.writes[-1] == "apply 14"
    lines = result.lines()
    assert lines[0] == "Lineups: folded 8 column group(s)"
    assert any("X-AN Games..CeilPct (17)" in line for line in lines)


def test_fold_is_idempotent_a_second_run_writes_nothing():
    client = today_on_the_template()
    sheet_lineup_columns.fold(client, "Lineups")
    client.writes.clear()
    again = sheet_lineup_columns.fold(client, "Lineups")
    assert not again.changed and client.writes == []
    assert again.lines()[0] == "Lineups: already folded, nothing written"


def test_a_group_expanded_by_hand_is_folded_shut_again():
    client = today_on_the_template()
    sheet_lineup_columns.fold(client, "Lineups")
    client.groups = [(f, l, d, False) if d == 1 and f == 6 else (f, l, d, c) for f, l, d, c in client.groups]  # noqa: E741
    assert sheet_lineup_columns.fold(client, "Lineups").changed
    assert client.groups == plan_for(TEMPLATE_HEADER)


def test_a_read_back_that_does_not_match_raises():
    client = today_on_the_template()
    client.drop_apply = True
    with pytest.raises(LineupColumnsError, match="read back"):
        sheet_lineup_columns.fold(client, "Lineups")


def test_a_missing_visible_column_writes_nothing():
    client = FakeClient([n for n in TEMPLATE_HEADER if n != "Issues"])
    with pytest.raises(LineupColumnsError, match="Issues"):
        sheet_lineup_columns.fold(client, "Lineups")
    assert client.writes == []


# --- the command -----------------------------------------------------------------------------------------


def _cli(monkeypatch, client):
    cfg = SimpleNamespace(
        google_sheets=SimpleNamespace(model_copy=lambda update: update),
        lineups=SimpleNamespace(builder_tab="Lineups"),
    )
    client.describe = lambda: ("Copy of Week 9 - Temp Working", "https://example.invalid/sheet")
    monkeypatch.setattr(cli, "_load_config_or_exit", lambda: cfg)
    monkeypatch.setattr(cli, "SheetsClient", lambda gs: client)


def test_setup_lineup_columns_names_the_sheet_and_says_what_it_folded(monkeypatch):
    client = today_on_the_template()
    _cli(monkeypatch, client)
    result = runner.invoke(cli.app, ["setup", "lineup-columns", "--sheet-id", "abc"])
    assert result.exit_code == 0, result.output
    assert "Folding the Lineups columns on: Copy of Week 9 - Temp Working" in result.output
    assert "Lineups: folded 8 column group(s)" in result.output
    out = " ".join(result.output.split())  # Rich wraps long lines
    assert "visible (17)" in out and "AP-BY OwnStatus..Player Key (36)" in out


def test_setup_lineup_columns_stops_on_a_missing_column(monkeypatch):
    client = FakeClient([n for n in TEMPLATE_HEADER if n != "Edge"])
    _cli(monkeypatch, client)
    result = runner.invoke(cli.app, ["setup", "lineup-columns"])
    assert result.exit_code == 1 and "no Edge column" in " ".join(result.output.split())
    assert client.writes == []


def test_the_refold_after_link_edge_fails_soft(monkeypatch):
    cfg = SimpleNamespace(lineups=SimpleNamespace(builder_tab="Lineups"))
    client = FakeClient([n for n in TEMPLATE_HEADER if n != "Edge"])
    cli._refold_lineup_columns(client, cfg)  # prints a yellow line, does not raise
    client = today_on_the_template()
    cli._refold_lineup_columns(client, cfg)
    assert client.groups == plan_for(TEMPLATE_HEADER)
