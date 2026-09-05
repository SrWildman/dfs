import pytest

from dfs.config import Config, ConfigError, load_config


def test_load_config_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr("dfs.config.CONFIG_FILE", tmp_path / "config.toml")
    load_config.cache_clear()
    with pytest.raises(ConfigError, match="No config.toml found"):
        load_config()


def test_config_rejects_unknown_keys():
    with pytest.raises(Exception):
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
    with pytest.raises(Exception):
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
    assert cfg.nfl_odds.default_week is None
