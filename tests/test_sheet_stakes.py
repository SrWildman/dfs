"""The Bankroll tab's STAKES block: the pure layout, the sheet inputs it reads, and the writer against a
recording fake (nothing real is touched)."""

import re
from contextlib import contextmanager

import pandas as pd
import pytest

from dfs import sheet_stakes as ss
from dfs import stakes
from dfs.config import BankrollConfig, Config, StakesConfig
from dfs.sheet_column_notes import STAKES_NOTES
from dfs.sheets import column_index
from dfs.week import BANKROLL_STARTING_CELL, BANKROLL_WEEKLY_BUDGET_CELL, BANKROLL_WEEKLY_COST_CELL

TAB = "Bankroll"


def typed_log(*rows):
    frame = pd.DataFrame(
        rows, columns=["season", "week", "format", "fee", "winnings", "place", "places_paid"]
    )
    for col in ("season", "week", "place", "places_paid"):
        frame[col] = frame[col].astype("Int64")
    return frame


LOG = typed_log(
    *[(2025, w, "Double Up", 10.0, 20.0 if w % 2 else 0.0, 3, 5) for w in range(1, 7)],
    *[(2025, w, "SE GPP", 20.0, 0.0, 30, 10) for w in range(1, 7)],
    (2026, 1, "Double Up", 10.0, 20.0, 1, 5),
    (2026, 1, "Freeroll", 0.0, 4.0, 1, 10),
)


def build(cfg=None, inputs=None, typed=LOG):
    inputs = inputs or ss.SheetInputs(budget=200.0, bankroll=1000.0, spent=None, largest_entry=None)
    return ss.compute(cfg or StakesConfig(), typed, inputs, 2026)


def texts(layout):
    return [str(c) for row in layout.rows for c in row if c != ""]


# ---- layout --------------------------------------------------------------------------------------


def test_every_row_is_the_block_width_and_the_last_column_follows_the_first():
    _, _, layout = build()
    assert all(len(r) == ss.COLUMN_COUNT for r in layout.rows)
    assert column_index(ss.STAKES_LAST_COLUMN) - column_index(ss.STAKES_FIRST_COLUMN) == ss.COLUMN_COUNT - 1
    assert layout.rows[0][0] == ss.STAKES_TITLE


def test_sections_come_in_order_and_the_table_leads_with_his_formats():
    _, _, layout = build()
    titles = [layout.rows[r - 1][0].split("  ")[0] for r in layout.section_rows]
    assert titles == ["YOUR NUMBERS BY FORMAT", "THIS WEEK'S PLAN", "HOW THE DEFAULT SPLIT IS WORKED OUT"]
    labels = [row[0] for row in layout.rows]
    assert labels.index("Double Up") < labels.index("SE GPP") < labels.index("Freeroll")
    assert labels.index("Cash") < labels.index("GPP") < labels.index("All formats")
    group = layout.rows[layout.group_rows[0] - 1]
    assert group[1] == "THIS SEASON (2026)" and group[5] == "ALL SEASONS (2025-2026)"


def test_missing_figures_show_a_dash_and_present_ones_are_numbers():
    _, _, layout = build()
    row = next(r for r in layout.rows if r[0] == "Freeroll")
    assert row[2] == ss.MISSING  # no fee paid: no season ROI
    assert row[4] == ss.MISSING  # one week: no standard deviation
    du = next(r for r in layout.rows if r[0] == "Double Up")
    assert isinstance(du[6], float) and isinstance(du[5], int)


def test_the_plan_leads_with_the_arithmetic_sentence_and_the_not_advice_note():
    _, plan, layout = build()
    assert stakes.plan_line(plan) in texts(layout)
    assert stakes.NOT_ADVICE in texts(layout)
    # the verdict row is styled as the verdict, the note rows as notes
    verdict = layout.rows[layout.verdict_rows[0] - 1][0]
    assert verdict.startswith("Your rules give: cash $")


def test_unset_rules_say_so_and_show_the_plan_with_them_off():
    _, plan, layout = build()
    flat = " ".join(texts(layout))
    assert "not set" in flat
    assert "Not set: max_pct_bankroll_per_week, max_entry_fee_pct." in flat
    assert "dfs bankroll stakes --propose" in flat
    assert layout.warning_rows == [] and plan.spend == 200.0


def test_set_rules_show_their_arithmetic_and_a_broken_rule_is_a_warning_row():
    cfg = StakesConfig(max_pct_bankroll_per_week=10, max_entry_fee_pct=2)
    _, plan, layout = build(cfg)
    flat = " ".join(texts(layout))
    assert "10% x $1,000.00 = $100.00" in flat and "2% x $1,000.00 = $20.00" in flat
    assert "min($200.00, $100.00): the weekly rule is lower" in flat
    assert layout.warning_rows
    assert layout.rows[layout.warning_rows[0] - 1][0].startswith("WARNING: Your weekly budget $200.00")


def test_the_split_table_shows_why_a_floored_bucket_gets_nothing():
    losing = typed_log(*[(2025, w, "SE GPP", 20.0, 0.0, 30, 10) for w in range(1, 4)])
    _, _, layout = build(typed=losing)
    row = next(r for r in layout.rows if r[0] == "GPP" and r[7])
    assert row[7].startswith("$0: ROI below the 0% floor")


def test_an_empty_log_still_builds_every_section():
    _, plan, layout = build(typed=typed_log())
    titles = [layout.rows[r - 1][0].split("  ")[0] for r in layout.section_rows]
    assert len(titles) == 3
    assert "All formats" in [r[0] for r in layout.rows]
    assert plan.amounts == {stakes.CASH: 0.0, stakes.GPP: 0.0}  # nothing to allocate on: no history


def test_every_header_in_the_block_has_a_hover_note():
    _, _, layout = build()
    headers = {str(c).strip() for row in layout.header_rows for c in layout.rows[row - 1] if str(c).strip()}
    assert not [h for h in headers if h not in STAKES_NOTES], "a header with no hover note"


def test_style_targets_are_rows_and_columns_inside_the_block():
    _, _, layout = build()
    cells = layout.percent_cells + layout.roi_cells + layout.money_cells + layout.count_cells
    assert all(1 <= r <= len(layout.rows) and 0 <= c < ss.COLUMN_COUNT for r, c in cells)
    for r, c in layout.roi_cells:  # an ROI cell holds a number or a dash, never text
        assert isinstance(layout.rows[r - 1][c], float | int) or layout.rows[r - 1][c] == ss.MISSING


# ---- a recording fake ----------------------------------------------------------------------------


class FakeClient:
    def __init__(self, ranges=None, unformatted=None):
        self.ranges = ranges or {}
        self.unformatted = unformatted or {}
        self.calls = []

    @contextmanager
    def batched(self):
        yield self

    def read_range(self, tab, a1):
        return self.ranges.get((tab, a1), [])

    def read_range_unformatted(self, tab, a1):
        return self.unformatted.get((tab, a1), [])

    def ensure_column_capacity(self, tab, n):
        self.calls.append(("columns", tab, n))

    def clear_ranges(self, tab, ranges):
        self.calls.append(("clear", tab, tuple(ranges)))

    def clear_notes(self, tab, a1):
        self.calls.append(("clear_notes", tab, a1))

    def update_range(self, tab, a1, rows):
        self.calls.append(("update", tab, a1, rows))

    def format_range(self, tab, a1, fmt):
        self.calls.append(("format", tab, a1))

    def set_column_widths(self, tab, widths):
        self.calls.append(("widths", tab, tuple(widths)))

    def set_row_notes(self, tab, row, notes, first_col=0):
        self.calls.append(("notes", tab, row, first_col))


def inside_the_block(a1):
    m = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", a1 if ":" in a1 else f"{a1}:{a1}")
    assert m, a1
    return (
        column_index(m.group(1)) >= column_index(ss.STAKES_FIRST_COLUMN)
        and column_index(m.group(3)) <= column_index(ss.STAKES_LAST_COLUMN)
        and int(m.group(4)) <= ss.STAKES_MAX_ROWS
    )


def test_writing_touches_only_the_blocks_own_columns_and_rows():
    _, _, layout = build()
    client = FakeClient()
    summary = ss.write_stakes(client, layout, TAB)
    assert "STAKES block written" in summary
    touched = [
        c[2] if c[0] != "clear" else c[2][0] for c in client.calls if c[0] in {"update", "format", "clear"}
    ]
    assert touched and all(inside_the_block(a1) for a1 in touched)
    widths = next(c for c in client.calls if c[0] == "widths")[2]
    assert set(widths) <= {chr(c) for c in range(ord("N"), ord("W"))}
    update = next(c for c in client.calls if c[0] == "update")
    assert update[2] == f"N1:V{len(layout.rows)}" and update[3] == layout.rows


def test_a_previous_stakes_block_is_replaced_wholesale():
    _, _, layout = build()
    old = [[ss.STAKES_TITLE, "stale"], ["old row"]]
    client = FakeClient(ranges={(TAB, f"N1:V{ss.STAKES_MAX_ROWS}"): old})
    ss.write_stakes(client, layout, TAB)
    cleared = next(c for c in client.calls if c[0] == "clear")
    assert cleared[2] == (f"N1:V{ss.STAKES_MAX_ROWS}",)  # the whole old extent goes before the new write
    assert client.calls.index(cleared) < client.calls.index(next(c for c in client.calls if c[0] == "update"))


def test_a_cell_typed_in_the_area_is_never_overwritten():
    _, _, layout = build()
    client = FakeClient(ranges={(TAB, f"N1:V{ss.STAKES_MAX_ROWS}"): [["my own note"]]})
    with pytest.raises(ss.StakesError, match="not a STAKES block"):
        ss.write_stakes(client, layout, TAB)
    assert not [c for c in client.calls if c[0] in {"update", "clear", "format"}]


def test_the_header_hover_notes_are_written_from_the_blocks_first_column():
    _, _, layout = build()
    client = FakeClient()
    ss.write_stakes(client, layout, TAB)
    notes = [c for c in client.calls if c[0] == "notes"]
    assert [n[2] for n in notes] == layout.header_rows
    assert all(n[3] == column_index(ss.STAKES_FIRST_COLUMN) for n in notes)


def test_has_block_reads_the_title_cell():
    assert ss.has_block(FakeClient(ranges={(TAB, "N1:N1"): [[ss.STAKES_TITLE]]}), TAB)
    assert not ss.has_block(FakeClient(), TAB)
    assert not ss.has_block(FakeClient(ranges={(TAB, "N1:N1"): [["Something else"]]}), TAB)


# ---- the sheet's own inputs ----------------------------------------------------------------------


def test_inputs_come_from_the_named_cells_and_the_ledgers_fee_columns_found_by_header():
    cfg = BankrollConfig.model_validate(
        {
            "tab": TAB,
            "cash": {"header_row": 40, "first_row": 41, "last_row": 44},
            "gpp": {"header_row": 60, "first_row": 61, "last_row": 64},
            "bets": {"header_row": 16, "first_row": 17, "last_row": 20},
        }
    )
    ledger_header = ["Entry", "Place", "Points", "Winnings", "Entries", "Entry Fee", "Prize Pool"]
    client = FakeClient(
        ranges={
            (TAB, "A40:40"): [ledger_header],
            (TAB, "A60:60"): [ledger_header],
            (TAB, "A16:16"): [["Name", "Odds %", "Odds", "Entered", "Won", "Net"]],
        },
        unformatted={
            (TAB, f"{BANKROLL_WEEKLY_BUDGET_CELL}:{BANKROLL_WEEKLY_BUDGET_CELL}"): [[200]],
            (TAB, f"{BANKROLL_STARTING_CELL}:{BANKROLL_STARTING_CELL}"): [["$1,234.50"]],
            (TAB, f"{BANKROLL_WEEKLY_COST_CELL}:{BANKROLL_WEEKLY_COST_CELL}"): [[57.0]],
            (TAB, "F41:F44"): [[5], [25], [], [""]],
            (TAB, "F61:F64"): [[10]],
            (TAB, "D17:D20"): [[30.0], [0]],
        },
    )
    got = ss.read_inputs(client, cfg)
    assert (got.budget, got.bankroll, got.spent) == (200.0, 1234.5, 57.0)
    assert got.largest_entry == 30.0  # the biggest of 25, 10 and the 30 bet


def test_blank_cells_and_a_missing_ledger_read_as_none():
    got = ss.read_inputs(FakeClient(), BankrollConfig(tab=TAB))
    assert got == ss.SheetInputs(None, None, None, None)


def test_refresh_block_reads_builds_and_writes_in_one_step(monkeypatch):
    monkeypatch.setattr(ss, "load_typed", lambda: LOG)
    client = FakeClient(
        unformatted={
            (TAB, f"{BANKROLL_WEEKLY_BUDGET_CELL}:{BANKROLL_WEEKLY_BUDGET_CELL}"): [[150]],
            (TAB, f"{BANKROLL_STARTING_CELL}:{BANKROLL_STARTING_CELL}"): [[500]],
        }
    )
    cfg = Config.model_validate(
        {
            "google_sheets": {"sheet_id": "x", "credentials_file": "c.json", "tab_mappings": {}},
            "stakes": {"max_pct_bankroll_per_week": 10},
        }
    )
    summary, plan = ss.refresh_block(client, cfg, 2026)
    assert "STAKES block written" in summary
    assert plan.spend == pytest.approx(50.0)  # 10% of $500 is under the $150 budget
    assert plan.warnings


# ---- the command ---------------------------------------------------------------------------------


class CommandClient(FakeClient):
    def __init__(self, *_args, **_kw):
        super().__init__(
            unformatted={
                (TAB, f"{BANKROLL_WEEKLY_BUDGET_CELL}:{BANKROLL_WEEKLY_BUDGET_CELL}"): [[200]],
                (TAB, f"{BANKROLL_STARTING_CELL}:{BANKROLL_STARTING_CELL}"): [[1000]],
            }
        )
        CommandClient.last = self

    def describe(self):
        return ("Week X", "https://example.test/sheet")


@pytest.fixture
def stakes_cli(monkeypatch):
    from typer.testing import CliRunner

    from dfs import cli

    cfg = Config.model_validate(
        {"google_sheets": {"sheet_id": "x", "credentials_file": "c.json", "tab_mappings": {}}}
    )
    monkeypatch.setattr(cli, "_load_config_or_exit", lambda: cfg)
    monkeypatch.setattr(cli, "SheetsClient", CommandClient)
    monkeypatch.setattr(ss, "load_typed", lambda: LOG)
    return lambda *args: CliRunner().invoke(cli.app, ["bankroll", "stakes", *args])


def test_the_command_prints_the_plan_and_writes_the_block(stakes_cli):
    result = stakes_cli()
    assert result.exit_code == 0, result.output
    assert "Your rules give: cash $" in result.output
    assert "not financial advice" in result.output
    assert any(c[0] == "update" for c in CommandClient.last.calls)


def test_propose_prints_the_arithmetic_and_writes_nothing(stakes_cli):
    result = stakes_cli("--propose")
    assert result.exit_code == 0, result.output
    assert "Nothing was written" in result.output
    assert "weeks with entries" in result.output or "Not enough history" in result.output
    assert not [c for c in CommandClient.last.calls if c[0] in {"update", "clear", "format"}]


def test_no_sheet_prints_the_plan_and_writes_nothing(stakes_cli):
    result = stakes_cli("--no-sheet")
    assert result.exit_code == 0, result.output
    assert "Your rules give" in result.output
    assert not [c for c in CommandClient.last.calls if c[0] in {"update", "clear", "format"}]


def test_week_new_never_fails_over_the_stakes_block(monkeypatch, capsys):
    from dfs import cli

    def boom(*_a, **_kw):
        raise ss.StakesError("something is in the way")

    monkeypatch.setattr(ss, "refresh_block", boom)
    cli._refresh_stakes_block(None, None)  # must not raise
    out = capsys.readouterr().out
    assert "STAKES block not rebuilt" in out and "dfs bankroll stakes" in out


def test_week_new_prints_the_block_summary_and_its_warnings(monkeypatch, capsys):
    from dfs import cli

    stat = stakes.Stat(300, 1.0, 0.1, 0.1, 0.5, 5.0, 8)
    plan = stakes.make_plan(
        StakesConfig(max_pct_bankroll_per_week=10),
        budget=200.0,
        bankroll=500.0,
        stats={stakes.CASH: stat, stakes.GPP: stat},
    )
    monkeypatch.setattr(ss, "refresh_block", lambda *_a, **_kw: ("Bankroll: STAKES block written", plan))
    cli._refresh_stakes_block(None, None)
    out = capsys.readouterr().out
    assert "STAKES block written" in out and "WARNING" in out


def test_no_cell_of_the_block_is_cut_off_at_the_widths_it_is_written_with():
    """`dfs setup audit-style` reads Bankroll for clipping: the block must fit `write_stakes`' widths."""
    from dfs.sheet_clipping import find_clipped

    cfg = StakesConfig(max_pct_bankroll_per_week=10, max_entry_fee_pct=2, split={"cash": 40, "gpp": 60})
    _, _, layout = build(cfg)

    def shown(value):
        if isinstance(value, float):
            return "-99.9%"
        return "9,999" if isinstance(value, int) else str(value)

    rows = [[shown(c) for c in row] for row in layout.rows]
    widths = [ss.FIRST_COLUMN_PX] + [ss.OTHER_COLUMN_PX] * (ss.COLUMN_COUNT - 1)
    bold = frozenset(layout.title_rows + layout.header_rows + layout.section_rows + layout.total_rows)
    assert find_clipped(rows, widths, set(), bold_rows=bold) == []
