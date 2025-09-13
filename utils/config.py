#!/usr/bin/env python3
"""
DFS Configuration Management Utilities

Centralized configuration loading and validation for DFS workflows.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

# Type aliases for better code documentation
ScraperConfig = Tuple[Path, str, str]  # (path, script_name, description)
ScraperConfigList = List[ScraperConfig]
ConfigDict = Dict[str, Union[str, Dict, List]]
SheetsConfig = Dict[str, Union[str, Dict, None]]

# Scraper Configuration Constants
SCRAPER_SETTINGS = {
    "nfl_odds": {
        "rotowire_base_url": "https://www.rotowire.com/betting/nfl/tables/nfl-games-by-market.php",
        "default_timeout": 30,
        "content_sample_size": 500,
        "min_nfl_week": 1,
        "max_nfl_week": 18
    }
}


def load_config() -> Optional[ConfigDict]:
    """Load and parse the main application configuration from config.json."""
    project_root = Path(__file__).parent.parent
    config_path = project_root / "config.json"

    try:
        with open(config_path, 'r', encoding='utf-8') as config_file:
            return json.load(config_file)
    except FileNotFoundError:
        print(f"❌ Configuration file not found: {config_path}")
        print("💡 Ensure config.json exists in the project root directory")
        return None
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON in configuration file: {config_path}")
        print(f"💡 JSON parsing error: {e}")
        return None
    except PermissionError:
        print(f"❌ Permission denied accessing configuration file: {config_path}")
        print("💡 Check file permissions and try again")
        return None


def get_scraper_configs() -> ScraperConfigList:
    """Get the complete configuration for all available DFS scrapers."""
    project_root = Path(__file__).parent.parent
    base_dir = project_root / "scrapers"

    return [
        (base_dir / "draftkings", "scraper.py", "DraftKings Salaries"),
        (base_dir / "nfl_odds", "nfl_odds_scraper.py", "NFL Odds Data"),
        (base_dir / "tffb_sos", "scraper.py", "Strength of Schedule"),
        (base_dir / "fantasy_footballers", "scraper.py", "Projections"),
    ]


def get_update_scrapers() -> ScraperConfigList:
    """Get configuration for frequently updated scrapers only (excludes DraftKings)."""
    project_root = Path(__file__).parent.parent
    base_dir = project_root / "scrapers"

    return [
        (base_dir / "nfl_odds", "nfl_odds_scraper.py", "NFL Odds Data"),
        (base_dir / "fantasy_footballers", "scraper.py", "Projections"),
    ]


def get_scraper_settings(scraper_name: str) -> Dict[str, Union[str, int]]:
    """Get configuration settings for a specific scraper."""
    if scraper_name not in SCRAPER_SETTINGS:
        raise KeyError(f"Scraper '{scraper_name}' not found in configuration")
    
    return SCRAPER_SETTINGS[scraper_name]


def get_google_sheets_config() -> Optional[SheetsConfig]:
    """Extract and process Google Sheets configuration from main config."""
    config = load_config()
    if not config:
        return None

    sheets_config = config.get('google_sheets', {})

    return {
        'sheet_id': sheets_config.get('sheet_id', 'YOUR_SHEET_ID_HERE'),
        'credentials_file': sheets_config.get('credentials_file', 'credentials.json'),
        'tab_mappings': sheets_config.get('tab_mappings')
    }


def validate_google_sheets_config(sheets_config: Optional[SheetsConfig]) -> bool:
    """Validate Google Sheets configuration for completeness and correctness."""
    if not sheets_config:
        print("❌ No Google Sheets configuration found in config.json")
        print("💡 Add 'google_sheets' section to your configuration")
        return False

    # Validate sheet ID configuration
    if sheets_config['sheet_id'] == 'YOUR_SHEET_ID_HERE':
        print("❌ Google Sheet ID not configured")
        print("💡 Edit config.json and replace 'YOUR_SHEET_ID_HERE' with your actual Sheet ID")
        return False

    # Validate credentials file existence and accessibility
    project_root = Path(__file__).parent.parent
    creds_path = project_root / sheets_config['credentials_file']
    if not creds_path.exists():
        print(f"❌ Google API credentials file not found: {creds_path}")
        print("💡 Download credentials.json from Google Cloud Console and place in project root")
        return False

    if not creds_path.is_file():
        print(f"❌ Credentials path exists but is not a file: {creds_path}")
        print("💡 Ensure the credentials path points to a valid JSON file")
        return False

    return True


if __name__ == "__main__":
    print("🧪 DFS Configuration Management - Testing Suite")
    print("=" * 60)

    config = load_config()
    if config:
        print("✅ Main configuration loaded successfully")
        print(f"📊 Configuration sections: {list(config.keys())}")
    else:
        print("❌ Main configuration loading failed")

    full_scrapers = get_scraper_configs()
    update_scrapers = get_update_scrapers()
    print(f"📈 Full workflow scrapers: {len(full_scrapers)}")
    print(f"⚡ Quick update scrapers: {len(update_scrapers)}")

    sheets_config = get_google_sheets_config()
    if sheets_config:
        is_valid = validate_google_sheets_config(sheets_config)
        status = "✅ Valid and ready" if is_valid else "❌ Invalid or incomplete"
        print(f"🔗 Google Sheets config: {status}")
    else:
        print("❌ Google Sheets configuration not found")

    print("\n🎯 Configuration testing completed!")
    print("=" * 60)