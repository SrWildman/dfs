"""A secondary slate is a normal slate (Sam, Nov 23): its own close and learning, every record under its own
folder with its key, main's records and the fitted models left alone (`slate.py`, `slate_close.py`).

- each record a slate's close writes goes under `data/slates/thu/` and carries `SlateKey`; main's keep their
  paths and their columns;
- readers see main's records, plus a slate's own while it is active, plus the fitted slates' with the
  `[learning]` switch; a refit on a slate's close saves only with the switch;
- the slate's DraftKings week (its players, its lock) is its own day's;
- the contest log tags each entry with its slate and never loses a tag;
- `dfs week close --slate thu` runs every step a main close runs.
"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from dfs import config as config_mod
from dfs import paths, slate

SEASON = 2026


@pytest.fixture
def thu_config(tmp_path, monkeypatch):
    """config.toml naming a Thanksgiving sheet for Week 12; `switch` turns `[learning]` on."""
    path = tmp_path / "config.toml"

    def write(switch: bool = False) -> None:
        text = (
            '[google_sheets]\nsheet_id = "MAIN"\ncredentials_file = "c.json"\n'
            '[google_sheets.tab_mappings]\nedge = "EdgeRaw"\n[slates.thu]\nsheet_id = "THU"\nweek = 12\n'
        )
        if switch:
            text += "[learning]\nfit_secondary_slates = true\n"
        path.write_text(text)
        config_mod.load_base_config.cache_clear()

    write()
    monkeypatch.setattr(config_mod, "CONFIG_FILE", path)
    config_mod.load_base_config.cache_clear()
    yield write
    config_mod.load_base_config.cache_clear()


# -- the records ---------------------------------------------------------------------------------------------


def test_edge_at_lock_rows_are_the_slates_own_and_tagged(thu_config):
    from dfs import edge_at_lock

    row = dict.fromkeys(edge_at_lock.COLUMNS, "")
    row.update(season=SEASON, week=12, contest_key="7001")
    edge_at_lock.replace_week([row], SEASON, 12)  # main
    with slate.active("thu"):
        edge_at_lock.replace_week([{**row, "contest_key": "7002"}], SEASON, 12)
        mine = edge_at_lock.read()
    main = edge_at_lock.read()
    assert [r["contest_key"] for r in main] == ["7001"] and "SlateKey" not in main[0]
    assert [(r["contest_key"], r["SlateKey"]) for r in mine] == [("7002", "thu")]
    assert slate.data_path(edge_at_lock.PATH, "thu").exists()


def test_the_post_mortem_scorecard_and_cash_shown_are_the_slates_own(thu_config, monkeypatch):
    from dfs import cash_postmortem, numbers, scorecard, week_review

    rows = pd.DataFrame([dict.fromkeys(cash_postmortem.CSV_COLUMNS, 1)]).assign(season=SEASON, week=12)
    monkeypatch.setattr(cash_postmortem, "csv_rows", lambda _pm: rows)
    pm = SimpleNamespace(season=SEASON, week=12)  # the writer reads these two; `csv_rows` is stubbed
    with slate.active("thu"):
        written = cash_postmortem.append_csv(pm)
        card = dict.fromkeys(scorecard.CSV_COLUMNS, "")
        card.update(season=SEASON, week=12)
        sc_path = week_review._contests_dir() / "scorecard.csv"
        scorecard.append_csv(sc_path, card)
        cs_path = week_review._contests_dir() / "cash_shown.csv"
        numbers.replace_week(cs_path, SEASON, 12, [{"season": SEASON, "week": 12, "label": "Cash 1"}])
    assert written == slate.data_path(cash_postmortem.POSTMORTEM_CSV, "thu")
    assert pd.read_csv(written)["SlateKey"].tolist() == ["thu"]
    for p in (sc_path, cs_path):
        assert p.is_relative_to(slate.root("thu")) and pd.read_csv(p)["SlateKey"].tolist() == ["thu"]
    assert not cash_postmortem.POSTMORTEM_CSV.exists()  # main's untouched


def test_the_ownership_log_and_the_decision_log_carry_the_slate(thu_config):
    from dfs import decisions_log, ownership

    rows = pd.DataFrame({"player": ["A"], "dk_position": ["QB"], "pct_drafted": [0.5], "fpts": [20.0]})
    rows["contest_entries"] = 100
    ownership.append_ownership(rows, season=SEASON, week=11, contest_id="1")
    with slate.active("thu"):
        ownership.append_ownership(rows, season=SEASON, week=12, contest_id="2")
        decisions_log._write({"id": "x", "season": SEASON, "week": 12}, None)
        lines = decisions_log.lines()
    main_log = pd.read_csv(paths.OWNERSHIP_LOG_FILE)
    thu_log = pd.read_csv(slate.data_path(paths.OWNERSHIP_LOG_FILE, "thu"))
    assert main_log["contest_id"].tolist() == [1] and "SlateKey" not in main_log.columns
    assert thu_log["contest_id"].tolist() == [2] and thu_log["SlateKey"].tolist() == ["thu"]
    assert lines[-1]["SlateKey"] == "thu" and not decisions_log.lines()


# -- the scored weeks and the fits ---------------------------------------------------------------------------


def _scored(path, week, names, key=None):
    from dfs import results_loop

    frame = pd.DataFrame({c: [None] * len(names) for c in results_loop.SCORED_COLUMNS})
    frame["season"], frame["week"], frame["Name"] = SEASON, week, names
    if key:
        frame["SlateKey"] = key
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def test_main_reads_main_a_slate_adds_its_own_and_the_switch_adds_every_slate(thu_config):
    from dfs import results_loop, results_update

    _scored(results_loop.scored_path(SEASON, 11), 11, ["Sunday guy"])
    with slate.active("thu"):
        _scored(results_loop.scored_path(SEASON, 12), 12, ["Thursday guy"], "thu")
        on_thu = results_update.load_all_scored(SEASON)
        assert slate.own_rows(on_thu)["Name"].tolist() == ["Thursday guy"]
        assert not slate.may_save_fit()
    assert results_update.load_all_scored(SEASON)["Name"].tolist() == ["Sunday guy"]  # main: unchanged
    assert sorted(on_thu["Name"]) == ["Sunday guy", "Thursday guy"]
    assert slate.may_save_fit()
    thu_config(switch=True)
    assert sorted(results_update.load_all_scored(SEASON)["Name"]) == ["Sunday guy", "Thursday guy"]
    with slate.active("thu"):
        assert slate.may_save_fit()


def test_model_check_on_a_slates_sheet_shows_its_own_players_for_its_week(thu_config):
    from dfs import results_loop, results_update

    _scored(results_loop.scored_path(SEASON, 11), 11, ["Week 11 guy"])
    _scored(results_loop.scored_path(SEASON, 12), 12, ["Sunday guy"])
    with slate.active("thu"):
        _scored(results_loop.scored_path(SEASON, 12), 12, ["Thursday guy"], "thu")
        scored = results_update.load_all_scored(SEASON)
        view = results_update.slate_view(scored, [12])
    assert sorted(view["Name"]) == ["Thursday guy", "Week 11 guy"]
    assert results_update.slate_view(scored, [12]) is scored  # main: as read


def test_a_slate_scores_its_own_week_once_its_own_games_are_final(thu_config):
    from dfs import results_loop

    games = pd.DataFrame(
        {
            "week": [12, 12, 12],
            "gameday": ["2026-11-26", "2026-11-26", "2026-11-29"],
            "home_score": [20, 17, None],
            "away_score": [10, 24, None],
        }
    )
    assert results_loop.completed_weeks(games) == []  # Sunday's game is still to play
    assert results_loop.slate_complete(games, "thu", SEASON, 12)
    games.loc[1, "home_score"] = None
    assert not results_loop.slate_complete(games, "thu", SEASON, 12)
    selected = pd.DataFrame(
        {"Id": [1, 2], "GameStart": ["2026-11-26 12:30:00", "2026-11-29 13:00:00"], "Snapshot": ["a", "a"]}
    )
    assert results_loop.slate_rows(selected, "thu", SEASON, 12)["Id"].tolist() == [1]
    assert results_loop.slate_rows(selected, "main", SEASON, 12)["Id"].tolist() == [1, 2]


def test_a_refit_on_a_slates_close_is_skipped_unless_the_switch_includes_it(thu_config, capsys):
    from dfs import cli

    with slate.active("thu"):
        assert cli._fit_skipped("The copies estimate", "dfs contests dupes")
    assert "not refitted on the thu (Thursday) slate's close" in capsys.readouterr().out
    assert not cli._fit_skipped("The copies estimate", "dfs contests dupes")  # main always refits
    thu_config(switch=True)
    with slate.active("thu"):
        assert not cli._fit_skipped("The copies estimate", "dfs contests dupes")


# -- the slate's DraftKings week -----------------------------------------------------------------------------


def _salaries(path, games):
    rows = [
        {
            "Name": f"P{i}",
            "Position": "WR",
            "TeamAbbrev": away,
            "Salary": 5000,
            "Game Info": f"{away}@{home} {when}",
        }
        for i, (away, home, when) in enumerate(games)
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def test_the_slates_week_is_its_own_days_players_and_its_lock_its_first_kickoff(thu_config):
    from datetime import UTC, datetime

    from dfs.contests import week_frames

    main_dir = paths.RAW_DIR / "draftkings"
    _salaries(main_dir / "20261128T120000Z.csv", [("NYG", "DAL", "11/29/2026 01:00PM ET")])
    with slate.active("thu"):
        thu_dir = slate.data_path(main_dir)
        _salaries(
            thu_dir / "20261125T120000Z.csv",
            [("DET", "GB", "11/26/2026 12:30PM ET"), ("KC", "DAL", "11/26/2026 04:30PM ET")],
        )
        lock = week_frames.lock_utc(SEASON, 12)
        frame, note = week_frames.week_frame(main_dir, SEASON, 12)
    assert lock == datetime(2026, 11, 26, 17, 30, tzinfo=UTC)  # 12:30 pm ET
    assert sorted(frame["Team"]) == ["DET", "KC"] and note == "20261125T120000Z.csv"
    main_frame, _ = week_frames.week_frame(main_dir, SEASON, 12)
    assert main_frame["Team"].tolist() == ["NYG"]
    assert week_frames.lock_utc(SEASON, 12) == datetime(2026, 11, 29, 18, 0, tzinfo=UTC)  # main: unchanged


# -- the contest log -----------------------------------------------------------------------------------------


def test_the_contest_log_tags_each_entry_with_its_slate_and_keeps_a_tag(thu_config, monkeypatch):
    from dfs import slate_chain, slate_close
    from dfs.config import load_base_config
    from dfs.contests import history

    monkeypatch.setattr(slate_close, "_title", lambda cfg, sid: None)  # no sheet read: config says week 12
    header = (
        "Sport,Game_Type,Entry_Key,Entry,Contest_Key,Contest_Date_EST,Place,Points,Winnings_Non_Ticket,"
        "Winnings_Ticket,Contest_Entries,Entry_Fee,Prize_Pool,Places_Paid"
    )
    rows = [
        "NFL,Classic,E1,NFL $5 Double Up,7001,2026-11-26 12:30:00,10,120.5,$10.00,$0.00,100,$5.00,$450.00,50",
        "NFL,Classic,E4,NFL $5 Double Up,7101,2026-11-29 13:00:00,60,100.25,$0.00,$0.00,100,$5.00,$450.00,50",
    ]
    export = paths.DATA_DIR / "export.csv"
    export.parent.mkdir(parents=True, exist_ok=True)
    export.write_text(header + "\n" + "\n".join(rows) + "\n")
    log_path = paths.CONTESTS_DIR / "contest_log.csv"
    history.merge_into(log_path, export)
    cfg = load_base_config()
    assert slate_close.tag_log(cfg, log_path, SEASON) == 2
    log = history.read_log(log_path)
    assert dict(zip(log["Entry_Key"], log["SlateKey"], strict=True)) == {"E1": "thu", "E4": "main"}
    assert slate_close.tag_log(cfg, log_path, SEASON) == 0  # nothing to change
    assert slate_chain.log_for_slate(log, "thu", SEASON, {})["Entry_Key"].tolist() == ["E1"]
    typed = history.typed(log)
    assert slate_chain.log_for_slate(typed, "main", SEASON, {})["entry_key"].tolist() == ["E4"]
    history.merge_into(log_path, export)  # a later merge keeps the tags
    assert history.read_log(log_path)["SlateKey"].tolist() == ["thu", "main"]
    with slate.active("thu"):
        assert slate_close.own_log(SEASON)["Entry_Key"].tolist() == ["E1"]
    assert slate_close.own_log(SEASON)["Entry_Key"].tolist() == ["E4"]


def test_with_no_secondary_slate_the_log_is_never_tagged(tmp_path, monkeypatch):
    from dfs import slate_close
    from dfs.config import Config

    cfg = Config.model_validate(
        {
            "google_sheets": {
                "sheet_id": "M",
                "credentials_file": "c.json",
                "tab_mappings": {"edge": "EdgeRaw"},
            }
        }
    )
    log_path = tmp_path / "contest_log.csv"
    log_path.write_text("x\n")
    assert slate_close.tag_log(cfg, log_path, SEASON) == 0
    assert log_path.read_text() == "x\n"


# -- the close -----------------------------------------------------------------------------------------------


def test_a_slates_close_runs_every_step_a_main_close_runs(thu_config, monkeypatch):
    from dfs import cli

    calls: list[tuple[str, str]] = []

    def step(name):
        return lambda *a, **k: calls.append((name, slate.current()))

    steps = [
        "_carry_before_close",
        "_sync_bankroll_from_csv",
        "_keep_dk_files_before_scoring",
        "_score_results_after_close",
        "_contests_after_close",
        "_contest_plan_after_close",
        "_entered_record_warning",
        "_cash_postmortem_after_close",
        "_secondary_slates_after_close",
        "_du_line_after_close",
        "_plan_k_after_close",
    ]
    for name in steps:
        monkeypatch.setattr(cli, name, step(name))
    monkeypatch.setattr(cli, "_load_config_or_exit", lambda: cli.load_config())
    monkeypatch.setattr(cli, "_resolve_history_csv", lambda cfg, csv: csv)
    monkeypatch.setattr(cli, "_closed_week", lambda cfg, week: 12)
    with slate.active("thu"):
        cli.week_close(csv=None, week=None, research=False, monthly=False, slate_key="thu")
    ran = [name for name, _ in calls]
    assert ran == [s for s in steps if s != "_secondary_slates_after_close"]  # every step but main's summary
    assert {key for _, key in calls} == {"thu"}
