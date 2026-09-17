import pytest

from dfs.sources.tffb_sos import (
    POSITION_PARAMS,
    TffbSosFetchError,
    TffbSosSource,
    _current_week_per_page,
    _row_to_record,
    _select_week_and_read_rows,
)


def _row(**overrides):
    base = {
        "team": "CIN",
        "name": "Cincinnati Bengals",
        "overall_rank": 1,
        "opponent_avg": 38.66,
        "week_2_opponent": "HOU",
    }
    base.update(overrides)
    return base


class FakePage:
    """Just enough of Playwright's Page for the pure-logic-adjacent
    helpers this module calls -- no real browser involved."""

    def __init__(self, *, label: str = "Weeks 2-18", table_data: list[dict] | None = None):
        self._label = label
        self._table_data = table_data if table_data is not None else [_row()]
        self.clicks: list[str] = []
        self.evaluated: list[str] = []

    def text_content(self, selector: str) -> str:
        assert selector == ".ffb-filters--button"
        return self._label

    def click(self, selector: str) -> None:
        self.clicks.append(selector)

    def wait_for_timeout(self, ms: int) -> None:
        pass

    def evaluate(self, script: str):
        self.evaluated.append(script)
        return self._table_data


def test_position_params_dst_uses_d_not_dst():
    # Confirmed live against the D/ST tab's own resulting URL.
    assert POSITION_PARAMS["DST"] == "D"
    assert POSITION_PARAMS["QB"] == "QB"


def test_tffb_sos_source_rejects_unknown_position():
    with pytest.raises(ValueError, match="Unknown position"):
        TffbSosSource("K")


def test_tffb_sos_source_name_is_derived_from_position():
    assert TffbSosSource("QB").name == "sos_qb"
    assert TffbSosSource("DST").name == "sos_dst"


def test_current_week_per_page_parses_the_lower_bound_of_a_range():
    # TFFB's own default label, e.g. "Weeks 2-18" -- the page's own
    # judgment of "current," not nfl_calendar.current_week().
    page = FakePage(label="Weeks 2-18")
    assert _current_week_per_page(page) == 2


def test_current_week_per_page_parses_a_single_week_label():
    page = FakePage(label="Week 5")
    assert _current_week_per_page(page) == 5


def test_current_week_per_page_raises_when_unparseable():
    page = FakePage(label="")
    with pytest.raises(TffbSosFetchError, match="Could not parse"):
        _current_week_per_page(page)


def test_select_week_and_read_rows_clicks_filter_then_the_specific_week():
    page = FakePage()
    rows = _select_week_and_read_rows(page, 2)
    assert page.clicks == [".ffb-filters--button", 'a.week-button[data-week="2"]']
    assert rows == [_row()]


def test_row_to_record_maps_fields_and_drops_pae():
    record = _row_to_record(_row(), week=2, position="QB")
    assert record == {
        "Team": "Cincinnati Bengals",
        "Team.1": "CIN",
        "Rank": 1,
        "FPA": 38.66,
        "Opp": "HOU",
    }
    assert "PAE" not in record


def test_row_to_record_raises_on_missing_required_field():
    bad = _row()
    del bad["overall_rank"]
    with pytest.raises(TffbSosFetchError, match="missing field"):
        _row_to_record(bad, week=2, position="QB")


def test_row_to_record_defaults_opponent_to_blank_when_absent():
    row = _row()
    del row["week_2_opponent"]
    record = _row_to_record(row, week=2, position="QB")
    assert record["Opp"] == ""
