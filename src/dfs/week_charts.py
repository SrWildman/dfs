"""Week page v2's charts, as inline SVG: no script, nothing loaded. Pure: rows in, an SVG string out.

- **Value map**: RB / WR / TE salary against CalPts, a dashed reference line at 3 points per $1K, the top
  value outliers labelled. Hover (the native `<title>`) shows name, salary and projection.
- **Leverage map** (once GPP ownership exists): GPP ownership against the ceiling, the leverage quadrant
  (low owned, high ceiling) shaded.
- **Usage**: target share against snap share (WR / TE / RB), for the Explorer's Usage lens.

Each chart is drawn at the size of the column it sits in (`width`), so 13px text stays 13px: a chart scaled
down from a wide drawing is what made the mockup's labels unreadable. A dot carries `data-id`, so a click on
it opens the player drawer like a name does.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from dfs import week_plays
from dfs.week_plays import num

SKILL = ("RB", "WR", "TE")
LABEL_PX = 13
TICK_PX = 12


def e(text: object) -> str:
    return html.escape(str(text), quote=True)


@dataclass
class Point:
    x: float
    y: float
    pos: str
    name: str
    id: str
    title: str
    label: str = ""  # shown beside the dot when set
    strong: bool = False  # a bigger dot


def _nice_ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    span = max(hi - lo, 1e-9)
    raw = span / n
    mag = 10 ** len(str(int(raw))) / 10 if raw >= 1 else 0.1
    step = min((s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw), default=raw)
    first = (lo // step + 1) * step if lo % step else lo
    out, v = [], first
    while v <= hi + 1e-9:
        out.append(round(v, 6))
        v += step
    return out


def scatter(
    points: list[Point],
    *,
    width: int,
    height: int,
    x_range: tuple[float, float],
    y_range: tuple[float, float],
    x_label: str,
    y_label: str,
    x_fmt=lambda v: f"{v:g}",
    y_fmt=lambda v: f"{v:g}",
    ref: tuple[tuple[float, float], tuple[float, float], str] | None = None,
    shade: tuple[float, float, float, float, str] | None = None,
    aria: str,
) -> str:
    """A labelled scatter. `ref` is a dashed line ((x0, y0), (x1, y1), its label); `shade` a rectangle in data
    units (x0, x1, y0, y1, its label)."""
    left, right, top, bottom = 44, 14, 12, 40
    x0, x1 = x_range
    y0, y1 = y_range

    def X(v: float) -> float:
        return left + (v - x0) / ((x1 - x0) or 1) * (width - left - right)

    def Y(v: float) -> float:
        return height - bottom - (v - y0) / ((y1 - y0) or 1) * (height - top - bottom)

    parts = [
        f'<svg class="chart" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" '
        f'aria-label="{e(aria)}">'
    ]
    if shade:
        sx0, sx1, sy0, sy1, slabel = shade
        parts.append(
            f'<rect class="shade" x="{X(sx0):.1f}" y="{Y(sy1):.1f}" width="{X(sx1) - X(sx0):.1f}" '
            f'height="{Y(sy0) - Y(sy1):.1f}"/>'
            f'<text class="shade-lbl" x="{X(sx0) + 6:.1f}" y="{Y(sy1) + 16:.1f}">{e(slabel)}</text>'
        )
    for t in _nice_ticks(x0, x1):
        parts.append(
            f'<line class="grid" x1="{X(t):.1f}" y1="{top}" x2="{X(t):.1f}" y2="{height - bottom}"/>'
            f'<text class="tick" x="{X(t):.1f}" y="{height - bottom + 16}" text-anchor="middle">'
            f"{e(x_fmt(t))}</text>"
        )
    for t in _nice_ticks(y0, y1):
        parts.append(
            f'<line class="grid" x1="{left}" y1="{Y(t):.1f}" x2="{width - right}" y2="{Y(t):.1f}"/>'
            f'<text class="tick" x="{left - 6}" y="{Y(t) + 4:.1f}" text-anchor="end">{e(y_fmt(t))}</text>'
        )
    parts.append(
        f'<text class="axis" x="{(left + width - right) / 2:.0f}" y="{height - 6}" text-anchor="middle">'
        f"{e(x_label)}</text>"
        f'<text class="axis" transform="translate(13 {(top + height - bottom) / 2:.0f}) rotate(-90)" '
        f'text-anchor="middle">{e(y_label)}</text>'
    )
    if ref:
        (rx0, ry0), (rx1, ry1), rlabel = ref
        parts.append(
            f'<line class="ref" x1="{X(rx0):.1f}" y1="{Y(ry0):.1f}" x2="{X(rx1):.1f}" y2="{Y(ry1):.1f}"/>'
            f'<text class="ref-lbl" x="{X(rx1) - 4:.1f}" y="{Y(ry1) + 16:.1f}" text-anchor="end">{e(rlabel)}</text>'
        )
    for p in sorted(points, key=lambda p: p.strong):
        r = 5 if p.strong else 3.5
        parts.append(
            f'<circle class="dot {e(p.pos)}{" strong" if p.strong else ""}" cx="{X(p.x):.1f}" cy="{Y(p.y):.1f}" '
            f'r="{r}" data-id="{e(p.id)}"><title>{e(p.title)}</title></circle>'
        )
    placed: list[tuple[float, float, float]] = []  # (x, y, width) of each label, to keep them apart
    if ref:
        rw = len(ref[2]) * TICK_PX * 0.56
        placed.append((X(ref[1][0]) - 4 - rw, Y(ref[1][1]) + 16, rw))
    if shade:
        sw = len(shade[4]) * TICK_PX * 0.56
        placed.append((X(shade[0]) + 6, Y(shade[3]) + 16, sw))
    for p in points:
        if not p.label:
            continue
        w = len(p.label) * LABEL_PX * 0.56
        cx, cy = X(p.x), Y(p.y)
        for dx, dy, anchor in ((7, -6, "start"), (-7, -6, "end"), (7, 15, "start"), (-7, 15, "end")):
            lx = cx + dx if anchor == "start" else cx + dx - w
            ly = cy + dy
            if lx < left or lx + w > width - right or ly < top + 10 or ly > height - bottom:
                continue
            if any(abs(ly - py) < LABEL_PX + 1 and lx < px + pw and px < lx + w for px, py, pw in placed):
                continue
            placed.append((lx, ly, w))
            parts.append(
                f'<text class="lbl" x="{cx + dx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" data-id="{e(p.id)}">'
                f"{e(p.label)}</text>"
            )
            break
    parts.append("</svg>")
    return "".join(parts)


def _healthy_skill(edge: list[dict]) -> list[dict]:
    return [
        r
        for r in week_plays.rosterable(edge or [])
        if r.get("Position") in SKILL and not week_plays.avail(r) and week_plays.salary(r) >= 3000
    ]


def short(name: str) -> str:
    from dfs.week_board import short_name

    return short_name(name)


def value_rows(edge: list[dict] | None) -> list[dict]:
    """The value map's players (the inline chart and the full-screen viewer draw the same ones)."""
    return [r for r in _healthy_skill(edge or []) if num(r.get("CalPts")) is not None]


def _ceil(r: dict) -> float:
    return num(r.get("CeilM")) or num(r.get("Ceiling")) or 0.0


def leverage_rows(edge: list[dict] | None) -> list[dict]:
    return [
        r
        for r in _healthy_skill(edge or [])
        if num(r.get("GppOwn")) is not None and _ceil(r) > 0 and (num(r.get("CalPts")) or 0) >= 5
    ]


def _share(value: float | None) -> float | None:
    return None if value is None else (value * 100 if value <= 1.5 else value)


def usage_rows(edge: list[dict] | None) -> list[tuple[dict, float, float]]:
    """(row, snap share %, target share %) for the usage chart."""
    out = []
    for r in _healthy_skill(edge or []):
        snap, tgt = _share(num(r.get("Snap% L5")) or num(r.get("Snap%"))), _share(num(r.get("Tgt%")))
        if snap is not None and tgt is not None:
            out.append((r, snap, tgt))
    return out


LOW_OWN = 10.0  # the leverage quadrant: under 10% GPP owned


def leverage_cut(rows: list[dict]) -> float:
    """The top-quarter ceiling the leverage quadrant starts at."""
    ceils = sorted(_ceil(r) for r in rows)
    return ceils[int(len(ceils) * 0.75)]


def value_map(edge: list[dict] | None, *, width: int = 400, height: int = 320, labels: int = 8) -> str:
    rows = value_rows(edge)
    if len(rows) < 3:
        return ""
    sal = [week_plays.salary(r) for r in rows]
    cal = [num(r.get("CalPts")) or 0.0 for r in rows]
    x0, x1 = 3000.0, max(sal) + 200
    y1 = max(cal) + 2
    best = sorted(rows, key=lambda r: -(week_plays.value_per_k(r) or 0))[:labels]
    best_ids = {id(r) for r in best}
    points = [
        Point(
            week_plays.salary(r),
            num(r.get("CalPts")) or 0.0,
            r.get("Position", ""),
            r.get("Name", ""),
            str(r.get("Id") or ""),
            f"{r.get('Name', '')} ({r.get('Position', '')}) ${week_plays.salary(r):,.0f} · "
            f"{num(r.get('CalPts')) or 0:.1f} pts · {week_plays.value_per_k(r) or 0:.2f} per $1K",
            short(r.get("Name", "")) if id(r) in best_ids else "",
            (week_plays.value_per_k(r) or 0) >= 3.0,
        )
        for r in rows
    ]
    line_end = min(x1, y1 / 3.0 * 1000)
    return scatter(
        points,
        width=width,
        height=height,
        x_range=(x0, x1),
        y_range=(0.0, y1),
        x_label="Salary",
        y_label="CalPts",
        x_fmt=lambda v: f"${v / 1000:g}K",
        ref=((x0, x0 * 3 / 1000), (line_end, line_end * 3 / 1000), "3 pts per $1K"),
        aria="RB, WR and TE salary against CalPts, with the 3 points per $1K line",
    )


def leverage_map(edge: list[dict] | None, *, width: int = 400, height: int = 320, labels: int = 6) -> str:
    """GPP ownership against the ceiling (CeilM, else Ceiling). Empty until GppOwn exists."""
    rows = leverage_rows(edge)
    if len(rows) < 3:
        return ""
    ceil = _ceil
    own = [(num(r.get("GppOwn")) or 0.0) * 100 for r in rows]
    ceils = sorted(ceil(r) for r in rows)
    hi_ceil = leverage_cut(rows)
    low_own = LOW_OWN
    lev = [r for r in rows if (num(r["GppOwn"]) or 0) * 100 < low_own and ceil(r) >= hi_ceil]
    lev.sort(key=lambda r: -ceil(r))
    label_ids = {id(r) for r in lev[:labels]}
    x1 = max(own) + 3
    y0, y1 = max(0.0, ceils[0] - 2), ceils[-1] + 3
    points = [
        Point(
            (num(r.get("GppOwn")) or 0) * 100,
            ceil(r),
            r.get("Position", ""),
            r.get("Name", ""),
            str(r.get("Id") or ""),
            f"{r.get('Name', '')} ({r.get('Position', '')}) {(num(r.get('GppOwn')) or 0) * 100:.0f}% GPP owned "
            f"· ceiling {ceil(r):.1f}",
            short(r.get("Name", "")) if id(r) in label_ids else "",
            id(r) in label_ids,
        )
        for r in rows
    ]
    return scatter(
        points,
        width=width,
        height=height,
        x_range=(0.0, x1),
        y_range=(y0, y1),
        x_label="GPP ownership",
        y_label="Ceiling",
        x_fmt=lambda v: f"{v:g}%",
        shade=(0.0, low_own, hi_ceil, y1, "leverage: <10% owned, top-quarter ceiling"),
        aria="GPP ownership against ceiling, the low-owned high-ceiling quadrant shaded",
    )


def usage_map(edge: list[dict] | None, *, width: int = 400, height: int = 320, labels: int = 8) -> str:
    """Target share against snap share (last five games), WR / TE / RB."""
    rows = usage_rows(edge)
    if len(rows) < 3:
        return ""
    top = sorted(rows, key=lambda t: -t[2])[:labels]
    top_ids = {id(t[0]) for t in top}
    points = [
        Point(
            s,
            t,
            r.get("Position", ""),
            r.get("Name", ""),
            str(r.get("Id") or ""),
            f"{r.get('Name', '')} ({r.get('Position', '')}) {s:.0f}% of snaps · {t:.0f}% of targets",
            short(r.get("Name", "")) if id(r) in top_ids else "",
            id(r) in top_ids,
        )
        for r, s, t in rows
    ]
    return scatter(
        points,
        width=width,
        height=height,
        x_range=(0.0, 100.0),
        y_range=(0.0, max(t for _, _, t in rows) + 3),
        x_label="Snap share, last 5",
        y_label="Target share",
        x_fmt=lambda v: f"{v:g}%",
        y_fmt=lambda v: f"{v:g}%",
        aria="Snap share against target share for RB, WR and TE",
    )


LEGEND = (
    '<div class="legend"><span class="key RB"></span>RB <span class="key WR"></span>WR '
    '<span class="key TE"></span>TE</div>'
)
# The Explorer's chart draws every position a lens passes (QB and DST too).
LEGEND_ALL = (
    '<div class="legend"><span class="key QB"></span>QB <span class="key RB"></span>RB '
    '<span class="key WR"></span>WR <span class="key TE"></span>TE <span class="key DST"></span>DST</div>'
)


# -- the full-screen viewer's data (Plan N item 9) -----------------------------------------------------------

HOW = {
    "value": "Every healthy RB / WR / TE at $3,000+ with a CalPts, from the last sync: salary against CalPts; the "
    "dashed line is 3 points per $1K; the best per $1K are named.",
    "leverage": "Every healthy RB / WR / TE at $3,000+ projected 5+ CalPts, once GppOwn is out: GPP ownership "
    "against the ceiling (CeilM, else TFFB's Ceiling); shaded = under 10% owned with a top-quarter ceiling.",
    "usage": "Every healthy RB / WR / TE at $3,000+ with usage: snap share over his last five games (else the "
    "season) against target share; the top target shares are named.",
}


# The caption under each chart (Plan O item 4): the formula first, then the columns it reads.
FORMULA = {
    "value": "Value = CalPts ÷ salary × 1,000 (points per $1K).",
    "leverage": "Leverage = ceiling against GPP ownership.",
    "usage": "Usage = snap share against target share.",
}
SOURCES = {
    "value": ("Salary", "CalPts"),
    "leverage": ("GppOwn", "CeilM", "Ceiling"),
    "usage": ("Snap% L5", "Snap%", "Tgt%"),
}


def _pt(r: dict, x: float, y: float, prio: float) -> dict:
    return {
        "id": str(r.get("Id") or ""),
        "name": str(r.get("Name") or ""),
        "short": short(str(r.get("Name") or "")),
        "pos": str(r.get("Position") or ""),
        "team": str(r.get("Team") or ""),
        "x": round(x, 3),
        "y": round(y, 3),
        "sal": week_plays.salary(r),
        "proj": num(r.get("CalPts")),
        "du": num(r.get("CashOwn")),
        "gpp": num(r.get("GppOwn")),
        "prio": round(prio, 4),
    }


def viewer_data(edge: list[dict] | None, ownership_out: bool = True) -> dict:
    """Each chart's points and axes for the full-screen viewer: the same players the inline charts draw
    (`value_rows` / `leverage_rows` / `usage_rows`), with what the hover shows (name, salary, projection,
    ownership, team) and a label priority (the inline chart's own: value per $1K, ceiling, target share)."""
    out: dict[str, dict] = {}
    rows = value_rows(edge)
    if len(rows) >= 3:
        x0, x1 = 3000.0, max(week_plays.salary(r) for r in rows) + 200
        y1 = max(num(r.get("CalPts")) or 0.0 for r in rows) + 2
        end = min(x1, y1 / 3.0 * 1000)
        out["value"] = {
            "title": "Value map",
            "x": "Salary",
            "y": "CalPts",
            "xfmt": "money",
            "yfmt": "num",
            "range": [x0, x1, 0.0, y1],
            "ref": [[x0, x0 * 3 / 1000], [end, end * 3 / 1000], "3 pts per $1K"],
            "how": HOW["value"],
            "points": [
                _pt(r, week_plays.salary(r), num(r.get("CalPts")) or 0.0, week_plays.value_per_k(r) or 0.0)
                for r in rows
            ],
        }
    rows = leverage_rows(edge) if ownership_out else []
    if len(rows) >= 3:
        ceils = sorted(_ceil(r) for r in rows)
        hi = leverage_cut(rows)
        y0, y1 = max(0.0, ceils[0] - 2), ceils[-1] + 3
        x1 = max((num(r.get("GppOwn")) or 0.0) * 100 for r in rows) + 3
        out["leverage"] = {
            "title": "Leverage map",
            "x": "GPP ownership",
            "y": "Ceiling",
            "xfmt": "pct",
            "yfmt": "num",
            "range": [0.0, x1, y0, y1],
            "shade": [0.0, LOW_OWN, hi, y1, "leverage: <10% owned, top-quarter ceiling"],
            "how": HOW["leverage"],
            "points": [_pt(r, (num(r.get("GppOwn")) or 0.0) * 100, _ceil(r), _ceil(r)) for r in rows],
        }
    urows = usage_rows(edge)
    if len(urows) >= 3:
        out["usage"] = {
            "title": "Usage",
            "x": "Snap share, last 5",
            "y": "Target share",
            "xfmt": "pct",
            "yfmt": "pct",
            "range": [0.0, 100.0, 0.0, max(t for _, _, t in urows) + 3],
            "how": HOW["usage"],
            "points": [_pt(r, snap, tgt, tgt) for r, snap, tgt in urows],
        }
    return out
