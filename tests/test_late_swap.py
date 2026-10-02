from datetime import UTC, datetime

import pandas as pd
import pytest

from dfs.late_swap import lineup_slot_status, swap_candidates
from dfs.models import ROSTER_SLOTS

# 18:00 UTC = 2:00 pm ET: the Sunday 1 pm ET games are underway. NOTE: TFFB's `GameStart` is Eastern
# wall-clock time labelled "Z" (`...T13:00:00Z` is the 1 pm ET game), so a 1 pm kickoff is written 13:00.
NOW = datetime(2026, 9, 14, 18, 0, tzinfo=UTC)


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
    edge = _edge(_edge_row("Josh Allen", "QB", "BUF", "2026-09-14T13:00:00Z"))
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
    edge = _edge(_edge_row("Josh Allen", "QB", "BUF", "2026-09-14T13:00:00Z"))
    names = _names()  # nothing typed in

    statuses = lineup_slot_status(names, edge, now=NOW)
    assert statuses[0].found is False
    assert statuses[0].locked is None


def test_lineup_slot_status_unmatched_name_is_not_found():
    edge = _edge(_edge_row("Josh Allen", "QB", "BUF", "2026-09-14T13:00:00Z"))
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
        _edge_row("Locked RB", "RB", "BUF", "2026-09-14T13:00:00Z", leverage=50.0),
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


# ---------------------------------------------------------------------------------------------
# TFFB's GameStart is Eastern wall-clock time labelled "Z". Read as UTC, a player was marked locked up to
# four hours before his real kickoff (five after the November clock change) and dropped from the swap
# candidates for the same window -- exactly the late-morning stretch the command is run in.
# ---------------------------------------------------------------------------------------------


def _at(month, day, hour, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


def _qb_status(game_start, now):
    edge = _edge(_edge_row("Quarterback", "QB", "AAA", game_start))
    return lineup_slot_status(_names({0: "Quarterback"}), edge, now=now)[0].locked


def test_a_1pm_et_player_is_open_until_1pm_et_not_9am():
    one_pm = "2026-09-14T13:00:00Z"  # written 13:00; really 1 pm ET = 17:00 UTC (EDT)
    assert _qb_status(one_pm, _at(9, 14, 13, 30)) is False  # 9:30 am ET: bug said locked
    assert _qb_status(one_pm, _at(9, 14, 16, 59)) is False  # 12:59 pm ET
    assert _qb_status(one_pm, _at(9, 14, 17, 0)) is True  # 1:00 pm ET: locks exactly at kickoff
    assert _qb_status(one_pm, _at(9, 14, 17, 1)) is True


def test_the_late_afternoon_window_stays_swappable_until_its_own_kickoff():
    late = "2026-09-14T16:25:00Z"  # 4:25 pm ET = 20:25 UTC (EDT)
    assert _qb_status(late, _at(9, 14, 17, 30)) is False  # 1:30 pm ET: bug said locked
    assert _qb_status(late, _at(9, 14, 20, 24)) is False
    assert _qb_status(late, _at(9, 14, 20, 25)) is True


def test_after_the_november_clock_change_the_offset_is_five_hours():
    one_pm = "2026-12-06T13:00:00Z"  # 1 pm EST = 18:00 UTC
    assert _qb_status(one_pm, _at(12, 6, 17, 30)) is False  # 12:30 pm EST
    assert _qb_status(one_pm, _at(12, 6, 18, 0)) is True


def test_swap_candidates_include_a_player_whose_game_starts_within_the_next_four_hours():
    now = _at(9, 14, 17, 30)  # 1:30 pm ET: the 1 pm games are on, the 4:25 pm game is not
    edge = _edge(
        _edge_row("On Field RB", "RB", "AAA", "2026-09-14T13:00:00Z", leverage=50),
        _edge_row("Late RB", "RB", "BBB", "2026-09-14T16:25:00Z", leverage=20),
        _edge_row("Night RB", "RB", "CCC", "2026-09-14T20:20:00Z", leverage=10),
    )
    got = swap_candidates(edge, "RB", set(), now=now)
    assert list(got["Name"]) == ["Late RB", "Night RB"]  # the 4:25 pm player used to vanish from this list


def test_a_blank_or_garbage_game_start_is_still_not_eligible_and_status_unknown():
    assert _qb_status("", NOW) is None
    assert _qb_status("not a time", NOW) is None
    edge = _edge(_edge_row("No Time", "RB", "AAA", ""))
    assert swap_candidates(edge, "RB", set(), now=NOW).empty
