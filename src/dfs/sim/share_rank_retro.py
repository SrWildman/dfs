"""Plan S, check 3: a Flashback comparison split by the GPPs Sam plays most, single-entry and small-field.

`dfs backtest --compare` reports every GPP together. This reruns the same comparison (the saved builds, so
nothing is rebuilt when the code stamp matches) and splits the paired ROI difference by contest group:

- `all GPP`;
- `single-entry`: a series named "[Single Entry]" (his Hundos, Fair Catches, Huddles, Daily Dollars);
- `small field`: under `SMALL_FIELD` entrants (the tiers' line for a SE GPP);
- `SE or small`: either, the check's target.

    python -m dfs.sim.share_rank_retro --weeks 2,4,5,6,7,8 --settings a.toml --compare b.toml [--code STAMP]

`--code` reads the builds saved under that code stamp (the one `dfs backtest` printed), for when `src/` has
changed since. Read-only: no sheet, nothing written.
"""

from __future__ import annotations

import argparse

import pandas as pd

from dfs import backtest as bt
from dfs import backtest_settings as bs

SMALL_FIELD = 1000

GROUPS = {
    "all GPP": lambda t: pd.Series(True, index=t.index),
    "single-entry": lambda t: t["series"].str.contains("Single Entry", case=False, regex=False),
    "small field": lambda t: t["entries_field"] < SMALL_FIELD,
    "SE or small": lambda t: (
        t["series"].str.contains("Single Entry", case=False, regex=False) | (t["entries_field"] < SMALL_FIELD)
    ),
}


def paired_table(a: bt.Result, b: bt.Result) -> pd.DataFrame:
    """Per contest group and scope: A minus B in ROI points by week (SE, weeks won) and by contest."""
    rows = pd.concat(
        [
            bt.rename_side(a.rows[a.rows["side"] == "backtest"], "A"),
            bt.rename_side(b.rows[b.rows["side"] == "backtest"], "B"),
        ],
        ignore_index=True,
    )
    rows = rows[rows["fmt"] == "gpp"]
    t = bt.contest_table(rows)
    size = rows.groupby("contest")["entries"].first()
    t["entries_field"] = t["contest"].map(size)
    out = []
    for name, pick in GROUPS.items():
        sub = t[pick(t)]
        for scope in ("entry", "any"):
            p = bt.paired_vs(sub, "A", "B", scope)
            side = sub[sub["scope"] == scope]
            roi = {
                s: side[side["side"] == s]["paid"].sum() / side[side["side"] == s]["cost"].sum() - 1
                for s in "AB"
            }
            out.append(
                {
                    "group": name,
                    "scope": "as entered" if scope == "entry" else "any build",
                    "contests": p["contests"],
                    "weeks": p["weeks"],
                    "ROI A": roi["A"],
                    "ROI B": roi["B"],
                    "by week": p["diff_week"],
                    "SE": p["se_week"],
                    "weeks won": p["weeks_won"],
                    "by contest": p["diff_contest"],
                    "SE (contest)": p["se_contest"],
                    "clears": bool(p["diff_week"] > 2 * p["se_week"] and p["weeks_won"] > p["weeks"] / 2),
                }
            )
    return pd.DataFrame(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", default="2,4,5,6,7,8")
    ap.add_argument("--settings", required=True)
    ap.add_argument("--compare", required=True)
    ap.add_argument("--code", help="the code stamp the builds were saved under")
    args = ap.parse_args()
    if args.code:
        bt.code_stamp = lambda: args.code  # read the saved builds of that run
    weeks = [int(w) for w in args.weeks.split(",")]
    a, b = bs.load(args.settings), bs.load(args.compare)
    res = bt.backtest(weeks, [a, b], workers=1)
    pd.set_option("display.width", 220)
    print(f"{a.name} (A) minus {b.name} (B), GPP contests, ROI points")
    print(paired_table(res[a.name], res[b.name]).round(3).to_string(index=False))


if __name__ == "__main__":
    main()
