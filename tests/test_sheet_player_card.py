"""The Player card's sheet side against a recording fake: what it reads, how soft it fails, what it writes."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from dfs import player_card as pc
from dfs import results_update, sheet_player_card, store
from dfs.sheet_protection import protect_player_card, protect_workbook
from dfs.sheets import SheetsError

EXPOSURE_HEADER = ["Name", "Pos", "Salary", "# Lineups", "Exposure", "Target", "vs Target"]


class FakeClient:
    def __init__(self, tabs=("Edge Finder", "EdgeRaw", "Exposure"), typed=""):
        self.tabs = list(tabs)
        self.typed = typed
        self.calls: list[tuple] = []

    def describe(self):
        return "Week 7", "https://example.test/sheet"

    def tab_exists(self, tab):
        return tab in self.tabs

    def list_tabs(self):
        return [SimpleNamespace(title=t) for t in self.tabs]

    def read_range(self, tab, rng):
        return [EXPOSURE_HEADER] if tab == "Exposure" else []

    def read_range_unformatted(self, tab, rng):
        return [[self.typed]] if self.typed else []

    def write_tab(self, tab, rows, **kw):
        self.calls.append(("write_tab", tab, rows))
        if tab not in self.tabs:
            self.tabs.append(tab)
        return len(rows)

    def __getattr__(self, name):  # every formatting / protection call is just recorded
        def record(*args, **kwargs):
            self.calls.append((name, *args, kwargs))

        return record

    def named(self, name):
        return [c for c in self.calls if c[0] == name]


def cfg():
    return SimpleNamespace(
        google_sheets=SimpleNamespace(tab_mappings={"edge": "EdgeRaw"}),
        lineups=SimpleNamespace(builder_tab="Lineups"),
    )


def edge():
    return pd.DataFrame(
        {
            "Name": ["Wide One"],
            "Position": ["WR"],
            "Team": ["AAA"],
            "GameStart": ["2026-10-25T16:25:00Z"],
            "Edge": [""],
            "Flags": [""],
        }
    )


def test_build_card_writes_formulas_keeps_the_typed_name_and_exempts_only_b1():
    client = FakeClient(tabs=("Edge Finder", "EdgeRaw", "Exposure", "Player"), typed="Wide One")
    lines = sheet_player_card.build_card(client, cfg())
    written = client.named("write_tab")[0]
    assert written[1] == "Player" and written[2][0][1] == "Wide One"
    assert written[2][0][7].startswith("=IFERROR(MATCH($B$1,EdgeRaw!$B$2:$B,0)")
    (validation,) = client.named("set_range_dropdown_validation")
    assert validation[1:3] == ("Player", "B1")
    assert validation[3]["source"] == "EdgeRaw!$B$2:$B" and validation[3]["strict"] is False
    (protect,) = client.named("protect_sheet")
    assert protect[1] == "Player" and protect[2]["unprotected_ranges"] == ["B1"]
    assert client.named("clear_protected_ranges")
    placed = client.named("set_tab_properties")[-1]
    assert placed[1] == "Player" and placed[2]["index"] == 1  # right after Edge Finder (index 0)
    assert any("placed right after Edge Finder" in line for line in lines)


def test_build_card_on_a_new_sheet_starts_with_b1_empty():
    client = FakeClient(typed="")
    sheet_player_card.build_card(client, cfg())
    assert client.named("write_tab")[0][2][0][1] == ""


def test_build_card_without_an_exposure_tab_says_so_on_the_card():
    client = FakeClient(tabs=("Edge Finder", "EdgeRaw"))
    sheet_player_card.build_card(client, cfg())
    rows = client.named("write_tab")[0][2]
    assert any("(no Exposure tab)" in str(cell) for row in rows for cell in row)


def test_protect_workbook_carves_b1_out_of_the_player_tab_and_protects_the_raw_tabs(monkeypatch):
    client = FakeClient(tabs=("Player", "PlayerCardRaw", "PlayerCardRows", "Lineups"))
    protect_workbook(client, lineups_tab="Lineups")
    protected = {c[1]: c[2] for c in client.named("protect_sheet")}
    assert protected["Player"]["unprotected_ranges"] == ["B1"]
    assert "unprotected_ranges" not in protected["PlayerCardRaw"]
    assert "PlayerCardRows" in protected
    assert "not present" in protect_player_card(FakeClient(tabs=()))


def test_write_raw_hides_a_new_tab_and_leaves_an_existing_one_where_it_is():
    client = FakeClient(tabs=("PlayerCardRaw",))
    raw = pc.raw_frame(edge())
    rows = pc.rows_frame([], [], props_note="n", signals_note="s")
    line = sheet_player_card.write_raw(client, raw, rows)
    assert "1 players" in line
    hidden = [c for c in client.named("set_tab_properties") if c[2].get("hidden")]
    assert [c[1] for c in hidden] == ["PlayerCardRows"]  # PlayerCardRaw existed already
    header = client.named("write_tab")[0][2][0]
    assert header == pc.RAW_COLUMNS


def test_refresh_is_silent_without_the_tab_and_never_raises(monkeypatch):
    assert sheet_player_card.refresh_raw_if_present(FakeClient(), cfg(), edge(), 7, 2026) == (True, "")

    def boom(*a, **k):
        raise SheetsError("quota")

    monkeypatch.setattr(sheet_player_card, "refresh_raw", boom)
    ok, line = sheet_player_card.refresh_raw_if_present(FakeClient(tabs=("Player",)), cfg(), edge(), 7, 2026)
    assert not ok and "quota" in line and "dfs setup player-card" in line


def test_gather_raw_fails_soft_per_part_and_names_what_is_missing(monkeypatch):
    def load(name):
        raise FileNotFoundError(name)

    monkeypatch.setattr(store, "load_current", load)
    monkeypatch.setattr(
        sheet_player_card, "_props", lambda e, w: (_ for _ in ()).throw(ValueError("bad pull"))
    )
    raw, rows, missing = sheet_player_card.gather_raw(edge(), 7, 2026)
    assert len(raw) == 1 and raw["Sleeper"].isna().all()
    assert {"sleeper", "fantasypros", "player_log"} <= set(missing) and any("props" in m for m in missing)
    meta = rows.set_index("Key")["A"]
    assert meta["meta|props"] == "Props could not be read."
    assert "No Model Check records saved yet" in meta["meta|signals"]


def test_gather_raw_quotes_the_saved_signal_records(monkeypatch, tmp_path):
    table = pd.DataFrame(
        {
            "Signal": ["USAGE↑"],
            "n": [62],
            "VsProj": [0.0],
            "VsCal": [0.5],
            "HitProj": [0.4],
            "HitCal": [0.42],
            "Thin": [False],
            "AsOf": ["2026-10-21"],
        }
    )
    table.to_csv(results_update.signal_table_path(2026), index=False)
    monkeypatch.setattr(store, "load_current", lambda name: (_ for _ in ()).throw(FileNotFoundError(name)))
    monkeypatch.setattr(sheet_player_card, "_props", lambda e, w: ([], "No props pulled for week 7 yet."))
    e = edge().assign(Edge="USAGE↑", ProjPts=15.0, **{"xFP/G": 18.0})
    _raw, rows, _missing = sheet_player_card.gather_raw(e, 7, 2026)
    sig = rows.set_index("Key").loc["wide one|sig|1"]
    assert sig["A"] == "USAGE↑" and sig["C"].startswith("n=62")
    assert "as of 2026-10-21" in rows.set_index("Key").loc["meta|signals", "A"]


def test_props_never_show_another_weeks_pull(monkeypatch):
    from dfs import props_edge

    stamp = pd.Timestamp("2026-10-17T17:21:00Z").to_pydatetime()
    monkeypatch.setattr(props_edge, "load_pulls", lambda: [(stamp, {"week": 6, "events": []})])
    rows, note = sheet_player_card._props(edge(), 7)
    assert rows == [] and "No props pulled for week 7" in note
