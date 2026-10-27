"""The saved late-swap run the Cockpit reads back."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from dfs import late_swap_store as lss
from dfs.late_swap_search import LineupSuggestions, Swap, SwapPlayer

NOW = datetime(2026, 10, 25, 16, 0, tzinfo=UTC)


def player(name):
    return SwapPlayer(name, "WR", "ATL", "SF", "SF@ATL", 6000, 10.0)


def swap(out, into, delta, p_cash=None, p_gpp=None, index=3):
    return Swap(
        changes={index: (player(out) if out else None, player(into))},
        delta=delta,
        salary_left=100,
        delta_p_cash=p_cash,
        delta_p_gpp=p_gpp,
    )


def suggestions(refill=None, pairs=(), singles=None, open_slots=("WR",)):
    state = SimpleNamespace(open=[SimpleNamespace(slot=s) for s in open_slots])
    return LineupSuggestions(state=state, refill=refill, pairs=list(pairs), singles=singles or {})


def test_the_swap_that_raises_the_goal_probability_most_wins_whatever_its_kind():
    s = suggestions(
        refill=swap("A", "B", 3.0, p_cash=0.01, p_gpp=0.05),
        pairs=[swap("C", "D", 2.0, p_cash=0.04, p_gpp=0.0)],
        singles={3: [swap("E", "F", 1.0, p_cash=0.02, p_gpp=0.0)]},
    )
    best = lss.best_change(s, "cash")
    assert (best.kind, best.out, best.into) == ("pair", ("C",), ("D",))
    assert lss.best_change(s, "gpp").kind == "refill"


def test_a_change_that_does_not_raise_the_goal_probability_is_not_a_best_change():
    s = suggestions(singles={3: [swap("E", "F", 5.0, p_cash=-0.01, p_gpp=0.02)]})
    assert lss.best_change(s, "cash") is None
    assert lss.best_change(s, "gpp") is not None


def test_without_probabilities_the_biggest_projection_gain_wins_and_must_be_a_gain():
    s = suggestions(singles={3: [swap("E", "F", 1.0), swap("G", "H", 2.5)]})
    assert lss.best_change(s, "cash").into == ("H",)
    assert lss.best_change(suggestions(singles={3: [swap("E", "F", 0.0)]}), "cash") is None
    assert lss.best_change(suggestions(), "gpp") is None


def test_an_empty_slot_has_nobody_going_out():
    best = lss.best_change(suggestions(refill=swap(None, "B", 2.0)), "cash")
    assert best.out == () and best.into == ("B",)


def test_summarize_records_names_goal_and_open_slots():
    result = lss.summarize(4, "gpp", ["a"] * 9, suggestions(open_slots=("WR", "FLEX")))
    assert (result.number, result.goal, result.open_slots, result.best) == (4, "gpp", ("WR", "FLEX"), None)


def test_save_then_load_round_trips(tmp_path):
    best = lss.BestChange("pair", ("A", "B"), ("C", "D"), 2.5, 0.031, None)
    saved = lss.Saved(
        NOW,
        True,
        (
            lss.LineupResult(2, "cash", ("x",) * 9, ("WR",), best),
            lss.LineupResult(3, "gpp", ("y",) * 9, (), None),
        ),
        (lss.NewsItem("A", "Q", "OUT", "ruled out", "2026-10-25T15:00:00+00:00"),),
    )
    lss.save(saved, tmp_path)
    loaded = lss.load(tmp_path)
    assert loaded == saved
    assert loaded.for_lineup(2).best.delta_p_cash == 0.031 and loaded.for_lineup(9) is None


def test_the_field_a_run_was_scored_against_is_saved_and_defaults_to_projected(tmp_path):
    real = "field: real (NFL GIANT $10 Double Up, 3,448 lineups, exported Sun 1:32 pm ET)"
    lss.save(lss.Saved(NOW, False, field_source=real), tmp_path)
    assert lss.load(tmp_path).field_source == real
    assert lss.Saved(NOW, False).field_source == "field: projected"
    # a file written before the field existed reads as projected
    (tmp_path / lss.STORE_FILE).write_text('{"saved_at": "2026-10-25T16:00:00+00:00", "lineups": []}')
    assert lss.load(tmp_path).field_source == lss.FIELD_PROJECTED


def test_a_missing_or_corrupt_file_loads_as_none(tmp_path):
    assert lss.load(tmp_path) is None
    (tmp_path / lss.STORE_FILE).write_text("{not json")
    assert lss.load(tmp_path) is None
    (tmp_path / lss.STORE_FILE).write_text('{"saved_at": "2026-10-25T16:00:00+00:00"}')
    assert lss.load(tmp_path) is None  # no "lineups" key


def test_a_run_is_stale_after_twelve_hours():
    saved = lss.Saved(NOW, False)
    assert not saved.is_stale(NOW + timedelta(hours=11))
    assert saved.is_stale(NOW + timedelta(hours=13))


def test_save_replaces_atomically_and_leaves_no_temp_file(tmp_path):
    lss.save(lss.Saved(NOW, False), tmp_path)
    lss.save(lss.Saved(NOW + timedelta(minutes=5), False), tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == [lss.STORE_FILE]
    assert lss.load(tmp_path).saved_at == NOW + timedelta(minutes=5)


def test_the_overlap_label_and_the_position_are_kept_for_the_cockpit(tmp_path):
    from dfs.late_swap_position import SwapOverlap

    swapped = swap("A", "B", 1.0, p_cash=0.0, p_gpp=0.02)
    swapped.overlap = SwapOverlap("LEVERAGE", 0.3, 0.05)
    result = lss.summarize(4, "gpp", ["a"] * 9, suggestions(singles={3: [swapped]}), "top 12%, 18 pts behind")
    assert result.best.overlap == "LEVERAGE" and result.position == "top 12%, 18 pts behind"
    lss.save(lss.Saved(NOW, False, (result,)), tmp_path)
    loaded = lss.load(tmp_path).for_lineup(4)
    assert loaded == result
    # a file from before the label reads without one
    (tmp_path / lss.STORE_FILE).write_text(
        '{"saved_at": "2026-10-25T16:00:00+00:00", "lineups": [{"number": 1, "goal": "gpp", "names": [], '
        '"open_slots": [], "best": {"kind": "single", "out": ["A"], "into": ["B"], "delta_pts": 1.0}}]}'
    )
    old = lss.load(tmp_path).for_lineup(1)
    assert old.best.overlap == "" and old.position == ""
