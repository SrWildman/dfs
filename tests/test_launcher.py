"""Offline tests for the bare-`dfs` launcher's pure state -> suggestion
logic. No network, no filesystem -- see launcher.py's own docstring for
why that's the point."""

from __future__ import annotations

from dfs.launcher import LauncherState, header_lines, suggest_actions


def test_no_config_suggests_starting_a_week():
    state = LauncherState(config_exists=False)
    actions = suggest_actions(state)
    assert len(actions) == 1
    assert actions[0].command == "dfs week new"


def test_unreachable_sheet_suggests_status_not_a_traceback():
    state = LauncherState(sheet_error="Could not open sheet")
    actions = suggest_actions(state)
    assert actions[0].command == "dfs status"


def test_never_synced_suggests_sync():
    state = LauncherState(sheet_title="Week 1", synced_sources=0)
    actions = suggest_actions(state)
    assert len(actions) == 1
    assert actions[0].command == "dfs sync"


def test_empty_pool_suggests_adding_players():
    state = LauncherState(sheet_title="Week 1", synced_sources=5, pool_count=0, lineups_total=20)
    actions = suggest_actions(state)
    commands = [a.command for a in actions]
    assert "dfs pool add" in commands


def test_pool_built_but_lineups_empty_points_at_the_sheet_not_a_command():
    state = LauncherState(
        sheet_title="Week 1", synced_sources=5, pool_count=40, lineups_filled=0, lineups_total=20
    )
    actions = suggest_actions(state)
    assert any(a.command is None for a in actions), "should tell Sam to build lineups in the sheet"


def test_ticking_a_player_changes_the_suggestion_the_2_4_staleness_test():
    """Task 2.4's own regression test: re-deriving suggestions after a real
    state change (one more ticked player) must actually change the output --
    a launcher that keeps saying "build your pool" once it's built has gone
    stale, which is exactly the failure this module exists to prevent."""
    empty_pool = LauncherState(sheet_title="Week 1", synced_sources=5, pool_count=0, lineups_total=20)
    built_pool = LauncherState(sheet_title="Week 1", synced_sources=5, pool_count=1, lineups_total=20)
    assert suggest_actions(empty_pool) != suggest_actions(built_pool)


def test_lineups_fully_filled_suggests_export():
    state = LauncherState(
        sheet_title="Week 1", synced_sources=5, pool_count=40, lineups_filled=20, lineups_total=20
    )
    actions = suggest_actions(state)
    assert actions[0].command == "dfs export"


def test_game_started_overrides_pool_and_lineup_state():
    state = LauncherState(
        sheet_title="Week 1",
        synced_sources=5,
        pool_count=40,
        lineups_filled=20,
        lineups_total=20,
        game_started=True,
    )
    actions = suggest_actions(state)
    commands = [a.command for a in actions]
    assert "dfs sync --live" in commands
    assert "dfs lineups late-swap" in commands
    assert "dfs export" not in commands


def test_games_finished_overrides_everything_else():
    state = LauncherState(
        sheet_title="Week 1",
        synced_sources=5,
        pool_count=40,
        lineups_filled=20,
        lineups_total=20,
        game_started=True,
        games_finished=True,
    )
    actions = suggest_actions(state)
    commands = [a.command for a in actions]
    assert any("bankroll sync" in c for c in commands)
    assert any("week close" in c for c in commands)


def test_stale_data_is_offered_alongside_the_primary_suggestion_not_instead_of_it():
    state = LauncherState(
        sheet_title="Week 1",
        synced_sources=5,
        pool_count=40,
        lineups_filled=0,
        lineups_total=20,
        freshest_sync_age_hours=18.0,
    )
    actions = suggest_actions(state)
    commands = [a.command for a in actions]
    assert "dfs sync" in commands
    assert any(a.command is None for a in actions), "the build-lineups suggestion must still be present"


def test_stale_data_under_threshold_is_not_offered():
    state = LauncherState(
        sheet_title="Week 1", synced_sources=5, pool_count=40, lineups_total=20, freshest_sync_age_hours=2.0
    )
    actions = suggest_actions(state)
    assert "dfs sync" not in [a.command for a in actions]


def test_header_lines_degrade_when_config_is_missing():
    lines = header_lines(LauncherState(config_exists=False))
    assert lines == ["No config.toml yet."]


def test_header_lines_mark_unreachable_sheet_instead_of_crashing():
    state = LauncherState(sheet_error="boom", pool_error="boom")
    lines = header_lines(state)
    assert "unreachable" in lines[0]
    assert "?" in lines[1]


def test_header_lines_render_a_fully_built_week():
    state = LauncherState(
        sheet_title="Week 1",
        week=1,
        synced_sources=5,
        freshest_sync_age_hours=1.0,
        pool_count=40,
        lineups_filled=3,
        lineups_total=20,
    )
    lines = header_lines(state)
    assert "Week 1" in lines[0]
    assert "synced 1h ago" in lines[0]
    assert "Pool 40 players" in lines[1]
    assert "Lineups 3/20 filled" in lines[1]


# --- the menu: always the full standard week, prompts instead of refusals -------------------------------

import pytest  # noqa: E402

from dfs.launcher import (  # noqa: E402
    HANDY_ITEMS,
    STANDARD_WEEK_ITEMS,
    MissingParam,
    answer_to_args,
    clean_path_or_text,
    command_path,
    menu_sections,
    missing_required,
    suggested_commands,
)


class _Param:
    def __init__(self, name, opts, required=True, nargs=1, help=None):
        self.name, self.opts, self.required, self.nargs, self.help = name, opts, required, nargs, help


def test_standard_week_is_always_on_the_menu_whatever_the_state():
    for state in (
        LauncherState(config_exists=False),
        LauncherState(sheet_error="boom"),
        LauncherState(sheet_title="Week 1", synced_sources=5, game_started=True),
    ):
        title, items = menu_sections(state)[0]
        assert title == "Your standard week"
        assert [i.command for i in items] == [i.command for i in STANDARD_WEEK_ITEMS]


def test_menu_commands_carry_no_placeholders_because_they_are_prompted_for():
    for _title, items in menu_sections(LauncherState(sheet_title="W", synced_sources=3, games_finished=True)):
        for item in items:
            assert item.command is None or "<" not in item.command


def test_a_suggestion_missing_from_the_fixed_menu_is_appended_not_dropped():
    state = LauncherState(sheet_title="W", synced_sources=3, games_finished=True)
    handy = dict(menu_sections(state))["Also handy"]
    assert "dfs bankroll sync" in [i.command for i in handy]
    assert len(handy) == len(HANDY_ITEMS) + 1


def test_suggested_commands_follow_the_state():
    live = LauncherState(sheet_title="W", synced_sources=3, game_started=True)
    assert {"dfs sync --live", "dfs lineups late-swap"} <= suggested_commands(live)
    assert suggested_commands(LauncherState(config_exists=False)) == {"dfs week new"}


def test_missing_required_option_and_argument_are_prompted_with_plain_questions():
    csv = _Param("csv", ["--csv"], help="Path to a DK contest-history CSV export.")
    asked = missing_required("dfs week close", [csv, _Param("week", ["--week"], required=False)])
    assert [m.flag for m in asked] == ["--csv"]
    assert "contest-history" in asked[0].question and not asked[0].question.endswith(".")
    url = missing_required("dfs week new", [_Param("sheet_url", ["sheet_url"])])
    assert url[0].flag is None and not url[0].many


def test_export_offers_lineups_csv_as_the_default_answer():
    out = missing_required("dfs export", [_Param("output", ["--output", "-o"])])
    assert out[0].default == "lineups.csv" and out[0].flag == "--output"


def test_an_unknown_required_param_still_gets_asked_from_its_own_help_or_name():
    asked = missing_required("dfs something new", [_Param("thing", ["--thing"], help="The thing.")])
    assert asked[0].question == "The thing"
    assert missing_required("dfs x", [_Param("bare", ["bare"])])[0].question == "bare"


def test_answers_become_argv():
    flagged = MissingParam("csv", "q", None, "--csv", False)
    assert answer_to_args(flagged, "/tmp/history.csv") == ["--csv", "/tmp/history.csv"]
    names = MissingParam("names", "q", None, None, True)
    assert answer_to_args(names, "Josh Allen, Travis Kelce") == ["Josh Allen", "Travis Kelce"]
    url = MissingParam("sheet_url", "q", None, None, False)
    assert answer_to_args(url, " https://docs.google.com/spreadsheets/d/abc/edit ") == [
        "https://docs.google.com/spreadsheets/d/abc/edit"
    ]


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("'/Users/Sam/My Contests/history.csv'", "/Users/Sam/My Contests/history.csv"),
        ("/Users/Sam/My\\ Contests/history.csv", "/Users/Sam/My Contests/history.csv"),
        ('"/tmp/a.csv"', "/tmp/a.csv"),
        ("it's.csv", "it's.csv"),  # not valid shell quoting: kept as typed
    ],
)
def test_dragged_in_paths_are_unquoted(typed, expected):
    assert clean_path_or_text(typed) == expected


def test_every_command_in_the_reference_is_reachable_from_the_menu_with_a_plain_label():
    """The menu is the 'no need to remember commands' promise: each visible command is either on the main menu
    or on the More screen, and the More screen has a hand-written label for it (not a docstring)."""
    from dfs.commands_doc import SECTIONS, all_commands
    from dfs.launcher import MENU_SECTIONS, MORE_LABELS

    on_menu = {command_path(i.command) for _t, items in MENU_SECTIONS for i in items if i.command}
    listed = {name for _t, _b, names in SECTIONS for name in names}
    assert on_menu <= set(all_commands()), "a main-menu command no longer exists"
    missing = sorted(listed - on_menu - set(MORE_LABELS))
    assert not missing, f"add a MORE_LABELS entry for: {missing}"
    stale = sorted(set(MORE_LABELS) - listed)
    assert not stale, f"MORE_LABELS names commands that no longer exist: {stale}"


def test_required_params_of_every_menu_command_get_a_question():
    from dfs.commands_doc import all_commands
    from dfs.launcher import MENU_SECTIONS

    commands = all_commands()
    for _t, items in MENU_SECTIONS:
        for item in items:
            if item.command:
                for m in missing_required(item.command, commands[command_path(item.command)].params):
                    assert m.question and m.question != m.name, f"{item.command}: {m.name} has no question"


def test_command_path_drops_flags():
    assert command_path("dfs sync --live") == "dfs sync"
    assert command_path("dfs week new") == "dfs week new"


# --- typed commands prompt too (prompt_missing.py) ------------------------------------------------------

from typer.testing import CliRunner  # noqa: E402

from dfs import prompt_missing  # noqa: E402
from dfs.cli import app  # noqa: E402


def test_every_group_in_the_cli_prompts_for_missing_parameters():
    from typer.main import get_command

    def groups(command):
        yield command
        for sub in getattr(command, "commands", {}).values():
            if hasattr(sub, "commands"):
                yield from groups(sub)

    for group in groups(get_command(app)):
        assert isinstance(group, prompt_missing.PromptingGroup), group.name


def test_without_a_terminal_a_missing_option_errors_exactly_as_before():
    result = CliRunner().invoke(app, ["week", "close"])
    assert result.exit_code != 0 and "Missing option" in result.output


def test_at_a_terminal_a_missing_option_is_asked_for_then_the_command_runs(monkeypatch):
    monkeypatch.setattr(prompt_missing, "interactive", lambda: True)
    from dfs import cli

    reached = {}

    def stop_here():
        reached["config"] = True
        raise SystemExit(0)

    monkeypatch.setattr(cli, "_load_config_or_exit", stop_here)
    result = CliRunner().invoke(app, ["bankroll", "sync"], input="/tmp/my history.csv\n")
    assert "DraftKings contest-history CSV" in result.output  # the question was asked
    assert "Missing option" not in result.output
    assert reached.get("config"), "the command body ran after the answer was supplied"


def test_a_blank_answer_cancels_and_shows_the_usual_error(monkeypatch):
    monkeypatch.setattr(prompt_missing, "interactive", lambda: True)
    result = CliRunner().invoke(app, ["week", "close"], input="\n")
    assert result.exit_code != 0 and "Missing option" in result.output


def test_a_positional_argument_is_asked_for_and_split_on_commas(monkeypatch):
    monkeypatch.setattr(prompt_missing, "interactive", lambda: True)
    captured = {}

    from dfs import cli

    def fake_client():
        captured["reached"] = True
        raise SystemExit(0)

    monkeypatch.setattr(cli, "_pool_client_and_edge_tab", fake_client)
    result = CliRunner().invoke(app, ["pool", "add"], input="Josh Allen, Travis Kelce\n")
    assert "Player name(s)" in result.output and captured.get("reached")


def test_live_sync_pulls_tffb_projections_before_recomputing_edge():
    from dfs.cli import LIVE_SYNC_SOURCES
    from dfs.sources import SOURCES

    assert set(LIVE_SYNC_SOURCES) <= set(SOURCES)
    assert "projections" in LIVE_SYNC_SOURCES
    assert LIVE_SYNC_SOURCES.index("projections") < LIVE_SYNC_SOURCES.index("edge")
    assert LIVE_SYNC_SOURCES[-1] == "edge", "edge reads what the others just saved"
