"""A second Classic slate in one week (`dfs.slate`, Thanksgiving first): the key, the default deny, the
config,
the sheet title, and above all that a secondary slate's files never touch the main slate's."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pandas as pd
import pytest
from typer.testing import CliRunner

from dfs import (
    entered,
    late_swap_store,
    news_store,
    paths,
    pool_store,
    runlog,
    signals_data,
    slate,
    store,
    suggest_state,
    week_inputs,
)
from dfs.cli import app
from dfs.config import Config, ConfigError, for_slate

# -- the key -------------------------------------------------------------------------------------------------


def test_the_default_is_main_and_main_paths_are_todays():
    assert slate.current() == slate.MAIN
    assert slate.root() == paths.DATA_DIR
    assert pool_store.pool_path(2026, 12) == pool_store.POOL_DIR / "2026-wk12.json"
    assert suggest_state.state_path(12) == suggest_state.STATE_DIR / "12.json"
    assert entered.record_path(2026, 12) == entered.ENTERED_DIR / "2026-wk12.json"
    assert late_swap_store._path() == late_swap_store.STORE_DIR / "late_swap.json"
    assert store.current_dir() == store.CURRENT_DIR
    assert store.raw_dir("edge") == store.RAW_DIR / "edge"
    assert store.manifest_file() == store.MANIFEST_FILE
    assert news_store.changes_path() == paths.DATA_DIR / "news" / "changes.csv"
    assert news_store.edge_dir() == paths.RAW_DIR / "edge"
    assert runlog.runlog_path() == paths.DATA_DIR / "runlog.csv"
    assert signals_data.signals_dir() == signals_data.SIGNALS_DIR
    assert week_inputs.data_root() == paths.DATA_DIR


def test_a_secondary_slate_lives_under_its_own_folder():
    with slate.active("thu"):
        root = paths.DATA_DIR / "slates" / "thu"
        assert slate.root() == root
        assert pool_store.pool_path(2026, 12) == root / "pools" / "2026-wk12.json"
        assert suggest_state.state_path(12) == root / "suggest_state" / "12.json"
        assert entered.record_path(2026, 12) == root / "entered" / "2026-wk12.json"
        assert late_swap_store._path() == root / "cockpit" / "late_swap.json"
        assert store.current_dir() == root / "current"
        assert store.raw_dir("draftkings") == root / "raw" / "draftkings"
        assert news_store.changes_path() == root / "news" / "changes.csv"
        assert runlog.runlog_path() == root / "runlog.csv"
        assert week_inputs.data_root() == root
        assert os.environ[slate.ENV_VAR] == "thu"
    assert slate.current() == slate.MAIN
    assert slate.ENV_VAR not in os.environ


def test_keys_are_checked():
    assert slate.validate(None) == "main"
    assert slate.validate("THU") == "thu"
    with pytest.raises(slate.SlateError):
        slate.validate("sun")
    with pytest.raises(slate.SlateError):
        slate.validate("thanksgiving")


def test_the_slate_comes_from_the_option_or_the_environment():
    assert slate.from_args(["sync"], env={}) == "main"
    assert slate.from_args(["sync", "--slate", "thu"], env={}) == "thu"
    assert slate.from_args(["sync", "--slate=sat"], env={}) == "sat"
    assert slate.from_args(["sync"], env={slate.ENV_VAR: "thu"}) == "thu"
    assert slate.from_args(["sync", "--slate", "thu"], env={slate.ENV_VAR: "thu"}) == "thu"
    with pytest.raises(slate.SlateError, match="Use one"):
        slate.from_args(["sync", "--slate", "sat"], env={slate.ENV_VAR: "thu"})


# -- the default deny ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["week", "close", "--slate", "thu"],
        ["results", "update", "--slate", "thu"],
        ["bankroll", "sync", "--slate", "thu"],
        ["contests", "update", "--slate", "thu"],
    ],
)
def test_a_main_only_command_refuses_a_secondary_slate_before_it_runs(argv):
    result = CliRunner().invoke(app, argv)
    assert result.exit_code == 1
    assert "runs for the main slate only" in result.output


def test_the_environment_is_enough_to_refuse(monkeypatch):
    monkeypatch.setenv(slate.ENV_VAR, "thu")
    result = CliRunner().invoke(app, ["week", "close"])
    assert result.exit_code == 1
    assert "runs for the main slate only" in result.output


def test_an_unknown_slate_is_an_error_not_main():
    result = CliRunner().invoke(app, ["sync", "--slate", "thanksgiving", "--no-upload"])
    assert result.exit_code == 1
    assert "Unknown slate" in result.output


def _commands(group, prefix="dfs"):
    for name, cmd in group.commands.items():
        path = f"{prefix} {name}"
        if hasattr(cmd, "commands"):
            yield from _commands(cmd, path)
        else:
            yield path, cmd


def test_exactly_the_slate_commands_take_slate():
    import typer.main

    root = typer.main.get_command(app)
    with_option = {path for path, cmd in _commands(root) if any("--slate" in p.opts for p in cmd.params)}
    assert with_option == set(slate.SLATE_COMMANDS)


def test_refusal_is_by_command_path():
    assert slate.refusal("dfs sync --live --slate", "thu") is None
    assert slate.refusal("dfs lineups suggest --iterate", "thu") is None
    assert slate.refusal("dfs week close --research", "main") is None
    assert "main slate only" in slate.refusal("dfs week close --research", "thu")


# -- the config ----------------------------------------------------------------------------------------------


def _cfg(**slates) -> Config:
    return Config.model_validate(
        {
            "google_sheets": {
                "sheet_id": "MAIN",
                "previous_sheet_id": "LAST",
                "credentials_file": "c.json",
                "tab_mappings": {"edge": "EdgeRaw"},
            },
            "slates": {k: {"sheet_id": v} for k, v in slates.items()},
        }
    )


def test_a_slate_gets_its_own_sheet_and_no_previous():
    cfg = for_slate(_cfg(thu="THU"), "thu")
    assert cfg.google_sheets.sheet_id == "THU"
    assert cfg.google_sheets.previous_sheet_id is None
    assert _cfg(thu="THU").google_sheets.sheet_id == "MAIN"


def test_a_slate_without_a_sheet_or_with_a_main_sheet_is_refused():
    with pytest.raises(ConfigError, match="No sheet for the thu slate"):
        for_slate(_cfg(), "thu")
    with pytest.raises(ConfigError, match="main slate's sheet"):
        for_slate(_cfg(thu="MAIN"), "thu")
    with pytest.raises(ConfigError, match="main slate's sheet"):
        for_slate(_cfg(thu="LAST"), "thu")


def test_slates_table_takes_only_secondary_keys():
    with pytest.raises(ValueError):
        _cfg(main="X")
    with pytest.raises(ValueError):
        _cfg(sun="X")


def test_load_config_follows_the_active_slate(tmp_path, monkeypatch):
    from dfs import config

    path = tmp_path / "config.toml"
    path.write_text(
        '[google_sheets]\nsheet_id = "MAIN"\ncredentials_file = "c.json"\n'
        '[google_sheets.tab_mappings]\nedge = "EdgeRaw"\n\n[slates.thu]\nsheet_id = "THU"\n'
    )
    monkeypatch.setattr(config, "CONFIG_FILE", path)
    config.load_config.cache_clear()
    try:
        assert config.load_config().google_sheets.sheet_id == "MAIN"
        with slate.active("thu"):
            assert config.load_config().google_sheets.sheet_id == "THU"
        assert config.load_config().google_sheets.sheet_id == "MAIN"
    finally:
        config.load_config.cache_clear()


def test_the_config_rewrite_never_touches_the_main_sheet():
    from dfs.week import rewrite_slate_sheet_id

    text = (
        '# my config\n[google_sheets]\nsheet_id = "MAIN"\nprevious_sheet_id = "LAST"\n'
        'credentials_file = "c.json"\n[google_sheets.tab_mappings]\nedge = "EdgeRaw"\n'
    )
    once = rewrite_slate_sheet_id(text, key="thu", new_sheet_id="THU1")
    assert once.startswith(text.rstrip("\n"))
    assert once.endswith('[slates.thu]\nsheet_id = "THU1"\n')
    twice = rewrite_slate_sheet_id(once, key="thu", new_sheet_id="THU2")
    assert twice.count("[slates.thu]") == 1 and 'sheet_id = "THU2"' in twice and "THU1" not in twice
    assert 'sheet_id = "MAIN"' in twice and 'previous_sheet_id = "LAST"' in twice
    with pytest.raises(ValueError):
        rewrite_slate_sheet_id(text, key="thu", new_sheet_id='x"\n[google_sheets]')


# -- the sheet title -----------------------------------------------------------------------------------------


def test_a_slate_sheet_is_titled_with_a_name_and_a_main_title_is_refused():
    from dfs.week import parse_week_from_title

    assert parse_week_from_title("Week 12") == 12
    with pytest.raises(ValueError):
        parse_week_from_title("Week 12 Thanksgiving")  # on main: unchanged
    with slate.active("thu"):
        assert parse_week_from_title("Week 12 Thanksgiving") == 12
        with pytest.raises(ValueError, match="main slate's sheet"):
            parse_week_from_title("Week 12")
    assert slate.default_title("thu", 12) == "Week 12 Thursday"


# -- the slate's day -----------------------------------------------------------------------------------------


def test_thanksgiving_is_the_thursday_of_week_12():
    from datetime import date

    assert slate.slate_date("thu", 2026, 12) == date(2026, 11, 26)
    assert slate.slate_date("fri", 2026, 16) == date(2026, 12, 25)  # Christmas, a Friday
    assert slate.slate_date("sat", 2026, 15) == date(2026, 12, 19)


def test_the_lock_baseline_is_the_night_before_the_slate():
    from dfs.lock_state import EASTERN, saturday_night

    first = datetime(2026, 11, 26, 17, 30, tzinfo=UTC)  # Thanksgiving, 12:30 pm ET
    assert saturday_night(first, first) == datetime(2026, 11, 28, 21, tzinfo=EASTERN)  # main: unchanged
    with slate.active("thu"):
        assert saturday_night(first, first) == datetime(2026, 11, 25, 21, tzinfo=EASTERN)


# -- writing one slate never changes the other ---------------------------------------------------------------


def _write_everything(tag: str) -> None:
    store.save("draftkings", pd.DataFrame({"Name": [f"{tag} player"], "Salary": [5000]}))
    store.record_success("draftkings", 1)
    pool_store.save(2026, 12, {f"{tag} player": "Both"}, source="sync")
    state = suggest_state.StateFile(week=12)
    suggest_state.save_state(state)
    entered.save(entered.make_record(2026, 12, f"{tag} entries", []))
    late_swap_store.save(late_swap_store.Saved(saved_at=datetime.now(UTC), refreshed_injuries=False))
    week_inputs.save_input("calls", {"tag": tag}, season=2026, week=12)
    news_store.append([{"name": tag, "week": 12}])
    signals_data.write_archive(pd.DataFrame({"x": [1]}), 2026, 12, datetime.now(UTC))
    runlog.record(f"dfs sync {tag}", True)


def _snapshot(root) -> dict:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "slates" not in p.relative_to(root).parts
    }


def test_a_thursday_slate_never_changes_a_main_file():
    _write_everything("main")
    before = _snapshot(paths.DATA_DIR)
    assert before, "the main slate wrote its files"
    with slate.active("thu"):
        _write_everything("thu")
        _write_everything("thu again")
    assert _snapshot(paths.DATA_DIR) == before
    thu = paths.DATA_DIR / "slates" / "thu"
    for part in (
        "current/draftkings.csv",
        "pools/2026-wk12.json",
        "suggest_state/12.json",
        "entered/2026-wk12.json",
        "cockpit/late_swap.json",
        "news/changes.csv",
        "runlog.csv",
        "manifest.json",
    ):
        assert (thu / part).is_file(), part
    assert len(list((thu / "raw" / "draftkings").glob("*.csv"))) >= 1
    with slate.active("thu"):
        assert store.load_current("draftkings")["Name"].iloc[0] == "thu again player"
    assert store.load_current("draftkings")["Name"].iloc[0] == "main player"


def test_main_never_changes_a_thursday_file():
    with slate.active("thu"):
        _write_everything("thu")
    thu = paths.DATA_DIR / "slates" / "thu"
    before = {str(p): p.read_bytes() for p in sorted(thu.rglob("*")) if p.is_file()}
    _write_everything("main")
    after = {str(p): p.read_bytes() for p in sorted(thu.rglob("*")) if p.is_file()}
    assert after == before


def test_clearing_a_slates_copies_leaves_mains_alone():
    _write_everything("main")
    with slate.active("thu"):
        _write_everything("thu")
        assert store.clear_current() == ["draftkings"]
        with pytest.raises(FileNotFoundError):
            store.load_current("draftkings")
    assert store.load_current("draftkings")["Name"].iloc[0] == "main player"


# -- the page ------------------------------------------------------------------------------------------------


def test_the_slates_page_is_its_own_file_and_says_which_slate():
    from dfs import week_page, week_server

    now = datetime(2026, 11, 25, 20, 0, tzinfo=UTC)
    main_page = week_page.generate(now=now, season=2026, week=12)
    with slate.active("thu"):
        thu_page = week_page.generate(now=now, season=2026, week=12)
        assert week_server.pid_path() == paths.DATA_DIR / "slates" / "thu" / "week" / "serve.pid"
    assert main_page == paths.DATA_DIR / "week" / "2026-wk12.html"
    assert thu_page == paths.DATA_DIR / "slates" / "thu" / "week" / "2026-wk12.html"
    html = thu_page.read_text()
    assert "Thursday slate" in html and "<title>Week 12 Thursday, 2026</title>" in html
    assert "Thursday slate" not in main_page.read_text()
    assert week_server.pid_path() == paths.DATA_DIR / "week" / "serve.pid"


def test_the_plays_snapshots_are_the_slates_own():
    from dfs import plays

    until = datetime(2026, 11, 30, tzinfo=UTC)
    for key in ("main", "thu"):
        with slate.active(key):
            folder = store.raw_dir("edge")
            folder.mkdir(parents=True, exist_ok=True)
            stamps = ["20261125T120000Z", "20261126T120000Z"] if key == "main" else ["20261125T130000Z"]
            for stamp in stamps:
                pd.DataFrame({"Name": [key]}).to_csv(folder / f"{stamp}.csv", index=False)
    with slate.active("thu"):
        got = plays.edge_snapshots(2026, 12, until)
    assert [ts.hour for ts, _ in got] == [13]  # Thanksgiving's own, none of main's
    assert len(plays.edge_snapshots(2026, 12, until)) == 2  # main: its own, as before
    assert store.raw_dir("edge") == paths.RAW_DIR / "edge"
