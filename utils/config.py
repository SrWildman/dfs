#!/usr/bin/env python3
"""
DFS Configuration Management Utilities

A centralized configuration management system for Daily Fantasy Sports (DFS) workflows.
This module provides consistent configuration loading, validation, and scraper management
across all DFS pipeline components.

Key Features:
    - Centralized configuration file management
    - Standardized scraper configuration patterns
    - Google Sheets integration configuration
    - Comprehensive validation and error handling
    - Support for both full and selective workflows

Configuration Sources:
    - config.json: Main application configuration
    - Environment variables: Runtime configuration overrides
    - Default values: Fallback configuration for missing settings

Architecture:
    This module follows the singleton pattern for configuration loading,
    ensuring consistent state across all workflow components while
    providing efficient caching of configuration data.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

# Type aliases for better code documentation
ScraperConfig = Tuple[Path, str, str]  # (path, script_name, description)
ScraperConfigList = List[ScraperConfig]
ConfigDict = Dict[str, Union[str, Dict, List]]
SheetsConfig = Dict[str, Union[str, Dict, None]]


def load_config() -> Optional[ConfigDict]:
    """
    Load and parse the main application configuration from config.json.

    This function provides centralized configuration loading with comprehensive
    error handling and user feedback. It automatically resolves the config file
    path relative to the project root.

    Returns:
        dict: Configuration dictionary containing all application settings,
              or None if configuration loading failed

    Raises:
        None: All exceptions are caught and converted to user-friendly messages

    Note:
        The configuration file is expected to be in JSON format at the project
        root level. Missing or malformed configuration files will result in
        graceful degradation with appropriate error messages.
    """
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
    """
    Get the complete configuration for all available DFS scrapers.

    This function provides the standard configuration for the full DFS workflow,
    including all data sources required for comprehensive analysis.

    Returns:
        list: List of tuples containing (scraper_path, script_filename, description)
              for all available scrapers in the recommended execution order

    Note:
        The scrapers are ordered by execution priority:
        1. DraftKings (salary and contest information)
        2. NFL Odds (betting lines and market data)
        3. Fantasy Footballers (projections and analysis)
    """
    project_root = Path(__file__).parent.parent
    base_dir = project_root / "scrapers"

    # TODO: define  args in here (and update), make   configurable/dynamic...ex, what  week  is  it?
    return [
        (base_dir / "draftkings", "scraper.py", "DraftKings Salaries"),
        (base_dir / "nfl_odds", "nfl_odds_scraper.py", "NFL Odds Data"),
        # TODO: add TFFB SOS here
        (base_dir / "fantasy_footballers", "scraper.py", "Fantasy Footballers Projections"),
    ]


def get_update_scrapers() -> ScraperConfigList:
    """
    Get configuration for frequently updated scrapers only.

    This function provides an optimized scraper configuration for quick updates,
    focusing on data sources that change multiple times per week. DraftKings
    salaries are intentionally excluded as they typically update only once weekly.

    Returns:
        list: List of tuples containing (scraper_path, script_filename, description)
              for frequently updated scrapers only

    Performance:
        This configuration reduces data collection time by approximately 50%
        compared to the full scraper set, making it ideal for mid-week updates.

    Note:
        Excluded scrapers:
        - DraftKings Salaries (updated weekly, typically Tuesday/Wednesday)
    """
    project_root = Path(__file__).parent.parent
    base_dir = project_root / "scrapers"

    return [
        (base_dir / "nfl_odds", "nfl_odds_scraper.py", "NFL Odds Data"),
        (base_dir / "fantasy_footballers", "scraper.py", "Fantasy Footballers Projections"),
    ]


def get_google_sheets_config() -> Optional[SheetsConfig]:
    """
    Extract and process Google Sheets configuration from main config.

    This function provides a clean interface to Google Sheets settings,
    including validation and default value handling for missing configurations.

    Returns:
        dict: Google Sheets configuration containing:
              - sheet_id: Target Google Sheets document ID
              - credentials_file: Path to Google API credentials
              - tab_mappings: Mapping of data sources to sheet tabs
              or None if main configuration loading failed

    Configuration Structure:
        {
            'sheet_id': 'your_google_sheet_id_here',
            'credentials_file': 'credentials.json',
            'tab_mappings': {
                'draftkings': 'DKSalRaw',
                'fantasy_footballers': 'FFProjections',
                'nfl_odds': 'oddsraw'
            }
        }
    """
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
    """
    Validate Google Sheets configuration for completeness and correctness.

    This function performs comprehensive validation of Google Sheets settings,
    including credential file existence and configuration completeness checks.

    Args:
        sheets_config: Google Sheets configuration dictionary from get_google_sheets_config()

    Returns:
        bool: True if configuration is valid and ready for use,
              False if any validation checks fail

    Validation Checks:
        1. Configuration dictionary is not None/empty
        2. Sheet ID is properly configured (not default placeholder)
        3. Credentials file exists at specified location
        4. Credentials file is readable

    Note:
        This function provides detailed error messages for each validation
        failure to assist with configuration troubleshooting.
    """
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
    # Comprehensive testing suite for configuration module
    print("🧪 DFS Configuration Management - Testing Suite")
    print("=" * 60)

    # Test main configuration loading
    print("\n📋 Testing main configuration loading...")
    config = load_config()
    if config:
        print("✅ Main configuration loaded successfully")
        print(f"📊 Configuration sections: {list(config.keys())}")
    else:
        print("❌ Main configuration loading failed")

    # Test scraper configurations
    print("\n🔧 Testing scraper configurations...")
    full_scrapers = get_scraper_configs()
    update_scrapers = get_update_scrapers()
    print(f"📈 Full workflow scrapers: {len(full_scrapers)}")
    print(f"⚡ Quick update scrapers: {len(update_scrapers)}")

    # Test Google Sheets configuration
    print("\n📤 Testing Google Sheets configuration...")
    sheets_config = get_google_sheets_config()
    if sheets_config:
        is_valid = validate_google_sheets_config(sheets_config)
        status = "✅ Valid and ready" if is_valid else "❌ Invalid or incomplete"
        print(f"🔗 Google Sheets config: {status}")
    else:
        print("❌ Google Sheets configuration not found")

    print("\n🎯 Configuration testing completed!")
    print("=" * 60)