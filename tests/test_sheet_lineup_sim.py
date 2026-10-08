"""Median / p90 / P(cash) / P(GPP) on the Lineups tab: what is written where, determinism, the GPP header."""

from contextlib import contextmanager

import numpy as np
import pandas as pd
import pytest

from dfs import sheet_lineup_sim as sls
from dfs.sheets import column_letter

BLOCKS = [(2, 10), (15, 23)]
REPEATS = [14]
HEADER = ["Name", "Pos.", "Games", "Min Unique", *sls.LINEUP_SIM_HEADERS, "Edge ↗"]
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


def test_each_built_lineup_gets_its_four_numbers_on_its_total_row_and_a_blank_for_an_unbuilt_one():
    client = FakeClient([FULL_A, None])
    report = _run(client)
    assert report.simulated == 1 and report.skipped == 1
    median, p90, p_cash, p_gpp = (column_letter(HEADER.index(h)) for h in sls.LINEUP_SIM_HEADERS)
    row_a = client.updates[f"{median}11:{p_gpp}11"][0]  # block 1's Total row is end + 1
    assert row_a[0] > 0 and row_a[1] > row_a[0] and 0 <= row_a[3] <= row_a[2] <= 1
    assert client.updates[f"{median}24:{p_gpp}24"] == [["", "", "", ""]]  # the unbuilt block is cleared


def test_the_cash_line_is_the_typed_median_and_is_reported():
    client = FakeClient([FULL_A, None])
    report = _run(client)
    assert report.cash_line.value == 140.16 and report.cash_line.weeks == (2, 3, 4)
    assert "Weeks 2, 3, 4" in report.line() and "140.16" in report.line()
    default = _run(FakeClient([FULL_A, None], results=[["Week", "Cash Line"], ["1", ""]]))
    assert default.cash_line.is_default


def test_the_portfolio_line_sits_on_the_first_blocks_remaining_row():
    client = FakeClient([FULL_A, FULL_B])
    report = _run(client)
    median, p90, p_cash, p_gpp = (column_letter(HEADER.index(h)) for h in sls.LINEUP_SIM_HEADERS)
    label, expected, any_cash, any_gpp = client.updates[f"{median}12:{p_gpp}12"][0]  # block 1 end + 2
    assert label == sls.PORTFOLIO_LABEL
    single = [client.updates[f"{median}{r}:{p_gpp}{r}"][0] for r in (11, 24)]
    assert any_cash >= max(single[0][2], single[1][2]) - 1e-9  # at least one cashing is at least as likely
    assert expected == pytest.approx(single[0][2] + single[1][2], abs=0.01)  # the expectation adds up
    assert any_gpp >= max(single[0][3], single[1][3]) - 1e-9
    assert report.portfolio["p_any_cash"] == pytest.approx(any_cash, abs=1e-4)


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


def test_the_header_is_rewritten_when_the_configured_target_changes_and_reflects_it():
    client = FakeClient([FULL_A, None])
    assert client.header[HEADER.index("P(190+)")] == "P(190+)"
    report = _run(client, gpp_target=200.0)
    assert report.renamed_header
    assert "P(200+)" in client.header and "P(190+)" not in client.header
    letter = column_letter(client.header.index("P(200+)"))
    renamed = [rng for rng, _ in client.single]
    assert f"{letter}1" in renamed and f"{letter}14" in renamed  # the primary header and the repeat
    assert any("200" in text for _cell, text in client.notes)  # the note names the new target
    assert client.widths  # and the width is set again
    assert sls.find_gpp_column(client.header) == client.header.index("P(200+)")
    again = _run(client, gpp_target=200.0)
    assert not again.renamed_header  # nothing to rewrite the second time


def test_a_sheet_without_the_columns_is_skipped_with_a_message():
    client = FakeClient([FULL_A, None], header=["Name", "Pos.", "Games"])
    message = _run(client)
    assert isinstance(message, str) and "reorder-columns" in message and "P(190+)" in message
    assert not client.updates


def test_only_one_batched_read_and_one_batched_write_are_needed():
    client = FakeClient([FULL_A, FULL_B])
    _run(client)
    assert client.reads == 1 and len(client.single) == 0  # no per-cell writes unless the header is renamed


def test_the_canonical_header_comes_back_for_the_column_reorder():
    client = FakeClient([FULL_A, None])
    _run(client, gpp_target=200.0)
    assert sls.restore_canonical_gpp_header(client, "Lineups", header_repeats_at=REPEATS)
    assert client.header[HEADER.index("P(190+)")] == "P(190+)"
    assert not sls.restore_canonical_gpp_header(client, "Lineups", header_repeats_at=REPEATS)


def test_gpp_header_text_carries_the_target_and_the_lookup_accepts_any_target():
    assert sls.gpp_header(190.0) == "P(190+)" and sls.gpp_header(187.5) == "P(187.5+)"
    assert sls.find_gpp_column(["a", "P(187.5+)", "P(cash)"]) == 1
    assert sls.find_gpp_column(["a", "P(cash)", "P(GPP)"]) is None
    notes = sls.lineup_sim_notes(200.0)
    assert set(notes) == {"Median", "p90", "P(cash)", "P(200+)"} and "200" in notes["P(200+)"]


def test_the_lineups_column_order_places_the_four_columns_after_min_unique():
    from dfs.config import SIM_GPP_TARGET_DEFAULT
    from dfs.sheet_columns import LINEUPS_COLUMN_ORDER

    i = LINEUPS_COLUMN_ORDER.index("Min Unique")
    assert LINEUPS_COLUMN_ORDER[i + 1 : i + 5] == ["Median", "p90", "P(cash)", "P(190+)"]
    assert sls.GPP_HEADER_DEFAULT == sls.gpp_header(SIM_GPP_TARGET_DEFAULT) == "P(190+)"
