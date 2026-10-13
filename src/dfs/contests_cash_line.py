"""Results' `Cash Line`, filled from the standings files when Sam has not typed it.

The cash line is the score of the last paid place of "the" double-up: the one contest series whose cash line
Sam types into Results each week. His typed values match the GIANT $25 Double Up's standings (106.0 against
105.98 in Week 4, 153.1 against 153.06 in Week 5), so that is the default series (`[contests]
cash_line_series` in config.toml). The rule is the same as for every other typed Results column: **a value
Sam typed is never overwritten.** A blank cell is filled; a typed one is kept and, when the standings
disagree by more than `TOLERANCE`, both numbers are shown so he can decide.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from dfs.config import CASH_LINE_SERIES_DEFAULT, ResultsConfig

DEFAULT_SERIES = CASH_LINE_SERIES_DEFAULT
TOLERANCE = 0.5  # points: typed and standings agree to the display rounding of the file
WEEK_HEADER, CASH_LINE_HEADER = "Week", "Cash Line"


@dataclass
class CashLineResult:
    filled: dict[int, float] = field(default_factory=dict)
    agreed: dict[int, float] = field(default_factory=dict)  # typed value matches the standings
    disagreed: dict[int, tuple[float, float]] = field(default_factory=dict)  # week -> (typed, standings)
    no_row: list[int] = field(default_factory=list)  # weeks with standings but no Results row yet

    def lines(self) -> list[str]:
        out = [
            f"  Week {w}: Cash Line {v:g} filled from the standings" for w, v in sorted(self.filled.items())
        ]
        out += [
            f"  Week {w}: typed Cash Line {v:g} matches the standings" for w, v in sorted(self.agreed.items())
        ]
        out += [
            f"  Week {w}: typed Cash Line {typed:g} kept; the standings say {found:g}"
            for w, (typed, found) in sorted(self.disagreed.items())
        ]
        out += [f"  Week {w}: no Results row to fill" for w in self.no_row]
        return out


def standings_cash_lines(
    profiles: pd.DataFrame, season: int, series: str = DEFAULT_SERIES
) -> dict[int, float]:
    """`{week: cash line}` of the given series in `season`, from `profiles.csv` (complete profiles with a cash
    line only). Two standings of one series in a week (an overlay run) take the larger field's."""
    if profiles is None or profiles.empty:
        return {}
    sub = profiles[(profiles["series"] == series) & (profiles["season"] == season)]
    sub = sub[sub["cash_line"].notna() & sub["week"].notna()]
    if "complete" in sub.columns:
        sub = sub[sub["complete"].astype(str).str.lower() == "true"]
    out: dict[int, float] = {}
    for week, group in sub.sort_values("entrants").groupby("week"):
        out[int(week)] = round(float(group["cash_line"].iloc[-1]), 2)
    return out


def _letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def fill_cash_lines(client, results: ResultsConfig, lines: dict[int, float]) -> CashLineResult:
    """Writes each cash line into a BLANK Results `Cash Line` cell (found by header); reports the rest."""
    result = CashLineResult()
    if not lines:
        return result
    rows = client.read_range_unformatted(results.tab, f"A{results.header_row}:Z{results.last_row}")
    header = [str(c).strip() for c in (rows[0] if rows else [])]
    if WEEK_HEADER not in header or CASH_LINE_HEADER not in header:
        raise ValueError(f"{results.tab} has no {WEEK_HEADER!r} / {CASH_LINE_HEADER!r} header to find")
    week_col, line_col = header.index(WEEK_HEADER), header.index(CASH_LINE_HEADER)
    row_of: dict[int, tuple[int, object]] = {}
    for offset, row in enumerate(rows[1:]):
        cell = row[week_col] if week_col < len(row) else ""
        if isinstance(cell, float) and cell.is_integer():
            cell = int(cell)
        if str(cell).strip().isdigit():
            current = row[line_col] if line_col < len(row) else ""
            row_of[int(str(cell).strip())] = (results.header_row + 1 + offset, current)
    updates: dict[str, list[list]] = {}
    for week, found in sorted(lines.items()):
        if week not in row_of:
            result.no_row.append(week)
            continue
        number, current = row_of[week]
        typed = current if isinstance(current, (int, float)) and not isinstance(current, bool) else None
        if typed is None and str(current).strip():
            try:
                typed = float(str(current).replace(",", ""))
            except ValueError:
                typed = None
        if typed is None or (isinstance(typed, float) and math.isnan(typed)):
            if str(current).strip():
                result.disagreed[week] = (math.nan, found)  # text we cannot read: leave it, show the number
                continue
            updates[f"{_letter(line_col)}{number}"] = [[found]]
            result.filled[week] = found
        elif abs(float(typed) - found) <= TOLERANCE:
            result.agreed[week] = float(typed)
        else:
            result.disagreed[week] = (float(typed), found)
    if updates:
        client.update_ranges(results.tab, updates)
    return result
