"""The ledger (blueprint Phase B): formats ported from the ground-truth pack, the week-clustered SE, H2H
counts (never names), this week's money by group, the H2H log, and the page's section. Fixtures only."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from dfs import contest_tiers, entered, h2h_log, ledger, week_money, week_page
from tests.test_week_plays import inputs, page_text, render, section, slate_rows

COLS = [
    "Season",
    "Week",
    "Entry",
    "Format",
    "Game_Type",
    "Entry_Fee",
    "Winnings_Non_Ticket",
    "Winnings_Ticket",
    "Prize_Pool",
    "Contest_Entries",
    "Opponent",
    "Contest_Key",
]


def entry(season, week, name, fmt, fee, win=0.0, *, game="Classic", pool=None, field=None, opp="", key=None):
    field = field if field is not None else 2
    pool = pool if pool is not None else round(fee * field * 0.9, 2)
    return {
        "Season": str(season),
        "Week": str(week),
        "Entry": name,
        "Format": fmt,
        "Game_Type": game,
        "Entry_Fee": f"${fee:,.2f}",
        "Winnings_Non_Ticket": f"${win:,.2f}",
        "Winnings_Ticket": "$0.00",
        "Prize_Pool": f"${pool:,.2f}",
        "Contest_Entries": f"{field:,}",
        "Opponent": opp,
        "Contest_Key": key or f"{season}-{week}-{name}-{opp}",
    }


def log(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=COLS).astype(str)


# -- the formats -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("game", "fmt", "name", "fee", "bucket"),
    [
        ("Classic", "Private", "Sunday league", 5.0, "Discord"),
        ("Classic", "N-max GPP", "NFL Discord $1 [5 Entry Max]", 1.0, "Discord"),
        ("Classic", "Private", "freeroll", 0.0, "Discord"),  # Discord first, even free
        ("Classic", "Double Up", "NFL $0 Double Up", 0.0, "Free"),
        ("Showdown Captain Mode", "Showdown", "NFL Showdown $3", 3.0, "Showdown"),
        ("Tiers", "Other", "NFL Tiers $5", 5.0, "Other game types"),
        ("Classic", "H2H", "NFL $2 vs. bob", 2.0, "H2H"),
        ("Classic", "Double Up", "NFL $10 Double Up", 10.0, "Double-up"),
        ("Classic", "50/50", "NFL $5 50/50", 5.0, "50/50"),
        ("Classic", "SE GPP", "NFL $8 [Single Entry]", 8.0, "SE GPP"),
        ("Classic", "Multiplier", "NFL $5 Quintuple Up", 5.0, "Multiplier"),
        ("Classic", "N-max GPP", "NFL $5 Mini [3 Entry Max]", 5.0, "3-max/5-max"),
        ("Classic", "N-max GPP", "NFL $3 Flea [20 Entry Max]", 3.0, "20-max and up"),
        ("Classic", "N-max GPP", "NFL $0.25 Play-Action", 0.25, "N-max, max not in name"),
        ("Classic", "Other", "NFL $5 Something", 5.0, "Other Classic GPP"),
    ],
)
def test_every_bucket_follows_the_ground_truth_order_of_checks(game, fmt, name, fee, bucket):
    assert ledger.bucket_of(game, fmt, name, fee) == bucket


def test_a_free_discord_contest_is_free_and_a_paid_one_is_fun():
    assert ledger.group_of("Discord", 0.0) == ledger.FREE
    assert ledger.group_of("Discord", 1.0) == ledger.FUN
    assert set(ledger.GROUP) == set(ledger.BUCKETS)


# -- the SE ------------------------------------------------------------------------------------------------


def se_frame():
    rows = []
    for wk, (fee, win) in enumerate([(10, 0), (10, 25), (10, 18), (10, 0), (10, 9)], start=1):
        rows.append(entry(2026, wk, "NFL $10 Double Up", "Double Up", fee, win))
    return ledger.load(log(rows))


def test_cluster_se_is_pinned_and_needs_two_weeks():
    frame = se_frame()
    assert ledger.cluster_se(frame) == pytest.approx(
        43.5521, abs=1e-3
    )  # pinned: gt_common.cluster_se gives the same
    assert ledger.cluster_se(frame[frame["Week"] == 1]) is None


def test_cluster_se_resamples_whole_weeks_not_entries():
    one = ledger.load(log([entry(2026, w, "DU", "Double Up", 10, 20 if w % 2 else 0) for w in (1, 2, 3, 4)]))
    rows = []
    for w in (1, 2, 3, 4):  # the same weeks, each entry split in two: the SE must not shrink
        rows += [entry(2026, w, "DU", "Double Up", 5, 10 if w % 2 else 0, key=f"{w}{i}") for i in "ab"]
    two = ledger.load(log(rows))
    assert ledger.cluster_se(one) == pytest.approx(ledger.cluster_se(two))


# -- rows --------------------------------------------------------------------------------------------------


def test_rows_are_in_bucket_order_with_season_and_all_seasons_and_the_seasons_shown_only():
    rows = [
        entry(2026, 1, "NFL $10 Double Up", "Double Up", 10, 18, field=100, pool=900),
        entry(2026, 2, "NFL $10 Double Up", "Double Up", 10, 0, field=100, pool=850),
        entry(2025, 3, "NFL $10 Double Up", "Double Up", 10, 18, field=100, pool=900),
        entry(2026, 1, "NFL $2 vs. bob", "H2H", 2, 3.6, opp="bob"),
        entry(2021, 5, "NFL $10 Double Up", "Double Up", 10, 18),  # before the seasons shown
        entry(2026, 10, "NFL $10 Double Up", "Double Up", 10, 18),  # after `through`
    ]
    frame = ledger.load(log(rows), through=(2026, 9))
    out = ledger.rows(frame, 2026)
    assert [r["format"] for r in out] == ["H2H", "Double-up"]
    du = out[1]
    assert du["all"]["n"] == 3 and du["season"]["n"] == 2
    assert du["all"]["net"] == pytest.approx(6.0) and du["season"]["net"] == pytest.approx(-2.0)
    assert du["season"]["roi"] == pytest.approx(-10.0) and du["season"]["cash"] == 50.0
    assert du["rake"] == pytest.approx(0.1)  # the median of 0.10, 0.15, 0.10
    assert du["by_week"] == {"1": 8.0, "2": -10.0} and du["by_season"] == {"2025": 8.0, "2026": -2.0}
    assert out[0]["season"]["se"] is None  # one week: no SE


# -- H2H ---------------------------------------------------------------------------------------------------


def h2h_rows():
    rows = []
    for i in range(5):  # a regular: five meetings, 1-4
        rows.append(
            entry(2025 if i < 3 else 2026, i + 1, "H2H vs. reg", "H2H", 2, 3.6 if i == 0 else 0, opp="Reg")
        )
    rows.append(entry(2026, 3, "H2H vs. once", "H2H", 2, 3.6, opp="Once"))
    rows.append(entry(2026, 4, "H2H vs. tie", "H2H", 2, 2.0, opp="Tied"))  # paid back: a tie
    rows.append(entry(2026, 5, "H2H", "H2H", 2, 3.6, opp=""))  # no opponent in the name
    return rows


def test_h2h_counts_regulars_others_and_unnamed_and_never_names_anyone():
    frame = ledger.load(log(h2h_rows()))
    h = ledger.h2h(frame, 2026)
    assert h["all"]["regular"] == {"opponents": 1, "games": 5, "wins": 1, "losses": 4, "ties": 0}
    assert h["all"]["other"] == {"opponents": 2, "games": 2, "wins": 1, "losses": 0, "ties": 1}
    assert h["all"]["unnamed"]["games"] == 1 and h["all"]["unnamed"]["opponents"] == 0
    assert h["season"]["regular"]["games"] == 2  # weeks 4 and 5 of 2026
    text = json.dumps(ledger.payload(log(h2h_rows()), season=2026, week=9))
    for name in ("Reg", "Once", "Tied"):
        assert f'"{name}"' not in text and name not in ledger.h2h_sentence(h, [2022, 2026])
    sentence = ledger.h2h_sentence(h, [2022, 2023, 2024, 2025, 2026])
    assert sentence.startswith("H2H: against 1 repeat opponents (5+ meetings, 2022-26) you are 1-4 (0-2 this")
    assert "everyone else 1-0-1" in sentence and "1 games had no opponent" in sentence


# -- this week's money ---------------------------------------------------------------------------------------


def record(**extra):
    contest = {"contest_id": "1", "name": "NFL $5 Double Up", "entry_fee": "$5", "format": "Double Up"}
    return {
        "lineups": [
            {"names": [], "contests": [{**contest, "entry_ids": ["a", "b"]}]},
            {
                "names": [],
                "contests": [
                    {
                        "contest_id": "2",
                        "name": "NFL $8 SE",
                        "entry_fee": "$8",
                        "format": "SE GPP",
                        "entry_ids": ["c"],
                    },
                    {
                        "contest_id": "3",
                        "name": "NFL $5 Mini [3 Entry Max]",
                        "entry_fee": "$5",
                        "format": "N-max GPP",
                        "entry_ids": ["d", "e"],
                    },
                ],
            },
        ],
        "blank": [
            {
                "contest_id": "4",
                "name": "NFL $2 vs. x",
                "entry_fee": "$2",
                "format": "H2H",
                "entry_ids": ["f"],
            }
        ],
        **extra,
    }


def test_entered_by_group_adds_lineups_blank_entries_and_skipped_showdown_fees():
    out = ledger.entered_by_group(record(skipped_fees={"Showdown": 6.0}))
    assert out == {"cash": 12.0, "small_gpp": 8.0, "fun": 16.0, "free": 0.0, "other": 0.0}
    assert ledger.entered_by_group(None)["cash"] == 0.0


def test_the_payload_plans_cash_and_small_gpp_from_the_tiers_and_fun_from_the_number_set():
    plan = contest_tiers.plan_dollars(100.0)
    p = ledger.payload(log(h2h_rows()), season=2026, week=9, plan=plan, fun_per_week=10.0, entered=record())
    assert p["groups"]["cash"]["planned"] == round(plan.a, 2)
    assert p["groups"]["small_gpp"]["planned"] == round(plan.b, 2)
    assert p["groups"]["fun"]["planned"] == 10.0 and p["recorded"] is True
    assert p["seasons"] == [2022, 2023, 2024, 2025, 2026]
    none = ledger.payload(log(h2h_rows()), season=2026, week=9)
    assert none["groups"]["cash"] == {"planned": None, "entered": None} and none["recorded"] is False


# -- the entered record --------------------------------------------------------------------------------------


def test_an_old_entered_file_loads_and_the_new_fields_round_trip(tmp_path):
    rec = entered.EnteredRecord(season=2026, week=9, recorded_at="t", source="s", lineups=[])
    path = entered.save(rec, tmp_path / "old.json")
    raw = json.loads(path.read_text())
    raw.pop("h2h"), raw.pop("skipped_fees")
    path.write_text(json.dumps(raw))
    old = entered.load(2026, 9, path)
    assert old.h2h == "" and old.skipped_fees == {}
    new = entered.EnteredRecord(
        season=2026,
        week=9,
        recorded_at="t",
        source="s",
        lineups=[],
        h2h="posted",
        skipped={"Showdown": 3},
        skipped_fees={"Showdown": 9.0},
    )
    back = entered.load(2026, 9, entered.save(new, tmp_path / "new.json"))
    assert back.h2h == "posted" and back.skipped_fees == {"Showdown": 9.0}
    text = "\n".join(entered.summary_lines(back))
    assert "skipped 3: Showdown ($9.00 in fees)" in text and "this week's H2Hs: ones you posted" in text


# -- the H2H log ---------------------------------------------------------------------------------------------


def test_the_h2h_log_appends_the_weeks_games_once_with_meetings_before(tmp_path):
    path = tmp_path / "h2h_log.csv"
    assert h2h_log.append(path, log(h2h_rows()), season=2026, week=5, posted_or_joined="joined") == 2
    before = path.read_bytes()
    assert h2h_log.append(path, log(h2h_rows()), season=2026, week=5) == 0  # a rerun adds nothing
    assert path.read_bytes() == before
    rows = pd.read_csv(path, keep_default_na=False)
    assert list(rows.columns) == h2h_log.COLUMNS
    reg = rows[rows["opponent"] == "Reg"].iloc[0]
    assert (
        str(reg["meetings_before"]) == "4" and reg["result"] == "loss" and reg["posted_or_joined"] == "joined"
    )
    assert h2h_log.result_of(2.0, 2.0) == "tie" and h2h_log.result_of(3.6, 2.0) == "win"


# -- the page ------------------------------------------------------------------------------------------------


def ledger_payload(**kw):
    rows = (
        h2h_rows()
        + [
            entry(2026, w, "NFL $10 Double Up", "Double Up", 10, 18 if w % 2 else 0, field=100, pool=870)
            for w in range(1, 9)
        ]
        + [entry(2026, 2, "NFL $3 Showdown", "Showdown", 3, 0, game="Showdown Captain Mode")]
    )
    return ledger.payload(log(rows), season=2026, week=9, plan=contest_tiers.plan_dollars(100.0), **kw)


def test_the_ledger_fold_shows_the_record_the_bar_and_the_h2h_line_and_never_says_over(tmp_path):
    page = render(
        inputs(tmp_path / "data", rows=slate_rows(), ledger=ledger_payload(entered=record(h2h="both")))
    )
    block = section(page, "ledger")
    text = page_text(block)
    assert "Double-up" in text and "ROI 2022-26 (±2 SE)" in text and "Plan $" in text
    assert 'class="iv"' in block and "<rect" in block  # the ±2 SE bar
    assert "Fun small budget, played for the fun of it" in " ".join(text.split())
    assert "no number set" in text and "This week's were some posted, some joined." in text
    assert "H2H: against 1 repeat opponents" in text
    assert "shared" in text  # Cash's plan is shared by H2H and Double-up
    for word in (" over", "Over", " cap ", 'class="bad"'):
        assert word not in block, word
    assert 'title="Tier A in the contest plan"' in block  # the tier stays a tooltip


def test_multiplier_and_the_other_formats_are_one_grey_line():
    rows = h2h_rows() + [
        entry(2026, 1, "NFL $5 Quintuple Up", "Multiplier", 5, 0),
        entry(2026, 2, "NFL Tiers $5", "Other", 5, 10, game="Tiers"),
    ]
    p = ledger.payload(log(rows), season=2026, week=9)
    assert p["other"]["formats"] == ["Multiplier", "Other game types"]
    assert p["other"]["all"]["n"] == 2 and p["other"]["all"]["net"] == 0.0
    cells = week_money.ledger_rows_html(p)
    grey = [c for c in cells if "Other: Multiplier, Other game types" in c[0]]
    assert len(grey) == 1 and 'class="faint"' in grey[0][0]
    assert not any(">Multiplier<" in c[0] for c in cells)


def test_the_ledger_says_entries_are_not_recorded_and_not_run_yet_without_its_input(tmp_path):
    page = render(inputs(tmp_path / "data", rows=slate_rows(), ledger=ledger_payload()))
    assert "Entered: not recorded yet (say 'record my entries')" in page_text(section(page, "ledger"))
    bare = render(inputs(tmp_path / "data2", rows=slate_rows()))
    assert "not run yet" in page_text(section(bare, "ledger")).lower()


def test_the_ledger_fold_comes_first_on_saturday_and_monday():
    assert week_page.section_order(5)[0] == "ledger"
    assert week_page.section_order(0)[0] == "ledger"
    assert week_page.section_order(5).index("ledger") < week_page.section_order(5).index("contest-plan")


def test_the_ledger_fold_line_is_plan_and_entered_by_group(tmp_path):
    inp = inputs(
        tmp_path / "data", rows=slate_rows(), ledger=ledger_payload(entered=record(), fun_per_week=10.0)
    )
    line, urgent = week_money.ledger_line(inp)
    assert line.startswith("Cash $12.00 of $") and "Fun $10.00 of $10.00" in line and not urgent
    assert week_money.fun_line(inp.ledger).endswith("your number $10.00 a week.")


# -- the CLI hooks -------------------------------------------------------------------------------------------


def test_h2h_is_asked_only_at_a_terminal_with_h2h_entries_and_never_when_piped(monkeypatch):
    from dfs import cli, prompt_missing

    monkeypatch.setattr(entered, "load", lambda *a, **k: None)
    asked = []
    monkeypatch.setattr(cli.typer, "prompt", lambda *a, **k: asked.append(1) or "Joined ")
    monkeypatch.setattr(prompt_missing, "interactive", lambda: False)
    assert cli._h2h_answer(None, True, 2026, 9) == "" and not asked  # piped: never asks
    assert cli._h2h_answer(cli.H2hKind.posted, True, 2026, 9) == "posted"
    monkeypatch.setattr(prompt_missing, "interactive", lambda: True)
    assert cli._h2h_answer(None, False, 2026, 9) == "" and not asked  # no H2H entries: nothing to ask
    assert cli._h2h_answer(None, True, 2026, 9) == "joined" and len(asked) == 1
    monkeypatch.setattr(
        entered, "load", lambda *a, **k: entered.EnteredRecord(2026, 9, "t", "s", [], h2h="both")
    )
    assert cli._h2h_answer(None, True, 2026, 9) == "both" and len(asked) == 1  # saved: asked once only


def test_the_close_saves_the_ledger_and_the_h2h_log_and_never_fails_over_them(monkeypatch, capsys):
    from dfs import cli

    # no config, no log under a test data dir is fine: a yellow line at most
    cli._h2h_log_after_close(None, 2026, 9)
    cli._save_ledger(object(), 2026, 9, through=9)  # no config at all: prints a warning, does not raise
    assert "not saved" in capsys.readouterr().out
