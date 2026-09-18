"""`dfs setup audit-style` -- a read-only reporter that checks the workbook
actually looks the way `sheet_style.py` claims, rather than trusting any
styling command's own "OK" output. Task 2.7's own mandate: number formats
and chips already drifted apart once (Task 2.1's `FIELD_FORMATS`
consolidation) purely because nothing was checking; this exists so that
can't happen silently a second time.

Per tab: the header row carries the shared dark fill, a freeze pane is
set, every column has an explicit pixel width (not Sheets' own 100px
default), every column whose header text is a `FIELD_FORMATS` key isn't
left on "Automatic" number format, and a `Flag`/`Avail` column found in
the header has at least one matching chip rule. Never writes anything.

Scoped to tabs with one flat header row over data rows below it -- every
"visible, daily-ish" tab fits that shape except `Board` (three side-by-
side panels, no single header row) and `Bankroll` (a KPI block plus two
separately-headered ledgers), which are structurally bespoke and are
skipped here with their own note rather than forced through a model that
doesn't fit them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dfs.sheet_style import AVAIL_CHIPS, FIELD_FORMATS, FLAG_CHIPS, HEADER_FMT
from dfs.sheets import SheetsClient, column_letter
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS, PLAYER_POOL_HEADER_ROW

# Sheets reports this for any column that was never explicitly widened --
# see SheetsClient.get_column_widths' own docstring.
_DEFAULT_COLUMN_PX = 100

# Rough floor for the pixel width a bold header label needs to render in
# full at Sheets' default zoom -- Phase 6, Part 1.5: EDGE_WIDTHS set a
# pixel width per column but nothing checked it against the actual
# rendered header text, so ten headers truncated live ("Posi", "ProjPt",
# "Ceilinc", "CeilPc", "ProjO", "Leverag", "OverU", "Spreac", "GameEn",
# "OppPosRar") with nothing to catch it.
#
# Calibrated deliberately LOW, not to reproduce Sheets' exact per-glyph
# rendering (impossible from a character count alone -- e.g. "CeilVal" at
# 68px renders fine live while "Ceiling"/"CeilPct" at 64px, also 7
# characters, both clip: the real boundary is per-glyph, not per-count)
# but so every column ALREADY known to render fine live (Avail 60px/5ch,
# Team 54px/4ch, Opp 54px/3ch, Val 58px/3ch, Salary 78px/6ch, CeilVal
# 68px/7ch, LevBasis 74px/8ch, Roof 76px/4ch, Wind 68px/4ch, OwnPct
# 68px/6ch) stays comfortably above this floor. A false negative (real
# truncation this floor is too generous to catch) is possible at that
# same fine margin; a false positive (flagging a column that's actually
# fine) is not, by construction against the data above -- and either way
# this catches a column shrunk well below where it needs to be, which is
# the actual regression this check exists to catch.
_HEADER_PX_PER_CHAR = 6.5
_HEADER_PX_PADDING = 18


def _min_header_width_px(text: str) -> int:
    return int(_HEADER_PX_PADDING + _HEADER_PX_PER_CHAR * len(text))


_HEADER_BG = HEADER_FMT["backgroundColor"]

# (tab, header_row) for every tab this audit's single-header-row model
# fits. Board and Bankroll are deliberately absent -- see module docstring.
AUDITED_TABS: list[tuple[str, int]] = [
    ("EdgeRaw", 1),
    ("PlayerPoolRaw", 1),
    # A3: real header moved to row 2 -- row 1 is the add-a-player control.
    ("Player Pool", PLAYER_POOL_HEADER_ROW),
    # Sat at row 11 while the pool deck occupied the rows above it
    # (2026-09-06 through 2026-09-16); derived, not hardcoded, so this
    # self-corrects if the header ever moves again -- a literal `11` here
    # once shipped anyway and silently mis-audited Lineups the moment the
    # deck was removed, caught only by re-reading this list, not by any
    # test (nothing here was exercised against the real constant).
    ("Lineups", LINEUPS_NAME_BLOCKS[0][0] - 1),
    ("Slate Grid", 1),
    ("Exposure", 1),
    ("Movement", 3),
    ("Results", 1),
    ("Scratch", 1),
    ("DK Upload", 1),
    ("DKLineupsFinal", 1),
    ("SoSQB", 1),
    ("SoSRB", 1),
    ("SoSWr", 1),
    ("SoSTE", 1),
    ("SoSDef", 1),
    ("SoSComb", 1),
]

# A tab whose frozen rows deliberately don't equal its header row, so the
# generic "frozen >= header_row" check would false-positive. Empty since
# the pool deck (Lineups' own former reason for one) was removed entirely
# -- Phase 5, 2026-09-16, see sheet_pool_deck.py's module docstring.
# Kept as a mechanism in case a future tab needs it again.
FREEZE_OVERRIDES: dict[str, int] = {}

# A tab whose real table header is narrower than its full header ROW.
# Exposure's row 1 has 7 real column headers (A-G) plus a spacer and a
# small-text "Slots filled" readout (H-J, deliberately muted, never dark
# -- see `style_exposure`) that reads as a supplementary label, not an
# 8th/9th/10th table column. Checking the dark fill across the full row
# would flag that intentional design choice as a defect. Width/
# FIELD_FORMATS/chip checks are unaffected -- they only ever look at
# columns whose header text actually matches something, so a label like
# "Slots filled" never triggers them regardless of this override.
HEADER_STYLE_WIDTH_OVERRIDES: dict[str, int] = {"Exposure": 7}

SKIPPED_TABS = [
    "Board (three side-by-side panels, no single header row)",
    "Bankroll (KPI block + two ledgers)",
]


@dataclass
class TabAudit:
    tab: str
    issues: list[str] = field(default_factory=list)
    present: bool = True

    @property
    def clean(self) -> bool:
        return self.present and not self.issues


def _bg_matches(fmt: dict, expected: dict) -> bool:
    bg = fmt.get("backgroundColor", {})
    return all(abs(bg.get(k, 0) - expected.get(k, 0)) < 1e-6 for k in ("red", "green", "blue"))


def audit_tab(client: SheetsClient, tab: str, *, header_row: int) -> TabAudit:
    audit = TabAudit(tab=tab)
    if not client.tab_exists(tab):
        audit.present = False
        return audit

    header_rows = client.read_range(tab, f"A{header_row}:{header_row}")
    header = header_rows[0] if header_rows else []
    if not header:
        audit.issues.append(f"empty header row {header_row}")
        return audit

    last_col = column_letter(len(header) - 1)

    styled_width = HEADER_STYLE_WIDTH_OVERRIDES.get(tab, len(header))
    styled_last_col = column_letter(styled_width - 1)
    header_fmt = client.get_cell_formats(tab, f"A{header_row}:{styled_last_col}{header_row}")
    header_cells = header_fmt[0] if header_fmt else []
    if not header_cells or not all(_bg_matches(c, _HEADER_BG) for c in header_cells):
        audit.issues.append("header row missing the shared dark fill on at least one cell")

    frozen_rows = client.frozen_rows(tab)
    min_freeze = FREEZE_OVERRIDES.get(tab, header_row)
    if frozen_rows < min_freeze:
        audit.issues.append(f"freeze pane too short (frozen {frozen_rows}, need >= {min_freeze})")

    widths = client.get_column_widths(tab, last_col)
    no_width = [
        column_letter(i)
        for i, w in enumerate(widths)
        if w.get("pixelSize", _DEFAULT_COLUMN_PX) == _DEFAULT_COLUMN_PX and not w.get("hiddenByUser")
    ]
    if no_width:
        audit.issues.append(f"no explicit width: {', '.join(no_width)}")

    too_narrow = []
    for i, name in enumerate(header):
        if not name or (i < len(widths) and widths[i].get("hiddenByUser")):
            continue
        pixel_size = widths[i].get("pixelSize", _DEFAULT_COLUMN_PX) if i < len(widths) else _DEFAULT_COLUMN_PX
        needed = _min_header_width_px(name)
        if pixel_size < needed:
            too_narrow.append(f"{column_letter(i)} ({name!r}: {pixel_size}px < ~{needed}px)")
    if too_narrow:
        audit.issues.append(f"header text likely truncated: {', '.join(too_narrow)}")

    data_row = header_row + 1
    data_fmt = client.get_cell_formats(tab, f"A{data_row}:{last_col}{data_row}")
    data_cells = data_fmt[0] if data_fmt else []
    general_fields = []
    for i, name in enumerate(header):
        if name not in FIELD_FORMATS:
            continue
        cell = data_cells[i] if i < len(data_cells) else {}
        if "numberFormat" not in cell:
            general_fields.append(name)
    if general_fields:
        audit.issues.append(f"General number format despite FIELD_FORMATS entry: {', '.join(general_fields)}")

    if "Flags" in header:
        letter = column_letter(header.index("Flags"))
        if not client.has_chip_rule(tab, letter, list(FLAG_CHIPS)):
            audit.issues.append(f"Flags column ({letter}) has no matching chip rule")
    if "Avail" in header:
        letter = column_letter(header.index("Avail"))
        if not client.has_chip_rule(tab, letter, list(AVAIL_CHIPS)):
            audit.issues.append(f"Avail column ({letter}) has no matching chip rule")

    return audit


def run_audit(client: SheetsClient) -> list[TabAudit]:
    return [audit_tab(client, tab, header_row=header_row) for tab, header_row in AUDITED_TABS]
