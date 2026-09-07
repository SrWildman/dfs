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

# Sheets reports this for any column that was never explicitly widened --
# see SheetsClient.get_column_widths' own docstring.
_DEFAULT_COLUMN_PX = 100

_HEADER_BG = HEADER_FMT["backgroundColor"]

# (tab, header_row) for every tab this audit's single-header-row model
# fits. Board and Bankroll are deliberately absent -- see module docstring.
AUDITED_TABS: list[tuple[str, int]] = [
    ("EdgeRaw", 1),
    ("PlayerPoolRaw", 1),
    ("Player Pool", 1),
    ("Lineups", 11),
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
    ("Pool Picks", 2),  # row 1 is Fix 3.1's plain-text title, not a header
]

# A tab whose frozen rows deliberately don't equal its header row, so the
# generic "frozen >= header_row" check would false-positive. Lineups
# freezes exactly DECK_ROWS (10) -- the pool deck sits above the real
# header at row 11, and freezing through the header itself would freeze
# into the first lineup block too (see sheet_pool_deck.py's own docstring
# on why `polish_builder_tab`'s Lineups call passes `freeze_rows=DECK_ROWS`
# instead of the default). Value is the minimum acceptable frozen-row count.
FREEZE_OVERRIDES: dict[str, int] = {"Lineups": 10}

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

    if "Flag" in header:
        letter = column_letter(header.index("Flag"))
        if not client.has_chip_rule(tab, letter, list(FLAG_CHIPS)):
            audit.issues.append(f"Flag column ({letter}) has no matching chip rule")
    if "Avail" in header:
        letter = column_letter(header.index("Avail"))
        if not client.has_chip_rule(tab, letter, list(AVAIL_CHIPS)):
            audit.issues.append(f"Avail column ({letter}) has no matching chip rule")

    return audit


def run_audit(client: SheetsClient) -> list[TabAudit]:
    return [audit_tab(client, tab, header_row=header_row) for tab, header_row in AUDITED_TABS]
