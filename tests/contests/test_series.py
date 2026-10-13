"""Series normalisation and format tagging, on every contest name Sam plays plus edge cases."""

from decimal import Decimal

import pandas as pd
import pytest

from dfs.contests import series as S


def classify(game_type, name, fee="10", entries=1000, paid=200, **kw):
    return S.classify(game_type, name, fee=Decimal(str(fee)), contest_entries=entries, places_paid=paid, **kw)


@pytest.mark.parametrize(
    ("name", "key", "fmt"),
    [
        # single-entry GPPs
        ("NFL $8K Fair Catch [Single Entry]", "NFL $8K Fair Catch [Single Entry]", S.SE_GPP),
        ("NFL $75K Fair Catch [Single Entry]", "NFL $75K Fair Catch [Single Entry]", S.SE_GPP),
        ("NFL $6K Huddle [Single Entry]", "NFL $6K Huddle [Single Entry]", S.SE_GPP),
        ("NFL $10 Hundo [Single Entry]", "NFL $10 Hundo [Single Entry]", S.SE_GPP),
        # multi-entry GPPs: the entry counter is stripped, the cap stays in the key
        ("NFL $3K Screen Pass [3 Entry Max] (2/3)", "NFL $3K Screen Pass [3 Entry Max]", S.N_MAX_GPP),
        ("NFL $4K Singleback [5 Entry Max] (3/5)", "NFL $4K Singleback [5 Entry Max]", S.N_MAX_GPP),
        ("NFL $4K Singleback [5 Entry Max] (1/5)", "NFL $4K Singleback [5 Entry Max]", S.N_MAX_GPP),
    ],
)
def test_gpp_names(name, key, fmt):
    info = classify("Classic", name, entries=2000, paid=400)
    assert (info.series, info.format) == (key, fmt)


@pytest.mark.parametrize(
    ("name", "key", "fmt"),
    [
        ("NFL GIANT $25 Double Up [Single Entry]", "NFL GIANT $25 Double Up [Single Entry]", S.DOUBLE_UP),
        ("NFL Single Entry $5 Double Up", "NFL Single Entry $5 Double Up", S.DOUBLE_UP),
        ("NFL $2 Double Up", "NFL $2 Double Up", S.DOUBLE_UP),
        ("NFL $5 50/50", "NFL $5 50/50", S.FIFTY_FIFTY),
    ],
)
def test_cash_names(name, key, fmt):
    info = classify("Classic", name, entries=8000, paid=3600)
    assert (info.series, info.format) == (key, fmt)
    assert info.format_class == "cash" and info.bankroll_class == "cash" and info.agrees


def test_double_up_wins_over_single_entry_in_the_same_name():
    assert classify("Classic", "NFL Single Entry $5 Double Up", entries=8000, paid=3600).format == S.DOUBLE_UP


def test_h2h_strips_the_opponent_whichever_side_it_is_on():
    a = classify("Classic", "NFL $1 srwildman vs. rival_one", fee=1, entries=2, paid=1)
    b = classify("Classic", "NFL $1 rival_one vs. srwildman", fee=1, entries=2, paid=1)
    assert (a.series, a.format, a.opponent) == ("H2H $1", S.H2H, "rival_one")
    assert (b.series, b.format, b.opponent) == ("H2H $1", S.H2H, "rival_one")
    assert a.format_class == "cash" and a.bankroll_class == "cash"


def test_h2h_username_comparison_ignores_case_and_follows_the_me_argument():
    info = classify("Classic", "NFL $5 Test_Me vs. other_guy", fee=5, entries=2, paid=1, me="test_me")
    assert (info.series, info.opponent) == ("H2H $5", "other_guy")


def test_a_two_entry_classic_with_no_vs_in_the_name_is_still_h2h():
    info = classify("Classic", "NFL $2 Head to Head", fee=2, entries=2, paid=1)
    assert (info.format, info.series, info.opponent) == (S.H2H, "H2H $2", "")


def test_h2h_fees_with_cents():
    assert classify("Classic", "NFL $0.50 a vs. b", fee="0.50", entries=2, paid=1).series == "H2H $0.50"


@pytest.mark.parametrize(
    "name",
    [
        "NFL Discord-Only Around the World Contest [Read Contest Description!]",
        "$2K T-Mobile Lightening Drive Football Takeover",
    ],
)
def test_freerolls_are_decided_by_a_zero_fee(name):
    info = classify("Classic", name, fee=0, entries=5000, paid=500)
    assert (info.format, info.series) == (S.FREEROLL, name)


def test_private_contest_keeps_the_creator_and_drops_the_instance_number():
    info = classify("Classic", "DREAMERS DFS  by bradikal36 #5", entries=12, paid=3)
    assert (info.format, info.series) == (S.PRIVATE, "DREAMERS DFS by bradikal36")
    assert classify("Classic", "DREAMERS DFS by bradikal36 #6", entries=12, paid=3).series == info.series


def test_a_public_nfl_contest_with_by_in_its_name_is_not_private():
    assert classify("Classic", "NFL $5K Pass by Pass [Single Entry]").format == S.SE_GPP


@pytest.mark.parametrize("name", ["NFL 10x Booster [Top 5 Win $100]", "NFL $10 Quintuple Up [Top 5 Win $50]"])
def test_small_top_heavy_contests(name):
    info = classify("Classic", name, entries=20, paid=5)
    assert (info.format, info.series) == (S.SMALL_TOP_HEAVY, name)


def test_small_top_heavy_boundaries():
    assert classify("Classic", "NFL x", entries=99, paid=5).format == S.SMALL_TOP_HEAVY
    assert classify("Classic", "NFL x", entries=100, paid=5).format == S.OTHER  # field not under 100
    assert classify("Classic", "NFL x", entries=99, paid=6).format == S.OTHER  # more than 5 places
    assert classify("Classic", "NFL x", entries=99, paid=0).format == S.OTHER  # nothing paid recorded


def test_a_small_double_up_is_a_double_up_not_top_heavy():
    assert classify("Classic", "NFL $2 Double Up", entries=10, paid=4).format == S.DOUBLE_UP


def test_the_single_stat_contest_that_the_payout_band_called_cash_is_other():
    """3 of 7 paid is a 0.43 ratio, inside the cash band; Game_Type decides first."""
    info = classify(
        "Single Stat - Total Yards", "NFL Single Stat $1 7-Player (Top 3 Win)", fee=1, entries=7, paid=3
    )
    assert info.format == S.OTHER and info.format_class == "gpp"
    assert info.bankroll_class == "gpp" and info.agrees


@pytest.mark.parametrize(
    "game_type",
    [
        "Best Ball",
        "Playoff Best Ball",
        "Madden Classic",
        "Snake",
        "Single Stat - Total Yards",
        "In-Game Showdown (Q4)",
    ],
)
def test_other_game_types_never_get_a_classic_format(game_type):
    assert classify(game_type, "NFL $2 Double Up", entries=8000, paid=3600).format == S.OTHER


@pytest.mark.parametrize(
    "game_type",
    ["Showdown Captain Mode", "Showdown", "KFC Sunday Night Showdown Series", "showdown captain mode"],
)
def test_showdown_family_is_showdown(game_type):
    assert classify(game_type, "NFL Showdown $5K [Single Entry] (KC @ DEN)").format == S.SHOWDOWN


def test_showdown_strips_the_matchup_and_the_counter_and_keeps_the_matchup_aside():
    info = classify("Showdown Captain Mode", "NFL Showdown $10K Cover Four [5 Entry Max] (BUF @ LAR) (2/5)")
    assert info.series == "NFL Showdown $10K Cover Four [5 Entry Max]"
    assert (info.matchup, info.entry_no, info.entry_max) == ("BUF @ LAR", 2, 5)


def test_matchup_and_counter_in_either_order_and_with_extra_spaces():
    a = classify("Showdown", "NFL Showdown  $5K  (2/3)  (KC @ DEN)")
    b = classify("Showdown", "NFL Showdown $5K (KC@DEN) (2/3)")
    assert a.series == b.series == "NFL Showdown $5K"
    assert a.matchup == b.matchup == "KC @ DEN"


def test_a_freeroll_showdown_is_still_showdown():
    assert classify("Showdown", "NFL Showdown Freeroll (KC @ DEN)", fee=0).format == S.SHOWDOWN


def test_an_unrecognised_classic_name_is_other_not_a_guess():
    info = classify("Classic", "NFL Mystery Contest", entries=5000, paid=1000)
    assert (info.format, info.series) == (S.OTHER, "NFL Mystery Contest")


def test_missing_counts_do_not_crash():
    info = S.classify("Classic", "NFL $1 Thing", fee=None, contest_entries=None, places_paid=None)
    assert info.format == S.OTHER and info.bankroll_class == "gpp"


def test_entry_counter_alone_makes_a_multi_entry_gpp():
    assert classify("Classic", "NFL $5K Wildcard (2/20)", entries=3000, paid=600).format == S.N_MAX_GPP


def test_crosscheck_reports_disagreements_and_does_not_override():
    log = pd.DataFrame(
        {
            "Bankroll_Class": ["cash", "gpp", "cash"],
            "Format_Class": ["cash", "cash", "gpp"],
            "Format": ["Double Up", "Double Up", "Freeroll"],
        }
    )
    assert list(S.crosscheck(log).index) == [1, 2]
    # a Double Up that pays 20% is still a Double Up by name, flagged by the band
    odd = classify("Classic", "NFL $2 Double Up", entries=1000, paid=200)
    assert odd.format == S.DOUBLE_UP and not odd.agrees


def test_game_family():
    assert S.game_family(" Classic ") == "classic"
    assert S.game_family("In-Game Showdown (Q4)") == "other"
    assert S.game_family("Madden Classic") == "other"
