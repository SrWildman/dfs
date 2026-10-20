import math
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from dfs import field_own as fo


def _slate(week: int = 1) -> pd.DataFrame:
    """Two QBs, three RBs, three WRs, two TEs, two DSTs; one RB is OUT."""
    rows = [
        ("Q1", "QB", 0.60, 20.0, 20.0, 7000, ""),
        ("Q2", "QB", 0.40, 18.0, 18.0, 6500, ""),
        ("R1", "RB", 0.50, 15.0, 15.0, 6000, ""),
        ("R2", "RB", 0.30, 12.0, 12.0, 5000, ""),
        ("R3", "RB", 0.20, 10.0, 10.0, 4000, "OUT"),
        ("W1", "WR", 0.50, 14.0, 14.0, 6000, ""),
        ("W2", "WR", 0.30, 12.0, 12.0, 5000, ""),
        ("W3", "WR", 0.20, 10.0, 10.0, 4000, ""),
        ("T1", "TE", 0.70, 10.0, 10.0, 5000, ""),
        ("T2", "TE", 0.30, 8.0, 8.0, 4000, ""),
        ("D1", "DST", 0.50, 8.0, 8.0, 3000, ""),
        ("D2", "DST", 0.50, 8.0, 8.0, 3000, ""),
    ]
    frame = pd.DataFrame(rows, columns=["Name", "Position", "Own%", "ProjPts", "CalPts", "Salary", "Avail"])
    frame["Id"] = range(len(frame))
    frame["week"] = week
    return frame


def _model(**overrides) -> fo.FieldOwnModel:
    totals = dict.fromkeys(fo.POSITIONS, 1.0)  # small slates: keep every share under MAX_OWN
    values = dict(
        exponent={"gpp": 1.0, "cash": 2.0},
        totals={"gpp": totals, "cash": totals},
        response=4.0,
        weeks=(1,),
    )
    values.update(overrides)
    return fo.FieldOwnModel(**values)


def test_exponent_one_reproduces_tffb_within_position_scaled_to_totals():
    slate = _slate()
    gpp = fo.predict(slate, _model(), "gpp")
    assert gpp.iloc[0] == pytest.approx(0.60)  # QB shares already sum to 1
    # RB: R3 is OUT, so R1 and R2 split the total of 1 in proportion 5:3
    assert gpp.iloc[2] == pytest.approx(5 / 8)
    assert gpp.iloc[4] == 0.0


def test_higher_exponent_concentrates_on_the_chalk():
    slate = _slate()
    gpp, cash = fo.predict(slate, _model(), "gpp"), fo.predict(slate, _model(), "cash")
    assert cash.iloc[8] > gpp.iloc[8]  # T1, the TE chalk
    assert cash.iloc[9] < gpp.iloc[9]
    for pos in fo.POSITIONS:
        rows = slate["Position"] == pos
        assert cash[rows].sum() == pytest.approx(gpp[rows].sum())


def test_ours_moves_toward_the_player_we_project_higher():
    slate = _slate()
    slate.loc[slate["Name"] == "W3", "CalPts"] = 12.0  # we like W3 20% more than the field does
    model = _model()
    ours, gpp = fo.predict_ours(slate, model), fo.predict(slate, model, "gpp")
    w3 = slate["Name"] == "W3"
    assert ours[w3].iloc[0] > gpp[w3].iloc[0]
    assert ours[slate["Name"] == "W1"].iloc[0] < gpp[slate["Name"] == "W1"].iloc[0]


def test_projection_gap_ignores_tiny_projections_and_is_capped():
    slate = _slate()
    slate.loc[0, ["CalPts", "ProjPts"]] = [2.0, 0.0]  # a 0-point TFFB projection is no disagreement
    slate.loc[1, ["CalPts", "ProjPts"]] = [40.0, 10.0]
    gap = fo.projection_gap(slate)
    assert gap[0] == 0.0
    assert gap[1] == pytest.approx(fo.GAP_LOG_CAP)


def test_no_player_is_shown_above_the_cap():
    slate = _slate()
    model = _model(totals={"gpp": {**_model().totals["gpp"], "TE": 3.0}, "cash": _model().totals["cash"]})
    assert fo.predict(slate, model, "gpp").max() <= fo.MAX_OWN


def test_attach_fills_fractions_and_blanks_without_a_model_or_ownership():
    slate = _slate().drop(columns="week")
    filled = fo.attach(slate, _model())
    assert set(fo.COLUMNS) <= set(filled.columns)
    assert filled.loc[4, list(fo.COLUMNS)].eq(0.0).all()  # OUT
    assert filled[fo.GPP_COLUMN].between(0, 1).all()
    assert fo.attach(slate, None)[fo.GPP_COLUMN].isna().all()
    unpublished = slate.assign(**{"Own%": 0.0})
    assert fo.attach(unpublished, _model())[fo.CASH_COLUMN].isna().all()


def test_fit_recovers_the_exponent_and_totals():
    weeks = []
    for week in (1, 2):
        slate = _slate(week)
        truth = _model(exponent={"gpp": 1.3, "cash": 1.8})
        slate["act_gpp"] = fo.predict(slate, truth, "gpp")
        slate["act_cash"] = fo.predict(slate, truth, "cash")
        weeks.append(slate)
    model = fo.fit(pd.concat(weeks, ignore_index=True), {"gpp": 2, "cash": 2})
    assert model.exponent["gpp"] == pytest.approx(1.3, abs=0.01)
    assert model.exponent["cash"] == pytest.approx(1.8, abs=0.01)
    assert model.totals["gpp"]["RB"] == pytest.approx(1.0, abs=1e-3)
    assert model.weeks == (1, 2)
    assert model.contests == {"gpp": 2, "cash": 2}


def test_fit_needs_both_formats():
    slate = _slate().assign(act_gpp=0.1, act_cash=0.0)
    assert fo.fit(slate) is None


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "field_own.json"
    fo.save(_model(), path)
    assert fo.load(path) == _model()
    assert fo.load(tmp_path / "missing.json") is None


def test_actual_ownership_is_the_mean_over_the_weeks_contests_of_a_format():
    contests = [
        SimpleNamespace(contest_id="a", week=1, fmt="gpp", own={"q1": 0.6, "r1": 0.4}),
        SimpleNamespace(contest_id="b", week=1, fmt="gpp", own={"q1": 0.4}),
        SimpleNamespace(contest_id="c", week=1, fmt="cash", own={"q1": 0.9}),
        SimpleNamespace(contest_id="d", week=2, fmt="gpp", own={"q1": 0.1}),
    ]
    own, n = fo.actual_ownership(contests, 1, "gpp")
    assert n == 2
    assert own["q1"] == pytest.approx(0.5)
    assert own["r1"] == pytest.approx(0.2)  # not drafted in b counts 0 there
    frame, counts = fo.week_frame(_slate().drop(columns="week"), contests, 1)
    assert counts == {"gpp": 2, "cash": 1}
    assert frame.loc[frame["Name"] == "W1", "act_gpp"].iloc[0] == 0.0


def test_score_weeks_judges_each_week_on_earlier_weeks_only():
    weeks = []
    for week in (1, 2, 3):
        slate = _slate(week)
        truth = _model(exponent={"gpp": 1.5, "cash": 2.0})
        slate["act_gpp"] = fo.predict(slate, truth, "gpp")
        slate["act_cash"] = fo.predict(slate, truth, "cash") if week > 1 else 0.0
        weeks.append(slate)
    scores = fo.score_weeks(pd.concat(weeks, ignore_index=True), curve=lambda x: x)
    assert sorted(scores.loc[scores["Format"] == "gpp", "Week"].unique()) == [2, 3]
    assert sorted(scores.loc[scores["Format"] == "cash", "Week"].unique()) == [3]
    ours = scores[scores["Model"] == "Ours"]
    tffb = scores[scores["Model"] == "TFFB Own%"]
    assert ours["MAE"].max() < 1e-3 < tffb["MAE"].min()
    assert "Double-up curve" in set(scores.loc[scores["Format"] == "cash", "Model"])
    summary = fo.score_summary(scores)
    assert set(summary["Model"]) == {"TFFB Own%", "Double-up curve", "Ours"}


def test_disagreement_share_is_one_when_calpts_was_right():
    rng = np.random.default_rng(0)
    n = 60
    proj = rng.uniform(8, 20, n)
    cal = proj + rng.normal(0, 2, n)
    snaps = pd.DataFrame(
        {
            "week": 1,
            "Id": [str(i) for i in range(n)],
            "Position": "WR",
            "Avail": "",
            "Own%": 0.05,
            "ProjPts": proj,
            "CalPts": cal,
        }
    )
    scored = pd.DataFrame({"week": 1, "Id": range(n), "DkActual": cal})
    table = fo.disagreement(snaps, scored)
    assert list(table["Week"]) == ["1", "All"]
    assert table["Gap share"].iloc[-1] == pytest.approx(1.0)
    assert table["Top fifth"].iloc[-1] > 0 > table["Bottom fifth"].iloc[-1]
    assert not math.isnan(table["n"].iloc[0])


def test_the_edgeraw_column_names_are_the_ones_derived_appends():
    from dfs import derived

    assert list(fo.COLUMNS) == derived.FIELD_OWN_COLUMNS
    assert derived.EDGE_COLUMNS[-3:] == derived.EDGE_SHEET_ORDER[-3:] == derived.FIELD_OWN_COLUMNS


def test_the_ownership_shadow_is_not_linked_onto_player_pool_or_lineups():
    from dfs import derived
    from dfs.sheet_links import LINKED_EDGE_COLUMNS

    assert not set(derived.FIELD_OWN_COLUMNS) & set(LINKED_EDGE_COLUMNS)
