from dfs.sheet_tab_removal import RETIRED_TABS, clean_instructions_tab, remove_retired_tabs


class FakeClient:
    def __init__(self, present_tabs, instructions_rows=None):
        self._present = set(present_tabs)
        self._instructions_rows = instructions_rows or []
        self.deleted: list[str] = []
        self.hidden: list[str] = []
        self.deleted_rows: list[tuple[str, int]] = []

    def tab_exists(self, tab_name):
        return tab_name in self._present

    def delete_tab(self, tab_name):
        self._present.discard(tab_name)
        self.deleted.append(tab_name)

    def set_tab_properties(self, tab_name, *, hidden=None):
        if hidden:
            self.hidden.append(tab_name)

    def read_range(self, tab_name, a1_range):
        return [[text] if text else [] for text in self._instructions_rows]

    def delete_rows(self, tab_name, *, at_row, count):
        self.deleted_rows.append((tab_name, at_row))
        del self._instructions_rows[at_row - 1 : at_row - 1 + count]


def test_remove_retired_tabs_deletes_every_retired_tab_in_delete_mode():
    client = FakeClient(RETIRED_TABS)
    results = remove_retired_tabs(client, mode="delete")

    assert client.deleted == RETIRED_TABS
    assert client.hidden == []
    assert all("deleted" in r for r in results[:-1])


def test_remove_retired_tabs_hides_every_retired_tab_in_hide_mode():
    client = FakeClient(RETIRED_TABS)
    results = remove_retired_tabs(client, mode="hide")

    assert client.hidden == RETIRED_TABS
    assert client.deleted == []
    assert all("hidden" in r for r in results[:-1])


def test_remove_retired_tabs_skips_a_tab_already_gone():
    client = FakeClient(["Scratch"])
    results = remove_retired_tabs(client, mode="delete")

    assert client.deleted == ["Scratch"]
    assert any("already absent" in r for r in results)
    # +1 for the trailing Instructions-cleanup result.
    assert len(results) == len(RETIRED_TABS) + 1


def test_remove_retired_tabs_also_cleans_instructions_when_present():
    client = FakeClient([*RETIRED_TABS, "Instructions"], instructions_rows=["Board", "Scratch", "DK Upload"])
    results = remove_retired_tabs(client, mode="delete")

    assert "Board" in client._instructions_rows
    assert "DK Upload" in client._instructions_rows
    assert "Scratch" not in client._instructions_rows
    assert any("removed 1 row" in r for r in results)


def test_clean_instructions_tab_deletes_single_tab_rows():
    client = FakeClient([], instructions_rows=["Board", "Scratch", "DK Upload"])
    client._present.add("Instructions")

    result = clean_instructions_tab(client)

    assert client._instructions_rows == ["Board", "DK Upload"]
    assert "removed 1 row" in result


def test_clean_instructions_tab_deletes_a_combined_row_only_if_every_token_is_retired():
    client = FakeClient(
        [],
        instructions_rows=["Board", "DKLineupsRaw / DKLineupsFinal", "GPPin", "SoSQB / SoSRB"],
    )
    client._present.add("Instructions")

    result = clean_instructions_tab(client)

    # "SoSQB / SoSRB" survives -- neither token is a retired tab.
    assert client._instructions_rows == ["Board", "SoSQB / SoSRB"]
    assert "removed 2 row" in result


def test_clean_instructions_tab_skips_cleanly_when_nothing_to_remove():
    client = FakeClient([], instructions_rows=["Board", "DK Upload"])
    client._present.add("Instructions")

    result = clean_instructions_tab(client)

    assert client._instructions_rows == ["Board", "DK Upload"]
    assert "no retired-tab rows found" in result


def test_clean_instructions_tab_skips_when_tab_absent():
    client = FakeClient([])

    result = clean_instructions_tab(client)

    assert "not present" in result
