"""The soft-yellow "Consider" highlight: a player the model likes who is not in your pool.

A player is a favourite when his Do verb is `Cash add`, `GPP add` or `GPP leverage`. The verbs are computed in
Python (`edge_finder_tab`), so the Edge Finder tab keeps the favourites' ids in two hidden columns
(`FAVOURITE_CASH_COL`, `FAVOURITE_GPP_COL`, written by every sync, in rank order). Every other tab reads them:
the Name cell goes soft yellow while `Pool` is blank and his `Id` is on either list, and goes back to normal
the moment he is pooled (the rule is live; only the lists wait for a sync).

On the Edge Finder tab itself the same rule is read off the row (`Pool` blank and `Do` starting with a
favourite verb). Conditional formats cannot name another tab directly, so the other tabs reach the lists
through `INDIRECT`. Player Pool lists only pooled players, so nothing is ever highlighted there and it has no
rule.
"""

from __future__ import annotations

from dfs import edge_finder_tab as eft
from dfs.sheets import SheetsClient

CONSIDER_BG = {"red": 1.0, "green": 0.957, "blue": 0.761}  # soft yellow, #FFF4C2

CONSIDER_NOTE = (
    "Soft yellow = the model likes him (Cash add, GPP add or GPP leverage) and he is not in your pool yet."
)


def favourites_ref() -> str:
    """The text of the hidden favourites columns on the Edge Finder, for use inside INDIRECT."""
    return f"'{eft.EDGE_FINDER_TAB}'!${eft.FAVOURITE_CASH_COL}:${eft.FAVOURITE_GPP_COL}"


def listed_formula(id_ref: str) -> str:
    """True when the id in `id_ref` is on either favourites list (first row's reference, relative)."""
    return f'COUNTIF(INDIRECT("{favourites_ref()}"),{id_ref})>0'


def consider_formula(pool_ref: str, id_ref: str) -> str:
    """`Pool` blank and his id on a favourites list: the rule for every tab but the Edge Finder."""
    return f'AND({pool_ref}="",{listed_formula(id_ref)})'


def edge_finder_formula(pool_ref: str, do_ref: str) -> str:
    """The same rule on the Edge Finder tab, from the row's own `Pool` and `Do` cells."""
    verbs = "|".join(eft.CONSIDER_VERBS)
    return f'AND({pool_ref}="",REGEXMATCH({do_ref},"^({verbs})"))'


def add_consider_rule(client: SheetsClient, tab: str, a1_range: str, formula: str) -> None:
    """Soft-yellow fill on `a1_range` where `formula` (written for its first cell) is true."""
    client.add_boolean_rule(
        tab,
        a1_range,
        condition_type="CUSTOM_FORMULA",
        values=[f"={formula}"],
        fmt={"backgroundColor": CONSIDER_BG},
    )
