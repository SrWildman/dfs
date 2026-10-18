"""`dfs contests analyze` end to end on the synthetic files: no sheet, no credentials, no network."""

from pathlib import Path

import httpx
import pandas as pd
import pytest
from typer.testing import CliRunner

from dfs.cli import app
from dfs.contests import analysis, history, slate_index

FIXTURES = Path(__file__).parent / "fixtures"
EXPORT, LATER = FIXTURES / "history_export.csv", FIXTURES / "history_export_later.csv"
STANDINGS = FIXTURES / "standings"
PLAYERS = FIXTURES / "players_week.csv"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Nothing in the engine may reach the network in these tests (and never DraftKings, anywhere)."""

    def refuse(*args, **kwargs):
        raise AssertionError(f"network call attempted: {args[:2]}")

    monkeypatch.setattr(httpx, "get", refuse)
    monkeypatch.setattr(httpx.Client, "send", refuse)


def run(tmp_path, **kw):
    kw.setdefault("fetch_season", None)
    return analysis.run(
        [EXPORT, LATER], tmp_path / "out", standings_dir=STANDINGS, players=PLAYERS, me="test_me", **kw
    )


def test_the_whole_run_on_the_fixtures(tmp_path):
    res = run(tmp_path)
    assert [m.added for _, m in res.merges] == [31, 1] and len(res.log) == 32
    # one profile per standings contest found, with Places_Paid joined from the history log by Contest_Key
    profiles = res.profiles.set_index("contest_id")
    assert set(profiles.index) == {"900000001", "900000002", "900000003", "900000004", "900000006"}
    tiny = profiles.loc["900000001"]
    assert (tiny["entrants"], tiny["places_paid"], tiny["cash_line"]) == (10, 4, 190.5)
    assert (tiny["format"], tiny["series"], tiny["season"], tiny["week"]) == (
        "Double Up",
        "NFL Tiny $2 Double Up [Single Entry]",
        2025,
        6,
    )
    assert pd.isna(profiles.loc["900000002", "cash_line"])  # no history row for it: no Places_Paid
    assert profiles.loc["900000004", "kind"] == "Showdown"
    # the field measures come from the one contest whose names the players frame knows
    row = res.field_rows[(res.field_rows["contest_id"] == "900000001") & (res.field_rows["group"] == "all")]
    assert row["avg_unspent"].iloc[0] == pytest.approx(2620.0)
    assert set(res.field_rows[res.field_rows["contest_id"] == "900000001"]["group"]) == {"all", "top10", "me"}
    assert len(res.lineup_errors) == 3
    assert any("no history row" in w for w in res.warnings)


def test_empty_downloads_are_reported_not_fatal(tmp_path):
    res = run(tmp_path)
    assert len(res.scan.empty) == 2
    assert any("empty: " in line for line in analysis.summary_lines(res))


def test_the_series_table_and_the_class_cross_check(tmp_path):
    res = run(tmp_path)
    table = res.series_table.set_index(["format", "series"])
    fair = table.loc[("SE GPP", "NFL $8K Fair Catch [Single Entry]")]
    assert fair["entries"] == 4 and fair["weeks"] == 4  # 2025 w6, 2025 w18, 2026 w1, 2026 w2
    assert table.loc[("H2H", "H2H $1"), "entries"] == 3
    assert res.disagreements.empty  # every fixture row agrees with the payout-ratio band


def test_idempotent_rerun_changes_nothing(tmp_path):
    first = run(tmp_path)
    log_bytes = (tmp_path / "out" / analysis.LOG_NAME).read_bytes()
    analysis.write(first)
    snapshot = {p.name: p.read_bytes() for p in (tmp_path / "out").iterdir()}
    again = run(tmp_path)
    analysis.write(again)
    assert (tmp_path / "out" / analysis.LOG_NAME).read_bytes() == log_bytes
    assert {p.name: p.read_bytes() for p in (tmp_path / "out").iterdir()} == snapshot
    assert [m.added for _, m in again.merges] == [0, 0]


def test_write_produces_every_csv_and_nothing_else(tmp_path):
    res = run(tmp_path)
    written = analysis.write(res)
    assert sorted(p.name for p in written) == sorted(
        [
            "contest_log.csv",
            "series.csv",
            "class_disagreements.csv",
            "profiles.csv",
            "field_stats.csv",
            "field_stacks.csv",
            "field_unmatched.csv",
            "winners.csv",
            "lineup_errors.csv",
            "toughness_series.csv",
            "toughness_formats.csv",
            "toughness_weeks.csv",
            "toughness_models.csv",
            "toughness_k.csv",
            "h2h_bands.csv",
            "h2h_opponents.csv",
            "slate_index.csv",
        ]
    )
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == sorted(p.name for p in written)
    assert (
        pd.read_csv(tmp_path / "out" / "profiles.csv")["contest_id"]
        .astype(str)
        .str.startswith("9000000")
        .all()
    )


def test_the_slate_index_comes_from_the_committed_backfill_for_past_seasons(tmp_path):
    res = run(tmp_path)
    got = res.index.set_index(["season", "week"])["index"]
    backfill = slate_index.load_backfill().set_index(["season", "week"])[f"rel_{slate_index.SELECTED}"]
    assert got.loc[(2025, 6)] == pytest.approx(backfill.loc[(2025, 6)])
    assert any("slate index for 2026 unavailable" in w for w in res.warnings)  # 2026 is not in the backfill
    assert 2026 not in set(res.index["season"])


def test_a_season_outside_the_backfill_is_fetched_through_the_given_loader(tmp_path):
    seen = []

    def fake_fetch(season):
        seen.append(season)
        return pd.DataFrame(
            {
                "season": season,
                "week": [1, 2],
                "n_games": 11,
                "top_n_lineup": [150.0, 140.0],
                "p95_optimal": [160.0, 150.0],
                "points_per_game": [45.0, 47.0],
            }
        )

    res = run(tmp_path, fetch_season=fake_fetch)
    assert seen == [2026] and set(res.index[res.index["season"] == 2026]["week"]) == {1, 2}
    assert not any("unavailable" in w for w in res.warnings)


def test_slate_index_none_leaves_the_index_out_and_never_fetches(tmp_path):
    res = run(tmp_path, index_candidate="none")
    assert res.index.empty
    assert "index" not in set(res.toughness.comparison["mode"])  # the comparator needs it
    assert not any("unavailable" in w for w in res.warnings)


def test_a_supplied_index_file_wins(tmp_path):
    supplied = tmp_path / "idx.csv"
    pd.DataFrame({"season": [2025], "week": [6], "index": [2.0]}).to_csv(supplied, index=False)
    res = run(tmp_path, index_file=supplied)
    assert res.index.to_dict("list") == {"season": [2025], "week": [6], "index": [2.0]}


def test_the_previous_runs_index_is_the_fallback_for_a_season_that_cannot_be_fetched(tmp_path):
    first = run(
        tmp_path,
        fetch_season=lambda season: pd.DataFrame(
            {
                "season": season,
                "week": [1],
                "n_games": 11,
                "top_n_lineup": [150.0],
                "p95_optimal": [160.0],
                "points_per_game": [45.0],
            }
        ),
    )
    analysis.write(first)
    second = run(tmp_path)  # no loader this time
    assert set(second.index[second.index["season"] == 2026]["week"]) == {1}
    assert any("using the last run's values" in w for w in second.warnings)


# ---- the command ---------------------------------------------------------------------------------


def invoke(*args):
    return CliRunner().invoke(app, ["contests", "analyze", *args])


def test_the_command_runs_writes_csvs_and_prints_a_summary(tmp_path):
    out = tmp_path / "out"
    result = invoke(
        "--history",
        str(EXPORT),
        "--history",
        str(LATER),
        "--standings",
        str(STANDINGS),
        "--players",
        str(PLAYERS),
        "--out",
        str(out),
        "--me",
        "test_me",
        "--slate-index",
        "none",
    )
    assert result.exit_code == 0, result.output
    assert (
        "formats:" in result.output
        and "toughness: week treatment" in result.output
        and "OK wrote 17 file(s)" in result.output
    )
    assert (out / "toughness_series.csv").exists() and (out / "field_stats.csv").exists()


def test_the_commands_are_in_help_and_the_command_reference_now_that_week_close_runs_them():
    assert "contests" in CliRunner().invoke(app, ["--help"]).output
    from dfs import commands_doc

    commands = commands_doc.all_commands()
    assert "dfs contests update" in commands and "dfs contests analyze" in commands


def test_a_missing_file_is_a_clear_failure(tmp_path):
    result = invoke("--history", str(tmp_path / "nope.csv"), "--out", str(tmp_path))
    assert result.exit_code == 1 and "No such file" in result.output


def test_a_zero_byte_history_file_fails_with_a_message(tmp_path):
    empty = tmp_path / "h.csv"
    empty.write_bytes(b"")
    result = invoke("--history", str(empty), "--out", str(tmp_path / "out"))
    assert result.exit_code == 1 and "empty" in result.output


def test_an_unknown_slate_index_name_is_rejected(tmp_path):
    result = invoke("--history", str(EXPORT), "--out", str(tmp_path), "--slate-index", "bogus")
    assert result.exit_code == 2 and "Unknown --slate-index" in result.output


def test_history_is_required():
    assert invoke().exit_code != 0


def test_the_package_has_no_sheet_code_and_no_draftkings_address():
    """Standing rules: nothing in the contests engine writes a sheet or contacts DraftKings."""
    package = Path(analysis.__file__).parent
    forbidden = ["gspread", "SheetsClient", "dfs.sheets", "draftkings.com", "api.draftkings", "playwright"]
    for source in package.glob("*.py"):
        text = source.read_text()
        for needle in forbidden:
            assert needle not in text, f"{source.name} mentions {needle}"


# ---- found in review -----------------------------------------------------------------------------


def test_a_series_with_no_entry_counts_does_not_crash_the_summary(tmp_path):
    src = tmp_path / "live.csv"
    df = history.read_export(EXPORT)
    df.loc[df["Entry_Key"] == "5000000002", ["Contest_Entries", "Places_Paid", "Place"]] = ""  # still in play
    df.to_csv(src, index=False)
    res = analysis.run([src], tmp_path / "out", fetch_season=None, index_candidate="none")
    row = res.series_table.set_index("series").loc["NFL $75K Fair Catch [Single Entry]"]
    assert pd.isna(row["median_field"]) and pd.isna(row["median_payout_ratio"])
    analysis.write(res)


def _standings_copy(tmp_path, transform):
    folder = tmp_path / "standings"
    folder.mkdir()
    text = (STANDINGS / "contest-standings-900000001.csv").read_text(encoding="utf-8-sig")
    (folder / "contest-standings-900000001.csv").write_text(transform(text), encoding="utf-8-sig")
    return folder


def test_a_live_contest_is_profiled_but_kept_out_of_the_fit(tmp_path):
    folder = _standings_copy(tmp_path, lambda t: t.replace(",0,208.66,", ",25,208.66,", 1))
    res = analysis.run(
        [EXPORT], tmp_path / "out", standings_dir=folder, fetch_season=None, index_candidate="none"
    )
    assert res.profiles["complete"].tolist() == [False]
    assert any("still in play" in w for w in res.warnings)
    assert (res.toughness.observations["kind"] == "profile").sum() == 0


def test_a_partial_standings_export_is_kept_out_of_the_fit(tmp_path):
    folder = _standings_copy(tmp_path, lambda t: "\n".join(t.splitlines()[:6]) + "\n")  # header + 5 entries
    res = analysis.run(
        [EXPORT], tmp_path / "out", standings_dir=folder, fetch_season=None, index_candidate="none"
    )
    assert any("5 entries but the history says 10" in w for w in res.warnings)
    assert (res.toughness.observations["kind"] == "profile").sum() == 0


def test_a_complete_standings_export_does_enter_the_fit(tmp_path):
    folder = _standings_copy(tmp_path, lambda t: t)
    res = analysis.run(
        [EXPORT], tmp_path / "out", standings_dir=folder, fetch_season=None, index_candidate="none"
    )
    assert (res.toughness.observations["kind"] == "profile").sum() == 5


def test_a_bad_slate_index_file_is_a_clear_failure_not_a_traceback(tmp_path):
    bad = tmp_path / "idx.csv"
    bad.write_text("season,week\n2025,6\n")
    result = invoke("--history", str(EXPORT), "--out", str(tmp_path / "out"), "--slate-index-file", str(bad))
    assert result.exit_code == 1 and "needs column" in result.output and "Traceback" not in result.output
    bad.write_text("season,week,index\n2025,six,1.0\n")
    with pytest.raises(ValueError, match="must all be numbers"):
        slate_index.read_index_file(bad)
    good = tmp_path / "good.csv"
    good.write_text("season,week,index\n2025,6,1.1\n")
    assert slate_index.read_index_file(good).to_dict("list") == {
        "season": [2025],
        "week": [6],
        "index": [1.1],
    }


def test_importing_the_command_does_not_import_the_analysis_stack():
    """The group is registered on every `dfs` start; the unfinished command must stay lazy."""
    import subprocess
    import sys

    code = "import sys, dfs.contests.cli; sys.exit(1 if 'dfs.contests.analysis' in sys.modules else 0)"
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0


def test_what_wins_looks_only_at_classic_gpps(tmp_path, monkeypatch):
    """The top-1% comparison is for GPP formats: a Double Up's top 1% is not a winners' lineup."""
    seen = []
    real = analysis.winners.contest_lineups

    def spy(parsed, players):
        seen.append(parsed.contest_id)
        return real(parsed, players)

    monkeypatch.setattr(analysis.winners, "contest_lineups", spy)
    run(tmp_path)
    assert "900000001" not in seen  # the Double Up is not looked at
    seen.clear()
    monkeypatch.setattr(analysis.series_mod, "SE_GPP", "Double Up")  # ...but a GPP format would be
    run(tmp_path / "again")
    assert seen == ["900000001"]
