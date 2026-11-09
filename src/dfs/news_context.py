"""Teammate news as facts on every affected player (Sam, 2026-11-09: "JSN had a backup QB, CeeDee did not
have his WR2, so he was for sure the main guy. I need that surfaced. It cost me last week").

Data, not a prediction (no model, no weights): who is out and how much of the team's work he had lately.
Every share is over a player's last `RECENT` games with that team (`usage_weeks.csv`, else the Player
card's `player_log.csv`), so a role that changed in October reads as it is now. Status is DraftKings' on
the slate (OUT / IR / D / Q), else the injury report's Out / Doubtful / Questionable. An absence is news
only while the player played in one of his team's last `FRESH` games (older: his role has already moved).

- **The QB situation.** The starter is the QB who started (50%+ of the snaps) the team's most recent game.
  When he is OUT / IR / Doubtful, the healthy QB with the highest CalPts on the slate is the likely
  starter: every pass catcher on the team (a WR / TE with `SEES_TGT`+ of the targets, a RB with
  `MATE_TGT`+) gets "QB downgrade: Lock (backup) for Darnold (OUT)" and the backup "Starting for Darnold
  (OUT)". Questionable reads "QB may miss".
- **A pass catcher out.** A WR / TE / RB with `MATE_TGT`+ of the targets who is OUT / IR / Doubtful
  (Questionable: "may miss") is named on every healthy pass catcher on the team ("Pickens out (WR2, 19% of
  DAL's targets)"; the role is his rank by target share among the team's players at his position), and
  the healthy pass catcher with the biggest share gets "Lead target: 36% of DAL's targets; next healthy
  Flournoy 13%".
- **A lead back out.** A RB with `LEAD_RB_SNAP`+ of the snaps who is out is named on the team's other RBs.

`team_news` / `player_news` / `team_lines` are pure; the Week page shows them on the board, the decisions,
the overview, the injury report, beside every plays call, in the drawer and the compare tray.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dfs.player_join import normalize_name

OUT = frozenset({"OUT", "IR", "D"})
MAYBE = frozenset({"Q"})
REPORT = {"Out": "OUT", "Doubtful": "D", "Questionable": "Q"}
RECENT = 5  # games: the shares are over each player's last five with his team
FRESH = 3  # an absence is news while he played in one of his team's last three games
STARTER_SNAP = 0.5  # a game the QB started: half the offensive snaps or more
MATE_TGT = 0.12  # a pass catcher whose absence is news (and a RB who is part of the passing game)
SEES_TGT = 0.05  # a WR / TE who gets the note
LEAD_RB_SNAP = 0.5
DOWN, UP, WARN = "down", "up", "warn"
ORDER = {"qb": 0, "qb_backup": 0, "rb_out": 1, "mate_out": 1, "lead": 2}
SUFFIXES = frozenset({"Jr", "Sr", "II", "III", "IV", "V"})


@dataclass
class Fact:
    kind: str  # qb | qb_backup | mate_out | lead | rb_out
    text: str
    tone: str  # down (worse for him) | up (more for him) | warn (may miss: watch the news)
    source: str = ""  # the player whose status it is about
    short: str = ""  # the chip's label


@dataclass
class Usage:
    name: str
    team: str
    position: str
    games: int = 0
    starts: int = 0  # QB: games at STARTER_SNAP+
    last_wk: int = 0  # his most recent game with the team
    last_start: int = 0  # QB: the most recent week he started
    snap: float | None = None
    tgt: float | None = None


@dataclass
class TeamNews:
    team: str
    facts: list[tuple[str, Fact]] = field(default_factory=list)  # (player NameKey, fact)


def _num(x: object) -> float | None:
    try:
        v = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if v != v else v


def _short(name: str) -> str:
    """'Sam Darnold' -> 'Darnold', 'Marvin Harrison Jr.' -> 'Harrison'."""
    parts = [p for p in str(name).split() if p.rstrip(".") not in SUFFIXES]
    return parts[-1] if parts else str(name)


def _key(r: dict) -> tuple[str, str]:
    return (str(r.get("NameKey") or normalize_name(r.get("Name"))), str(r.get("Team", "")))


def _pct(x: float | None) -> str:
    return f"{(x or 0) * 100:.0f}%"


def usage_by_player(games: list[dict]) -> dict[tuple[str, str], Usage]:
    """(NameKey, team) -> his usage over his last `RECENT` games with that team, from per-game rows."""
    by: dict[tuple[str, str], list[dict]] = {}
    for g in games or []:
        by.setdefault(_key(g), []).append(g)
    out: dict[tuple[str, str], Usage] = {}
    for key, rows in by.items():
        recent = sorted(rows, key=lambda g: _num(g.get("Wk")) or 0)[-RECENT:]
        u = Usage(str(recent[-1].get("Name", "")), key[1], str(recent[-1].get("Position", "")))
        snaps, tgts = [], []
        for g in recent:
            wk = int(_num(g.get("Wk")) or 0)
            u.games += 1
            u.last_wk = max(u.last_wk, wk)
            snap, tgt = _num(g.get("Snap%")), _num(g.get("Tgt%"))
            if snap is not None:
                snaps.append(snap)
                if u.position == "QB" and snap >= STARTER_SNAP:
                    u.starts += 1
                    u.last_start = max(u.last_start, wk)
            if tgt is not None:
                tgts.append(tgt)
        u.snap = sum(snaps) / len(snaps) if snaps else None
        u.tgt = sum(tgts) / len(tgts) if tgts else None
        out[key] = u
    return out


def statuses(edge: list[dict], report: list[dict] | None) -> dict[tuple[str, str], str]:
    """(NameKey, team) -> OUT / IR / D / Q: DraftKings' on the slate first, else the injury report's."""
    out: dict[tuple[str, str], str] = {}
    for r in report or []:
        st = REPORT.get(str(r.get("report_status") or ""))
        if st:
            out[_key(r)] = st
    for r in edge or []:
        st = str(r.get("Avail") or "").strip().upper()
        if st in OUT | MAYBE:
            out[_key(r)] = st
    return out


def _word(st: str) -> str:
    return {"OUT": "OUT", "IR": "IR", "D": "Doubtful", "Q": "Questionable"}[st]


def _qb_facts(team, mine, status, on_slate, catches, fresh) -> list[tuple[str, Fact]]:
    qbs = sorted(
        [(k, u) for k, u in mine.items() if u.position == "QB" and u.starts and fresh(u)],
        key=lambda x: (-x[1].last_start, -x[1].starts),
    )
    if not qbs:
        return []
    sk, starter = qbs[0]
    st = status.get(sk)
    if st not in OUT | MAYBE:
        return []
    backups = sorted(
        [
            r
            for k, r in on_slate.items()
            if k[1] == team and r.get("Position") == "QB" and k != sk and status.get(k) not in OUT
        ],
        key=lambda r: -(_num(r.get("CalPts")) or 0),
    )
    backup = backups[0] if backups else None
    bname = _short(backup.get("Name", "")) if backup is not None else "a backup"
    who = _short(starter.name)
    if st in OUT:
        fact = Fact("qb", f"QB downgrade: {bname} (backup) for {who} ({_word(st)})", DOWN, starter.name)
        fact.short = f"Backup QB: {bname}"
    else:
        fact = Fact("qb", f"QB may miss: {who} is Questionable ({bname} would start)", WARN, starter.name)
        fact.short = f"QB Q: {who}"
    out = [(k[0], fact) for k, u in mine.items() if k != sk and status.get(k) not in OUT and catches(u)]
    if backup is not None and st in OUT:
        out.append(
            (
                _key(backup)[0],
                Fact("qb_backup", f"Starting for {who} ({_word(st)})", UP, starter.name, "Starting QB"),
            )
        )
    return out


def team_news(edge: list[dict], games: list[dict], report: list[dict] | None = None) -> dict[str, TeamNews]:
    edge = edge or []
    use = usage_by_player(games)
    status = statuses(edge, report)
    on_slate = {_key(r): r for r in edge}
    out: dict[str, TeamNews] = {}
    for team in sorted({str(r.get("Team", "")) for r in edge if r.get("Team")}):
        tn = TeamNews(team)
        mine = {k: u for k, u in use.items() if k[1] == team}
        latest = max([u.last_wk for u in mine.values()] or [0])

        def healthy(k: tuple[str, str]) -> bool:
            return status.get(k) not in OUT

        def fresh(u: Usage, latest: int = latest) -> bool:
            return u.last_wk > 0 and u.last_wk >= latest - (FRESH - 1)

        def catches(u: Usage) -> bool:
            need = MATE_TGT if u.position == "RB" else SEES_TGT
            return u.position in ("WR", "TE", "RB") and (u.tgt or 0) >= need

        tn.facts += _qb_facts(team, mine, status, on_slate, catches, fresh)

        catchers = [(k, u) for k, u in mine.items() if u.position in ("WR", "TE", "RB") and u.tgt is not None]
        rank: dict[tuple[str, str], str] = {}
        for pos in ("WR", "TE", "RB"):
            ordered = sorted([x for x in catchers if x[1].position == pos], key=lambda x: -(x[1].tgt or 0))
            for i, (k, _u) in enumerate(ordered, start=1):
                rank[k] = f"{pos}{i}"
        gone = sorted(
            [
                (k, u)
                for k, u in catchers
                if (u.tgt or 0) >= MATE_TGT and status.get(k) in OUT | MAYBE and fresh(u)
            ],
            key=lambda x: -(x[1].tgt or 0),
        )
        for gk, gu in gone:
            st = status[gk]
            role, word = rank.get(gk, gu.position), "out" if st in OUT else "may miss"
            text = f"Teammate {_short(gu.name)} {word} ({role}, {_pct(gu.tgt)} of {team}'s targets)"
            short = f"{_short(gu.name)} {'out' if st in OUT else 'Q'} ({role})"
            for k, u in catchers:
                if k != gk and healthy(k) and catches(u):
                    tn.facts.append((k[0], Fact("mate_out", text, UP if st in OUT else WARN, gu.name, short)))
        if any(status.get(k) in OUT for k, _ in gone):
            alive = sorted(
                [(k, u) for k, u in catchers if healthy(k) and catches(u)], key=lambda x: -(x[1].tgt or 0)
            )
            if alive:
                lk, lu = alive[0]
                nxt = (
                    f"; next healthy {_short(alive[1][1].name)} {_pct(alive[1][1].tgt)}"
                    if len(alive) > 1
                    else ""
                )
                tn.facts.append(
                    (
                        lk[0],
                        Fact(
                            "lead",
                            f"Lead target: {_pct(lu.tgt)} of {team}'s targets{nxt}",
                            UP,
                            "",
                            f"Lead target {_pct(lu.tgt)}",
                        ),
                    )
                )
        for k, u in mine.items():
            if u.position == "RB" and (u.snap or 0) >= LEAD_RB_SNAP and status.get(k) in OUT and fresh(u):
                text = f"Lead back {_short(u.name)} out ({_pct(u.snap)} of snaps)"
                for k2, u2 in mine.items():
                    if k2 != k and u2.position == "RB" and healthy(k2):
                        tn.facts.append(
                            (k2[0], Fact("rb_out", text, UP, u.name, f"{_short(u.name)} out (RB1)"))
                        )
        if tn.facts:
            out[team] = tn
    return out


def player_news(
    edge: list[dict], games: list[dict], report: list[dict] | None = None
) -> dict[str, list[Fact]]:
    """EdgeRaw Id -> his facts (the QB line first, then teammates out, then the lead-target line)."""
    by_key: dict[tuple[str, str], list[Fact]] = {}
    for team, tn in team_news(edge, games, report).items():
        for name_key, fact in tn.facts:
            by_key.setdefault((name_key, team), []).append(fact)
    out: dict[str, list[Fact]] = {}
    for r in edge or []:
        if _key(r) in by_key and r.get("Id"):
            out[str(r["Id"])] = sorted(by_key[_key(r)], key=lambda f: ORDER.get(f.kind, 9))
    return out


def team_lines(edge: list[dict], games: list[dict], report: list[dict] | None = None) -> dict[str, list[str]]:
    """Team -> its news, one line each (the QB first, then who is out, then who leads the targets): the
    injury report's team header."""
    names = {_key(r)[0]: str(r.get("Name", "")) for r in edge or []}
    out: dict[str, list[str]] = {}
    for team, tn in team_news(edge, games, report).items():
        qb, mates, lead = [], [], []
        for name_key, fact in tn.facts:
            if fact.kind == "qb" and fact.text not in qb:
                qb.append(fact.text)
            elif fact.kind in ("mate_out", "rb_out"):
                line = fact.text.replace("Teammate ", "", 1)
                if line not in mates:
                    mates.append(line)
            elif fact.kind == "lead":
                lead.append(f"{names.get(name_key, name_key)}: {fact.text}")
        if qb or mates or lead:
            out[team] = qb + mates + lead
    return out
