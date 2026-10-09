import re

import pytest

from dfs.derived import EDGE_COLUMNS
from dfs.player_join import normalize_name
from dfs.sheet_names import (
    ALIAS_TAB,
    NAME_KEY_HEADER,
    TEAMS,
    dst_alias_rows,
    norm_expr,
    resolve_name_expr,
    resolve_typed_name,
)


def _sheet_norm_model(text: str) -> str:
    """A Python model of `norm_expr`'s formula, built from the SAME regex
    strings the formula contains, so a change to either shows up here. RE2
    (Sheets) and `re` agree on these simple classes."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9_\s]", "", text)
    text = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def test_formula_contains_the_same_regexes_the_model_uses():
    f = norm_expr("$A5")
    for fragment in ('"[^a-z0-9_\\s]"', '"\\b(jr|sr|ii|iii|iv|v)\\b"', '"\\s+"'):
        assert fragment in f


@pytest.mark.parametrize(
    "typed",
    [
        "Kenneth Walker III",
        "kenneth walker",
        "  AJ Brown ",
        "A.J. Brown",
        "Marvin Harrison Jr.",
        "Marvin Harrison",
        "Ja'Marr Chase",
        "Ja’Marr Chase",
        "Amon-Ra St. Brown",
        "Michael Pittman Jr",
        "Odell   Beckham  Jr.",
        "D'Andre Swift",
        "Aaron Jones Sr.",
        "Chiefs D/ST",
        "KC DST",
        "",
    ],
)
def test_sheet_normalization_matches_python_normalize_name(typed):
    assert _sheet_norm_model(typed) == normalize_name(typed)


def test_resolve_expression_uses_alias_tab_then_namekey_and_falls_back_to_the_typed_value():
    f = resolve_name_expr("$A5", "EdgeRaw")
    assert f.startswith("IFERROR(INDEX(EdgeRaw!")
    assert f"{ALIAS_TAB}!$A:$B" in f
    assert f.endswith(",$A5)")  # unmatched -> exactly what was typed, so nothing regresses
    assert NAME_KEY_HEADER in EDGE_COLUMNS  # the lookup column really exists on EdgeRaw


EDGE_NAMES = ["Kenneth Walker III", "Marvin Harrison Jr.", "AJ Brown", "Chiefs", "49ers", "Rams", "Giants"]


@pytest.mark.parametrize(
    "typed,expected",
    [
        ("kenneth walker", "Kenneth Walker III"),
        ("Marvin Harrison", "Marvin Harrison Jr."),
        ("  AJ Brown ", "AJ Brown"),
        ("chiefs", "Chiefs"),
        ("KC DST", "Chiefs"),
        ("KC", "Chiefs"),
        ("Kansas City", "Chiefs"),
        ("Kansas City Chiefs", "Chiefs"),
        ("Chiefs D/ST", "Chiefs"),
        ("Chiefs DST", "Chiefs"),
        ("SF", "49ers"),
        ("San Francisco 49ers", "49ers"),
        ("LAR", "Rams"),
        ("LA Rams", None),  # not a spelling we invent
        ("Los Angeles Rams", "Rams"),
        ("NYG", "Giants"),
        ("Nobody Real", None),
        ("", None),
    ],
)
def test_resolve_typed_name(typed, expected):
    assert resolve_typed_name(typed, EDGE_NAMES) == expected


def test_alias_rows_cover_all_32_teams_and_never_map_two_teams_to_one_alias():
    rows = dst_alias_rows()
    keys = [r[0] for r in rows]
    assert len(keys) == len(set(keys))
    assert {r[2] for r in rows} == set(TEAMS)  # every team has at least one alias
    by_key = dict((r[0], r[2]) for r in rows)
    assert "los angeles" not in by_key and "new york" not in by_key  # ambiguous bare cities skipped
    assert by_key["kc"] == "KC" and by_key["kansas city chiefs"] == "KC" and by_key["chiefs dst"] == "KC"


def test_rebuilding_the_alias_tab_carries_the_apps_script_version_stamp_across():
    from dfs.sheet_names import ALIAS_TAB, STAMP_CELL, build_name_alias_tab

    class Client:
        def __init__(self, stamp):
            self.stamp, self.calls = stamp, []

        def read_range(self, tab, rng):
            assert (tab, rng) == (ALIAS_TAB, STAMP_CELL)
            return [[self.stamp]] if self.stamp else []

        def write_tab(self, tab, rows):
            self.calls.append("write")

        def update_range(self, tab, rng, rows):
            self.calls.append(("restore", rng, rows))

    kept = Client("2")
    build_name_alias_tab(kept)
    assert kept.calls == ["write", ("restore", STAMP_CELL, [["2"]])]
    fresh = Client("")
    build_name_alias_tab(fresh)
    assert fresh.calls == ["write"]  # nothing to carry, nothing invented
