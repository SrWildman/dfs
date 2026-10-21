"""portfolio_inputs.py: the live plan from builds and DK's entries, its file and the `entries fill` hint."""

import sys
from datetime import UTC, datetime

import pandas as pd
import pytest

sys.path.insert(0, "tests")
import test_entries as te  # noqa: E402
import test_lineup_suggest as syn  # noqa: E402

from dfs import entries as ent  # noqa: E402
from dfs import lineup_suggest as ls  # noqa: E402
from dfs import portfolio_inputs as pi  # noqa: E402
from dfs import stacks_inputs  # noqa: E402
from dfs.sim import field_gen, payouts, stacks_retro  # noqa: E402
from dfs.sim_inputs import build_specs  # noqa: E402

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
SINGLEBACK = "NFL $4K Singleback [3 Entry Max]"
FAIR_CATCH = "NFL $8K Fair Catch [Single Entry]"


def entry(entry_id, name, contest_id, fee, fmt):
    return ent.Entry(ent.FileEntry(entry_id, name, contest_id, fee, ("",) * 9), fmt)


def week_entries():
    return [entry(str(i), SINGLEBACK, "703", "$5", "N-max GPP") for i in range(3)] + [
        entry("9", FAIR_CATCH, "704", "$12", "SE GPP"),
        entry("10", "NFL GIANT $25 Double Up [Single Entry]", "700", "$25", "Double Up"),
    ]


def history_log():
    def row(series, fmt, fee, entries, paid, pool):
        return {
            "season": 2026, "game_type": "Classic", "slate": "main", "fee": fee, "format": fmt,
            "places_paid": paid, "week": 4, "date": pd.Timestamp("2026-10-11"), "series": series,
            "entries": entries, "prize_pool": pool,
        }  # fmt: skip

    return pd.DataFrame(
        [
            row(SINGLEBACK, "N-max GPP", 5.0, 4756, 1000, 4000.0),
            row(FAIR_CATCH, "SE GPP", 12.0, 784, 200, 8000.0),
        ]
    )


@pytest.fixture
def field_model(monkeypatch):
    model = stacks_retro.FieldModel(
        rates=field_gen.FieldRates(0.6, 0.3, 0.4, 0.1, (0.0, 200.0, 500.0)),
        herd=field_gen.HerdModel(share=0.05),
        weeks=(4,),
    )
    monkeypatch.setattr(stacks_inputs, "load_field_model", lambda path=None: model)
    return model


def test_a_contest_is_covered_by_its_series_in_the_log_and_cash_entries_are_left_out():
    covered, skipped = pi.contest_kinds(week_entries(), history_log(), 2026, payouts.MinCash())
    assert set(covered) == {"703", "704"} and not skipped  # the double-up is not a GPP
    name, kind, n = covered["703"]
    assert (name, n, kind.entries, kind.max_entries) == (SINGLEBACK, 3, 4756, 3)


def test_a_series_the_log_has_never_seen_keeps_todays_pick_and_says_so():
    new = [entry("1", "NFL $50K Brand New [Single Entry]", "800", "$33", "SE GPP")]
    covered, skipped = pi.contest_kinds(new, history_log(), 2026, payouts.MinCash())
    assert not covered and "no contest like it" in skipped["800"]


def test_a_new_contest_of_the_same_fee_and_format_borrows_the_latest_one():
    same_fee = [entry("1", "NFL $9K Some Other Name [Single Entry]", "801", "$12", "SE GPP")]
    covered, _ = pi.contest_kinds(same_fee, history_log(), 2026, payouts.MinCash())
    assert covered["801"][1].fee == 12.0 and covered["801"][1].entries == 784


def test_no_fitted_field_model_gives_a_note_and_no_plan(monkeypatch):
    monkeypatch.setattr(stacks_inputs, "load_field_model", lambda path=None: None)
    plan = pi.plan_portfolio(
        week=7, builds=[], specs={}, edge=syn.slate(), entries=week_entries(), log=history_log(), season=2026
    )
    assert not plan.portfolio and "dfs stacks --refit" in plan.notes[0]


def test_the_plan_fills_every_gpp_entry_with_a_build_and_compares_to_todays_pick(field_model):
    edge = syn.slate(teams=8, seed=2)
    specs = build_specs(edge)
    report = ls.suggest_builds(
        edge, dict.fromkeys(edge["Name"], "Both"), specs, now=NOW, cap=50000, cash_line=145.0,
        gpp_target=190.0, metric="CalPts", cash_builds=1, gpp_builds=6, n_sims=300, time_limit=20,
    )  # fmt: skip
    plan = pi.plan_portfolio(
        week=7, builds=report.builds, specs=specs, edge=edge, entries=week_entries(), log=history_log(),
        season=2026, objective="top1", n_sims=300, min_cash=payouts.MinCash(), now=NOW,
    )  # fmt: skip
    by_id = {c.contest_id: c for c in plan.contests}
    assert [len(by_id[c].builds) for c in ("703", "704")] == [3, 1]
    assert len(set(by_id["703"].builds)) == 3  # never the same build twice in one contest
    gpp_numbers = {b.number for b in report.builds if b.kind == "gpp"}
    assert set(by_id["703"].builds + by_id["704"].builds) <= gpp_numbers
    # the plan's own objective is at least as good as today's pick on the draws it was chosen on
    assert plan.portfolio["p_any_top1"] >= plan.today["p_any_top1"] - 0.02
    text = "\n".join(pi.format_plan(plan))
    assert "Portfolio (top1)" in text and SINGLEBACK in text and "default off until it is proven" in text


def test_the_plan_round_trips_through_its_file(tmp_path):
    plan = pi.PortfolioPlan(
        7, "edollars", "2026-10-18T12:00:00+00:00", notes=["n"], portfolio={"e_dollars": 1.0}
    )
    plan.contests.append(pi.PlannedContest("703", SINGLEBACK, 5.0, [["a", "b"], None], [3, None]))
    path = pi.save_plan(plan, tmp_path / "7_portfolio.json")
    again = pi.load_plan(7, path)
    assert again == plan
    assert pi.load_plan(7, tmp_path / "missing.json") is None
    path.write_text("not json")
    assert pi.load_plan(7, path) is None


def plan_for(lineup_names):
    plan = pi.PortfolioPlan(7, "top1", "2026-10-18T12:00:00+00:00", portfolio={"e_dollars": 1.0})
    # contest 703 (the 3-entry Singleback) is planned L4 then L3; 704's planned lineup is not on Lineups
    plan.contests = [
        pi.PlannedContest("703", SINGLEBACK, 5.0, [list(lineup_names[4]), list(lineup_names[3])], [11, 12]),
        pi.PlannedContest("704", FAIR_CATCH, 8.0, [["not", "on", "the", "sheet"]], [13]),
    ]
    return plan


def test_entries_fill_shows_the_plans_lineup_as_the_hint_once_it_is_on_lineups(tmp_path):
    parsed = ent.read_entries_file(te.write_entries_file(tmp_path / "DraftKings Entries (3).csv"))
    lineups = te.sheet_lineups(**te.STANDARD_LINEUPS)
    names = {n: tuple(lineups[n - 1].names) for n in (1, 2, 3, 4)}
    suggester = pi.portfolio_suggester(plan_for(names), ent.suggest_pick)
    plan = ent.plan_fill(parsed, {}, lineups, now=te.BEFORE, suggester=suggester)
    rows = {r.entry.entry_id: r for r in plan.rows}
    # the 3-entry GPP: the plan's L4 then L3, then (its two lineups taken) today's pick has nothing left
    assert [rows[e].suggested for e in ("9004", "9005", "9006")] == ["L4", "L3", ""]
    assert rows["9004"].why == "portfolio: build 11"
    # the single-entry GPP's planned lineup is not on Lineups: today's pick, and the reason says why
    assert rows["9007"].suggested == "L3" and "not on Lineups yet" in rows["9007"].why
    # cash entries never use the plan
    assert rows["9001"].suggested == "L1" and "best Cash %" in rows["9001"].why
    assert all(r.lineup is None for r in plan.rows)  # a hint is still not a choice


def test_a_lineup_sam_already_put_in_the_contest_is_not_hinted_again(tmp_path):
    parsed = ent.read_entries_file(te.write_entries_file(tmp_path / "DraftKings Entries (3).csv"))
    lineups = te.sheet_lineups(**te.STANDARD_LINEUPS)
    names = {n: tuple(lineups[n - 1].names) for n in (1, 2, 3, 4)}
    suggester = pi.portfolio_suggester(plan_for(names), ent.suggest_pick)
    plan = ent.plan_fill(parsed, te.tab_rows_for(e9004="L4"), lineups, now=te.BEFORE, suggester=suggester)
    rows = {r.entry.entry_id: r for r in plan.rows}
    assert rows["9004"].lineup == 4  # his pick stands
    assert rows["9005"].suggested == "L3"


# -- the wide pool -----------------------------------------------------------------------------------------


def test_noisy_inputs_scale_the_edge_and_the_specs_by_the_same_factor():
    import numpy as np

    edge = syn.slate(teams=4, seed=1)
    specs = build_specs(edge)
    noisy_edge, noisy_specs = pi.noisy_inputs(edge, specs, "CalPts", 0.2, np.random.default_rng(3))
    for name, spec in specs.items():
        row = edge.index[edge["Name"] == name][0]
        edge_factor = noisy_edge.loc[row, "CalPts"] / edge.loc[row, "CalPts"]
        assert noisy_specs[name].projection / spec.projection == pytest.approx(edge_factor, rel=1e-6)
    assert not noisy_edge["CalPts"].equals(edge["CalPts"]) and (noisy_edge["CalPts"] > 0).all()


def quick_builds(edge, specs, **kw):
    return ls.suggest_builds(
        edge, dict.fromkeys(edge["Name"], "Both"), specs, now=NOW, cap=50000, cash_line=145.0,
        gpp_target=190.0, metric="CalPts", n_sims=200, time_limit=10, **kw,
    ).builds  # fmt: skip


def test_the_wide_pool_comes_from_noisy_builder_runs_and_its_picks_become_new_builds(field_model):
    edge = syn.slate(teams=8, seed=2)
    specs = build_specs(edge)
    builds = quick_builds(edge, specs, cash_builds=1, gpp_builds=2)
    wide = pi.wide_lineups(
        edge, specs, "CalPts", lambda e, s: quick_builds(e, s, cash_builds=0, gpp_builds=6), runs=2
    )
    assert wide and all(len(lu) == 9 for lu in wide)
    entries = [entry(str(i), SINGLEBACK, "703", "$5", "N-max GPP") for i in range(3)]
    plan = pi.plan_portfolio(
        week=7, builds=builds, specs=specs, edge=edge, entries=entries, log=history_log(), season=2026,
        objective="top1", n_sims=300, min_cash=payouts.MinCash(), now=NOW, wide=wide,
    )  # fmt: skip
    assert any("wide pool" in n for n in plan.notes)
    picks = plan.for_contest("703")
    assert len(picks.lineups) == 3  # three entries filled though there are only two GPP builds
    assert plan.new_lineups and None in picks.builds
    players = {p.name: p for lineup in wide for p in lineup}
    scored = ls.score(
        [[players[n] for n in names] for names in plan.new_lineups],
        specs, {}, cash_line=145.0, gpp_target=190.0, n_sims=200,
    )  # fmt: skip
    added = pi.add_new_builds(plan, builds, scored, gpp_entries=1000)
    top = max(b.number for b in builds)
    assert [b.number for b in added] == list(range(top + 1, top + 1 + len(added)))
    assert all(b.kind == "gpp" and b.note == "portfolio pick (wide pool)" for b in added)
    assert None not in plan.for_contest("703").builds  # the plan now names them by their new numbers


def test_robust_is_chosen_on_shocked_draws(field_model, monkeypatch):
    from dfs import portfolio as pf

    edge = syn.slate(teams=8, seed=2)
    specs = build_specs(edge)
    builds = quick_builds(edge, specs, cash_builds=0, gpp_builds=3)
    used = []
    real = pf.shocked_points
    monkeypatch.setattr(pf, "shocked_points", lambda *a, **k: used.append(1) or real(*a, **k))
    entries = [entry("1", FAIR_CATCH, "704", "$12", "SE GPP")]
    kw = dict(week=7, builds=builds, specs=specs, edge=edge, entries=entries, log=history_log(), season=2026,
              n_sims=200, min_cash=payouts.MinCash(), now=NOW)  # fmt: skip
    pi.plan_portfolio(objective="edollars", **kw)
    assert not used
    pi.plan_portfolio(objective="robust", **kw)
    assert used
