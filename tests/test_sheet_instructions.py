from dfs.models import ROSTER_SLOTS
from dfs.sheet_instructions import (
    _DOC_LINK_ROWS,
    _GENERAL_ROWS,
    _TAB_ROWS,
    INSTRUCTIONS_TAB,
    build_instructions_tab,
)
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS, PLAYER_POOL_NAME_BLOCKS


class FakeClient:
    def __init__(self, *, present: bool = True):
        self._present = present
        self.update_calls: list[tuple[str, str, list[list]]] = []

    def tab_exists(self, tab_name: str) -> bool:
        return self._present

    def update_range(self, tab_name: str, a1_range: str, rows: list[list]) -> None:
        self.update_calls.append((tab_name, a1_range, rows))


def _cells(client: FakeClient) -> dict[str, list]:
    return {a1: rows[0] for _, a1, rows in client.update_calls}


def test_skips_cleanly_when_tab_is_absent():
    client = FakeClient(present=False)
    result = build_instructions_tab(client)
    assert "not present" in result
    assert client.update_calls == []


def test_writes_title_and_every_general_row():
    client = FakeClient()
    build_instructions_tab(client)
    cells = _cells(client)

    assert cells["A1:B1"] == ["How this sheet works", ""]
    assert cells["A2:B2"][0] == "THE ONE THING TO KNOW"
    assert cells["A5:B5"][0] == "Reading the colours"
    # Row 6 (the divider before "Tab-by-tab reference") is never touched.
    assert "A6:B6" not in cells


def test_writes_the_tab_header_and_every_tab_row_at_the_expected_positions():
    client = FakeClient()
    build_instructions_tab(client)
    cells = _cells(client)

    assert cells["A7:B7"] == ["Tab-by-tab reference", "Columns / what to do"]
    assert cells["A8:B8"][0] == "Board"
    # 18 tab rows, 8 through 25.
    assert cells["A25:B25"][0] == "Results"


def test_writes_the_doc_links_header_and_all_five_link_rows():
    client = FakeClient()
    build_instructions_tab(client)
    cells = _cells(client)

    assert cells["A26:B26"] == ["Full documentation", _DOC_LINK_ROWS[0]]
    assert cells["A27:B27"] == ["", _DOC_LINK_ROWS[1]]
    assert cells["A30:B30"] == ["", _DOC_LINK_ROWS[4]]


def test_player_pool_row_derives_caps_and_column_letters_not_hardcoded():
    # Fix 4's own incident: Player Pool's row used to hardcode stale
    # column letters (O/Z/AA from a pre-Phase-3 layout) after the real
    # columns had moved. This asserts the generated text is built from
    # PLAYER_POOL_NAME_BLOCKS/PLAYER_POOL_COLUMN_ORDER, not retyped.
    player_pool_text = next(body for name, body in _TAB_ROWS if name == "Player Pool")
    caps = [end - start + 1 for start, end in PLAYER_POOL_NAME_BLOCKS]
    assert f"QB {caps[0]}" in player_pool_text
    assert f"RB {caps[1]}" in player_pool_text
    assert f"WR {caps[2]}" in player_pool_text
    assert f"TE {caps[3]}" in player_pool_text
    assert f"DST {caps[4]}" in player_pool_text


def test_lineups_row_derives_roster_slots_and_block_layout_not_hardcoded():
    lineups_text = next(body for name, body in _TAB_ROWS if name == "Lineups")
    assert f"starting at row {LINEUPS_NAME_BLOCKS[0][0]}" in lineups_text
    assert f"{len(ROSTER_SLOTS)}-player roster block" in lineups_text
    assert f"({', '.join(ROSTER_SLOTS)})" in lineups_text
    assert f"{len(LINEUPS_NAME_BLOCKS)} blocks total" in lineups_text


def test_playerpoolraw_row_says_ownstatus_not_the_stale_levbasis():
    # Found by diffing this module's generated content against the live
    # sheet after Fix 4: PlayerPoolRaw's own row was missed during that
    # manual pass and still said "LevBasis" (renamed to OwnStatus, Part
    # 7.9) on the live sheet even after every other row was corrected.
    text = next(body for name, body in _TAB_ROWS if name == "PlayerPoolRaw")
    assert "OwnStatus" in text
    assert "LevBasis" not in text


def test_no_row_mentions_the_retired_pool_picks_or_poolsort_tabs():
    all_text = " ".join(body for _, body in _GENERAL_ROWS) + " ".join(body for _, body in _TAB_ROWS)
    assert "Pool Picks" not in all_text
    assert "PoolSort" not in all_text


def test_build_is_idempotent_full_rewrite():
    client = FakeClient()
    build_instructions_tab(client)
    first_call_count = len(client.update_calls)
    build_instructions_tab(client)
    assert len(client.update_calls) == first_call_count * 2


def test_reports_the_tab_name_it_wrote_to():
    client = FakeClient()
    result = build_instructions_tab(client, tab=INSTRUCTIONS_TAB)
    assert INSTRUCTIONS_TAB in result
    assert "row(s) written" in result
