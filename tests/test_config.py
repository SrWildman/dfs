import pytest
from pydantic import ValidationError

from dfs.config import Config, ConfigError, load_config


def test_load_config_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr("dfs.config.CONFIG_FILE", tmp_path / "config.toml")
    load_config.cache_clear()
    with pytest.raises(ConfigError, match="No config.toml found"):
        load_config()


def test_config_rejects_unknown_keys():
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                "google_sheets": {
                    "sheet_id": "abc",
                    "credentials_file": "creds.json",
                    "tab_mappings": {"projections": "Projections"},
                },
                "this_key_does_not_exist": True,
            }
        )


def test_config_requires_sheet_id():
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                "google_sheets": {
                    "credentials_file": "creds.json",
                    "tab_mappings": {},
                }
            }
        )


def test_config_valid_minimal():
    cfg = Config.model_validate(
        {
            "google_sheets": {
                "sheet_id": "abc123",
                "credentials_file": "creds.json",
                "tab_mappings": {"projections": "Projections"},
            }
        }
    )
    assert cfg.google_sheets.sheet_id == "abc123"
    assert cfg.google_sheets.previous_sheet_id is None
    assert cfg.nfl_odds.default_week is None


def test_config_accepts_previous_sheet_id():
    cfg = Config.model_validate(
        {
            "google_sheets": {
                "sheet_id": "abc123",
                "previous_sheet_id": "old-id",
                "credentials_file": "creds.json",
                "tab_mappings": {},
            }
        }
    )
    assert cfg.google_sheets.previous_sheet_id == "old-id"


def _minimal(**extra):
    return {
        "google_sheets": {"sheet_id": "abc", "credentials_file": "creds.json", "tab_mappings": {}},
        **extra,
    }


def test_sim_gpp_target_defaults_to_190_and_is_configurable():
    assert Config.model_validate(_minimal()).sim.gpp_target == 190.0
    assert Config.model_validate(_minimal(sim={"gpp_target": 205})).sim.gpp_target == 205.0
    with pytest.raises(ValidationError):
        Config.model_validate(_minimal(sim={"cash_line": 140}))  # the cash line is Sam's typed Results value


def test_projection_shadow_defaults_and_overrides():
    base = {"google_sheets": {"sheet_id": "x", "credentials_file": "c.json", "tab_mappings": {}}}
    cfg = Config.model_validate(base)
    assert cfg.projection.shadow_sources == ["EspnPts", "PropPts"]
    assert cfg.projection.shadow_min_weeks == 3
    cfg = Config.model_validate(
        {**base, "projection": {"shadow_sources": ["PropPts"], "shadow_min_weeks": 4}}
    )
    assert cfg.projection.shadow_sources == ["PropPts"]
    assert cfg.projection.shadow_min_weeks == 4
    with pytest.raises(ValidationError):
        Config.model_validate({**base, "projection": {"shadow_min_weeks": 0}})


def _stakes_config(**stakes):
    return Config.model_validate(
        {
            "google_sheets": {"sheet_id": "abc", "credentials_file": "c.json", "tab_mappings": {}},
            "stakes": stakes,
        }
    )


def test_stakes_rules_are_all_off_by_default():
    cfg = Config.model_validate(
        {"google_sheets": {"sheet_id": "abc", "credentials_file": "c.json", "tab_mappings": {}}}
    )
    assert cfg.stakes.max_pct_bankroll_per_week is None
    assert cfg.stakes.max_entry_fee_pct is None
    assert cfg.stakes.split is None


def test_stakes_accepts_rules_and_a_split_that_adds_to_100():
    cfg = _stakes_config(max_pct_bankroll_per_week=5, max_entry_fee_pct=1.5, split={"cash": 70, "gpp": 30})
    assert cfg.stakes.max_entry_fee_pct == 1.5 and cfg.stakes.split.gpp == 30


def test_stakes_rejects_a_typo_a_budget_key_and_nonsense_values():
    for bad in (
        {"max_pct_bankroll": 5},  # a typo'd key
        {"weekly_budget": 200},  # the budget lives on the Bankroll tab, not here
        {"max_pct_bankroll_per_week": 0},
        {"max_entry_fee_pct": 101},
        {"split": {"cash": 60, "gpp": 60}},
        {"split": {"cash": 100}},
    ):
        with pytest.raises(ValidationError):
            _stakes_config(**bad)


def test_the_example_config_documents_stakes_and_still_loads():
    import tomllib

    from dfs.paths import CONFIG_EXAMPLE_FILE

    raw = tomllib.loads(CONFIG_EXAMPLE_FILE.read_text())
    assert raw["stakes"] == {}  # every rule is commented out until Sam sets it
