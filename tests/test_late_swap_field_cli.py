"""`dfs lineups late-swap --field` end to end: a hand-made EdgeRaw, stub sheet and standings export."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from dfs import cli, late_swap_store, sim_inputs
from dfs.late_swap_sim import SimSettings
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS, PLAYER_POOL_HEADER_ROW, PLAYER_POOL_NAME_BLOCKS

NOW = datetime(2026, 10, 11, 17, 30, tzinfo=UTC)  # 1:30 pm ET
EARLY, LATE = "2026-10-11T13:00:00Z", "2026-10-11T16:25:00Z"
HEADER = "Rank,EntryId,EntryName,TimeRemaining,Points,Lineup,,Player,Roster Position,%Drafted,FPTS"

# (name, position, team, projection, salary, kickoff)
PLAYERS = [
    ("Quinn Early", "QB", "AAA", 20.0, 6000, EARLY),
    ("Rex Early", "RB", "BBB", 14.0, 5000, EARLY),
    ("Rex Late", "RB", "CCC", 12.0, 5000, LATE),
    ("Rex Better", "RB", "DDD", 17.0, 5200, LATE),
    ("Walt One", "WR", "EEE", 13.0, 5000, EARLY),
    ("Walt Two", "WR", "FFF", 12.0, 5000, EARLY),
    ("Walt Three", "WR", "GGG", 11.0, 5000, EARLY),
    ("Tom Early", "TE", "HHH", 9.0, 4000, EARLY),
    ("Walt Flex", "WR", "III", 9.0, 4000, EARLY),
    ("Dallas Defense", "DST", "JJJ", 8.0, 3000, EARLY),
]
LINEUP = [
    "Quinn Early",
    "Rex Early",
    "Rex Late",
    "Walt One",
    "Walt Two",
    "Walt Three",
    "Tom Early",
    "Walt Flex",
    "Dallas Defense",
]


def edge():
    rows = [
        {
            "Id": i, "Name": n, "Position": pos, "Team": team, "Opp": "ZZZ", "Salary": sal, "ProjPts": proj,
            "AggPts": proj, "GameID": f"g{i}", "GameStart": start, "Avail": None, "Flags": None, "Own%": 0.1,
        }
        for i, (n, pos, team, proj, sal, start) in enumerate(PLAYERS)
    ]  # fmt: skip
    return pd.DataFrame(rows)


def standings_file(tmp_path, *, scored=5.0):
    """40 field lineups holding `LINEUP`, half with `Rex Better`; the early players have scored."""

    def text(names):
        qb, rb1, rb2, wr1, wr2, wr3, te, flex, dst = names
        return f"DST {dst} FLEX {flex} QB {qb} RB {rb1} RB {rb2} TE {te} WR {wr1} WR {wr2} WR {wr3}"

    lineups = [LINEUP if i % 2 else [*LINEUP[:2], "Rex Better", *LINEUP[3:]] for i in range(40)]
    early = sum(scored for p in PLAYERS if p[5] == EARLY and p[0] in LINEUP)
    table = [
        (n, pos, 100.0 if n in LINEUP else 50.0, scored if start == EARLY else 0.0)
        for n, pos, _, _, _, start in PLAYERS
    ]
    rows = []
    for i in range(max(len(lineups), len(table))):
        left = ["", "", "", "", "", ""]
        if i < len(lineups):
            left = [str(i + 1), str(i), f"u{i} (1/1)", "60", f"{early:.2f}", f'"{text(lineups[i])}"']
        right = ["", "", "", ""]
        if i < len(table):
            n, pos, pct, f = table[i]
            right = [n, pos, f"{pct:.2f}%", str(f)]
        rows.append(",".join([*left, "", *right]))
    path = tmp_path / "contest-standings-555.csv"
    path.write_text("\n".join([HEADER, *rows]))
    return path


class StubClient:
    def describe(self):
        return ("Week X", "https://example.test/sheet")

    def tab_exists(self, tab):
        return False

    def batch_read_ranges(self, specs):
        return [[] for _ in specs]

    def read_range(self, tab, a1):
        if tab == "Lineups":
            rows = [[""] for _ in range(LINEUPS_NAME_BLOCKS[-1][1] - 1)]
            for i, name in enumerate(LINEUP):
                rows[LINEUPS_NAME_BLOCKS[0][0] - 2 + i] = [name]
            return rows
        if a1.startswith(f"A{PLAYER_POOL_HEADER_ROW}:"):
            return [["Name", "Pos."]]
        first = PLAYER_POOL_NAME_BLOCKS[0][0]
        rows = [[""] for _ in range(PLAYER_POOL_NAME_BLOCKS[-1][1] - first + 1)]
        for i, name in enumerate(["Rex Better"]):
            rows[PLAYER_POOL_NAME_BLOCKS[0][0] - first + i] = [name]
        return rows


@pytest.fixture
def cfg(tmp_path):
    return SimpleNamespace(
        google_sheets=SimpleNamespace(),
        lineups=SimpleNamespace(builder_tab="Lineups", player_pool_tab="Player Pool", salary_cap=50000),
        contests=SimpleNamespace(downloads_dir=str(tmp_path / "downloads")),
    )


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(cli.store, "load_current", lambda name: edge())
    monkeypatch.setattr(
        cli,
        "_late_swap_sim_setup",
        lambda cfg, client, e, goal, now, fixed_lines=False: (
            sim_inputs.build_specs(e),
            SimSettings(cash_line=140.0, gpp_target=190.0, goal=goal),
        ),
    )


def run(cfg, capsys, **kw):
    cli._run_late_swap(
        cfg, top=3, metric="ProjPts", all_players=False, now=NOW, client=StubClient(), goal="cash", **kw
    )
    return capsys.readouterr().out


def test_without_a_field_the_run_says_it_scored_against_the_projected_field(cfg, capsys):
    out = run(cfg, capsys)
    assert "Field file" not in out
    assert late_swap_store.load().field_source == "field: projected"


def test_a_field_file_is_named_and_scores_the_swaps(cfg, capsys, tmp_path):
    path = standings_file(tmp_path)
    out = run(cfg, capsys, field=[str(path)])
    assert "Field file: contest 555" in out and "40 lineups" in out
    assert "field: real (contest 555, 40 lineups" in out
    assert "P(cash)" in out and "Rex Better" in out  # the swap is rated against the real lines
    assert late_swap_store.load().field_source.startswith("field: real (contest 555")


def test_a_bad_field_file_falls_back_to_the_projected_field(cfg, capsys, tmp_path):
    bad = tmp_path / "contest-standings-1.csv"
    bad.write_text("a,b\n1,2\n")
    out = run(cfg, capsys, field=[str(bad)])
    flat = " ".join(out.split())  # the terminal wraps a long path
    assert "--field" in flat and "not a DraftKings standings export" in flat
    assert late_swap_store.load().field_source == "field: projected"


def test_field_auto_with_nothing_in_downloads_says_so_and_runs_projected(cfg, capsys, tmp_path):
    (tmp_path / "downloads").mkdir()
    out = run(cfg, capsys, field=["auto"])
    assert "no live standings export for this week" in out
    assert late_swap_store.load().field_source == "field: projected"


# ---- --state live: ESPN's scores, no download -------------------------------------------------------------


def _scores():
    from dfs import live_scores

    return live_scores.LiveScores(
        {
            "quinn early": 18.0,
            "rex early": 9.0,
            "walt one": 7.0,
            "walt two": 12.0,
            "walt three": 3.0,
            "tom early": 6.0,
        },
        {name: 1.0 for name in ("AAA", "BBB", "EEE", "FFF", "GGG", "HHH", "III", "JJJ")},
        [],
        NOW,
    )


@pytest.fixture
def state_cfg(cfg):
    from dfs.config import SimLinesConfig

    cfg.sim = SimpleNamespace(lines=SimLinesConfig())
    return cfg


def test_state_live_scores_swaps_on_the_projected_field_plus_espns_points(state_cfg, capsys, monkeypatch):
    from dfs import live_scores

    monkeypatch.setattr(live_scores, "fetch_scores", lambda dates, **kw: _scores())
    out = run(state_cfg, capsys, state="live")
    assert "field: projected + live scores (ESPN" in out and "P(cash)" in out
    assert late_swap_store.load().field_source.startswith("field: projected + live scores")


def test_state_live_with_espn_down_says_so_and_runs_as_today(state_cfg, capsys, monkeypatch):
    from dfs import live_scores

    def down(dates, **kw):
        raise live_scores.ScoresError("scoreboard 20261011: boom")

    monkeypatch.setattr(live_scores, "fetch_scores", down)
    out = run(state_cfg, capsys, state="live")
    flat = " ".join(out.split())
    assert "ESPN's scores could not be read" in flat and "running as today" in flat
    assert "P(cash)" in out  # the fixed-line run still rates the swaps
    assert late_swap_store.load().field_source == "field: projected"


def test_state_live_without_published_ownership_runs_as_today(state_cfg, capsys, monkeypatch):
    from dfs import live_scores

    monkeypatch.setattr(live_scores, "fetch_scores", lambda dates, **kw: _scores())
    monkeypatch.setattr(cli.store, "load_current", lambda name: edge().assign(**{"Own%": float("nan")}))
    out = run(state_cfg, capsys, state="live")
    assert "no projected ownership has been published" in " ".join(out.split())
    assert late_swap_store.load().field_source == "field: projected"


def test_field_and_state_together_are_refused(state_cfg, monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.setattr(cli, "_load_config_or_exit", lambda: state_cfg)
    result = CliRunner().invoke(cli.app, ["lineups", "late-swap", "--field", "x.csv", "--state", "live"])
    assert result.exit_code != 0 and "use one" in " ".join(result.output.split())
