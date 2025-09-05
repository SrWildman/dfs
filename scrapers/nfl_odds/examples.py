#!/usr/bin/env python3

"""
NFL Odds Scraper Usage Examples
Demonstrates various ways to use the scraper
"""

import sys
from pathlib import Path

from nfl_odds_scraper import NFLOddsScraper

def example_basic_usage():
    """Basic usage example"""
    print("📋 Example 1: Basic Usage")
    print("-" * 30)

    scraper = NFLOddsScraper()

    # Scrape current week (Week 1)
    result = scraper.scrape_week(week=1, verbose=True)

    if result:
        print(f"✅ Success! File saved: {result}")
    else:
        print("❌ Failed to scrape data")

def example_multiple_weeks():
    """Scrape multiple weeks"""
    print("\n📋 Example 2: Multiple Weeks")
    print("-" * 30)

    scraper = NFLOddsScraper()

    weeks = [1, 2, 3, 4, 5]  # First 5 weeks

    for week in weeks:
        print(f"\nScraping Week {week}...")
        result = scraper.scrape_week(week=week)
        if result:
            print(f"✅ Week {week} saved")
        else:
            print(f"❌ Week {week} failed")

def example_custom_directory():
    """Use custom output directory"""
    print("\n📋 Example 3: Custom Directory")
    print("-" * 30)

    # Create custom directory
    custom_dir = Path.home() / "nfl_odds_data"
    custom_dir.mkdir(exist_ok=True)

    scraper = NFLOddsScraper(output_dir=custom_dir)

    result = scraper.scrape_week(week=1)
    if result:
        print(f"✅ File saved to custom directory: {result}")

def example_different_season():
    """Scrape data from a different season"""
    print("\n📋 Example 4: Different Season")
    print("-" * 30)

    scraper = NFLOddsScraper()

    # Scrape Week 10 of 2024 season
    result = scraper.scrape_week(week=10, season=2024, verbose=True)

    if result:
        print(f"✅ 2024 Season data saved: {result}")
    else:
        print("❌ No data available for that season/week")

def example_programmatic_access():
    """Access data programmatically without saving"""
    print("\n📋 Example 5: Programmatic Access")
    print("-" * 30)

    scraper = NFLOddsScraper()

    # Get raw data
    raw_data = scraper.fetch_odds_data(week=1)
    if raw_data:
        print(f"✅ Fetched data for {len(raw_data)} entries")

        # Parse just the odds
        odds_data = scraper.parse_draftkings_odds(raw_data)

        # Process the data however you want
        favorites = [team for team in odds_data if team['moneyline'].startswith('-')]
        underdogs = [team for team in odds_data if team['moneyline'].startswith('+')]

        print(f"📊 Found {len(favorites)} favorites and {len(underdogs)} underdogs")

        # Show biggest favorite and underdog
        if favorites:
            biggest_favorite = min(favorites, key=lambda x: int(x['moneyline']))
            print(f"🏆 Biggest favorite: {biggest_favorite['team']} ({biggest_favorite['moneyline']})")

        if underdogs:
            biggest_underdog = max(underdogs, key=lambda x: int(x['moneyline'].replace('+', '')))
            print(f"🎯 Biggest underdog: {biggest_underdog['team']} ({biggest_underdog['moneyline']})")

def run_all_examples():
    """Run all examples"""
    print("🏈 NFL ODDS SCRAPER - USAGE EXAMPLES")
    print("=" * 50)

    try:
        example_basic_usage()
        example_multiple_weeks()
        example_custom_directory()
        example_different_season()
        example_programmatic_access()

        print(f"\n🎉 All examples completed!")
        print(f"📂 Check your Downloads folder for CSV files")

    except Exception as e:
        print(f"❌ Error running examples: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        example_name = sys.argv[1]

        examples = {
            'basic': example_basic_usage,
            'multiple': example_multiple_weeks,
            'custom': example_custom_directory,
            'season': example_different_season,
            'programmatic': example_programmatic_access
        }

        if example_name in examples:
            examples[example_name]()
        else:
            print(f"Available examples: {', '.join(examples.keys())}")
    else:
        run_all_examples()