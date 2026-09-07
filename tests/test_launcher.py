"""Offline tests for the bare-`dfs` launcher's pure state -> suggestion
logic. No network, no filesystem -- see launcher.py's own docstring for
why that's the point."""

from __future__ import annotations

from dfs.launcher import LauncherState, header_lines, suggest_actions


def test_no_config_suggests_starting_a_week():
    state = LauncherState(config_exists=False)
    actions = suggest_actions(state)
    assert len(actions) == 1
    assert actions[0].command == "dfs week new <url-of-the-copy>"


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
    assert "dfs pool add <name>" in commands


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
    assert actions[0].command == "dfs export -o lineups.csv"


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
    assert "dfs export -o lineups.csv" not in commands


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
