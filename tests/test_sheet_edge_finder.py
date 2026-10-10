"""The Edge Finder writer: id-keyed formulas, the Pool dropdowns, notes, hidden columns, and open/shut groups
that survive a rewrite."""

import numpy as np
import pandas as pd

from dfs import edge_finder_tab as eft
from dfs import sheet_edge_finder as writer
from dfs import sheet_pool_cells as pc
from dfs.derived import EDGE_COLUMNS


def _edge(rows):
    frame = pd.DataFrame(rows)
    for column in EDGE_COLUMNS:
        if column not in frame.columns:
            frame[column] = "" if column in ("Edge", "Avail") else np.nan
    return frame


def _player(i, pos="WR", **kw):
    base = {
        "Id": 1000 + i,
        "Name": f"{pos}{i}",
        "Position": pos,
        "Team": "DEN",
        "Salary": 5000 + 100 * i,
        "ProjPts": 10.0 + i / 10,
        "CalPts": 11.0 + i / 10,
        "Hit3x%": 20.0 + i,
        "Boom%": 10.0 + i,
        "Bust%": 40.0,
        "Floor": 4.0,
        "CeilM": 20.0,
        "Own%": 0.0,
        "CalPts%ile": 80.0,
        "Avail": "",
        "Edge": "",
        "OwnStatus": "unpublished",
    }
    return {**base, **kw}


def _inputs(edge):
    players = pd.DataFrame(
        {"Id": edge["Id"], "Games": 3, "DkG": 10.0, "UsageN": 8, "GsisId": edge["Id"].astype(str)}
    )
    return eft.Inputs(
        edge=edge,
        players=players,
        beneficiaries=pd.DataFrame(),
        matchups=pd.DataFrame(),
        status={"week": 5, "calpts_weeks": [1, 2, 3, 4], "calpts_sources": ["ProjPts"]},
    )


class FakeClient:
    """Records what the writer sends and keeps row groups as the sheet does (a second write reads them)."""

    spreadsheet_id = "SHEETID"

    def __init__(self, *, groups=None, keys=None, filter_views=None):
        self.tab_written = None
        self.rules, self.scales, self.formats = [], [], []
        self.validations, self.hidden, self.calls = [], [], []
        self.notes, self.cleared_notes = {}, []
        self.groups = list(groups or [])  # {"start","end","depth","collapsed"}
        self.keys = dict(keys or {})  # row -> text in the hidden group-key column
        self.filter_views = {"Cash": 11, "GPP": 22} if filter_views is None else filter_views

    def tab_exists(self, tab):
        return True

    def tab_gid(self, tab):
        return 5

    def read_range(self, tab, rng):
        column = rng.split(":")[0][0]
        assert column == eft.KEY_COL
        last = int(rng.split(":")[1][1:])
        return [[self.keys.get(r, "")] if self.keys.get(r) else [] for r in range(1, last + 1)]

    def read_row_groups(self, tab):
        return list(self.groups)

    def clear_row_groups(self, tab):
        self.calls.append("clear_row_groups")
        self.groups = []

    def unhide_rows(self, tab, first, last):
        self.calls.append(("unhide", first, last))

    def ensure_row_capacity(self, tab, n):
        self.calls.append(("capacity", n))

    def filter_view_id(self, tab, title):
        return self.filter_views.get(title)

    def write_tab(self, tab, rows):
        self.tab_written = (tab, rows)
        self.keys = {i + 1: r[eft.COLUMN_COUNT - 1] for i, r in enumerate(rows) if r[eft.COLUMN_COUNT - 1]}
        return len(rows)

    def clear_conditional_formats(self, tab):
        pass

    def format_range(self, tab, rng, fmt):
        self.formats.append((rng, fmt))

    def set_column_widths(self, tab, widths):
        self.widths = widths

    def add_boolean_rule(self, tab, rng, *, condition_type, values, fmt):
        self.rules.append((rng, condition_type, values))

    def add_color_scale(self, tab, rng, **kw):
        self.scales.append((rng, kw))

    def set_dropdown_validation(self, tab, rng, options, **kwargs):
        self.validations.append((rng, options))
        self.validation_kwargs = getattr(self, "validation_kwargs", []) + [kwargs]

    def set_note(self, tab, cell, note):
        self.notes[cell] = note

    def clear_notes(self, tab, rng):
        self.cleared_notes.append(rng)

    def clear_data_validation(self, tab, rng):
        self.cleared_validation = rng

    def hide_columns(self, tab, first, last, hidden=True):
        self.hidden.append((first, last))

    def freeze(self, tab, **kw):
        self.freeze_args = kw

    def set_row_group_control_before(self, tab):
        self.calls.append("control_before")

    def apply_row_groups(self, tab, groups):
        self.applied = groups
        self.groups = [
            {"start": f, "end": last, "depth": d, "collapsed": c}
            for f, last, d, c in sorted(groups, key=lambda g: (g[2], g[0]))
        ]


def test_formulas_find_the_player_by_his_hidden_id_never_his_name():
    layout = eft.build_layout(_inputs(_edge([_player(1)])))
    rows = writer.tab_rows(layout, "EdgeRaw")
    row = layout.player_rows[0]
    cells = rows[row - 1]
    idl = writer._id_letter()
    pool, do = cells[ord(eft.POOL_COL) - 65], cells[ord(eft.DO_COL) - 65]
    lookup = f"MATCH(${eft.ID_COL}{row},EdgeRaw!${idl}:${idl},0)"
    assert lookup in pool and pool.startswith(f'=IF(${eft.ID_COL}{row}="","",')  # blank Id, blank result
    assert pool == pc.pool_formula(row, "EdgeRaw", id_col=eft.ID_COL)  # the one formula the script restores
    assert "MATCH($B" not in pool and "COUNTIF" not in pool  # never by name, no Added list
    assert do.startswith(f'=IF(${eft.POOL_COL}{row}<>"","In pool ("&${eft.POOL_COL}{row}&")","')
    assert do.endswith(('Cash add")', 'Cash option")'))
    assert cells[ord(eft.NAME_COL) - 65] == "WR1" and cells[ord(eft.ID_COL) - 65] == 1001  # untouched


def test_the_columns_run_pool_name_pos_team_salary_own_then_numbers_do_and_why_last():
    assert eft.LEAD == ["Pool", "Name", "Pos", "Team", "Salary", "Own%"]
    assert (eft.POOL_COL, eft.NAME_COL, eft.OWN_COL) == ("A", "B", "F")
    assert eft.TRAILING_HEADERS == ["Do", "Why", "Id"]
    assert ord(eft.DO_COL) < ord(eft.WHY_COL) < ord(eft.ID_COL)  # Why is the last visible column
    assert eft.LAST_VISIBLE_COL == eft.WHY_COL


def test_the_writer_adds_pool_dropdowns_notes_hides_id_and_key_and_shows_the_all_links():
    client = FakeClient()
    edge = _edge([_player(i) for i in range(1, 4)])
    message = writer.write_tab(client, _inputs(edge), edge_tab="EdgeRaw")
    assert "player rows" in message and client.tab_written[0] == "Edge Finder"
    assert client.hidden == [(eft.ID_COL, eft.KEY_COL)]
    assert client.cleared_validation.startswith("A1:")  # the old layout's Set dropdowns are cleared first
    assert client.validations and all(o == ["Cash", "GPP", "Both"] for _, o in client.validations)
    assert all(rng.startswith(eft.POOL_COL) for rng, _ in client.validations)
    # warnings, not rejections: a strict rule made Apps Script refuse the formula it writes back
    assert all(k.get("strict") is False for k in client.validation_kwargs)
    assert "control_before" in client.calls and client.freeze_args == {"rows": 0, "cols": 2}
    assert client.cleared_notes == [f"{eft.WHY_COL}1:{eft.WHY_COL}{max(len(client.tab_written[1]), 40) + 20}"]
    rows = client.tab_written[1]
    player = [i + 1 for i, r in enumerate(rows) if r[ord(eft.ID_COL) - 65] == 1001][0]
    assert f"{eft.WHY_COL}{player}" in client.notes  # the full reason sits in the note
    cash_row = [i for i, r in enumerate(rows) if str(r[0]).startswith("CASH CORE")][0]
    link = rows[cash_row][ord(eft.DO_COL) - 65]
    assert link == '=HYPERLINK("https://docs.google.com/spreadsheets/d/SHEETID/edit#gid=5&fvid=11","All ↗")'
    gpp_row = [i for i, r in enumerate(rows) if str(r[0]).startswith("GPP UPSIDE")][0]
    assert "fvid=22" in rows[gpp_row][ord(eft.DO_COL) - 65]


def test_no_all_link_when_the_filter_view_does_not_exist():
    client = FakeClient(filter_views={})
    writer.write_tab(client, _inputs(_edge([_player(1)])), edge_tab="EdgeRaw")
    cash_row = [i for i, r in enumerate(client.tab_written[1]) if str(r[0]).startswith("CASH CORE")][0]
    assert client.tab_written[1][cash_row][ord(eft.DO_COL) - 65] == ""


def test_gradients_are_per_position_block_and_the_pooled_override_is_a_conditional_format():
    client = FakeClient()
    edge = _edge([_player(i) for i in range(1, 4)] + [_player(i, pos="RB") for i in range(11, 14)])
    writer.write_tab(client, _inputs(edge), edge_tab="EdgeRaw")
    assert len(client.scales) >= 4  # Hit3x% and Bust% for two positions, plus Boom%
    in_pool = [r for r in client.rules if r[2] == ["In pool"]]
    assert in_pool and in_pool[0][0].startswith(eft.DO_COL)
    chips = {
        v[0]
        for _rng, kind, v in client.rules
        if kind == "TEXT_CONTAINS" and v[0] not in ("add", "leverage", "In pool")
    }
    assert chips == {"INJ+", "FADE↓", "USAGE↑", "USAGE↓", "Proj ▲", "Proj ▼", "Proj ▲?", "Proj ▼?"}


def _wr_edge(n):
    return _edge([_player(i, **{"Hit3x%": 20.0 + i}) for i in range(1, n + 1)])


def test_a_first_write_uses_the_default_state_sections_open_overflow_shut():
    client = FakeClient()
    writer.write_tab(client, _inputs(_wr_edge(20)), edge_tab="EdgeRaw")
    depths = {(d, c) for _f, _l, d, c in client.applied}
    assert (1, False) in depths and (2, True) in depths
    assert client.calls.index("clear_row_groups") < client.calls.index("control_before")


def test_open_and_shut_state_survives_a_second_write_even_when_the_count_changes():
    client = FakeClient()
    writer.write_tab(client, _inputs(_wr_edge(20)), edge_tab="EdgeRaw")  # sync 1: WR overflow shut
    wr = [g for g in client.groups if g["depth"] == 2][0]
    wr["collapsed"] = False  # Sam opens the WR overflow ...
    section = [g for g in client.groups if g["depth"] == 1][0]
    section["collapsed"] = True  # ... and shuts the first section
    first_section_start = section["start"]
    writer.write_tab(client, _inputs(_wr_edge(24)), edge_tab="EdgeRaw")  # sync 2: 12 more WRs overflow now
    after = {(g["start"], g["depth"]): g["collapsed"] for g in client.groups}
    assert after[(first_section_start, 1)] is True
    new_wr = [g for g in client.groups if g["depth"] == 2 and g["end"] - g["start"] + 1 == 12][0]
    assert new_wr["collapsed"] is False  # the header said 8 more, now 12: same key, same state
    other_sections = [g for g in client.groups if g["depth"] == 1 and g["start"] != first_section_start]
    assert other_sections and all(g["collapsed"] is False for g in other_sections)


def test_the_group_state_is_read_from_the_key_on_the_row_above_each_group():
    client = FakeClient(
        groups=[
            {"start": 12, "end": 30, "depth": 1, "collapsed": True},
            {"start": 20, "end": 25, "depth": 2, "collapsed": False},
        ],
        keys={11: "CASH CORE", 19: "CASH CORE|WR|more"},
    )
    assert writer.read_group_state(client) == {"CASH CORE": True, "CASH CORE|WR|more": False}
    assert writer.read_group_state(FakeClient()) == {}


def test_group_specs_keep_a_known_key_and_default_a_new_one():
    layout = eft.Layout(rows=[])
    layout.groups = [
        eft.Group(5, 9, 1, "A", False),
        eft.Group(10, 12, 2, "B", True),
        eft.Group(13, 14, 2, "C", True),
    ]
    assert writer.group_specs(layout, {"A": True, "B": False}) == [
        (5, 9, 1, True),
        (10, 12, 2, False),
        (13, 14, 2, True),
    ]


def test_the_empty_state_writes_without_error_and_ensure_tab_leaves_an_existing_tab_alone():
    client = FakeClient()
    assert "written" in writer.write_tab(client, None)
    assert "already present" in writer.ensure_tab(client)
    assert client.tab_written[0] == "Edge Finder"


def _through_why(widths):
    return sum(
        widths[letter] for letter in (chr(ord("A") + i) for i in range(ord(eft.WHY_COL) - ord("A") + 1))
    )


def test_pool_through_why_fits_the_budget_with_nothing_cut_off_on_a_realistic_slate():
    edge = _edge([_player(1, Name="Jacory Croskey-Merritt", Edge="INJ+ Proj ▼"), _player(2)])
    layout = eft.build_layout(_inputs(edge))
    fitted = writer.fit_widths(layout)
    assert _through_why(fitted) <= writer.BUDGET  # about 1,270 px: no horizontal scroll on a laptop
    assert writer.NAME_MIN <= fitted[eft.NAME_COL] <= writer.NAME_MAX
    assert fitted[eft.WHY_COL] >= writer.WHY_MIN
    assert (
        fitted[eft.POOL_COL] == 70 and fitted["C"] == 44 and fitted["D"] == 48
    )  # the fixed columns stay tight


def test_edge_and_do_take_the_width_of_their_longest_real_value_capped():
    short = writer.fit_widths(eft.build_layout(_inputs(_edge([_player(1, Edge="INJ+")]))))
    stacked = writer.fit_widths(
        eft.build_layout(_inputs(_edge([_player(1, Edge="INJ+ USAGE↑ Proj ▼ FADE↓ more chips")])))
    )
    assert short[eft.EDGE_COL] < stacked[eft.EDGE_COL] <= writer.CHIP_MAX  # follows the value, never past 140
    assert stacked[eft.EDGE_COL] == writer.CHIP_MAX
    assert writer.CHIP_MAX >= short[eft.DO_COL] >= 90


def test_a_wider_slot_is_paid_for_by_why_then_name_never_by_the_budget():
    edge = _edge([_player(1, Name="Jacory Croskey-Merritt", Edge="INJ+ USAGE↑ Proj ▼ FADE↓ more chips")])
    layout = eft.build_layout(_inputs(edge))
    base = writer.fit_widths(layout)
    row = layout.rows[layout.player_rows[0] - 1]
    for slot in (eft.FIRST_TRAILING - 4, eft.FIRST_TRAILING - 3, eft.FIRST_TRAILING - 2):
        row[slot] = "1234567890123"  # a number never overflows: the slot must widen to show it
    wide = writer.fit_widths(layout)
    assert wide["K"] > base["K"] and wide["J"] > base["J"]  # the slots grew to show it ...
    assert wide[eft.WHY_COL] < base[eft.WHY_COL]  # ... and Why paid for it
    assert wide[eft.WHY_COL] >= writer.WHY_MIN
    assert _through_why(wide) <= writer.BUDGET or wide[eft.WHY_COL] == writer.WHY_MIN


def test_edge_chips_share_one_column_in_every_section():
    edge = _edge([_player(i, Edge="INJ+", Salary=2500 + 10 * i, ValAdj=2.0) for i in range(1, 4)])
    layout = eft.build_layout(_inputs(edge))
    last_slot = eft.FIRST_TRAILING - 1
    with_edge = [r for r in layout.player_rows if layout.rows[r - 1][last_slot] == "INJ+"]
    assert len(with_edge) >= 6  # cash, GPP, punt, leverage-free sections: all show the chips in the last slot
    punt = [
        layout.rows[r - 1]
        for r in layout.player_rows
        if layout.rows[r - 1][_idx(eft.DO_COL)] == "Punt option"
    ]
    assert punt and all(row[last_slot] == "INJ+" and row[last_slot - 1] == "" for row in punt)


def _idx(letter):
    return ord(letter) - ord("A")


def test_widths_are_fitted_to_the_text_the_sheet_shows_not_the_stored_float():
    edge = _edge(
        [_player(1, **{"Own%": 0.0727499999999999, "OwnStatus": "real", "Boom%": 11.127333333333334})]
    )
    layout = eft.build_layout(_inputs(edge))
    shown = writer._display_rows(layout)
    row = shown[layout.player_rows[0] - 1]
    assert row[ord(eft.OWN_COL) - 65] == "7.3%" and row[eft.LEAD.index("Salary")] == "$5,100"
    assert writer.fit_widths(layout)[eft.OWN_COL] == writer.WIDTHS[eft.OWN_COL]  # not blown up by 18 digits
