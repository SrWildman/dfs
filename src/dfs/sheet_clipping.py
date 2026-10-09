"""Clipping: a visible cell whose text is wider than its column and cannot spill into the next one.

Sheets shows a long text in full only when the cell next to it is empty (it overflows) or the cell wraps; a
number never overflows (it turns into ``###`` or is cut), and a text with a filled neighbour is cut at the
column edge. `dfs setup audit-style` runs `audit_clipping` on every tab to find those cells, headers
included, from the real values and the real column widths, so a column that was fine last week cannot quietly
start cutting names off this week.

The width estimate is deliberately on the low side (`DETECT_PX_PER_CHAR`): a false alarm costs a look, but
the check is only worth having if what it flags is really cut. The fit used to set widths
(`FIT_PX_PER_CHAR`) is a little more generous, so a column fitted by `sheet_widths` never trips the check.
Pure functions at the top, the sheet reading below.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from dfs.sheets import SheetsClient, column_letter

DETECT_PX_PER_CHAR = 6.2  # at 10 pt; scaled by the font size
FIT_PX_PER_CHAR = 7.0
DETECT_PADDING_PX = 10
FIT_PADDING_PX = 16
BOLD_FACTOR = 1.08
DEFAULT_FONT_PT = 10
_NUMBER = re.compile(r"^[-+−$]?\s*[\d,]*\.?\d+\s*(%|x|pts)?$|^[-+]?\d[\d,.]*\s*[a-zA-Z%]{0,3}$")
SAMPLE_ROWS = 600  # how far down a tab the check reads
DEFAULT_MAX_PX = 300  # the widest a fitted column grows; past that it is a layout question


def estimate_px(
    text: str, *, per_char: float, padding: int, font_pt: float = DEFAULT_FONT_PT, bold: bool = False
) -> int:
    """About how wide `text` renders: characters x pixels per character (scaled to the font) + padding. A
    multi-line cell is as wide as its widest line."""
    longest = max((len(line) for line in str(text).split("\n")), default=0)
    width = longest * per_char * font_pt / DEFAULT_FONT_PT * (BOLD_FACTOR if bold else 1.0)
    return math.ceil(width) + padding


def is_number(text: str) -> bool:
    """A value that shows as a number: it never overflows, it is cut."""
    return bool(_NUMBER.match(text.strip()))


@dataclass(frozen=True)
class Clip:
    column: int  # 0-based
    row: int  # 1-based
    text: str
    needed: int
    width: int

    @property
    def letter(self) -> str:
        return column_letter(self.column)


def find_clipped(
    rows: list[list[str]],
    widths: list[int],
    hidden: set[int],
    *,
    font_pt: float = DEFAULT_FONT_PT,
    bold_rows: frozenset[int] = frozenset(),
    skip_columns: frozenset[int] = frozenset(),
) -> list[Clip]:
    """Every cell (1-based `row`) in `rows` that is wider than its column and cannot overflow.

    A text overflows when the next visible cell in its row is empty; a number never does. Hidden columns and
    `skip_columns` are not checked and do not block (an empty hidden cell is skipped, a filled one is not
    assumed to block either: it is not on screen)."""
    clips = []
    visible = [i for i in range(len(widths)) if i not in hidden]
    for r, row in enumerate(rows, start=1):
        for pos, col in enumerate(visible):
            if col in skip_columns or col >= len(row):
                continue
            text = str(row[col])
            if not text.strip():
                continue
            needed = estimate_px(
                text,
                per_char=DETECT_PX_PER_CHAR,
                padding=DETECT_PADDING_PX,
                font_pt=font_pt,
                bold=r in bold_rows,
            )
            if needed <= widths[col]:
                continue
            if not is_number(text):
                next_filled = False
                for after in visible[pos + 1 :]:
                    if after < len(row) and str(row[after]).strip():
                        next_filled = True
                    break
                if not next_filled:
                    continue
            clips.append(Clip(col, r, text, needed, widths[col]))
    return clips


def summarize(clips: list[Clip], header_row_text: dict[int, str] | None = None, limit: int = 6) -> str:
    """One line per finding: `K ('Why': 3 cells, e.g. row 12 "…" needs ~340px, has 320px)`, widest first."""
    by_column: dict[int, list[Clip]] = {}
    for clip in clips:
        by_column.setdefault(clip.column, []).append(clip)
    parts = []
    for column, group in sorted(by_column.items(), key=lambda kv: -max(c.needed - c.width for c in kv[1])):
        worst = max(group, key=lambda c: c.needed - c.width)
        label = (header_row_text or {}).get(column, "")
        sample = worst.text if len(worst.text) <= 28 else worst.text[:27] + "…"
        parts.append(
            f"{column_letter(column)}{f' ({label!r})' if label else ''}: {len(group)} cell(s), e.g. row "
            f"{worst.row} {sample!r} needs ~{worst.needed}px, has {worst.width}px"
        )
    extra = f" (+{len(parts) - limit} more column(s))" if len(parts) > limit else ""
    return "; ".join(parts[:limit]) + extra


def fitted_widths(
    rows: list[list[str]],
    widths: list[int],
    hidden: set[int],
    *,
    font_pt: float = DEFAULT_FONT_PT,
    bold_rows: frozenset[int] = frozenset(),
    skip_columns: frozenset[int] = frozenset(),
    maxima: dict[int, int] | None = None,
    default_max: int = DEFAULT_MAX_PX,
    clips: list[Clip] | None = None,
) -> dict[int, int]:
    """New pixel widths (0-based column -> px) for the columns that `find_clipped` flags: wide enough for
    their longest cut cell at the fitting estimate (a little more generous than the detecting one, so a fitted
    column no longer trips the check), never past that column's maximum (`maxima`, else `default_max`) and
    never narrower than it already is. A column that is long free text stays flagged at its maximum: that one
    is a layout question (let it overflow, or wrap it), not a width."""
    flagged = (
        clips
        if clips is not None
        else find_clipped(
            rows, widths, hidden, font_pt=font_pt, bold_rows=bold_rows, skip_columns=skip_columns
        )
    )
    out: dict[int, int] = {}
    for clip in flagged:
        need = estimate_px(
            clip.text,
            per_char=FIT_PX_PER_CHAR,
            padding=FIT_PADDING_PX,
            font_pt=font_pt,
            bold=clip.row in bold_rows,
        )
        cap = (maxima or {}).get(clip.column, default_max)
        target = min(need, max(cap, widths[clip.column]))
        if target > widths[clip.column]:
            out[clip.column] = max(out.get(clip.column, 0), target)
    return out


# ---------------------------------------------------------------------------------------------
# The sheet side
# ---------------------------------------------------------------------------------------------


def audit_clipping(
    client: SheetsClient,
    tab: str,
    *,
    header_row: int | None = None,
    font_pt: float = DEFAULT_FONT_PT,
    skip_columns: frozenset[int] = frozenset(),
) -> list[str]:
    """Findings (strings) for one tab: a read of its values and column widths, nothing written. A column
    whose flagged cell is formatted to wrap is not clipped and is dropped."""
    if not client.tab_exists(tab):
        return []
    n_rows = min(client.row_count(tab), SAMPLE_ROWS)
    grid = client.read_range(tab, f"A1:{column_letter(_width_of(client, tab) - 1)}{n_rows}")
    if not grid:
        return []
    last_col = max(len(r) for r in grid)
    metadata = client.get_column_widths(tab, column_letter(last_col - 1))
    widths = [int(m.get("pixelSize", 100)) for m in metadata][:last_col]
    widths += [100] * (last_col - len(widths))
    hidden = {i for i, m in enumerate(metadata[:last_col]) if m.get("hiddenByUser")}
    bold = frozenset({header_row}) if header_row else frozenset()
    clips = find_clipped(grid, widths, hidden, font_pt=font_pt, bold_rows=bold, skip_columns=skip_columns)
    clips = _drop_wrapped(client, tab, clips)
    if not clips:
        return []
    labels = {}
    if header_row and header_row <= len(grid):
        labels = {i: str(v) for i, v in enumerate(grid[header_row - 1]) if v}
    return [summarize(clips, labels)]


def _width_of(client: SheetsClient, tab: str) -> int:
    return max(client.column_count(tab), 1)


def _drop_wrapped(client: SheetsClient, tab: str, clips: list[Clip]) -> list[Clip]:
    """Drop a column's findings when its worst cell is set to wrap (it grows taller instead of being cut)."""
    wrapped: set[int] = set()
    seen: set[int] = set()
    for clip in clips:
        if clip.column in seen:
            continue
        seen.add(clip.column)
        formats = client.get_cell_formats(tab, f"{clip.letter}{clip.row}:{clip.letter}{clip.row}")
        cell = formats[0][0] if formats and formats[0] else {}
        if cell.get("wrapStrategy") == "WRAP":
            wrapped.add(clip.column)
    return [c for c in clips if c.column not in wrapped]


def fit_tab_widths(
    client: SheetsClient,
    tab: str,
    *,
    header_row: int | None = None,
    font_pt: float = DEFAULT_FONT_PT,
    maxima: dict[str, int] | None = None,
    skip_columns: frozenset[int] = frozenset(),
) -> dict[str, int]:
    """Widen every column of `tab` whose cells are cut off to fit them (`fitted_widths`), from a read of the
    real values and widths; wrapped columns are left alone. Returns the new widths by column letter (empty
    when nothing was cut). Never narrows a column."""
    if not client.tab_exists(tab):
        return {}
    n_rows = min(client.row_count(tab), SAMPLE_ROWS)
    grid = client.read_range(tab, f"A1:{column_letter(_width_of(client, tab) - 1)}{n_rows}")
    if not grid:
        return {}
    last_col = max(len(r) for r in grid)
    metadata = client.get_column_widths(tab, column_letter(last_col - 1))
    widths = [int(m.get("pixelSize", 100)) for m in metadata][:last_col]
    widths += [100] * (last_col - len(widths))
    hidden = {i for i, m in enumerate(metadata[:last_col]) if m.get("hiddenByUser")}
    bold = frozenset({header_row}) if header_row else frozenset()
    clips = find_clipped(grid, widths, hidden, font_pt=font_pt, bold_rows=bold, skip_columns=skip_columns)
    clips = _drop_wrapped(client, tab, clips)
    caps = {_index(letter): px for letter, px in (maxima or {}).items()}
    fitted = fitted_widths(grid, widths, hidden, font_pt=font_pt, bold_rows=bold, maxima=caps, clips=clips)
    result = {column_letter(i): px for i, px in fitted.items()}
    if result:
        client.set_column_widths(tab, result)
    return result


def _index(letter: str) -> int:
    n = 0
    for ch in letter:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1
