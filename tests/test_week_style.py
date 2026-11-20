"""The Week page's colours come from the design tokens only (Sam, 2026-11-19: dark mode was unreadable where
a block hard-coded white cards and light-grey text). A colour literal outside a custom property declaration
(`--name: #...` in `week_style.LIGHT` / `DARK` and the shading's own tokens) fails here, in every state the
page is rendered in. `states` is shared with the browser contrast test (`test_week_contrast_browser.py`)."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from test_close_view import close_input
from test_week_page import cash_core_payload, fixtures, load

from dfs import commands_doc, week_actions, week_live, week_page, week_style, week_tools, week_writes

SUN = datetime(2026, 11, 8, 17, 40, tzinfo=UTC).astimezone()  # 12:40 pm ET, games on
TUE = datetime(2026, 11, 10, 15, 0, tzinfo=UTC).astimezone()


def _extra(edge: list[dict], name: str, pos: str, team: str, salary: int, proj: float, **kw) -> dict:
    base = next(r for r in edge if r["Name"] == "Dak Prescott")
    row = {k: "" for k in base}
    row.update({"Name": name, "Id": f"id-{name}", "Position": pos, "Team": team, "Opp": "SEA"})
    row.update({"Salary": str(salary), "CalPts": str(proj), "ProjPts": str(proj), "CashOwn": "0.12"})
    row.update(kw)
    return row


def _p(name, pos, team, salary, proj, **extra):
    return {
        "name": name, "id": f"id-{name}", "position": pos, "team": team, "opp": "SEA", "salary": salary,
        "projection": proj, "cash_own": 0.2, "kickoff": "Sun 1:00 pm", "late": False, "flags": [], **extra,
    }  # fmt: skip


def built(inp):
    """The full fixture after a build: a core (one Questionable, one faded), and four open spots: FLEX with
    two alternatives (one better by 2+ points and 45%+ owned), an RB already answered on the rules box, a TE
    with nothing close at the price, a WR whose alternative is OUT."""
    edge = list(inp.edge) + [
        _extra(inp.edge, "Rb Keep", "RB", "SEA", 6000, 15.0),
        _extra(inp.edge, "Rb Swap", "RB", "SF", 5600, 14.2),
        _extra(inp.edge, "Te Lone", "TE", "KC", 6200, 13.0),
        _extra(inp.edge, "Wr Late", "WR", "LV", 5000, 12.0),
        _extra(inp.edge, "Wr Gone", "WR", "LV", 4800, 11.6, Avail="OUT"),
        _extra(inp.edge, "Wr Better", "WR", "DEN", 6800, 20.1, CashOwn="0.52"),
    ]
    core = cash_core_payload()
    core["core"] = [
        {**core["core"][0]},
        {**_p("Ja'Marr Chase", "WR", "CIN", 8000, 21.0, status="Q"), "in": 11, "of": 12, "faded": False},
        {"name": "Hurt Guy", "faded": True},
    ]
    flex = core["open"][0]
    flex["alternatives"].append(
        {**_p("Wr Better", "WR", "DEN", 6800, 20.1, cash_own=0.52), "d_points": 2.1, "d_salary": 200, "in": 4}
    )
    core["open"] += [
        {
            "slot": "RB2", "position": "RB", "default": {**_p("Rb Keep", "RB", "SEA", 6000, 15.0), "in": 8},
            "alternatives": [{**_p("Rb Swap", "RB", "SF", 5600, 14.2, late=True, kickoff="Sun 4:25 pm"),
                              "d_points": -0.8, "d_salary": 400, "in": 4}],
            "picked": "",
        },
        {"slot": "TE", "position": "TE", "default": {**_p("Te Lone", "TE", "KC", 6200, 13.0), "in": 12},
         "alternatives": [], "picked": ""},
        {
            "slot": "WR3", "position": "WR", "default": {**_p("Wr Late", "WR", "LV", 5000, 12.0), "in": 7},
            "alternatives": [{**_p("Wr Gone", "WR", "LV", 4800, 11.6, flags=["OUT"]), "d_points": -2.4,
                              "d_salary": -300, "in": 5}],
            "picked": "",
        },
    ]  # fmt: skip
    return replace(inp, edge=edge, cash_core=core)


RULES = week_writes.LiveSheet(
    rules={"Always use": "cash: Rb Swap", "Never use": "", "Max exposure": "", "Stack rules": ""},
    rules_found=True,
)


def _render(inp, *, live: bool, sheet=None) -> str:
    page = week_page.render(
        inp,
        registered=set(commands_doc.all_commands()),
        tools=week_tools.build(inp.runs),
        live=live,
        nonce="n",
        sheet=sheet,
        answers=week_actions.answers(inp),
    )
    return week_live.augment(page, token="t", nonce="n", read_only=False) if live else page


def states(tmp_path) -> dict[str, str]:
    """Every state the page is drawn in: nothing synced; Saturday before a build; Saturday after one (served,
    with an answered spot; and static); Sunday with games on; Tuesday with the close."""
    empty = tmp_path / "empty"
    empty.mkdir()
    sat_dir, sun_dir, tue_dir = tmp_path / "sat", tmp_path / "sun", tmp_path / "tue"
    fixtures(sat_dir)
    fixtures(sun_dir, now=SUN)
    fixtures(tue_dir, now=TUE)
    sat = load(sat_dir)
    return {
        "empty": _render(load(empty), live=False),
        "sat-before": _render(sat, live=True),
        "sat-build": _render(built(sat), live=True, sheet=RULES),
        "sat-build-static": _render(built(sat), live=False),
        "sun": _render(built(load(sun_dir, now=SUN)), live=True),
        "tue": _render(replace(load(tue_dir, now=TUE), close=close_input()), live=True),
    }


# -- no colour outside the tokens ----------------------------------------------------------------------------

HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
FUNC = re.compile(r"\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(", re.I)
NAMED = re.compile(
    r"(?<![-\w])(?:white|black|red|green|blue|gr[ae]y|silver|orange|purple|yellow|navy|teal|maroon|olive|"
    r"lime|aqua|fuchsia|pink|brown|gold)(?![-\w])",
    re.I,
)


def _literals(value: str) -> list[str]:
    return HEX.findall(value) + FUNC.findall(value) + NAMED.findall(value)


def colour_literals(page: str) -> list[str]:
    """Every colour literal in the page's CSS (style blocks, style attributes, SVG paint attributes) that is
    not the value of a custom property."""
    bad: list[str] = []
    for css in re.findall(r"<style[^>]*>(.*?)</style>", page, re.S):
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        for block in re.findall(r"\{([^{}]*)\}", css):
            for decl in block.split(";"):
                prop, _, value = decl.partition(":")
                if prop.strip().startswith("--"):
                    continue
                bad += [f"{decl.strip()}" for _ in _literals(value)]
    for attr in re.findall(r'\sstyle="([^"]*)"', page):
        bad += [f"style={attr}" for _ in _literals(attr)]
    for name, value in re.findall(r'\s(fill|stroke|stop-color|color|bgcolor)="([^"]*)"', page):
        bad += [f"{name}={value}" for _ in _literals(value)]
    for js in re.findall(r"<script(?![^>]*application/json)[^>]*>(.*?)</script>", page, re.S):
        for m in re.finditer(r"""(?:color|background|fill|stroke)\w*\s*[=:]\s*['"`]([^'"`]*)['"`]""", js):
            bad += [f"script: {m.group(0)}" for _ in _literals(m.group(1))]
    return bad


def test_no_colour_is_hard_coded_on_the_served_page_after_a_build(tmp_path):
    """The editing run's check: every block's CSS is on every page, so one page catches a stylesheet; the
    slow test below also covers what only some states draw (inline styles, SVG, scripts)."""
    fixtures(tmp_path)
    assert colour_literals(_render(built(load(tmp_path)), live=True, sheet=RULES)) == []


@pytest.mark.slow
def test_no_colour_is_hard_coded_anywhere_on_the_page_in_any_state(tmp_path):
    """And both themes define the same tokens, each used on the page."""
    pages = states(tmp_path)
    for name, page in pages.items():
        assert colour_literals(page) == [], name
    assert set(week_style.LIGHT) == set(week_style.DARK)
    for token in week_style.LIGHT:
        assert any(f"var(--{token})" in page for page in pages.values()), token


def test_the_checker_catches_a_hard_coded_colour():
    assert colour_literals("<style>.x{background:#fff;color:rgba(0,0,0,.5)}</style>") != []
    assert colour_literals('<p style="color:white">') != []
    assert colour_literals('<svg><circle fill="#2563eb"/></svg>') != []
    assert colour_literals("<style>:root{--a:#fff}.x{color:var(--a)}#cafe{color:var(--ink)}</style>") == []


def test_open_spots_are_one_line_per_candidate_with_the_default_first_in_bold(tmp_path):
    fixtures(tmp_path)
    inp = built(load(tmp_path))
    page = _render(inp, live=True, sheet=RULES)
    start = page.index('id="open-spots"')
    spots = page[start : page.index("</section>", start)]
    assert "<button" in spots and 'class="cand' not in spots  # no cards
    flex = re.search(r'<tbody class="spot" data-slot="FLEX">(.*?)</tbody>', spots, re.S).group(1)
    rows = re.findall(r"<tr class=\"([^\"]*)\">(.*?)</tr>", flex, re.S)
    # the slot, the Default, two alternatives, "Someone else"
    assert [cls for cls, _ in rows] == ["grp", "base", "", "", "else"]
    base = re.sub(r"<[^>]+>", "", rows[1][1])
    assert "Puka Nacua" in base and "18.0 pts" in base and "$7,000" in base and "in 7 of 12" in base
    alt = re.sub(r"<[^>]+>", "", rows[2][1])
    assert "-1.1 pts" in alt and "+$1,200 freed" in alt and "20%" in alt and "in 5 of 12" in alt
    assert 'data-opt="id-Wan\'Dale Robinson"' in rows[2][1] or "data-opt=" in rows[2][1]
    assert "Pick" in alt and "Someone else" in rows[4][1]
    better = re.sub(r"<[^>]+>", "", rows[3][1])
    assert 'class="g"' in rows[3][1] and "+2.1 pts" in better and 'class="n hm a"' in rows[3][1]
    te = re.search(r'<tbody class="spot" data-slot="TE">(.*?)</tbody>', spots, re.S).group(1)
    assert te.count("<tr") == 1  # nothing close: a single line
    assert re.sub(r"<[^>]+>", "", te).startswith("TE: Te Lone, nothing close at the price")
    assert "Someone else" in te
    rb = re.search(r'<tbody class="spot" data-slot="RB2">(.*?)</tbody>', spots, re.S).group(1)
    assert "Rb Swap: on Always use for cash builds" in rb and "Undo" in rb
    assert 'class="picked"' in rb and "Picked" in rb and ">Pick<" not in rb
    wr = re.search(r'<tbody class="spot" data-slot="WR3">(.*?)</tbody>', spots, re.S).group(1)
    assert 'class="chip q">OUT<' in wr and 'class="rd"' in wr


def test_a_core_row_keeps_its_description_to_one_line_with_the_whole_on_hover(tmp_path):
    fixtures(tmp_path)
    page = _render(built(load(tmp_path)), live=True, sheet=RULES)
    start = page.index('id="cash-board"')
    board = page[start : page.index("</section>", start)]
    desc = re.findall(r'<span class="desc" title="([^"]*)">([^<]*)</span>', board)
    assert desc and all(title == text for title, text in desc)
    assert ".crow .who .desc{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis" in page
    assert ".crow .who{display:flex;align-items:baseline;gap:6px;min-width:0;white-space:nowrap}" in page
