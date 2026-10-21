"""`dfs backtest`'s settings file and flag registry."""

import pytest

from dfs import backtest_settings as bs


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    monkeypatch.setattr(bs, "FLAGS", {})
    monkeypatch.setattr(bs, "PLUGIN_MODULES", ())


def write(tmp_path, text, name="s.toml"):
    p = tmp_path / name
    p.write_text(text)
    return p


def test_no_file_is_todays_defaults():
    s = bs.load(None)
    assert (s.name, s.cash, s.gpp, s.rank, s.pool, s.flags, s.rules) == (
        "today",
        3,
        None,
        "top1",
        "auto",
        {},
        {},
    )


def test_a_file_overrides_what_it_names_and_is_called_by_its_stem_unless_it_says_otherwise(tmp_path):
    s = bs.load(write(tmp_path, '[builds]\ncash = 2\ngpp = 12\nrank = "share"\nseconds = 30\n', "mine.toml"))
    assert (s.name, s.cash, s.gpp, s.rank, s.seconds) == ("mine", 2, 12, "share", 30.0)
    assert bs.load(write(tmp_path, 'name = "labelled"\n')).name == "labelled"


def test_rules_keys_take_the_box_labels_or_their_short_names(tmp_path):
    s = bs.load(
        write(tmp_path, '[rules]\nnever = "Bijan"\n"Adjust" = "Nacua +2"\nbuild_rules = "min unique 2"\n')
    )
    assert s.rules == {"Never use": "Bijan", "Adjust": "Nacua +2", "Build rules": "min unique 2"}
    assert s.unsupported_rules() == ["Build rules"]


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("colour = 1\n", "unknown settings key"),
        ("[builds]\nfoo = 1\n", r"unknown \[builds\] key"),
        ('[builds]\nrank = "best"\n', "rank must be"),
        ('pool = "mine"\n', "pool must be"),
        ('[rules]\nlunch = "x"\n', "unknown rules line"),
        ("[builds\n", "not valid TOML"),
    ],
)
def test_a_typo_is_an_error_not_a_silent_default(tmp_path, text, fragment):
    with pytest.raises(bs.SettingsError, match=fragment):
        bs.load(write(tmp_path, text))


def test_an_unregistered_flag_is_an_error_naming_the_registered_ones(tmp_path):
    bs.register(bs.Flag("portfolio", "a set"))
    with pytest.raises(bs.SettingsError) as e:
        bs.load(write(tmp_path, "[flags]\nownership_uncertainty = true\n"))
    assert "ownership_uncertainty" in str(e.value) and "portfolio" in str(e.value)


def test_a_flag_set_false_is_off_and_needs_no_registration(tmp_path):
    s = bs.load(write(tmp_path, "[flags]\nownership_uncertainty = false\n"))
    assert s.active_flags() == []


def test_active_flags_come_in_registration_order():
    a = bs.register(bs.Flag("a", "first"))
    b = bs.register(bs.Flag("b", "second"))
    s = bs.Settings(flags={"b": True, "a": {"size": 3}, "c": False})
    assert s.active_flags() == [a, b]


def test_the_fingerprint_follows_what_changes_the_builds_and_not_the_label():
    base = bs.Settings()
    assert base.fingerprint("x") == bs.Settings(name="renamed").fingerprint("x")
    assert base.fingerprint("x") != base.fingerprint("y")  # another commit
    assert base.fingerprint("x") != bs.Settings(rank="share").fingerprint("x")
    assert base.fingerprint("x") != bs.Settings(rules={"Never use": "Bijan"}).fingerprint("x")
    assert bs.Settings(rules={"Never use": " "}).fingerprint("x") == base.fingerprint(
        "x"
    )  # a blank line is no line
    bs.register(bs.Flag("f", "d", version="1"))
    on = bs.Settings(flags={"f": True})
    assert on.fingerprint("x") != base.fingerprint("x")
    before = on.fingerprint("x")
    bs.register(bs.Flag("f", "d", version="2"))  # the implementation changed: a saved run is stale
    assert on.fingerprint("x") != before
    assert bs.Settings(flags={"f": False}).fingerprint("x") == base.fingerprint("x")


def test_plugin_modules_are_imported_when_a_flag_is_missing_and_a_missing_module_is_skipped(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(bs, "PLUGIN_MODULES", ("dfs.not_on_this_branch",))
    assert bs.load_plugins() == ["dfs.not_on_this_branch"]
    with pytest.raises(bs.SettingsError):
        bs.load(write(tmp_path, "[flags]\nsomething = true\n"))
