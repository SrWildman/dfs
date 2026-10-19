"""Retro-test helpers: the contest class of a week whose history export is not in the log yet."""

import pandas as pd

from dfs.sim import retro


def test_class_from_name_grades_double_ups_and_gpps_and_skips_h2h_showdown_and_free():
    assert retro.class_from_name("NFL GIANT $25 Double Up [Single Entry]", 25.0) == "cash"
    assert retro.class_from_name("NFL $4K Singleback [5 Entry Max]", 1.0) == "gpp"
    assert retro.class_from_name("NFL $1 srwildman vs. TBD", 1.0) == ""
    assert retro.class_from_name("NFL Showdown $3K Cover Four", 3.0) == ""
    assert retro.class_from_name("$2K T-Mobile Football Takeover", 0.0) == ""  # a freeroll


def test_entries_file_meta_reads_contest_name_and_fee_once_per_contest(tmp_path):
    path = tmp_path / "DraftKings Entries (1).csv"
    pd.DataFrame(
        {
            "Entry ID": ["1", "2", "3"],
            "Contest Name": ["NFL $10 Hundo [Single Entry]", "NFL $10 Hundo [Single Entry]", "NFL $5 DU"],
            "Contest ID": ["900", "900", "901"],
            "Entry Fee": ["$10", "$10", "$5"],
        }
    ).to_csv(path, index=False)
    meta = retro.entries_file_meta(path, 6)
    assert meta == {
        "900": {"week": 6, "series": "NFL $10 Hundo [Single Entry]", "fee": 10.0},
        "901": {"week": 6, "series": "NFL $5 DU", "fee": 5.0},
    }
