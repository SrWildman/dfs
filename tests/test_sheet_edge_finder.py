"""The Edge Finder writer: id-keyed formulas, the Set dropdowns, hidden columns, and open/shut groups that
survive a rewrite."""

import numpy as np
import pandas as pd

from dfs import edge_finder_tab as eft
from dfs import sheet_edge_finder as writer
from dfs.derived import EDGE_COLUMNS
from dfs.weekly_reset import PLAYER_POOL_ADDED_NAMES_ROWS, PLAYER_POOL_HEADER_ROW


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

    def __init__(self, *, groups=None, keys=None, filter_views=None, header=None):
        self.tab_written = None
        self.rules, self.scales, self.formats = [], [], []
        self.validations, self.hidden, self.calls = [], [], []
        self.groups = list(groups or [])  # {"start","end","depth","collapsed"}
        self.keys = dict(keys or {})  # row -> text in hidden column Q
        self.filter_views = {"Cash": 11, "GPP": 22} if filter_views is None else filter_views
        self.header = header if header is not None else [["Name", "Pos"] + [""] * 23 + ["Added"]]

    def tab_exists(self, tab):
        return True

    def tab_gid(self, tab):
        return 5

    def read_range(self, tab, rng):
        if tab == "Player Pool":
            return self.header
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

    def set_dropdown_validation(self, tab, rng, options):
        self.validations.append((rng, options))

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
    added = "'Player Pool'!$Z$4:$Z$53"
    rows = writer.tab_rows(layout, "EdgeRaw", 777, added_range=added)
    row = layout.player_rows[0]
    cells = rows[row - 1]
    idl = writer._id_letter()
    pool, do, link = cells[ord(eft.POOL_COL) - 65], cells[ord(eft.DO_COL) - 65], cells[ord(eft.LINK_COL) - 65]
    lookup = f"MATCH(${eft.ID_COL}{row},EdgeRaw!${idl}:${idl},0)"
    assert lookup in pool and pool.startswith(f'=IF(${eft.ID_COL}{row}="","",')  # blank Id, blank result
    assert (
        f"COUNTIF({added},$A{row})" in pool and '"Added"' in pool
    )  # the Added list is the one by-name lookup
    assert "MATCH($A" not in pool and "MATCH($A" not in link  # EdgeRaw is never matched by name
    assert do.startswith(f'=IF(${eft.POOL_COL}{row}<>"","In pool ("&${eft.POOL_COL}{row}&")","')
    assert do.endswith(('Cash add")', 'Cash option")'))
    assert "#gid=777" in link and lookup in link
    assert cells[0] == "WR1" and cells[ord(eft.ID_COL) - 65] == 1001  # untouched


def test_the_pool_cell_reads_edgeraw_alone_when_there_is_no_added_list():
    layout = eft.build_layout(_inputs(_edge([_player(1)])))
    rows = writer.tab_rows(layout, "EdgeRaw", 1, added_range=None)
    assert "COUNTIF" not in rows[layout.player_rows[0] - 1][ord(eft.POOL_COL) - 65]


def test_the_added_range_is_found_by_header_text_and_missing_is_none():
    client = FakeClient()
    first = PLAYER_POOL_HEADER_ROW + 1
    last = PLAYER_POOL_HEADER_ROW + PLAYER_POOL_ADDED_NAMES_ROWS
    assert writer.added_names_range(client) == f"'Player Pool'!$Z${first}:$Z${last}"
    assert writer.added_names_range(FakeClient(header=[["Name", "Pos"]])) is None


def test_the_writer_adds_set_dropdowns_hides_id_and_key_and_shows_the_all_links():
    client = FakeClient()
    edge = _edge([_player(i) for i in range(1, 4)])
    message = writer.write_tab(client, _inputs(edge), edge_tab="EdgeRaw")
    assert "player rows" in message and client.tab_written[0] == "Edge Finder"
    assert client.hidden == [(eft.ID_COL, eft.KEY_COL)]
    assert client.validations and all(o == ["Cash", "GPP", "Both", "Remove"] for _, o in client.validations)
    assert all(rng.startswith(eft.SET_COL) for rng, _ in client.validations)
    assert "control_before" in client.calls and client.freeze_args == {"rows": 0, "cols": 1}
    rows = client.tab_written[1]
    cash_row = [i for i, r in enumerate(rows) if str(r[0]).startswith("CASH CORE")][0]
    link = rows[cash_row][ord(eft.LINK_COL) - 65]
    assert link == '=HYPERLINK("https://docs.google.com/spreadsheets/d/SHEETID/edit#gid=5&fvid=11","All ↗")'
    gpp_row = [i for i, r in enumerate(rows) if str(r[0]).startswith("GPP UPSIDE")][0]
    assert "fvid=22" in rows[gpp_row][ord(eft.LINK_COL) - 65]


def test_no_all_link_when_the_filter_view_does_not_exist():
    client = FakeClient(filter_views={})
    writer.write_tab(client, _inputs(_edge([_player(1)])), edge_tab="EdgeRaw")
    cash_row = [i for i, r in enumerate(client.tab_written[1]) if str(r[0]).startswith("CASH CORE")][0]
    assert client.tab_written[1][cash_row][ord(eft.LINK_COL) - 65] == ""


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
    assert chips == {"INJ+", "FADE↓", "USAGE↑", "USAGE↓"}


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
