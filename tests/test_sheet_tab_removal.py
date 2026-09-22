from dfs.sheet_tab_removal import RETIRED_TABS, remove_retired_tabs


class FakeClient:
    def __init__(self, present_tabs):
        self._present = set(present_tabs)
        self.deleted: list[str] = []
        self.hidden: list[str] = []

    def tab_exists(self, tab_name):
        return tab_name in self._present

    def delete_tab(self, tab_name):
        self._present.discard(tab_name)
        self.deleted.append(tab_name)

    def set_tab_properties(self, tab_name, *, hidden=None):
        if hidden:
            self.hidden.append(tab_name)


def test_remove_retired_tabs_deletes_every_retired_tab_in_delete_mode():
    client = FakeClient(RETIRED_TABS)
    results = remove_retired_tabs(client, mode="delete")

    assert client.deleted == RETIRED_TABS
    assert client.hidden == []
    assert all("deleted" in r for r in results)


def test_remove_retired_tabs_hides_every_retired_tab_in_hide_mode():
    client = FakeClient(RETIRED_TABS)
    results = remove_retired_tabs(client, mode="hide")

    assert client.hidden == RETIRED_TABS
    assert client.deleted == []
    assert all("hidden" in r for r in results)


def test_remove_retired_tabs_skips_a_tab_already_gone():
    client = FakeClient(["Scratch"])
    results = remove_retired_tabs(client, mode="delete")

    assert client.deleted == ["Scratch"]
    assert any("already absent" in r for r in results)
    assert len(results) == len(RETIRED_TABS)
