from contextlib import contextmanager
from datetime import date
from html.parser import HTMLParser

import pandas as pd
import pytest

from dfs import slate
from dfs.sources import tffb_gps
from dfs.sources.tffb_gps import (
    GPS_EXPECTED_HEADER,
    TffbGpsFetchError,
    _is_pace_of_play_link,
    parse_gps_csv,
)


def _real_csv(week: int) -> str:
    """Shape confirmed against real Week 2/Week 3 CSVs (Step 0, 2026-09-26):
    title line, group-label banner line, then the real header, then one row
    per team. Two teams (a full game) is enough to exercise the parser."""
    header = ",".join(GPS_EXPECTED_HEADER)
    return (
        f"NFL Pace of Play for Week {week},,,,,,,,,,,,,,,,\r\n"
        f"Week {week},,,,Pace of Play,,,EPA Ranks per FPts,,,,Game Pace Scores,,,,,\r\n"
        f"{header}\r\n"
        ",JAX,Jacksonville Jaguars,29.0,22,55.5,-5.0,7,7,14,15,H,,New England Patriots,46.5,MAIN,2.75\r\n"
        ",NE,New England Patriots,17.5,19,60.0,2.0,20,24,2,17,A,,Jacksonville Jaguars,46.5,MAIN,2.75\r\n"
    )


def test_parse_gps_csv_real_shape():
    df = parse_gps_csv(_real_csv(3))
    assert list(df.columns) == ["Team", "ImpliedTotal", "GPS"]
    assert list(df["Team"]) == ["JAX", "NE"]
    assert list(df["ImpliedTotal"]) == [29.0, 17.5]
    assert list(df["GPS"]) == [2.75, 2.75]


def test_parse_gps_csv_week9_shape_two_preamble_lines_and_proe_without_percent():
    """Week 9 (2026-11-04): the title and group-label lines were merged into ONE
    line, so the header is on line 2, and the column read `PROE` not `PROE %`."""
    header = ",".join(GPS_EXPECTED_HEADER).replace("PROE %", "PROE")
    text = (
        "Week 9,,,,Pace of Play,,,EPA Ranks per FPts,,,,Game Pace Scores,,,,,\n"
        f"{header}\n"
        ",LV,Las Vegas Raiders,20.8,5,63.8,0.2,7,30,27,24,A,,San Francisco 49ers,51.5,MAIN,3.75\n"
        ",SF,San Francisco 49ers,30.8,20,58.4,3.1,2,4,15,3,H,,Las Vegas Raiders,51.5,MAIN,3.75\n"
    )
    df = parse_gps_csv(text)
    assert list(df["Team"]) == ["LV", "SF"]
    assert list(df["ImpliedTotal"]) == [20.8, 30.8]
    assert list(df["GPS"]) == [3.75, 3.75]


def test_parse_gps_csv_accepts_proe_with_percent_on_the_old_shape():
    assert len(parse_gps_csv(_real_csv(3))) == 2  # the 3-line shape still carries `PROE %`


def test_parse_gps_csv_no_header_row_still_raises_drift():
    text = "Week 9,,,,\nsomething,else\nnot,a,header\n,LV,Las Vegas Raiders,20.8\n"
    with pytest.raises(TffbGpsFetchError, match="doesn't match the expected shape"):
        parse_gps_csv(text)


def test_parse_gps_csv_normalizes_team_code():
    text = _real_csv(3).replace(",JAX,", ",JAC,")  # a known drift normalize_team already fixes
    df = parse_gps_csv(text)
    assert df.iloc[0]["Team"] == "JAX"


def test_parse_gps_csv_raises_on_header_drift():
    text = _real_csv(3).replace("Neutral Pace Rk", "Something Else")
    with pytest.raises(TffbGpsFetchError, match="doesn't match the expected shape"):
        parse_gps_csv(text)


def test_parse_gps_csv_coerces_non_numeric_to_nan():
    text = _real_csv(3).replace("29.0", "N/A", 1)
    df = parse_gps_csv(text)
    assert pd.isna(df.iloc[0]["ImpliedTotal"])


@pytest.mark.parametrize(
    ("href", "text", "week", "expected"),
    [
        ("https://x.com/dfs/pace-of-play-matchups-stacks-for-week-2-fantasy-football/", "", 2, True),
        ("https://x.com/dfs/pace-of-play-matchups-stacks-for-week-3-fantasy-football/", "", 3, True),
        ("https://x.com/dfs/nfl-dfs-pace-of-play-stacks-for-week-1-fantasy-football/", "", 1, True),
        # Week 1 must never match week 10-19 -- the real bug this guards against.
        ("https://x.com/dfs/pace-of-play-matchups-stacks-for-week-10-fantasy-football/", "", 1, False),
        ("https://x.com/dfs/pace-of-play-matchups-stacks-for-week-11-fantasy-football/", "", 1, False),
        # Wrong week entirely.
        ("https://x.com/dfs/pace-of-play-matchups-stacks-for-week-2-fantasy-football/", "", 3, False),
        # Right week, wrong article (no "pace of play" anywhere).
        ("https://x.com/dfs/best-plays-for-dfs-week-2-fantasy-football/", "", 2, False),
        # Matches via link text instead of href.
        ("https://x.com/some-slug/", "Pace of Play: Matchups & Stacks for Week 2", 2, True),
    ],
)
def test_is_pace_of_play_link(href, text, week, expected):
    assert _is_pace_of_play_link(href, text, week) is expected


def test_a_missing_article_is_pending_but_every_other_fetch_problem_is_a_real_error():
    from dfs.sources.base import SourcePending
    from dfs.sources.tffb_gps import TffbGpsPending

    assert issubclass(TffbGpsPending, SourcePending) and issubclass(TffbGpsPending, TffbGpsFetchError)
    assert not issubclass(
        TffbGpsFetchError, SourcePending
    )  # a missing CSV link, a 500, a drifted header fail


# -- a named slate (Thanksgiving, a Saturday, Christmas, the playoffs) --------------------------------------

_SITE = "https://www.thefantasyfootballers.com"
THANKSGIVING = f"{_SITE}/dfs/pace-of-play-dfs-stacks-for-thanksgiving-day-slate/"
WEEK_11 = f"{_SITE}/dfs/pace-of-play-matchups-stacks-for-week-11-fantasy-football/"
WEEK_12 = f"{_SITE}/dfs/pace-of-play-matchups-stacks-for-week-12-fantasy-football/"
WEEK_12_THANKSGIVING = f"{_SITE}/dfs/pace-of-play-stacks-for-week-12-thanksgiving-slate/"
WEEK_16 = f"{_SITE}/dfs/pace-of-play-matchups-stacks-for-week-16-fantasy-football/"
WEEK_16_SATURDAY = f"{_SITE}/dfs/pace-of-play-dfs-stacks-for-week-16-saturday-slate/"
SATURDAY_NO_WEEK = f"{_SITE}/dfs/pace-of-play-dfs-stacks-for-saturday-slate/"
CHRISTMAS = f"{_SITE}/dfs/pace-of-play-dfs-stacks-for-christmas-day-slate/"
WILD_CARD = f"{_SITE}/dfs/pace-of-play-dfs-stacks-for-wild-card-round/"
WILD_CARD_SATURDAY = f"{_SITE}/dfs/pace-of-play-dfs-stacks-for-wild-card-saturday/"
DIVISIONAL = f"{_SITE}/dfs/pace-of-play-dfs-stacks-for-divisional-round/"
DFS_PASS_TOOL = f"{_SITE}/2024-ultimate-dfs-pass/dfs-pass-pace-of-play/"

# The real listing's shape (2026-11-24): each article twice, as a title link and as an image card whose text
# is the lazy-load <img> markup (a file name and all), plus the DFS Pass "Pace of Play" tool page.
_CARD = (
    '<a href="{url}">&lt;img loading="lazy" width="1024" src="//s26212.pcdn.co/lazy_placeholder.gif" '
    'data-src="https://s26212.pcdn.co/wp-content/uploads/{img}.jpg"&gt;</a>'
)


def _listing(*articles: tuple[str, str], img: str = "Thursday-Thanksgiving-Monday") -> str:
    body = [f'<a href="{DFS_PASS_TOOL}">Pace of Play</a>']
    for url, title in articles:
        body.append(f'<a href="{url}">{title}</a>')
        body.append(_CARD.format(url=url, img=img))
    return "<html><body>" + "\n".join(body) + "</body></html>"


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[list[str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href, self._text = dict(attrs).get("href", ""), []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append([self._href, "".join(self._text).strip()])
            self._href = None


class _FakePage:
    """Playwright's `Page`, as `_find_article_url` uses it, over hand-made listing pages (no fetch)."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages, self.visited, self._html = pages, [], ""

    def goto(self, url, **_):
        self.visited.append(url)
        self._html = self.pages.get(url, "<html></html>")

    def eval_on_selector_all(self, selector, _script):
        parser = _Links()
        parser.feed(self._html)
        if selector == "a":
            return parser.links
        return [href for href, _ in parser.links if href.endswith(".csv")]

    def close(self):
        pass


def _find(html: str, week: int, key: str = "main", day: date | None = None, round_no: int | None = None):
    page = _FakePage({tffb_gps.LISTING_URL: html})
    return tffb_gps._find_article_url(page, week, key=key, slate_day=day, round_no=round_no)


THANKSGIVING_DAY = date(2026, 11, 26)
BLACK_FRIDAY = date(2026, 11, 27)
WEEK_16_SAT = date(2026, 12, 26)
CHRISTMAS_DAY = date(2026, 12, 25)  # a Friday, Week 16


def test_the_slate_days_are_the_calendar_days_the_tests_use():
    assert slate.slate_date("thu", 2026, 12) == THANKSGIVING_DAY
    assert slate.slate_date("sat", 2026, 16) == WEEK_16_SAT
    assert slate.slate_date("fri", 2026, 16) == CHRISTMAS_DAY


def test_thanksgiving_listing_each_article_matches_only_its_own_slate():
    """Today's real listing: Thanksgiving's article (no week number) and last week's main article."""
    html = _listing(
        (THANKSGIVING, "Pace of Play &amp; DFS Stacks for Thanksgiving Day Slate"),
        (WEEK_11, "Pace of Play: Matchups &amp; Stacks for Week 11"),
    )
    assert _find(html, 12, "thu", THANKSGIVING_DAY) == THANKSGIVING
    assert _find(html, 12) is None  # main: Week 12's isn't up, and Thanksgiving's is never main's
    assert _find(html, 11) == WEEK_11  # the main match is today's, whatever the cards' image names say
    assert _find(html, 12, "fri", BLACK_FRIDAY) is None
    assert _find(html, 12, "sat", date(2026, 11, 28)) is None


def test_week_12_main_and_thanksgiving_never_cross():
    html = _listing(
        (WEEK_12_THANKSGIVING, "Pace of Play &amp; DFS Stacks for Week 12 Thanksgiving Slate"),
        (WEEK_12, "Pace of Play: Matchups &amp; Stacks for Week 12"),
    )
    assert _find(html, 12) == WEEK_12
    assert _find(html, 12, "thu", THANKSGIVING_DAY) == WEEK_12_THANKSGIVING


def test_thanksgiving_is_not_a_thursday_slate_in_another_month():
    html = _listing((THANKSGIVING, "Pace of Play &amp; DFS Stacks for Thanksgiving Day Slate"))
    assert _find(html, 3, "thu", date(2026, 9, 24)) is None


def test_saturday_and_christmas_match_only_their_own_slates():
    html = _listing(
        (CHRISTMAS, "Pace of Play &amp; DFS Stacks for Christmas Day Slate"),
        (WEEK_16_SATURDAY, "Pace of Play &amp; DFS Stacks for Week 16 Saturday Slate"),
        (WEEK_16, "Pace of Play: Matchups &amp; Stacks for Week 16"),
        img="generic",
    )
    assert _find(html, 16) == WEEK_16
    assert _find(html, 16, "sat", WEEK_16_SAT) == WEEK_16_SATURDAY
    assert _find(html, 16, "fri", CHRISTMAS_DAY) == CHRISTMAS


def test_last_weeks_saturday_article_is_not_this_weeks():
    html = _listing((WEEK_16_SATURDAY, "Pace of Play &amp; DFS Stacks for Week 16 Saturday Slate"), img="x")
    assert _find(html, 17, "sat", date(2027, 1, 2)) is None


def test_a_saturday_article_with_no_week_matches_the_saturday_slate_only():
    html = _listing((SATURDAY_NO_WEEK, "Pace of Play &amp; DFS Stacks for Saturday Slate"), img="x")
    assert _find(html, 16, "sat", WEEK_16_SAT) == SATURDAY_NO_WEEK
    assert _find(html, 16) is None
    assert _find(html, 16, "fri", CHRISTMAS_DAY) is None


def test_playoff_articles_match_their_round_and_slate_only():
    html = _listing(
        (WILD_CARD_SATURDAY, "Pace of Play &amp; DFS Stacks for Wild Card Saturday"),
        (WILD_CARD, "Pace of Play &amp; DFS Stacks for Wild Card Round"),
        img="x",
    )
    sat = date(2027, 1, 16)
    assert _find(html, 18, round_no=1) == WILD_CARD  # the Sunday main: the round's name, no week number
    assert _find(html, 18, "sat", sat, round_no=1) == WILD_CARD_SATURDAY
    assert _find(html, 18) is None  # the regular season's main never takes a round's article
    assert _find(html, 18, round_no=2) is None  # last round's article is not the Divisional round's
    assert _find(html, 18, "sat", sat, round_no=2) is None


def test_the_divisional_round_is_found_with_last_rounds_article_still_listed():
    html = _listing(
        (DIVISIONAL, "Pace of Play &amp; DFS Stacks for the Divisional Round"),
        (WILD_CARD, "Pace of Play &amp; DFS Stacks for Wild Card Round"),
        img="x",
    )
    assert _find(html, 18, round_no=2) == DIVISIONAL
    assert _find(html, 18, round_no=1) == WILD_CARD


def test_a_regular_season_main_title_may_say_divisional():
    url = f"{_SITE}/dfs/pace-of-play-matchups-stacks-for-week-18-divisional-showdowns/"
    assert tffb_gps._is_pace_of_play_link(url, "", 18) is True


def test_the_listing_search_reads_page_two():
    page = _FakePage(
        {
            tffb_gps.LISTING_URL: _listing((WEEK_11, "Pace of Play: Matchups &amp; Stacks for Week 11")),
            f"{tffb_gps.LISTING_URL}page/2/": _listing((THANKSGIVING, "Thanksgiving Day Slate"), img="x"),
        }
    )
    assert tffb_gps._find_article_url(page, 12, key="thu", slate_day=THANKSGIVING_DAY) == THANKSGIVING
    assert page.visited == [tffb_gps.LISTING_URL, f"{tffb_gps.LISTING_URL}page/2/"]


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 11, 26), {"thanksgiving"}),
        (date(2026, 11, 27), {"black friday"}),
        (date(2026, 11, 19), set()),  # the third Thursday
        (date(2025, 11, 27), {"thanksgiving"}),
        (date(2026, 12, 25), {"christmas"}),
        (date(2027, 12, 25), {"christmas"}),  # a Saturday
        (date(2026, 12, 24), set()),
    ],
)
def test_holidays_on(day, expected):
    assert tffb_gps.holidays_on(day) == expected


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 11, 24), None),
        (date(2027, 1, 9), None),  # week 18's Saturday
        (date(2027, 1, 16), 1),  # Wild Card Saturday
        (date(2027, 1, 24), 2),
        (date(2027, 1, 31), 3),
        (date(2027, 2, 7), None),  # the Super Bowl's bye week
        (date(2027, 2, 14), 5),
    ],
)
def test_playoff_round(day, expected):
    assert tffb_gps.playoff_round(day, 2026) == expected


def _gps(*teams: str) -> pd.DataFrame:
    return pd.DataFrame({"Team": list(teams), "ImpliedTotal": 24.0, "GPS": 3.0})


THANKSGIVING_TEAMS = ["BUF", "CHI", "DAL", "DET", "KC", "PHI"]


def test_a_six_team_csv_covers_a_six_team_slate():
    assert tffb_gps.check_slate_teams(_gps(*THANKSGIVING_TEAMS), THANKSGIVING_TEAMS) == []


def test_a_slate_team_missing_from_the_csv_is_named():
    assert tffb_gps.check_slate_teams(_gps("BUF", "CHI", "DAL", "DET"), THANKSGIVING_TEAMS) == ["KC", "PHI"]


def test_a_csv_with_none_of_the_slates_teams_is_another_slates_article():
    with pytest.raises(TffbGpsFetchError, match="another slate's article"):
        tffb_gps.check_slate_teams(_gps("LV", "SF"), THANKSGIVING_TEAMS)


def test_no_slate_teams_known_checks_nothing():
    assert tffb_gps.check_slate_teams(_gps("LV", "SF"), []) == []


def test_the_main_slates_32_team_csv_has_no_missing_sunday_team():
    assert tffb_gps.check_slate_teams(_gps("BUF", "CHI", "LV", "SF"), ["LV", "SF"]) == []


def test_gps_url_must_be_a_tffb_page():
    from pydantic import ValidationError

    from dfs.config import SlateSheetConfig

    assert SlateSheetConfig(sheet_id="x", gps_url=THANKSGIVING).gps_url == THANKSGIVING
    assert SlateSheetConfig(sheet_id="x").gps_url is None
    with pytest.raises(ValidationError, match="TFFB article"):
        SlateSheetConfig(sheet_id="x", gps_url="https://example.com/pace-of-play.csv")


# The real Thanksgiving article's CSV (2026-11-24) is the whole week's, every team tagged with its slate.
WEEK_12_SLATES = {**dict.fromkeys(THANKSGIVING_TEAMS, "THANKS"), "DEN": "BLACK", "PIT": "BLACK"}
WEEK_12_SLATES.update(dict.fromkeys(["JAX", "CIN", "HOU", "WAS", "SF", "MIN", "NYG", "TB"], "MAIN"))


def _week_csv() -> str:
    header = ",".join(GPS_EXPECTED_HEADER).replace("PROE %", "PROE")
    row = ",{t},{t} Team,24.5,10,60.0,1.0,5,5,5,5,H,,Opp,47.5,{label},3.5\r\n"
    rows = "".join(row.format(t=t, label=label) for t, label in WEEK_12_SLATES.items())
    return (
        "NFL Pace of Play for Week 12,,,,,,,,,,,,,,,,\r\n"
        "Week 12,,,,Pace of Play,,,EPA Ranks per FPts,,,,Game Pace Scores,,,,,\r\n"
        f"{header}\r\n{rows}"
    )


def _fake_tffb(monkeypatch, listing_html: str, article_url: str, csv_text: str) -> _FakePage:
    page = _FakePage(
        {
            tffb_gps.LISTING_URL: listing_html,
            article_url: f'<html><a href="{_SITE}/wp-content/uploads/gps.csv">CSV</a></html>',
        }
    )

    class _Response:
        status = 200

        def body(self):
            return csv_text.encode("utf-8")

    class _Context:
        class request:  # noqa: N801 - Playwright's attribute name
            @staticmethod
            def get(_url):
                return _Response()

        def new_page(self):
            return page

    @contextmanager
    def _persistent_context(profile, headless):
        assert profile == "tffb" and headless
        yield _Context()

    monkeypatch.setattr(tffb_gps, "persistent_context", _persistent_context)
    return page


def test_fetch_on_the_thanksgiving_slate_finds_its_article_and_keeps_six_teams(monkeypatch, caplog):
    html = _listing(
        (THANKSGIVING, "Pace of Play &amp; DFS Stacks for Thanksgiving Day Slate"),
        (WEEK_11, "Pace of Play: Matchups &amp; Stacks for Week 11"),
    )
    page = _fake_tffb(monkeypatch, html, THANKSGIVING, _week_csv())
    monkeypatch.setattr(tffb_gps, "override_url", lambda key, week: None)
    monkeypatch.setattr(tffb_gps, "_slate_teams", lambda: THANKSGIVING_TEAMS)
    with slate.active("thu"):
        df = tffb_gps.TffbGpsSource().fetch(tffb_gps.SyncContext(week=12, season=2026))
    assert sorted(df["Team"]) == THANKSGIVING_TEAMS  # the week's 16 rows, kept to the slate's own 6
    assert THANKSGIVING in page.visited
    assert "is not in the Pace of Play CSV" not in caplog.text


def test_fetch_names_a_slate_team_the_csv_lacks(monkeypatch, caplog):
    html = _listing((THANKSGIVING, "Pace of Play &amp; DFS Stacks for Thanksgiving Day Slate"))
    _fake_tffb(monkeypatch, html, THANKSGIVING, _week_csv())
    monkeypatch.setattr(tffb_gps, "override_url", lambda key, week: None)
    monkeypatch.setattr(tffb_gps, "_slate_teams", lambda: [*THANKSGIVING_TEAMS, "NYJ"])
    with slate.active("thu"):
        df = tffb_gps.TffbGpsSource().fetch(tffb_gps.SyncContext(week=12, season=2026))
    assert len(df) == 6
    assert "slate team NYJ is not in the Pace of Play CSV" in caplog.text


def test_fetch_keeps_every_row_when_the_slate_has_no_salaries_yet(monkeypatch, caplog):
    html = _listing((THANKSGIVING, "Pace of Play &amp; DFS Stacks for Thanksgiving Day Slate"))
    _fake_tffb(monkeypatch, html, THANKSGIVING, _week_csv())
    monkeypatch.setattr(tffb_gps, "override_url", lambda key, week: None)
    monkeypatch.setattr(tffb_gps, "_slate_teams", lambda: [])
    with slate.active("thu"):
        df = tffb_gps.TffbGpsSource().fetch(tffb_gps.SyncContext(week=12, season=2026))
    assert len(df) == len(WEEK_12_SLATES)
    assert "no DraftKings salaries saved" in caplog.text


def test_the_main_slate_keeps_every_row(monkeypatch):
    week_12 = f"{_SITE}/dfs/pace-of-play-matchups-stacks-for-week-12-fantasy-football/"
    html = _listing((week_12, "Pace of Play: Matchups &amp; Stacks for Week 12"))
    _fake_tffb(monkeypatch, html, week_12, _week_csv())
    monkeypatch.setattr(tffb_gps, "_slate_teams", lambda: ["JAX", "CIN"])
    monkeypatch.setattr(tffb_gps, "playoff_round", lambda today, season: None)
    df = tffb_gps.TffbGpsSource().fetch(tffb_gps.SyncContext(week=12, season=2026))
    assert len(df) == len(WEEK_12_SLATES)


def test_keep_slate_teams_normalizes_the_salary_codes():
    kept = tffb_gps.keep_slate_teams(_gps("JAX", "BUF", "KC"), ["JAC", "KC"])
    assert list(kept["Team"]) == ["JAX", "KC"]


def test_fetch_uses_the_slates_gps_url_as_is_without_the_listing(monkeypatch):
    custom = f"{_SITE}/dfs/some-new-name-for-a-holiday-slate/"
    page = _fake_tffb(monkeypatch, "<html></html>", custom, _week_csv())
    overrides = {("thu", 12): custom}
    monkeypatch.setattr(tffb_gps, "override_url", lambda key, week: overrides.get((key, week)))
    monkeypatch.setattr(tffb_gps, "_slate_teams", lambda: THANKSGIVING_TEAMS)
    with slate.active("thu"):
        df = tffb_gps.TffbGpsSource().fetch(tffb_gps.SyncContext(week=12, season=2026))
    assert len(df) == 6
    assert page.visited == [custom]


def test_fetch_on_the_main_slate_is_pending_while_only_thanksgivings_article_is_up(monkeypatch):
    from dfs.sources.tffb_gps import TffbGpsPending

    html = _listing((THANKSGIVING, "Pace of Play &amp; DFS Stacks for Thanksgiving Day Slate"))
    _fake_tffb(monkeypatch, html, THANKSGIVING, _week_csv())
    monkeypatch.setattr(tffb_gps, "_slate_teams", lambda: [])
    monkeypatch.setattr(tffb_gps, "playoff_round", lambda today, season: None)
    with pytest.raises(TffbGpsPending, match="Week 12"):
        tffb_gps.TffbGpsSource().fetch(tffb_gps.SyncContext(week=12, season=2026))


def test_override_url_is_never_the_main_slates():
    assert tffb_gps.override_url("main", 12) is None


@pytest.mark.parametrize(("table_week", "expected"), [(12, THANKSGIVING), (None, THANKSGIVING), (11, None)])
def test_override_url_is_only_for_the_week_its_table_is_for(monkeypatch, table_week, expected):
    from dfs import config

    cfg = config.Config.model_validate(
        {
            "google_sheets": {"sheet_id": "main-sheet", "credentials_file": "x.json", "tab_mappings": {}},
            "slates": {"thu": {"sheet_id": "thu-sheet", "week": table_week, "gps_url": THANKSGIVING}},
        }
    )
    monkeypatch.setattr(config, "load_base_config", lambda: cfg)
    assert tffb_gps.override_url("thu", 12) == expected
    assert tffb_gps.override_url("sat", 12) is None
