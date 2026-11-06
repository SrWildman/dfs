"""Plan N item 17: the Betting ledger's Odds % becomes a true percent column (`repair_odds_column`), against a
fake sheet: values converted to what they showed, the format set, the formulas rewritten, and never twice."""

from __future__ import annotations

from dfs.sheet_bankroll_view import (
    LEGACY_ODDS_PCT_PATTERN,
    ODDS_TYPE_HINT,
    compute_weekly_betting_stats,
    odds_cells_are_percent,
    odds_formula,
    plan_odds_repair,
    repair_odds_column,
)

TAB = "Bankroll"


class FakeBankroll:
    """B17:B19 typed like Week 9 (46, 55, 0.7) under the legacy literal-% format; rows 20-36 empty."""

    def __init__(
        self, values: dict[int, float], pattern: str = LEGACY_ODDS_PCT_PATTERN, kind: str = "NUMBER"
    ):
        self.values = dict(values)
        self.fmt = {r: {"numberFormat": {"type": kind, "pattern": pattern}} for r in range(17, 37)}
        self.formulas: dict[str, str] = {}
        self.formats_set: list[tuple[str, dict]] = []

    @staticmethod
    def _rows(a1: str) -> tuple[int, int]:
        a, _, b = a1.partition(":")
        first = int(a[1:])
        return first, int(b[1:]) if b else first

    def get_cell_formats(self, tab, a1):
        first, last = self._rows(a1)
        return [[self.fmt[r]] for r in range(first, last + 1)]

    def read_range_unformatted(self, tab, a1):
        first, last = self._rows(a1)
        return [[self.values[r]] if r in self.values else [] for r in range(first, last + 1)]

    def update_ranges(self, tab, updates):
        for a1, rows in updates.items():
            self.values[int(a1[1:])] = rows[0][0]

    def update_range(self, tab, a1, rows):
        first, _ = self._rows(a1)
        for i, row in enumerate(rows):
            self.formulas[f"{a1[0]}{first + i}"] = row[0]

    def format_range(self, tab, a1, fmt):
        self.formats_set.append((a1, fmt))
        if a1.startswith("B"):
            first, last = self._rows(a1)
            for r in range(first, last + 1):
                self.fmt[r] = fmt


def test_week9_rows_are_converted_to_what_they_showed():
    sheet = FakeBankroll({17: 46, 18: 55, 19: 0.7})
    plan = repair_odds_column(sheet, TAB, 17, 36)
    assert plan.format_changed
    assert plan.values == [(17, 46.0, 0.46), (18, 55.0, 0.55), (19, 0.7, 0.007)]
    assert sheet.values == {17: 0.46, 18: 0.55, 19: 0.007}
    assert sheet.fmt[19]["numberFormat"] == {"type": "PERCENT", "pattern": "0.0%"}
    assert sheet.formulas["C19"] == odds_formula(19)
    assert odds_cells_are_percent(sheet, TAB, 17) is True


def test_a_second_run_never_divides_again():
    sheet = FakeBankroll({17: 46, 18: 55, 19: 0.7})
    repair_odds_column(sheet, TAB, 17, 36)
    again = repair_odds_column(sheet, TAB, 17, 36)
    assert not again.format_changed and again.values == []
    assert sheet.values == {17: 0.46, 18: 0.55, 19: 0.007}


def test_the_template_gets_the_format_and_formulas_only():
    sheet = FakeBankroll({})
    plan = repair_odds_column(sheet, TAB, 17, 36)
    assert plan.values == [] and plan.formulas == 20
    assert len([k for k in sheet.formulas if k.startswith("C")]) == 20


def test_dry_run_writes_nothing():
    sheet = FakeBankroll({19: 0.7})
    plan = plan_odds_repair(sheet, TAB, 17, 36)
    assert plan.values == [(19, 0.7, 0.007)]
    assert sheet.values == {19: 0.7} and not sheet.formulas and not sheet.formats_set
    assert any("+#,##0" in line for line in plan.lines(TAB))


def test_expected_wins_reads_a_repaired_column_as_probabilities():
    rows = [(0.007, 1.0, 0.0), (0.46, 4.8, 10.0), (53.3, 1.0, 0.0)]  # a stray 53.3 is not a probability
    stats = compute_weekly_betting_stats(rows, percent_cells=True)
    assert round(stats.expected_wins, 3) == 0.467
    legacy = compute_weekly_betting_stats([(46, 4.8, 10.0)], percent_cells=False)
    assert round(legacy.expected_wins, 2) == 0.46  # an unrepaired closed week reads as before


def test_the_hint_is_what_a_bad_cell_shows():
    assert ODDS_TYPE_HINT in odds_formula(17)
