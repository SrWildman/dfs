"""Game-day injury news laid over the sync's Avail for late swap."""

from datetime import UTC, datetime

import pandas as pd

from dfs import late_swap_news as n


def _edge():
    return pd.DataFrame(
        {
            "Name": ["Josh Allen", "Puka Nacua", "Bench Guy", "Twin Name", "Twin Name"],
            "Avail": ["", "Q", "OUT", "", ""],
        }
    )


def _payload():
    def item(name, status, date="2026-10-18T19:30Z"):
        return {"athlete": {"displayName": name}, "status": status, "date": date, "shortComment": "note"}

    return {
        "injuries": [
            {
                "injuries": [
                    item("Josh Allen", "Questionable"),
                    item("Puka Nacua", "Out"),
                    item("Bench Guy", "Active"),
                    item("Twin Name", "Out"),
                    item("Nobody Here", "Out"),
                ]
            }
        ]
    }


def test_news_only_escalates_and_skips_ambiguous_or_unknown_names():
    edge, changes = n.apply_news(_edge(), n.parse_news(_payload()))
    assert list(edge["Avail"]) == [
        "Q",
        "OUT",
        "OUT",
        "",
        "",
    ]  # Active never clears OUT; the twins are skipped
    assert [(c.name, c.before, c.after) for c in changes] == [
        ("Puka Nacua", "Q", "OUT"),
        ("Josh Allen", "", "Q"),
    ]  # worst first


def test_a_softer_status_never_overwrites_a_harder_one():
    edge = pd.DataFrame({"Name": ["Puka Nacua"], "Avail": ["OUT"]})
    news = pd.DataFrame(
        {"Name": ["Puka Nacua"], "Status": ["Questionable"], "Updated": [pd.NaT], "Note": [""]}
    )
    assert n.apply_news(edge, news)[1] == []


def test_the_timestamp_and_age():
    news = n.parse_news(_payload())
    assert news["Updated"].iloc[0] == pd.Timestamp("2026-10-18T19:30Z")
    assert (
        n.age_minutes(datetime(2026, 10, 18, 19, 30, tzinfo=UTC), datetime(2026, 10, 18, 20, 0, tzinfo=UTC))
        == 30
    )


def test_an_empty_feed_changes_nothing():
    edge, changes = n.apply_news(_edge(), pd.DataFrame(columns=n.NEWS_COLUMNS))
    assert changes == [] and edge.equals(_edge())
