from dfs.sheet_style import POOL_RAW_ROWS
from dfs.sheet_typo_guard import add_lineups_typo_guard


class FakeTypoGuardClient:
    def __init__(self, *, present: bool = True):
        self._present = present
        self.validation_calls: list[tuple[str, str, bool]] = []

    def tab_exists(self, tab_name: str) -> bool:
        return self._present

    def set_range_dropdown_validation(
        self, tab_name: str, a1_range: str, *, source: str, strict: bool = False
    ) -> None:
        self.validation_calls.append((a1_range, source, strict))


def test_skips_missing_tab():
    client = FakeTypoGuardClient(present=False)
    result = add_lineups_typo_guard(client, "Lineups", [(12, 21)])
    assert result == "Lineups: not present -- skipped"
    assert client.validation_calls == []


def test_applies_non_strict_validation_to_every_block_column_a():
    client = FakeTypoGuardClient()
    add_lineups_typo_guard(client, "Lineups", [(12, 21), (25, 34)])

    assert [c[0] for c in client.validation_calls] == ["A12:A21", "A25:A34"]
    for _rng, _source, strict in client.validation_calls:
        assert strict is False  # non-strict: warn, don't hard-block


def test_validates_against_player_pool_raws_name_column():
    client = FakeTypoGuardClient()
    add_lineups_typo_guard(client, "Lineups", [(12, 21)])

    _rng, source, _strict = client.validation_calls[0]
    assert source == f"PlayerPoolRaw!$A$2:$A${POOL_RAW_ROWS}"
