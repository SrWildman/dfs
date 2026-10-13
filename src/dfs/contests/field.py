"""How the field builds its lineups, per contest, for all entries and for the top 10%.

Input is one parsed `standings.Standings` (Classic contests; a Showdown contest is parsed and profiled but
not analysed here -- none of these measures is defined for CPT/FLEX rosters) and a **players frame** supplied
by the caller (the local session passes EdgeRaw's): columns `Name`, `Team`, `Position`, `Opponent` (or
EdgeRaw's `Opp`) and, optionally, `Salary`. Without a frame (or without a `Salary` column) every measure
that needs team, opponent or salary comes out blank -- never a guess.

Measures (each is a share of, or an average over, the group's lineups for which it is defined; `n_resolved` /
`n_salary` say how many that was):

- **Stacking.** `qb_stack1` / `qb_stack2`: the share with the QB and 1+ / 2+ same-team pass catchers (WR or
  TE, in any slot including FLEX; a RB is not a pass catcher here). `bringback`: a non-DST player from the
  QB's opponent. `top_stacks`: the most common QB teams among the stacked lineups.
- **Salary unspent.** `$50,000 - sum of the nine salaries`; `avg_unspent` and `share_unspent_1k` (the share
  leaving $1,000 or more).
- **FLEX position.** `flex_rb` / `flex_wr` / `flex_te`.
- **Chalk.** `avg_chalk`: how many of the contest's five most-owned players a lineup holds. The five are by
  total `%Drafted` (summed over a player's slot rows, a DST counting as a player); ties at the cut are broken
  by name, so the set is the same on every run.
- **DST.** `dst_with_qb` (the DST is the QB's own team) and `dst_vs_qb` (the DST is the QB's opponent).
- **Duplicates.** `dup_entries`: lineups in the group whose exact nine players appear 2+ times in the whole
  contest; `dup_groups`: how many distinct such lineups; `dup_share`.
- **One named lineup.** Pass `me` and his entries (counter-stripped name) are a third group, `me`, measured
  the same way, for comparison.

**Joins.** The standings carry names but no team, so a lineup player is matched on `player_join`'s
normalised name + position (the slot's position; for FLEX the position of the player's non-FLEX row in the
ownership table, else any of RB/WR/TE) and the team is then taken from the frame. Two frame rows that match
one name (same name and position, different teams) are ambiguous and reported, never guessed; so is a name
that matches none. Every miss is in `FieldResult.unmatched`, and a lineup with a miss is left out of the
measures that need it.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

import pandas as pd

from dfs.contests.standings import Standings
from dfs.player_join import normalize_name, normalize_position

SALARY_CAP = 50_000
UNSPENT_THRESHOLD = 1_000
CHALK_N = 5
TOP_FRACTION = 0.10
TOP_STACKS_SHOWN = 3
PASS_CATCHERS = frozenset({"WR", "TE"})
FLEX_POSITIONS = ("RB", "WR", "TE")
REQUIRED_PLAYER_COLUMNS = ("Name", "Team", "Position", "Opponent")

FIELD_COLUMNS = [
    "contest_id",
    "group",
    "n",
    "n_unparsed",
    "n_resolved",
    "n_salary",
    "qb_stack1",
    "qb_stack2",
    "bringback",
    "avg_unspent",
    "share_unspent_1k",
    "flex_rb",
    "flex_wr",
    "flex_te",
    "avg_chalk",
    "dst_with_qb",
    "dst_vs_qb",
    "dup_entries",
    "dup_groups",
    "dup_share",
    "top_stacks",
    "chalk_players",
]


@dataclass(frozen=True)
class Resolved:
    name: str
    position: str
    team: str
    opponent: str
    salary: float | None


@dataclass
class FieldResult:
    rows: pd.DataFrame  # one row per group, FIELD_COLUMNS
    unmatched: pd.DataFrame  # contest_id, player, slot, reason
    stacks: pd.DataFrame  # contest_id, group, team, n, share
    features: pd.DataFrame  # one row per parsed lineup
    skipped: str = ""  # why a contest was not analysed (Showdown, no lineups)


class Roster:
    """The players frame, indexed by normalised name for the join."""

    def __init__(self, players: pd.DataFrame | None):
        self.by_name: dict[str, list[Resolved]] = {}
        self.has_salary = False
        if players is None or players.empty:
            return
        if "Opponent" not in players.columns and "Opp" in players.columns:
            players = players.rename(columns={"Opp": "Opponent"})  # EdgeRaw's own spelling
        missing = [c for c in REQUIRED_PLAYER_COLUMNS if c not in players.columns]
        if missing:
            raise ValueError(f"the players frame needs column(s) {missing} (has {list(players.columns)})")
        self.has_salary = "Salary" in players.columns
        seen: set[tuple[str, str, str]] = set()
        for rec in players.to_dict("records"):
            key = normalize_name(rec["Name"])
            pos = normalize_position(rec["Position"])
            marker = (key, str(rec["Team"]).strip().upper(), pos)
            if not key or marker in seen:
                continue
            seen.add(marker)
            salary = pd.to_numeric(rec.get("Salary"), errors="coerce") if self.has_salary else None
            self.by_name.setdefault(key, []).append(
                Resolved(
                    str(rec["Name"]),
                    pos,
                    str(rec["Team"]).strip().upper(),
                    str(rec["Opponent"]).strip().upper(),
                    None if salary is None or pd.isna(salary) else float(salary),
                )
            )

    def resolve(self, name: str, slot: str, known_position: str | None) -> tuple[Resolved | None, str]:
        """`(player, "")` or `(None, reason)`."""
        if not self.by_name:
            return None, "no players frame"
        if slot == "DST":
            allowed = {"DST"}
        elif slot == "FLEX":
            allowed = {known_position} if known_position in FLEX_POSITIONS else set(FLEX_POSITIONS)
        else:
            allowed = {slot}
        found = [p for p in self.by_name.get(normalize_name(name), []) if p.position in allowed]
        if len(found) == 1:
            return found[0], ""
        if not found:
            return None, "not in the players frame"
        return None, f"ambiguous: {len(found)} players match ({', '.join(sorted(p.team for p in found))})"


def chalk_players(ownership: pd.DataFrame, n: int = CHALK_N) -> list[str]:
    """The contest's `n` most-owned players: total ownership descending, ties by name."""
    if ownership is None or ownership.empty:
        return []
    ranked = ownership.assign(_name=ownership["player"].astype(str)).sort_values(
        ["pct_drafted", "_name"], ascending=[False, True], kind="stable"
    )
    return list(ranked["_name"].head(n))


def _features(slots, roster: Roster, known_position: dict[str, str], chalk: set[str]) -> dict:
    resolved: list[tuple[str, str, Resolved | None]] = []
    problems: list[tuple[str, str, str]] = []
    for slot, name in slots:
        player, reason = roster.resolve(name, slot, known_position.get(name))
        resolved.append((slot, name, player))
        if player is None:
            problems.append((name, slot, reason))

    qb = next((p for s, _, p in resolved if s == "QB"), None)
    dst = next((p for s, _, p in resolved if s == "DST"), None)
    flex = next((p for s, _, p in resolved if s == "FLEX"), None)
    offense = [(s, p) for s, _, p in resolved if s != "DST"]
    offense_ok = qb is not None and all(p is not None for _, p in offense)
    out: dict = {
        "chalk": sum(1 for _, name, _ in resolved if name in chalk) if chalk else math.nan,
        "resolved": offense_ok,
        "stack1": math.nan,
        "stack2": math.nan,
        "bringback": math.nan,
        "qb_team": "",
        "flex_pos": flex.position if flex is not None else "",
        "dst_with_qb": math.nan,
        "dst_vs_qb": math.nan,
        "unspent": math.nan,
        "problems": problems,
    }
    if offense_ok:
        catchers = [p for s, p in offense if s != "QB" and p.position in PASS_CATCHERS and p.team == qb.team]
        out["stack1"] = float(len(catchers) >= 1)
        out["stack2"] = float(len(catchers) >= 2)
        out["bringback"] = float(any(s != "QB" and p.team == qb.opponent for s, p in offense))
        out["qb_team"] = qb.team if catchers else ""
    if qb is not None and dst is not None:
        out["dst_with_qb"] = float(dst.team == qb.team)
        out["dst_vs_qb"] = float(dst.team == qb.opponent)
    if all(p is not None for _, _, p in resolved) and all(p.salary is not None for _, _, p in resolved):
        out["unspent"] = float(SALARY_CAP - sum(p.salary for _, _, p in resolved))
    return out


def _mean(values: pd.Series) -> float:
    valid = values.dropna()
    return float(valid.mean()) if len(valid) else math.nan


def _group_row(
    contest_id: str, group: str, feats: pd.DataFrame, n_unparsed: int, top_stacks: Counter
) -> dict:
    n = len(feats)
    valid_stack = feats["stack1"].notna()
    shown = ";".join(
        f"{team}:{round(100 * count / max(int(valid_stack.sum()), 1))}%"
        for team, count in top_stacks.most_common(TOP_STACKS_SHOWN)
    )
    flex_known = feats["flex_pos"] != ""
    flex_n = int(flex_known.sum())

    def flex_share(pos: str) -> float:
        return float((feats["flex_pos"] == pos).sum() / flex_n) if flex_n else math.nan

    unspent = feats["unspent"].dropna()
    dups = feats["duplicated"]
    return {
        "contest_id": contest_id,
        "group": group,
        "n": n,
        "n_unparsed": n_unparsed,
        "n_resolved": int(feats["resolved"].sum()),
        "n_salary": int(len(unspent)),
        "qb_stack1": _mean(feats["stack1"]),
        "qb_stack2": _mean(feats["stack2"]),
        "bringback": _mean(feats["bringback"]),
        "avg_unspent": float(unspent.mean()) if len(unspent) else math.nan,
        "share_unspent_1k": float((unspent >= UNSPENT_THRESHOLD).mean()) if len(unspent) else math.nan,
        "flex_rb": flex_share("RB"),
        "flex_wr": flex_share("WR"),
        "flex_te": flex_share("TE"),
        "avg_chalk": _mean(feats["chalk"]),
        "dst_with_qb": _mean(feats["dst_with_qb"]),
        "dst_vs_qb": _mean(feats["dst_vs_qb"]),
        "dup_entries": int(dups.sum()),
        "dup_groups": int(feats.loc[dups, "lineup_key"].nunique()),
        "dup_share": float(dups.mean()) if n else math.nan,
        "top_stacks": shown,
        "chalk_players": "",
    }


def field_stats(
    standings: Standings, players: pd.DataFrame | None = None, *, me: str | None = None
) -> FieldResult:
    """The field-building measures for one contest. See the module docstring."""
    empty = pd.DataFrame(columns=FIELD_COLUMNS)
    no_unmatched = pd.DataFrame(columns=["contest_id", "player", "slot", "reason"])
    no_stacks = pd.DataFrame(columns=["contest_id", "group", "team", "n", "share"])
    if standings.kind != "Classic":
        reasons = {
            "Showdown": "Showdown contest: the field measures are defined for Classic rosters only",
            "Mixed": "the file mixes Classic and Showdown lineups",
            "Unknown": "no lineup parsed",
        }
        reason = reasons.get(standings.kind, f"{standings.kind} contest")
        return FieldResult(empty, no_unmatched, no_stacks, pd.DataFrame(), skipped=reason)

    cid = standings.contest_id
    roster = Roster(players)
    owned = standings.ownership
    known_position = {}
    if len(owned):
        known_position = {
            str(p).strip(): pos
            for p, pos in zip(owned["player"], owned["dk_position"], strict=True)
            if pos != "FLEX"
        }
    chalk_list = chalk_players(owned)
    chalk = set(chalk_list)

    rows = []
    unmatched: dict[tuple[str, str], str] = {}
    for rec in standings.lineups.itertuples(index=False):
        feats = _features(rec.slots, roster, known_position, chalk)
        for name, slot, reason in feats.pop("problems"):
            unmatched.setdefault((name, slot), reason)
        feats.update(
            entry_id=rec.entry_id,
            entry_name=rec.entry_name,
            points=rec.points,
            lineup_key="|".join(sorted(name for _, name in rec.slots)),
        )
        rows.append(feats)
    features = pd.DataFrame(rows)
    if features.empty:
        return FieldResult(empty, no_unmatched, no_stacks, features, skipped="no lineup parsed")

    counts = features["lineup_key"].value_counts()
    features["duplicated"] = features["lineup_key"].map(counts) >= 2

    entries = standings.entries
    top_cut = standings.profile.top10
    in_top = entries["points"] >= top_cut
    groups: list[tuple[str, pd.Series, int]] = [
        ("all", pd.Series(True, index=features.index), len(entries)),
        (
            "top10",
            features["points"] >= top_cut,
            int(in_top.sum()),
        ),
    ]
    if me:
        mine = features["entry_name"].str.casefold() == me.casefold()
        groups.append(("me", mine, int((entries["entry_name"].str.casefold() == me.casefold()).sum())))

    out_rows, stack_rows = [], []
    for label, mask, n_entries in groups:
        sub = features[mask]
        if label == "me" and sub.empty and n_entries == 0:
            continue
        stacked = Counter(t for t in sub["qb_team"] if t)
        row = _group_row(cid, label, sub, n_entries - len(sub), stacked)
        row["chalk_players"] = ";".join(chalk_list)
        out_rows.append(row)
        denominator = max(int(sub["stack1"].notna().sum()), 1)
        for team, count in stacked.most_common():
            stack_rows.append(
                {"contest_id": cid, "group": label, "team": team, "n": count, "share": count / denominator}
            )

    miss = pd.DataFrame(
        [{"contest_id": cid, "player": p, "slot": s, "reason": r} for (p, s), r in sorted(unmatched.items())],
        columns=["contest_id", "player", "slot", "reason"],
    )
    return FieldResult(
        rows=pd.DataFrame(out_rows, columns=FIELD_COLUMNS),
        unmatched=miss,
        stacks=pd.DataFrame(stack_rows, columns=["contest_id", "group", "team", "n", "share"]),
        features=features,
    )
