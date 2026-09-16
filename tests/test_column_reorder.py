import pytest

from dfs.column_reorder import compute_column_moves, group_into_contiguous_runs


def _apply(order: list[str], moves: list[tuple[int, int]]) -> list[str]:
    order = list(order)
    for from_index, to_index in moves:
        name = order.pop(from_index)
        order.insert(to_index, name)
    return order


def test_no_moves_when_already_in_order():
    order = ["A", "B", "C"]
    assert compute_column_moves(order, order) == []


def test_single_swap():
    moves = compute_column_moves(["A", "B"], ["B", "A"])
    assert _apply(["A", "B"], moves) == ["B", "A"]


def test_move_last_to_first():
    current = ["A", "B", "C", "D"]
    target = ["D", "A", "B", "C"]
    moves = compute_column_moves(current, target)
    assert _apply(current, moves) == target


def test_move_first_to_last():
    current = ["A", "B", "C", "D"]
    target = ["B", "C", "D", "A"]
    moves = compute_column_moves(current, target)
    assert _apply(current, moves) == target


def test_full_reversal():
    current = ["A", "B", "C", "D", "E"]
    target = list(reversed(current))
    moves = compute_column_moves(current, target)
    assert _apply(current, moves) == target


def test_interleaved_reorder_matches_phase_3_shape():
    # Small stand-in for the real Phase 3 reorder: native and linked
    # fields starting in one arrangement, ending up interleaved by zone.
    current = ["Name", "Pos", "Team", "Salary", "Pts", "Val", "Ceil", "CeilVal", "Leverage", "Flag"]
    target = ["Name", "Pos", "Team", "Salary", "Pts", "Val", "Ceil", "CeilVal", "Leverage", "Flag"]
    # Shuffle "current" so it's genuinely out of order relative to target.
    current = ["Val", "Name", "CeilVal", "Pos", "Flag", "Team", "Ceil", "Salary", "Leverage", "Pts"]
    moves = compute_column_moves(current, target)
    assert _apply(current, moves) == target


def test_already_in_place_name_generates_no_move_for_itself():
    # The algorithm isn't move-count-optimal (it doesn't need to be --
    # moveDimension calls are cheap and correctness matters far more than
    # count), but a name that's ALREADY at its final target position when
    # its turn comes up must not generate a no-op move for itself.
    current = ["A", "X", "Y"]
    target = ["A", "Y", "X"]
    moves = compute_column_moves(current, target)
    assert (0, 0) not in moves  # "A" never needs to move
    assert _apply(current, moves) == target


def test_raises_on_mismatched_membership():
    with pytest.raises(ValueError, match="only in current"):
        compute_column_moves(["A", "B", "C"], ["A", "B", "D"])


def test_raises_on_different_lengths():
    with pytest.raises(ValueError):
        compute_column_moves(["A", "B"], ["A", "B", "C"])


def test_group_into_contiguous_runs_merges_adjacent_names():
    columns = {"A": 0, "B": 1, "C": 2, "D": 5}
    runs = group_into_contiguous_runs(["A", "B", "C", "D"], columns)
    assert runs == [["A", "B", "C"], ["D"]]


def test_group_into_contiguous_runs_ignores_a_name_not_in_the_group():
    # Column 1 sits between A and C, but it belongs to some OTHER column
    # entirely (not passed in `names`) -- A and C must not merge into one
    # run just because their indices are numerically adjacent.
    columns = {"A": 0, "Other": 1, "C": 2}
    runs = group_into_contiguous_runs(["A", "C"], columns)
    assert runs == [["A"], ["C"]]


def test_group_into_contiguous_runs_orders_runs_by_column_position():
    columns = {"Z": 10, "A": 0}
    runs = group_into_contiguous_runs(["Z", "A"], columns)
    assert runs == [["A"], ["Z"]]


def test_large_random_permutation_round_trips():
    import random

    names = [f"col{i}" for i in range(30)]
    current = names.copy()
    target = names.copy()
    rng = random.Random(42)
    rng.shuffle(current)
    rng.shuffle(target)
    moves = compute_column_moves(current, target)
    assert _apply(current, moves) == target
