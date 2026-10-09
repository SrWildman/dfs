"""Median / p90 / P(cash) / P(GPP) on the Lineups Total rows: what is written where, the labels, determinism,
the Cash/GPP marker, and the retired far-right columns."""

from contextlib import contextmanager

import numpy as np
import pandas as pd
import pytest

from dfs import sheet_lineup_sim as sls
from dfs.sheets import column_letter

BLOCKS = [(2, 10), (15, 23)]
REPEATS = [14]
HEADER = ["Name", "Pos.", "Team", "Opp.", "DK Sal", "Pts", *sls.SIM_SLOT_HEADERS, "Hit3x%", "Edge ↗"]
RESULTS = [
    ["Week", "Cash Pts", "Cash Line"],
    ["1", "131", "147.56"],
    ["2", "170", "140.16"],
    ["3", "166", "158.7"],
    ["4", "344", "105.98"],
    ["5"],
]
ROSTER = [  # name, position, team, opp, projection
    ("QB One", "QB", "DEN", "KC", 20.0),
    ("RB One", "RB", "DEN", "KC", 16.0),
    ("RB Two", "RB", "KC", "DEN", 13.0),
    ("WR One", "WR", "DEN", "KC", 15.0),
    ("WR Two", "WR", "DEN", "KC", 12.0),
    ("WR Three", "WR", "KC", "DEN", 14.0),
    ("TE One", "TE", "KC", "DEN", 9.0),
    ("Flex Rb", "RB", "KC", "DEN", 10.0),
    ("Broncos", "DST", "DEN", "KC", 7.0),
    ("Alt Qb", "QB", "KC", "DEN", 19.0),
    ("Alt Wr", "WR", "KC", "DEN", 13.0),
]


def _edge():
    return pd.DataFrame(
        [
            {
                "Id": i,
                "Name": n,
                "Position": p,
                "Team": t,
                "Opp": o,
                "Salary": 5000,
                "ProjPts": pts,
                "CalPts": np.nan,
                "GameID": "G1",
                "Avail": "",
                "Tgt%": 0.1,
                "Rush%": 0.1,
            }
            for i, (n, p, t, o, pts) in enumerate(ROSTER)
        ]
    )


class FakeClient:
    def __init__(self, names, header=HEADER, results=RESULTS):
        self.header = list(header)
        self.names = names  # list of 9-name lists (or None) per block
        self.results = results
        self.updates = {}
        self.single = []
        self.notes = []
        self.formats = []
        self.widths = []
        self.reads = 0

    def tab_exists(self, tab):
        return True

    def batch_read_ranges(self, specs):
        self.reads += 1
        rows = [[""] for _ in range(25)]  # A2:A27
        for (start, end), names in zip(BLOCKS, self.names, strict=True):
            for offset, name in enumerate(names or [""] * (end - start + 1)):
                rows[start - 2 + offset] = [name]
        return [[self.header], rows, self.results]

    def read_range(self, tab, rng):
        return [self.header]

    def update_ranges(self, tab, updates):
        self.updates.update(updates)

    def update_range(self, tab, rng, rows):
        self.single.append((rng, rows))
        if rng.endswith("1") and len(rows) == 1 and rng[0].isalpha() and rng[1:] == "1":
            self.header[ord(rng[0]) - ord("A")] = rows[0][0]

    def set_note(self, tab, cell, text):
        self.notes.append((cell, text))

    def format_range(self, tab, rng, fmt):
        self.formats.append((rng, fmt))

    def set_column_widths(self, tab, widths):
        self.widths.append(widths)

    @contextmanager
    def batched(self):
        yield self


def _lineup(*names):
    return list(names)


FULL_A = ["QB One", "RB One", "RB Two", "WR One", "WR Two", "WR Three", "TE One", "Flex Rb", "Broncos"]
FULL_B = ["Alt Qb", "RB One", "RB Two", "WR One", "WR Two", "Alt Wr", "TE One", "Flex Rb", "Broncos"]


def _run(client, **kw):
    args = {
        "header_row": 1,
        "name_blocks": BLOCKS,
        "header_repeats_at": REPEATS,
        "edge": _edge(),
        "depth_rows": None,
        "gsis_by_id": None,
        "results_tab": "Results",
        "week": 5,
        "gpp_target": 190.0,
    }
    return sls.write_lineup_sim(client, "Lineups", **{**args, **kw})


def _slot_letters():
    return [column_letter(HEADER.index(h)) for h in sls.SIM_SLOT_HEADERS]


def test_each_built_lineup_gets_its_four_numbers_on_its_total_row_and_a_blank_for_an_unbuilt_one():
    client = FakeClient([FULL_A, None])
    report = _run(client)
    assert report.simulated == 1 and report.skipped == 1
    first, _, _, last = _slot_letters()
    assert (first, last) == ("G", "J")  # the four columns right after Pts: AggPts, CalPts, Val, ValAdj
    row_a = client.updates[f"{first}11:{last}11"][0]  # block 1's Total row is end + 1
    assert row_a[0] > 0 and row_a[1] > row_a[0] and 0 <= row_a[3] <= row_a[2] <= 1
    assert client.updates[f"{first}24:{last}24"] == [["", "", "", ""]]  # the unbuilt block is cleared


def test_the_labels_go_on_every_remaining_row_with_the_configured_target():
    client = FakeClient([FULL_A, None])
    _run(client, gpp_target=200.0)
    first, _, _, last = _slot_letters()
    assert client.updates[f"{first}12:{last}12"] == [["Median", "p90", "P(cash)", "P(200+)"]]
    assert client.updates[f"{first}25:{last}25"] == [["Median", "p90", "P(cash)", "P(200+)"]]


def test_the_portfolio_is_in_the_report_for_the_board_not_written_to_lineups():
    client = FakeClient([FULL_A, FULL_B])
    report = _run(client)
    first, _, _, last = _slot_letters()
    single = [client.updates[f"{first}{r}:{last}{r}"][0] for r in (11, 24)]
    p = report.portfolio
    assert p["p_any_cash"] >= max(single[0][2], single[1][2]) - 1e-9
    assert p["expected_cashes"] == pytest.approx(single[0][2] + single[1][2], abs=0.01)
    assert p["p_any_gpp"] >= max(single[0][3], single[1][3]) - 1e-9
    assert not any("Portfolio" in str(v) for v in client.updates.values())  # the old far-right line is gone


def test_the_cash_line_is_the_typed_median_and_is_reported():
    client = FakeClient([FULL_A, None])
    report = _run(client)
    assert report.cash_line.value == 143.86 and report.cash_line.weeks == (
        1,
        2,
        3,
        4,
    )  # the whole season so far
    assert "Weeks 1, 2, 3, 4" in report.line() and "143.86" in report.line()
    default = _run(FakeClient([FULL_A, None], results=[["Week", "Cash Line"], ["1", ""]]))
    assert default.cash_line.is_default


def test_the_numbers_are_deterministic_under_the_fixed_seed():
    one, two = FakeClient([FULL_A, FULL_B]), FakeClient([FULL_A, FULL_B])
    _run(one)
    _run(two)
    assert one.updates == two.updates  # no jitter between syncs


def test_an_incomplete_lineup_or_an_unknown_name_is_left_blank():
    partial = FULL_A[:8] + [""]
    unknown = [*FULL_A[:8], "Not In Edge"]
    for names in (partial, unknown):
        client = FakeClient([names, None])
        report = _run(client)
        assert report.simulated == 0 and "nothing to simulate" in report.line()


def test_a_sheet_without_the_four_slot_columns_is_skipped_with_a_message():
    for header in (
        ["Name", "Pos.", "Games"],
        ["Name", "Pos.", "AggPts", "Edge ↗", "CalPts", "Val", "ValAdj"],
    ):
        client = FakeClient([FULL_A, None], header=header)
        message = _run(client)
        assert isinstance(message, str) and "not found as one run" in message
        assert not client.updates


def test_only_one_batched_read_and_one_batched_write_are_needed():
    client = FakeClient([FULL_A, FULL_B])
    _run(client)
    assert client.reads == 1 and len(client.single) == 0  # no per-cell writes


def test_gpp_label_text_carries_the_target_and_the_retired_headers_are_recognised():
    assert sls.gpp_header(190.0) == "P(190+)" and sls.gpp_header(187.5) == "P(187.5+)"
    assert sls.labels(200.0) == ["Median", "p90", "P(cash)", "P(200+)"]
    notes = sls.lineup_sim_notes(200.0, "cash line 141.3 (season median, 4 weeks)")
    assert set(notes) == set(sls.labels(200.0)) and "200" in notes["P(200+)"]
    assert (
        "every typed Cash Line" in notes["P(cash)"]
        and "cash line 141.3 (season median, 4 weeks)" in notes["P(cash)"]
    )
    assert all(sls.is_retired_sim_header(h) for h in ("Median", "p90", "P(cash)", "P(190+)", "P(187.5+)"))
    assert not sls.is_retired_sim_header("Edge ↗") and not sls.is_retired_sim_header("P(GPP)")


def test_the_lineups_column_order_no_longer_has_the_four_columns():
    from dfs.sheet_columns import LINEUPS_COLUMN_ORDER

    assert not [h for h in LINEUPS_COLUMN_ORDER if sls.is_retired_sim_header(h)]
    assert all(h in LINEUPS_COLUMN_ORDER for h in sls.SIM_SLOT_HEADERS)
    i = LINEUPS_COLUMN_ORDER.index("Pts")
    assert LINEUPS_COLUMN_ORDER[i + 1 : i + 5] == list(sls.SIM_SLOT_HEADERS)


class _ScoreboardClient(FakeClient):
    def __init__(self):
        super().__init__([FULL_A, None])
        self.rules, self.validations = [], []

    def read_range(self, tab, rng):
        return [self.header]

    def add_boolean_rule(self, tab, rng, *, condition_type, values, fmt):
        self.rules.append((rng, condition_type, values))

    def set_dropdown_validation(self, tab, rng, options):
        self.validations.append((rng, options))


def test_the_scoreboard_formats_the_marker_the_greens_and_the_matching_highlight():
    client = _ScoreboardClient()
    sls.format_sim_columns(client, "Lineups", header_row=1, name_blocks=BLOCKS)
    assert client.validations == [
        ("A11", ["Cash", "GPP"]),
        ("A24", ["Cash", "GPP"]),
    ]  # column A of each Total row
    green = {(rng, v[0]) for rng, kind, v in client.rules if kind == "NUMBER_GREATER_THAN_EQ"}
    assert ("I11", "0.5") in green and ("J11", "0.05") in green  # P(cash) 50%, P(GPP) 5%
    highlight = {(rng, v[0]) for rng, kind, v in client.rules if kind == "CUSTOM_FORMULA"}
    assert ("I11", '=$A$11="Cash"') in highlight and ("J11", '=$A$11="GPP"') in highlight
    formats = dict(client.formats)
    assert formats["G11:H11"]["numberFormat"]["pattern"] == "0.0"
    assert formats["I11:J11"]["numberFormat"]["type"] == "PERCENT"
    assert "backgroundColor" in formats["A11"]  # the pale-yellow input look


def test_label_notes_land_on_the_first_lineups_remaining_row():
    client = _ScoreboardClient()
    assert sls.apply_label_notes(client, "Lineups", header_row=1, name_blocks=BLOCKS, target=190.0) == 4
    assert [cell for cell, _ in client.notes] == ["G12", "H12", "I12", "J12"]


def test_lineup_types_reads_column_a_of_each_total_row_and_blank_means_both():
    class C:
        def batch_read_ranges(self, specs):
            assert [s[1] for s in specs] == ["A11", "A24"]
            return [[["Cash"]], [[]]]

    assert sls.lineup_types(C(), "Lineups", BLOCKS) == ["Cash", ""]


def test_the_late_swap_goal_is_the_flag_else_the_lineups_marker_else_cash():
    assert sls.resolve_goal(None, "GPP") == "gpp" and sls.resolve_goal(None, "Cash") == "cash"
    assert sls.resolve_goal(None, "") == "cash"  # a blank marker means both: the long-standing default
    assert sls.resolve_goal("cash", "GPP") == "cash" and sls.resolve_goal("gpp", "") == "gpp"  # the flag wins
