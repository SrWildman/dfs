"""Payout tables: flat and shifted-power-law tables pay out the prize pool, the min cash sits at the last paid
place, and the fits recover what generated the data."""

import numpy as np
import pandas as pd
import pytest

from dfs.sim import payouts as pay


def _gpp(**kw) -> pay.ContestKind:
    base = dict(
        series="NFL $8K Fair Catch [Single Entry]",
        format="SE GPP",
        entries=784,
        places_paid=174,
        prize_pool=8000.0,
        fee=12.0,
        min_cash=25 / 12,
    )
    base.update(kw)
    return pay.ContestKind(**base)


def test_a_flat_contest_pays_every_paid_place_the_same():
    du = pay.ContestKind("NFL GIANT $10 Double Up", "Double Up", 4597, 2000, 40000.0, 10.0)
    table = pay.payout_table(du)
    assert len(table) == 2000
    assert np.allclose(table, 20.0)
    assert du.flat and du.cash


def test_a_gpp_table_pays_out_the_pool_down_to_the_min_cash():
    kind = _gpp()
    for form in pay.FORMS:
        table = pay.payout_table(kind, form=form)
        assert len(table) == 174
        assert table.sum() == pytest.approx(8000.0)
        assert table[-1] == pytest.approx(25.0)
        assert np.all(np.diff(table) <= 1e-9)  # never pays a worse place more
        assert table[0] > 20 * table[-1]


def test_a_steeper_exponent_pays_first_more():
    assert pay.payout_table(_gpp(), exponent=1.3)[0] > pay.payout_table(_gpp(), exponent=0.8)[0]


def test_a_min_cash_the_pool_cannot_cover_is_capped():
    kind = _gpp(prize_pool=1000.0, places_paid=100, fee=12.0, min_cash=2.0)  # 100 x $24 > $1,000
    table = pay.payout_table(kind)
    assert table.sum() == pytest.approx(1000.0)
    assert table[-1] == pytest.approx(pay.MIN_CASH_SHARE * 1000.0 / 100)


def test_places_outside_the_paid_places_pay_nothing():
    out = pay.payout_at(_gpp(), [1, 174, 175, 0, 900])
    assert out[0] > 0 and out[1] == pytest.approx(25.0)
    assert list(out[2:]) == [0.0, 0.0, 0.0]


def test_the_entry_limit_comes_from_the_name():
    assert pay.max_entries_of("NFL $5K Screen Pass [5 Entry Max]") == 5
    assert pay.max_entries_of("NFL $10 Hundo [Single Entry]") == 1
    assert pay.max_entries_of("NFL GIANT $10 Double Up") == 1


def _log(rows) -> pd.DataFrame:
    cols = [
        "season",
        "week",
        "date",
        "game_type",
        "series",
        "format",
        "contest_key",
        "entries",
        "places_paid",
        "prize_pool",
        "fee",
        "place",
        "winnings",
    ]
    frame = pd.DataFrame(rows, columns=cols)
    frame["date"] = pd.to_datetime(frame["date"])
    frame["week"] = frame["week"].astype("Int64")
    return frame


def _synthetic_log(b: float = 1.0) -> pd.DataFrame:
    """Entries landing at a spread of places in three contests over three weeks, paid by the shifted curve."""
    rows = []
    for week, (key, n, m, pool, fee, c) in enumerate(
        [
            ("1", 784, 174, 8000.0, 12.0, 25 / 12),
            ("2", 1426, 300, 6000.0, 5.0, 2.0),
            ("3", 4756, 1103, 4000.0, 1.0, 1.5),
        ],
        start=1,
    ):
        kind = pay.ContestKind("s" + key, "SE GPP", n, m, pool, fee, c)
        table = pay.payout_table(kind, exponent=b)
        for place in (1, 3, 8, 20, 60, m // 2, m - 5, m):
            rows.append(
                (
                    2025,
                    week,
                    f"2025-10-0{week}",
                    "Classic",
                    "s" + key,
                    "SE GPP",
                    key,
                    n,
                    m,
                    pool,
                    fee,
                    place,
                    float(table[place - 1]),
                )
            )
        rows.append(
            (
                2025,
                week,
                f"2025-10-0{week}",
                "Classic",
                "s" + key,
                "SE GPP",
                key,
                n,
                m,
                pool,
                fee,
                m + 10,
                0.0,
            )
        )
    return _log(rows)


def test_paid_entries_keeps_paid_gpp_rows_and_keys_weeks():
    paid = pay.paid_entries(_synthetic_log())
    assert len(paid) == 24  # the unpaid row in each contest is left out
    assert set(paid["week_key"]) == {"2025-1", "2025-2", "2025-3"}
    assert paid["rel_place"].max() == 1.0


def test_a_slate_without_a_week_number_is_its_own_fold_by_date():
    log = _synthetic_log()
    log.loc[log["contest_key"] == "3", "week"] = pd.NA
    paid = pay.paid_entries(log)
    assert "2025-2025-10-03" in set(paid["week_key"])


def test_min_cash_is_read_from_the_bottom_half_then_falls_back_by_fee_and_format():
    paid = pay.paid_entries(_synthetic_log())
    mc = pay.fit_min_cash(paid)
    assert mc.by_series["s1"] == pytest.approx(25 / 12)
    assert mc.lookup("s3", 1.0, "SE GPP") == pytest.approx(1.5)
    assert mc.lookup("unknown", 5.0, "SE GPP") == pytest.approx(2.0)  # by fee
    assert mc.lookup("unknown", 7.0, "SE GPP") == pytest.approx(np.median([25 / 12, 2.0, 1.5]))  # by format
    assert mc.lookup("unknown", 7.0, "N-max GPP") == pay.DEFAULT_MIN_CASH


@pytest.mark.parametrize("b", [0.8, 1.0, 1.2])
def test_the_exponent_fit_recovers_the_one_that_paid(b):
    paid = pay.paid_entries(_synthetic_log(b))
    assert pay.fit_exponent(paid) == pytest.approx(b, abs=0.01)


def test_held_out_predictions_never_see_their_own_week():
    paid = pay.paid_entries(_synthetic_log())
    pred = pay.held_out_predictions(paid)
    assert pred["predicted"].notna().all()
    # each held-out contest's own min cash is unknown to its fold: it falls back to the other series' median
    held = pred[pred["series"] == "s1"]
    assert held["predicted"].iloc[-1] == pytest.approx(12.0 * np.median([2.0, 1.5]))
    summary = pay.summarize(pred)
    assert summary["entries"].iloc[0] == 24
    assert list(pay.summarize(pred, "series")["group"]) == ["s1", "s2", "s3"]


def test_recent_kinds_takes_each_series_latest_contest():
    log = _synthetic_log()
    log["slate"] = "main"
    later = log[log["contest_key"] == "1"].copy()
    later["week"], later["contest_key"], later["entries"] = 4, "9", 900
    kinds = pay.recent_kinds(pd.concat([log, later]), 2025)
    by = {k.series: k for k in kinds}
    assert by["s1"].entries == 900
    assert by["s1"].min_cash == pytest.approx(25 / 12)
    early = {k.series: k for k in pay.recent_kinds(pd.concat([log, later]), 2025, through_week=3)}
    assert early["s1"].entries == 784
