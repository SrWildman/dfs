from datetime import UTC, datetime

import pandas as pd
import pytest

from dfs.late_swap import lineup_slot_status, swap_candidates
from dfs.models import ROSTER_SLOTS

NOW = datetime(2026, 9, 14, 18, 0, tzinfo=UTC)  # kickoff Sunday 1pm ET games already underway


def _edge_row(name, position, team, game_start, leverage=10.0, flag=""):
    return {
        "Name": name,
        "Position": position,
        "Team": team,
        "ProjPts": 15.0,
        "Leverage": leverage,
        "Flag": flag,
        "GameStart": game_start,
    }


def _edge(*rows):
    return pd.DataFrame(list(rows))


def _names(by_index: dict[int, str] | None = None):
    """Build a full ROSTER_SLOTS-length name list; unspecified slots blank.
    Slots repeat (RB/WR), so callers key by index, not by name."""
    names = [""] * len(ROSTER_SLOTS)
    for index, name in (by_index or {}).items():
        names[index] = name
    return names


def test_lineup_slot_status_marks_past_kickoff_as_locked():
    edge = _edge(_edge_row("Josh Allen", "QB", "BUF", "2026-09-14T17:00:00Z"))
    names = _names({0: "Josh Allen"})

    statuses = lineup_slot_status(names, edge, now=NOW)
    qb = statuses[0]
    assert qb.found is True
    assert qb.locked is True


def test_lineup_slot_status_marks_future_kickoff_as_open():
    edge = _edge(_edge_row("Patrick Mahomes", "QB", "KC", "2026-09-14T20:20:00Z"))
    names = _names({0: "Patrick Mahomes"})

    statuses = lineup_slot_status(names, edge, now=NOW)
    assert statuses[0].locked is False


def test_lineup_slot_status_blank_slot_is_not_found_and_unlocked_status_unknown():
    edge = _edge(_edge_row("Josh Allen", "QB", "BUF", "2026-09-14T17:00:00Z"))
    names = _names()  # nothing typed in

    statuses = lineup_slot_status(names, edge, now=NOW)
    assert statuses[0].found is False
    assert statuses[0].locked is None


def test_lineup_slot_status_unmatched_name_is_not_found():
    edge = _edge(_edge_row("Josh Allen", "QB", "BUF", "2026-09-14T17:00:00Z"))
    names = _names({0: "Some Typo Name"})

    statuses = lineup_slot_status(names, edge, now=NOW)
    assert statuses[0].found is False
    assert statuses[0].locked is None


def test_lineup_slot_status_missing_game_start_is_unknown_lock_status():
    edge = _edge(_edge_row("Josh Allen", "QB", "BUF", ""))
    names = _names({0: "Josh Allen"})

    statuses = lineup_slot_status(names, edge, now=NOW)
    assert statuses[0].found is True
    assert statuses[0].locked is None


def test_lineup_slot_status_wrong_length_raises():
    with pytest.raises(ValueError, match="expected"):
        lineup_slot_status(["only one name"], pd.DataFrame(), now=NOW)


def test_swap_candidates_excludes_locked_players():
    edge = _edge(
        _edge_row("Locked RB", "RB", "BUF", "2026-09-14T17:00:00Z", leverage=50.0),
        _edge_row("Open RB", "RB", "KC", "2026-09-14T20:20:00Z", leverage=20.0),
    )
    candidates = swap_candidates(edge, "RB", exclude_names=set(), now=NOW)
    assert list(candidates["Name"]) == ["Open RB"]


def test_swap_candidates_excludes_unknown_lock_status():
    edge = _edge(_edge_row("Mystery RB", "RB", "KC", ""))
    candidates = swap_candidates(edge, "RB", exclude_names=set(), now=NOW)
    assert candidates.empty


def test_swap_candidates_excludes_already_rostered_players():
    edge = _edge(
        _edge_row("Open RB One", "RB", "KC", "2026-09-14T20:20:00Z", leverage=20.0),
        _edge_row("Open RB Two", "RB", "SF", "2026-09-14T20:20:00Z", leverage=15.0),
    )
    candidates = swap_candidates(edge, "RB", exclude_names={"Open RB One"}, now=NOW)
    assert list(candidates["Name"]) == ["Open RB Two"]


def test_swap_candidates_flex_allows_rb_wr_te_only():
    edge = _edge(
        _edge_row("Flex RB", "RB", "KC", "2026-09-14T20:20:00Z", leverage=10.0),
        _edge_row("Flex WR", "WR", "SF", "2026-09-14T20:20:00Z", leverage=20.0),
        _edge_row("Flex TE", "TE", "DAL", "2026-09-14T20:20:00Z", leverage=5.0),
        _edge_row("Flex QB", "QB", "BUF", "2026-09-14T20:20:00Z", leverage=99.0),
        _edge_row("Flex DST", "DST", "NYJ", "2026-09-14T20:20:00Z", leverage=99.0),
    )
    candidates = swap_candidates(edge, "FLEX", exclude_names=set(), now=NOW)
    assert set(candidates["Name"]) == {"Flex RB", "Flex WR", "Flex TE"}


def test_swap_candidates_sorted_by_leverage_descending_and_capped_at_top():
    edge = _edge(
        *[_edge_row(f"RB {i}", "RB", "KC", "2026-09-14T20:20:00Z", leverage=float(i)) for i in range(10)]
    )
    candidates = swap_candidates(edge, "RB", exclude_names=set(), now=NOW, top=3)
    assert list(candidates["Name"]) == ["RB 9", "RB 8", "RB 7"]
