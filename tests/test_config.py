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
