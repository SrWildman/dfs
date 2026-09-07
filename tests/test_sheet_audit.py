from dfs.sheet_audit import AUDITED_TABS, TabAudit, audit_tab, run_audit
from dfs.sheet_style import AVAIL_CHIPS, FLAG_CHIPS, HEADER_FMT

_HEADER_BG = HEADER_FMT["backgroundColor"]
_OTHER_BG = {"red": 1, "green": 1, "blue": 1}


class FakeAuditClient:
    """Implements exactly the SheetsClient surface `audit_tab` calls,
    fully scriptable per test rather than going through gspread's
    response shapes -- those are covered by a live run against the real
    API (see CONTRIBUTING.md's changelog), not worth re-modeling here."""

    def __init__(
        self,
        *,
        present: bool = True,
        header: list[str] | None = None,
        header_row: int = 1,
        header_styled: bool = True,
        styled_columns: int | None = None,
        frozen: int = 1,
        widths: dict[str, int] | None = None,
        hidden_cols: set[str] | None = None,
        general_fields: set[str] | None = None,
        chip_columns: set[str] | None = None,
    ):
        self._present = present
        self._header = header if header is not None else ["Name", "DK Sal", "Pts", "Flag", "Avail"]
        self._header_row = header_row
        self._header_styled = header_styled
        self._styled_columns = len(self._header) if styled_columns is None else styled_columns
        self._frozen = frozen
        self._widths = widths or {}
        self._hidden_cols = hidden_cols or set()
        self._general_fields = general_fields or set()
        self._chip_columns = {"Flag", "Avail"} if chip_columns is None else chip_columns

    def tab_exists(self, tab_name: str) -> bool:
        return self._present

    def read_range(self, tab_name: str, a1_range: str):
        return [self._header] if self._header else []

    def get_cell_formats(self, tab_name: str, a1_range: str):
        # e.g. "A1:E1" -> row 1, "A2:E2" -> row 2 -- real gspread A1
        # parsing isn't the point of this fake, just telling the header
        # request apart from the data-row request `audit_tab` makes.
        start, end = a1_range.split(":")
        row_num = int(start.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
        col_count = ord(end[0]) - ord("A") + 1
        if row_num == self._header_row:
            cells = []
            for i in range(col_count):
                styled = self._header_styled and i < self._styled_columns
                cells.append({"backgroundColor": _HEADER_BG if styled else _OTHER_BG})
            return [cells]
        return [
            [
                {} if name in self._general_fields else {"numberFormat": {"type": "NUMBER", "pattern": "0.0"}}
                for name in self._header[:col_count]
            ]
        ]

    def get_column_widths(self, tab_name: str, last_col_a1: str):
        from dfs.sheets import column_letter

        out = []
        for i in range(len(self._header)):
            letter = column_letter(i)
            entry: dict = {"pixelSize": self._widths.get(letter, 100)}
            if letter in self._hidden_cols:
                entry["hiddenByUser"] = True
            out.append(entry)
        return out

    def frozen_rows(self, tab_name: str) -> int:
        return self._frozen

    def has_chip_rule(self, tab_name: str, column_a1: str, values: list[str]) -> bool:
        name = self._header[ord(column_a1) - ord("A")] if len(column_a1) == 1 else None
        return name in self._chip_columns


def test_audit_tab_reports_not_present():
    audit = audit_tab(FakeAuditClient(present=False), "Ghost", header_row=1)
    assert audit.present is False
    assert audit.clean is False


def test_audit_tab_clean_when_everything_matches():
    client = FakeAuditClient(
        header=["Name", "DK Sal", "Pts", "Flag", "Avail"],
        widths={"A": 165, "B": 78, "C": 62, "D": 96, "E": 60},
    )
    audit = audit_tab(client, "EdgeRaw", header_row=1)
    assert audit.clean, audit.issues


def test_audit_tab_flags_unstyled_header():
    client = FakeAuditClient(header_styled=False, widths={"A": 165, "B": 78, "C": 62, "D": 96, "E": 60})
    audit = audit_tab(client, "T", header_row=1)
    assert any("dark fill" in i for i in audit.issues)


def test_audit_tab_uses_header_style_width_override_for_exposure():
    # Exposure's real table header is only 7 columns (A-G); H-J are a
    # spacer plus a deliberately muted "Slots filled" readout, never
    # meant to carry the dark fill. Only the first 7 must be checked.
    header = [
        "Name",
        "Pos",
        "Salary",
        "# Lineups",
        "Exposure",
        "Target",
        "vs Target",
        "",
        "Slots filled",
        "10/9",
    ]
    client = FakeAuditClient(header=header, styled_columns=7, widths={c: 100 for c in "ABCDEFGHIJ"})
    audit = audit_tab(client, "Exposure", header_row=1)
    assert not any("dark fill" in i for i in audit.issues)

    # A tab with the SAME shape but no override still gets the full check.
    client_generic = FakeAuditClient(header=header, styled_columns=7, widths={c: 100 for c in "ABCDEFGHIJ"})
    audit_generic = audit_tab(client_generic, "SomeOtherTab", header_row=1)
    assert any("dark fill" in i for i in audit_generic.issues)


def test_audit_tab_flags_missing_freeze():
    client = FakeAuditClient(frozen=0, widths={"A": 165, "B": 78, "C": 62, "D": 96, "E": 60})
    audit = audit_tab(client, "T", header_row=1)
    assert any("freeze pane" in i for i in audit.issues)


def test_audit_tab_uses_freeze_override_for_lineups():
    # Lineups' pool deck freezes exactly DECK_ROWS (10), not through its
    # real header at row 11 -- the generic "frozen >= header_row" check
    # would false-positive there by design, not by a real gap.
    client = FakeAuditClient(header_row=11, frozen=10, widths={"A": 165, "B": 78, "C": 62, "D": 96, "E": 60})
    audit = audit_tab(client, "Lineups", header_row=11)
    assert not any("freeze pane" in i for i in audit.issues)

    client_short = FakeAuditClient(
        header_row=11, frozen=9, widths={"A": 165, "B": 78, "C": 62, "D": 96, "E": 60}
    )
    audit_short = audit_tab(client_short, "Lineups", header_row=11)
    assert any("freeze pane" in i for i in audit_short.issues)


def test_audit_tab_flags_default_width_columns_but_not_hidden_ones():
    # A column left at the default 100px is a gap; one that's hidden on
    # purpose (e.g. EdgeRaw's Id) is not -- nobody sets a width for a
    # column nobody sees.
    client = FakeAuditClient(widths={"A": 165}, hidden_cols={"B"})
    audit = audit_tab(client, "T", header_row=1)
    width_issue = next(i for i in audit.issues if "no explicit width" in i)
    assert "B" not in width_issue
    assert "C" in width_issue and "D" in width_issue and "E" in width_issue


def test_audit_tab_flags_general_number_format_on_a_field_formats_column():
    client = FakeAuditClient(
        header=["Name", "DK Sal", "Pts"],
        widths={"A": 165, "B": 78, "C": 62},
        general_fields={"DK Sal"},
    )
    audit = audit_tab(client, "T", header_row=1)
    assert any("DK Sal" in i and "General" in i for i in audit.issues)
    # Name isn't a FIELD_FORMATS key -- never flagged even though it has
    # no number format either.
    assert not any("Name" in i for i in audit.issues)


def test_audit_tab_flags_flag_and_avail_columns_with_no_chip_rule():
    client = FakeAuditClient(
        header=["Name", "Flag", "Avail"],
        widths={"A": 165, "B": 96, "C": 60},
        chip_columns=set(),
    )
    audit = audit_tab(client, "T", header_row=1)
    assert any("Flag column" in i for i in audit.issues)
    assert any("Avail column" in i for i in audit.issues)


def test_flag_and_avail_chip_sets_are_not_empty():
    # Sanity check the audit is actually asking about real chip values,
    # not an accidentally-empty list that would make has_chip_rule's
    # `any(v in values ...)` vacuously true or false regardless of input.
    assert FLAG_CHIPS
    assert AVAIL_CHIPS


def test_run_audit_covers_every_registered_tab():
    client = FakeAuditClient(widths={"A": 165, "B": 78, "C": 62, "D": 96, "E": 60})
    results = run_audit(client)
    assert [r.tab for r in results] == [tab for tab, _ in AUDITED_TABS]
    assert all(isinstance(r, TabAudit) for r in results)
