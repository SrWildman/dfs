"""Every slate is a ledger of its own (`dfs.slate_chain`; Sam, Nov 23): a test season of Week 11, a Week 12
Thanksgiving slate and Week 12 main, on in-memory sheets driven through the real commands.

- every entry is booked once, in exactly one ledger;
- the Season rows read 11, 12 Thu, 12, in that order, and the totals and Ending chain through them;
- the starting bankroll chains whether Week 12 main was set up before or after Thanksgiving closed;
- `dfs bankroll carry` fixes a stale start.
"""

from __future__ import annotations

import re
from contextlib import contextmanager

import pandas as pd
import pytest

from dfs import cli, slate, slate_chain
from dfs.bankroll import parse_contest_history
from dfs.config import Config, for_slate
from dfs.sheet_season_view import FIRST_ROW, build_season_grid, chart_data_grid
from dfs.sheets import column_index, column_letter

SEASON = 2026
HEADER = (
    "Sport,Game_Type,Entry_Key,Entry,Contest_Key,Contest_Date_EST,Place,Points,Winnings_Non_Ticket,"
    "Winnings_Ticket,Contest_Entries,Entry_Fee,Prize_Pool,Places_Paid"
)
ROWS = [
    # Week 11 (Sunday Nov 22): booked on Week 11's sheet long ago
    "NFL,Classic,E6,NFL $5 Double Up,6101,2026-11-22 13:00:00,20,131.0,$10.00,$0.00,100,$5.00,$450.00,50",
    # Thanksgiving (Thursday Nov 26): a double-up (won) and a GPP (lost), Classic
    "NFL,Classic,E1,NFL $5 Double Up,7001,2026-11-26 12:30:00,10,120.5,$10.00,$0.00,100,$5.00,$450.00,50",
    "NFL,Classic,E2,NFL $3 Thanksgiving Special,7002,2026-11-26 12:30:00,900,110.0,$0.00,$0.00,5000,$3.00,"
    '"$13,000.00",1000',
    # Thanksgiving night's Showdown: not Classic, so the main slate's week (as `slate.contest_slate` says)
    "NFL,Showdown Captain Mode,E3,NFL Showdown $5,7003,2026-11-26 20:20:00,40,60.0,$0.00,$0.00,100,$5.00,"
    "$450.00,20",
    # the Sunday main slate: a double-up (lost) and an H2H (won)
    "NFL,Classic,E4,NFL $5 Double Up,7101,2026-11-29 13:00:00,60,100.25,$0.00,$0.00,100,$5.00,$450.00,50",
    "NFL,Classic,E5,NFL $10 Head-to-Head vs. someone,7102,2026-11-29 13:00:00,1,140.25,$18.00,$0.00,2,$10.00,"
    "$18.00,1",
]

RESULTS_HEADER = (
    "Week",
    "Cash Pts",
    "Cash Line",
    "Cash Results",
    "H2H Entered",
    "H2H Win",
    "H2H %",
    "Red",
    "Blue",
    "Black",
)
CASH = {"header_row": 38, "first_row": 39, "last_row": 83}
GPP = {"header_row": 85, "first_row": 86, "last_row": 149}
_REF = re.compile(r"(\$?)([A-Z]+)(\$?)(\d+)")


# -- an in-memory sheet ------------------------------------------------------------------------------------


class Sheet:
    """One spreadsheet: A1 cells per tab. A row insert moves cells and shifts every row reference at or
    below it, in every formula, as Sheets does (a range that spans the insert grows; one above it does not).
    The Bankroll summary (row 12/13 cost and net, B2 Ending) is worked out from the ledgers on every read, as
    the real tab's formulas do."""

    def __init__(self, title: str):
        self.title = title
        self.cells: dict[str, dict[str, object]] = {"Bankroll": {}, "Season": {}, "Results": {}}
        self.inserted: list[tuple[str, int]] = []
        grid = build_season_grid()
        for r, row in enumerate(grid, start=1):
            for c, v in enumerate(row):
                if v != "":
                    self.cells["Season"][f"{column_letter(c)}{r}"] = v
        for r, row in enumerate(chart_data_grid(), start=1):
            for c, v in enumerate(row):
                self.cells["Season"][f"{column_letter(column_index('T') + c)}{r}"] = v
        for c, name in zip("RS", ("Other Net", "Other Risked"), strict=True):
            self.cells["Season"][f"{c}1"] = name
        # Results as the template has it: weeks 1-18 in rows 2-19, one blank formula row, the averages at 23
        res = self.cells["Results"]
        for c, name in zip("ABCDEFGHIJ", RESULTS_HEADER, strict=True):
            res[f"{c}1"] = name
        for r in range(2, 21):
            if r <= 19:
                res[f"A{r}"] = r - 1
            res[f"D{r}"] = f'=IF(B{r}, B{r}>=C{r}, "")'
            res[f"G{r}"] = f'=IF(E{r}="","",IFERROR(F{r}/E{r},""))'
        res["B23"] = '=IF(COUNT(B2:B20)=0,"",AVERAGE(B2:B20))'
        res["E23"] = "=SUM(E2:E22)"

    # SheetsClient
    def describe(self):
        return self.title, f"https://example/{self.title}"

    def set_title(self, title):
        self.title = title

    @staticmethod
    def _bounds(a1):
        m = re.fullmatch(r"([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?", a1)
        c1, r1 = column_index(m.group(1)), int(m.group(2))
        c2, r2 = (column_index(m.group(3)), int(m.group(4))) if m.group(3) else (c1, r1)
        return c1, r1, c2, r2

    def _recalc(self):
        b = self.cells["Bankroll"]

        def ledger(t):
            rows = range(t["first_row"], t["last_row"] + 1)
            fee = sum(float(b.get(f"F{r}") or 0) for r in rows)
            won = sum(float(b.get(f"D{r}") or 0) for r in rows)
            return fee, won - fee

        (cf, cn), (gf, gn) = ledger(CASH), ledger(GPP)
        b.update({"D12": cf, "H12": cn, "D13": gf, "H13": gn})
        start = b.get("B1")
        b["B2"] = round(float(start) + cn + gn, 2) if isinstance(start, (int, float)) else ""

    def _read(self, tab, a1):
        self._recalc()
        c1, r1, c2, r2 = self._bounds(a1)
        cells = self.cells.setdefault(tab, {})
        rows = [
            [cells.get(f"{column_letter(c)}{r}", "") for c in range(c1, c2 + 1)] for r in range(r1, r2 + 1)
        ]
        while rows and not any(v != "" for v in rows[-1]):
            rows.pop()  # as the API does: trailing empty rows are left out
        return [r[: max((i + 1 for i, v in enumerate(r) if v != ""), default=0)] for r in rows]

    def read_range(self, tab, a1):
        return [[str(v) for v in row] for row in self._read(tab, a1)]

    read_formula = read_range

    def read_range_unformatted(self, tab, a1):
        return self._read(tab, a1)

    def update_range(self, tab, a1, rows):
        c1, r1, _, _ = self._bounds(a1)
        for i, row in enumerate(rows):
            for j, v in enumerate(row):
                key = f"{column_letter(c1 + j)}{r1 + i}"
                if isinstance(v, str) and re.fullmatch(r"-?\d+(\.\d+)?", v.strip()):
                    v = float(v) if "." in v else int(v)  # USER_ENTERED: a number typed as text
                if v == "" or v is None:
                    self.cells.setdefault(tab, {}).pop(key, None)
                else:
                    self.cells.setdefault(tab, {})[key] = v

    def update_ranges(self, tab, updates):
        for a1, rows in updates.items():
            self.update_range(tab, a1, rows)

    def insert_rows(self, tab, *, at_row, count):
        self.inserted.append((tab, at_row))

        def shift(m):
            row = int(m.group(4))
            return f"{m.group(1)}{m.group(2)}{m.group(3)}{row + count if row >= at_row else row}"

        moved = {}
        for key, v in self.cells.get(tab, {}).items():
            col, row = re.fullmatch(r"([A-Z]+)(\d+)", key).groups()
            if isinstance(v, str) and v.startswith("="):
                v = _REF.sub(shift, v)
            moved[f"{col}{int(row) + count if int(row) >= at_row else row}"] = v
        self.cells[tab] = moved

    def format_range(self, *a, **k):
        pass

    def hide_columns(self, *a, **k):
        pass

    def delete_charts(self, *a, **k):
        pass

    def add_line_chart(self, *a, **k):
        pass

    def results_labels(self):
        out, r = [], 2
        while str(self.cells["Results"].get(f"A{r}", "")).strip():
            out.append(str(self.cells["Results"][f"A{r}"]))
            r += 1
        return out

    # a tiny evaluator for the Season tab's SUM / + formulas
    def value(self, tab, cell):
        v = self.cells.get(tab, {}).get(cell, "")
        if not (isinstance(v, str) and v.startswith("=")):
            return float(v) if isinstance(v, (int, float)) else 0.0
        expr = v[1:].replace("$", "")

        def total(m):
            c1, r1, c2, r2 = self._bounds(m.group(1))
            return str(
                sum(
                    self.value(tab, f"{column_letter(c)}{r}")
                    for c in range(c1, c2 + 1)
                    for r in range(r1, r2 + 1)
                )
            )

        expr = re.sub(r"SUM\(([A-Z]+\d+:[A-Z]+\d+)\)", total, expr)
        expr = re.sub(r"([A-Z]+\d+)", lambda m: str(self.value(tab, m.group(1))), expr)
        return float(eval(expr))  # noqa: S307 - a test's own arithmetic

    def labels(self):
        out = []
        for r in range(FIRST_ROW, FIRST_ROW + 40):
            v = self.cells["Season"].get(f"A{r}", "")
            if slate_chain.parse_label(v) is None:
                break
            out.append(str(v))
        return out

    def season_row(self, label):
        for r in range(FIRST_ROW, FIRST_ROW + 40):
            if str(self.cells["Season"].get(f"A{r}", "")) == label:
                return r
        raise KeyError(label)

    def ledger_keys(self):
        b = self.cells["Bankroll"]
        return sorted(
            str(b[f"L{r}"])
            for t in (CASH, GPP)
            for r in range(t["first_row"], t["last_row"] + 1)
            if b.get(f"L{r}")
        )


# -- the season ----------------------------------------------------------------------------------------------


@pytest.fixture
def season(tmp_path, monkeypatch):
    """Week 11's sheet, closed: $100 start, its double-up won $5, Ending $105, Season row 11 written."""
    sheets: dict[str, Sheet] = {
        "W11": Sheet("Week 11"),
        "THU": Sheet("Copy of Template"),
        "W12": Sheet("Week 12"),
    }
    w11 = sheets["W11"]
    w11.cells["Bankroll"].update(
        {"B1": 100.0, "A39": "NFL $5 Double Up", "D39": 10.0, "F39": 5.0, "L39": "E6"}
    )
    for col, v in {"B": 5.0, "C": 0.0, "F": 105.0, "G": 5.0, "H": 0.0}.items():
        w11.cells["Season"][f"{col}{w11.season_row('11')}"] = v

    class Factory:
        def __call__(self, gs):
            return sheets[gs.sheet_id]

    monkeypatch.setattr(cli, "SheetsClient", Factory())
    monkeypatch.setattr(cli, "_refresh_stakes_block", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_keep_history_export", lambda *a, **k: None)
    monkeypatch.setattr(cli.nfl_calendar, "current_season", lambda *a, **k: SEASON)
    csv = tmp_path / "draftkings-contest-entry-history.csv"
    csv.write_text(HEADER + "\n" + "\n".join(ROWS) + "\n")
    return sheets, csv


def _cfg(main: str, previous: str | None, thu: str | None) -> Config:
    raw = {
        "google_sheets": {
            "sheet_id": main,
            "previous_sheet_id": previous,
            "credentials_file": "c.json",
            "tab_mappings": {},
        },
        "bankroll": {"cash": CASH, "gpp": GPP},
    }
    if thu:
        raw["slates"] = {"thu": {"sheet_id": thu}}
    return Config.model_validate(raw)


@contextmanager
def _on_thu(monkeypatch, base: Config):
    from dfs import config

    monkeypatch.setattr(config, "load_base_config", lambda: base)
    with slate.active("thu"):
        yield for_slate(base, "thu")


def _set_up_thanksgiving(monkeypatch, sheets, base):
    """`dfs week new --slate thu`'s carry-forward (`_carry_into_slate_sheet`): bankroll, Results, Season."""
    thu = sheets["THU"]
    thu.title = "Week 12 Thanksgiving"
    with _on_thu(monkeypatch, base) as cfg:
        chain = cli._slate_chain(cfg, always=True)[1]
        at = slate_chain.position(12, "thu")
        prev = slate_chain.previous(chain, at)
        cli._carry_into_slate_sheet(cfg, thu, prev, prev, chain, at)


def _close(monkeypatch, cfg_main: Config, sheet_key: str, csv, *, thu: bool):
    if thu:
        with _on_thu(monkeypatch, cfg_main) as cfg:
            cli._carry_before_close(cfg, None)
            cli._sync_bankroll_from_csv(cfg, csv, close=True)
    else:
        cli._carry_before_close(cfg_main, None)
        cli._sync_bankroll_from_csv(cfg_main, csv, close=True)


def _assert_week_12(sheets, w12_start_expected=110.0):
    thu, w12 = sheets["THU"], sheets["W12"]
    # every entry booked once, in exactly one ledger
    assert thu.ledger_keys() == ["E1", "E2"]
    assert w12.ledger_keys() == ["E3", "E4", "E5"]
    assert sheets["W11"].ledger_keys() == ["E6"]
    all_keys = thu.ledger_keys() + w12.ledger_keys() + sheets["W11"].ledger_keys()
    assert sorted(all_keys) == sorted(e.entry_key for e in _history_entries())
    # Thanksgiving: start 105 (Week 11's Ending), +5 -3 = 107
    assert thu.cells["Bankroll"]["B1"] == 105.0
    thu._recalc()
    assert thu.cells["Bankroll"]["B2"] == 107.0
    # Week 12 main starts at Thanksgiving's Ending: 107, then -5 (Showdown) -5 (double-up) +8 (H2H) = 105
    assert w12.cells["Bankroll"]["B1"] == w12_start_expected
    # the Season rows on Week 12 main: 11, 12 Thu, 12 in that order, the Ending chaining through them
    assert w12.labels()[10:13] == ["11", "12 Thu", "12"]
    assert w12.labels()[-1] == "18" and len(w12.labels()) == 19
    rows = {lab: w12.season_row(lab) for lab in ("11", "12 Thu", "12")}
    assert w12.value("Season", f"F{rows['11']}") == 105.0
    assert w12.value("Season", f"F{rows['12 Thu']}") == 107.0
    assert w12.value("Season", f"F{rows['12']}") == 105.0
    assert (w12.value("Season", f"B{rows['12 Thu']}"), w12.value("Season", f"C{rows['12 Thu']}")) == (
        5.0,
        -3.0,
    )
    assert (w12.value("Season", f"B{rows['12']}"), w12.value("Season", f"C{rows['12']}")) == (3.0, -5.0)
    # totals: the cumulative and the Year to date count all three: +5, +2, -2 = +5 = 105 - 100
    assert w12.value("Season", f"Q{rows['12']}") == 5.0
    ytd = next(r for r in range(20, 40) if w12.cells["Season"].get(f"A{r}") == "Total")
    assert w12.value("Season", f"B{ytd}") == 5.0
    assert w12.value("Season", f"C{ytd}") == 33.0  # risked: Week 11 5, Thanksgiving 8, Week 12 20
    # Results: Thanksgiving's own row on both sheets, from its own entries; Week 12's from the main slate's
    for sheet in (thu, w12):
        labels = sheet.results_labels()
        assert labels[10:13] == ["11", "12 Thu", "12"] and labels[-1] == "18" and len(labels) == 19
        r = labels.index("12 Thu") + 2
        assert sheet.cells["Results"][f"B{r}"] == 120.5  # E1, the Thanksgiving double-up
        assert sheet.cells["Results"][f"D{r}"] == f'=IF(B{r}, B{r}>=C{r}, "")'  # its own formulas
        assert sheet.cells["Results"][f"G{r}"] == f'=IF(E{r}="","",IFERROR(F{r}/E{r},""))'
    labels = w12.results_labels()
    r12 = labels.index("12") + 2
    assert w12.cells["Results"][f"B{r12}"] == 100.25  # E4, the main slate's double-up (E1 is not in it)
    assert (w12.cells["Results"][f"E{r12}"], w12.cells["Results"][f"F{r12}"]) == (1, 1)  # E5's H2H
    assert w12.cells["Results"]["B24"] == '=IF(COUNT(B2:B21)=0,"",AVERAGE(B2:B21))'  # the range grew


def _history_entries():
    import io

    return parse_contest_history(pd.read_csv(io.StringIO(HEADER + "\n" + "\n".join(ROWS) + "\n")))


def test_week_12_main_set_up_after_thanksgiving_closed(season, monkeypatch, tmp_path):
    sheets, csv = season
    # Tuesday: Thanksgiving set up from Week 11 (Week 12 main not yet)
    base = _cfg("W11", None, "THU")
    _set_up_thanksgiving(monkeypatch, sheets, base)
    assert sheets["THU"].labels()[10:13] == ["11", "12 Thu", "12"]
    # Friday: Thanksgiving closed
    _close(monkeypatch, base, "THU", csv, thu=True)
    # Week 12 main set up: its bankroll and Season come from Thanksgiving's sheet
    _week_new_main(monkeypatch, tmp_path, base, sheets, "W12")
    assert sheets["W12"].cells["Bankroll"]["B1"] == 107.0
    # Tuesday: Week 12 main closed
    _close(monkeypatch, _cfg("W12", "W11", "THU"), "W12", csv, thu=False)
    _assert_week_12(sheets, 107.0)


def test_week_12_main_set_up_before_thanksgiving_closed(season, monkeypatch, tmp_path):
    sheets, csv = season
    base = _cfg("W11", None, "THU")
    _set_up_thanksgiving(monkeypatch, sheets, base)
    # Week 12 main set up before Thanksgiving is played: it starts at Thanksgiving's start, 105
    _week_new_main(monkeypatch, tmp_path, base, sheets, "W12")
    assert sheets["W12"].cells["Bankroll"]["B1"] == 105.0
    after = _cfg("W12", "W11", "THU")
    _close(monkeypatch, after, "THU", csv, thu=True)
    # the Week 12 main close re-reads Thanksgiving's Ending first, on its own
    _close(monkeypatch, after, "W12", csv, thu=False)
    _assert_week_12(sheets, 107.0)


def test_bankroll_carry_fixes_a_stale_start(season, monkeypatch, tmp_path):
    sheets, csv = season
    base = _cfg("W11", None, "THU")
    _set_up_thanksgiving(monkeypatch, sheets, base)
    _week_new_main(monkeypatch, tmp_path, base, sheets, "W12")
    after = _cfg("W12", "W11", "THU")
    _close(monkeypatch, after, "THU", csv, thu=True)
    assert sheets["W12"].cells["Bankroll"]["B1"] == 105.0  # stale: Thanksgiving has closed at 107
    monkeypatch.setattr(cli, "_load_config_or_exit", lambda: after)
    cli.bankroll_carry(dry_run=True, slate_key="main")
    assert sheets["W12"].cells["Bankroll"]["B1"] == 105.0  # --dry-run writes nothing
    cli.bankroll_carry(dry_run=False, slate_key="main")
    assert sheets["W12"].cells["Bankroll"]["B1"] == 107.0
    cli.bankroll_carry(dry_run=False, slate_key="main")  # again: nothing to change
    assert sheets["W12"].cells["Bankroll"]["B1"] == 107.0


def test_a_close_run_twice_books_nothing_twice_and_inserts_one_row(season, monkeypatch, tmp_path):
    sheets, csv = season
    base = _cfg("W11", None, "THU")
    _set_up_thanksgiving(monkeypatch, sheets, base)
    _close(monkeypatch, base, "THU", csv, thu=True)
    _close(monkeypatch, base, "THU", csv, thu=True)
    assert sheets["THU"].ledger_keys() == ["E1", "E2"]
    assert sheets["THU"].labels().count("12 Thu") == 1
    assert sorted(sheets["THU"].inserted) == [("Results", 13), ("Season", 13)]  # one row on each tab


def test_a_slate_whose_week_cannot_be_told_stops_the_close_before_any_booking(season, monkeypatch):
    sheets, csv = season
    sheets["THU"].title = "Thanksgiving"  # no week in the title, and none in config.toml
    import typer

    with pytest.raises(typer.Exit):
        cli._sync_bankroll_from_csv(_cfg("W12", "W11", "THU"), csv, close=True)
    assert sheets["W12"].ledger_keys() == []


def test_no_secondary_slate_books_the_whole_week_as_before(season, monkeypatch):
    sheets, csv = season
    cli._sync_bankroll_from_csv(_cfg("W12", "W11", None), csv, close=True)
    assert sheets["W12"].ledger_keys() == ["E1", "E2", "E3", "E4", "E5"]
    assert sheets["W12"].inserted == []
    assert "12 Thu" not in sheets["W12"].labels()


# -- `dfs week new` (main), with everything but the carry-forward stubbed ---------------------------------


def _week_new_main(monkeypatch, tmp_path, base: Config, sheets, new_id: str):
    from dfs import paths

    config = tmp_path / "config.toml"
    config.write_text(
        f'[google_sheets]\nsheet_id = "{base.google_sheets.sheet_id}"\ncredentials_file = "c.json"\n'
        '\n[slates.thu]\nsheet_id = "THU"\n'
    )
    monkeypatch.setattr(paths, "CONFIG_FILE", config)
    monkeypatch.setattr(cli, "_load_config_or_exit", lambda: base)
    monkeypatch.setattr(cli, "run_doctor", lambda *a, **k: [])
    monkeypatch.setattr(cli, "clear_previous_week", lambda *a, **k: [])
    monkeypatch.setattr(cli, "clear_synced_tabs", lambda *a, **k: [])
    monkeypatch.setattr(cli.store, "clear_current", lambda: [])
    monkeypatch.setattr(cli, "run_sync", lambda *a, **k: [])

    class Ctx:
        week, season = 12, SEASON

        @classmethod
        def current(cls):
            return cls

    monkeypatch.setattr(cli, "SyncContext", Ctx)
    monkeypatch.setattr(cli, "_opening_lines_line", lambda ctx: "")
    monkeypatch.setattr(cli, "_reset_suggested_on_new_sheet", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_rebuild_cockpit_on_new_sheet", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_refresh_stacks", lambda *a, **k: None)
    sheets[new_id].title = "Copy of Template"
    cli.week_new(sheet_url=new_id, week=12, yes=True, slate_key="main")
    assert sheets[new_id].title == "Week 12"


# -- the pure parts ------------------------------------------------------------------------------------------


def test_labels_and_their_order():
    assert slate_chain.season_label(12) == "12" and slate_chain.season_label(12, "thu") == "12 Thu"
    assert slate_chain.parse_label("12 Thu") == (12, "thu")
    assert slate_chain.parse_label(12) == (12, "main") and slate_chain.parse_label("12") == (12, "main")
    assert slate_chain.parse_label("Year to date") is None and slate_chain.parse_label("") is None
    order = sorted(["12", "13", "12 Thu", "11", "12 Mon", "12 Sat"], key=slate_chain.label_position)
    assert order == ["11", "12 Thu", "12 Sat", "12", "12 Mon", "13"]


def test_the_previous_slate_by_date():
    links = [
        slate_chain.Link("main", 11, "W11"),
        slate_chain.Link("thu", 12, "THU"),
        slate_chain.Link("main", 12, "W12"),
    ]
    assert slate_chain.previous(links, slate_chain.position(12, "thu")).sheet_id == "W11"
    assert slate_chain.previous(links, slate_chain.position(12)).sheet_id == "THU"
    assert slate_chain.previous(links, slate_chain.position(13)).sheet_id == "W12"
    assert [lk.sheet_id for lk in slate_chain.since_last_main(links, slate_chain.position(12))] == [
        "W11",
        "THU",
    ]
    assert [lk.sheet_id for lk in slate_chain.since_last_main(links, slate_chain.position(13))] == ["W12"]


def test_a_thursday_classic_of_another_week_is_the_main_slates():
    """[slates.thu] still names Week 12's sheet in Week 13: a Thursday Classic of Week 13 is main's."""
    from datetime import datetime

    weeks = {"thu": 12}
    assert slate_chain.entry_slate(datetime(2026, 11, 26, 12, 30), "Classic", SEASON, weeks) == "thu"
    assert slate_chain.entry_slate(datetime(2026, 12, 3, 20, 15), "Classic", SEASON, weeks) == "main"
    assert (
        slate_chain.entry_slate(datetime(2026, 11, 26, 20, 20), "Showdown Captain Mode", SEASON, weeks)
        == "main"
    )


def test_the_contest_log_is_split_by_slate():
    from dfs.contests import history

    log = history.enrich(
        history.read_export(__import__("io").BytesIO((HEADER + "\n" + "\n".join(ROWS)).encode()))
    )
    weeks = {"thu": 12}
    thu = slate_chain.log_for_slate(log, "thu", SEASON, weeks)
    main = slate_chain.log_for_slate(log, "main", SEASON, weeks)
    assert sorted(thu["Entry_Key"]) == ["E1", "E2"]
    assert sorted(main["Entry_Key"]) == ["E3", "E4", "E5", "E6"]
    assert slate_chain.log_for_slate(log, "main", SEASON, {}) is log  # no secondary slate: as it was


def test_a_slate_row_after_the_last_week_is_summed_too():
    """A slate after the last week row (a Monday slate of Week 18) goes under it, outside the old "Year to
    date" sums: they are rewritten over every slate row. A Saturday slate goes before its week's Sunday."""
    from dfs.sheet_season_view import insert_slate_row

    sheet = Sheet("Week 18")
    sat, _ = insert_slate_row(sheet, "Season", "18 Sat")
    assert sat == FIRST_ROW + 17 and sheet.labels()[-3:] == ["17", "18 Sat", "18"]
    at, _ = insert_slate_row(sheet, "Season", "18 Mon")
    assert at == FIRST_ROW + 19 and sheet.labels()[-3:] == ["18 Sat", "18", "18 Mon"]
    sheet.cells["Season"][f"B{at}"] = 7.0
    sheet.cells["Season"][f"B{sat}"] = 2.0
    total = next(r for r in range(20, 40) if sheet.cells["Season"].get(f"A{r}") == "Cash")
    assert sheet.value("Season", f"B{total}") == 9.0
    assert sheet.value("Season", f"N{at}") == 9.0
    assert sheet.cells["Season"][f"T{at + 1}"] == f"=A{at}"  # the chart's point for it
    assert sheet.cells["Season"][f"T{sat + 1}"] == f"=A{sat}"


def test_doctor_finds_no_formula_gap_on_a_season_tab_with_a_slate_row():
    """`dfs doctor`'s formula-range check (run after every sync) reads the Season rows by position: the
    inserted "12 Thu" row has the same per-row formulas as a week row, so nothing is flagged or repaired."""
    from dfs.sheet_formula_ranges import find_gaps, formula_ranges, repair_formula_ranges
    from dfs.sheet_season_view import HEADER, insert_slate_row

    sheet = Sheet("Week 12 Thanksgiving")
    insert_slate_row(sheet, "Season", "12 Thu")
    cfg = _cfg("W12", None, None)
    specs = [s for s in formula_ranges(cfg, {"Season": list(HEADER)}) if s.tab == "Season"]
    assert specs and all(find_gaps(sheet, spec) == [] for spec in specs)
    assert repair_formula_ranges(sheet, cfg, {"Season": list(HEADER)}) == []
