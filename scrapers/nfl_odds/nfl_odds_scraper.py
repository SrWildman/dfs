#!/usr/bin/env python3

"""
NFL Odds Scraper for Rotowire DraftKings Data
Configurable for any NFL week, extracts real-time odds data
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

import requests

# TODO: get values from config
# Configuration constants
ROTOWIRE_BASE_URL = "https://www.rotowire.com/betting/nfl/tables/nfl-games-by-market.php"
DEFAULT_TIMEOUT = 30  # Seconds for HTTP requests
CONTENT_SAMPLE_SIZE = 500  # Characters to read for content analysis
MIN_NFL_WEEK = 1  # Minimum NFL week number
MAX_NFL_WEEK = 18  # Maximum NFL week number

class NFLOddsScraper:
    """
    Scraper for NFL odds data from Rotowire's DraftKings integration.

    Fetches real-time NFL odds data including moneylines, spreads, and totals
    for any NFL week and saves the data in CSV format.
    """

    def __init__(self, output_dir=None):
        self.output_dir = Path(output_dir) if output_dir else Path.home() / "Downloads"
        self.base_url = "https://www.rotowire.com/betting/nfl/tables/nfl-games-by-market.php"

    def fetch_odds_data(self, week=1, season=None):
        """
        Fetch NFL odds data from Rotowire API.

        Args:
            week (int): NFL week number (1-18)
            season (int, optional): NFL season year. Defaults to current year

        Returns:
            dict or None: JSON response from API, or None if request failed
        """
        if season is None:
            season = datetime.now().year

        params = {
            'week': str(week),
            'season': str(season)
        }

        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://www.rotowire.com/betting/nfl/odds',
            'Connection': 'keep-alive'
        }

        try:
            print(f"🔍 Fetching NFL Week {week} odds from Rotowire...")
            response = requests.get(self.base_url, params=params, headers=headers, timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()

            print(f"✅ Response received: {len(response.text)} characters")
            return response.json()

        except requests.RequestException as e:
            print(f"❌ Error fetching data: {e}")
            return None
        except json.JSONDecodeError as e:
            print(f"❌ Error parsing JSON: {e}")
            return None

    def parse_draftkings_odds(self, data):
        """
        Parse DraftKings odds from the API response.

        Extracts and formats DraftKings-specific odds data from the raw API response.

        Args:
            data (dict): Raw JSON response from Rotowire API

        Returns:
            list: List of formatted odds dictionaries for each team
        """
        if not data:
            return []

        odds_data = []

        for game in data:
            try:
                # Extract basic game info
                team_name = game.get('nickname', '')
                game_date = game.get('gameDate', '')
                home_away = game.get('homeAway', '')
                abbr = game.get('abbr', '')

                # Extract DraftKings odds
                dk_moneyline = game.get('draftkings_moneyline')
                dk_spread = game.get('draftkings_spread')
                dk_ou = game.get('draftkings_ou')
                dk_team_total_over = game.get('draftkings_teamTotalOver')

                # Skip if no DraftKings data
                if not any([dk_moneyline, dk_spread, dk_ou]):
                    continue

                # Format the odds data
                formatted_moneyline = self._format_moneyline(dk_moneyline)
                formatted_spread = self._format_spread(dk_spread)
                team_points = str(dk_team_total_over) if dk_team_total_over else ""

                odds_entry = {
                    'team': team_name,
                    'date': game_date,
                    'moneyline': formatted_moneyline,
                    'spread': formatted_spread,
                    'total': str(dk_ou) if dk_ou else "",
                    'team_points': team_points,
                    'home_away': home_away,
                    'abbr': abbr
                }

                odds_data.append(odds_entry)

            except Exception as e:
                print(f"❌ Error processing game: {e}")
                continue

        return odds_data

    def _format_moneyline(self, moneyline):
        """
        Format moneyline with proper +/- signs.

        Args:
            moneyline (str or int): Raw moneyline value

        Returns:
            str: Formatted moneyline with proper +/- prefix
        """
        if not moneyline:
            return ""
        try:
            ml = int(moneyline)
            return f"+{ml}" if ml > 0 else str(ml)
        except (ValueError, TypeError):
            return str(moneyline) if moneyline else ""

    def _format_spread(self, spread):
        """
        Format spread with proper +/- signs.

        Args:
            spread (str or float): Raw spread value

        Returns:
            str: Formatted spread with proper +/- prefix
        """
        if not spread:
            return ""
        try:
            sp = float(spread)
            return f"+{sp}" if sp > 0 else str(sp)
        except (ValueError, TypeError):
            return str(spread) if spread else ""

    def save_to_csv(self, odds_data, week, season=None):
        """
        Save odds data to CSV in the required format.

        Args:
            odds_data (list): List of formatted odds dictionaries
            week (int): NFL week number
            season (int, optional): NFL season year. Defaults to current year

        Returns:
            str or None: Path to saved CSV file, or None if save failed
        """
        if season is None:
            season = datetime.now().year

        filename = self.output_dir / f"NFL_Odds_Week_{week}_{season}_DraftKings.csv"

        try:
            with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
                # Write header matching required format
                csvfile.write(',,Win,Cover,Total Points,Total Touchdowns,Team Points,Team TDs,Team TDs\n')
                csvfile.write('Team,Date,Moneyline,Spread,Over-Under,Over-Under,Over-Under,Over-Under,Over-Under\n')

                # Write data rows
                for data in odds_data:
                    team = data.get('team', '')
                    date = data.get('date', '')
                    moneyline = data.get('moneyline', '')
                    spread = data.get('spread', '')
                    total = data.get('total', '')
                    team_points = data.get('team_points', '')

                    row = f"{team},{date},{moneyline},{spread},{total},,{team_points},,\n"
                    csvfile.write(row)

            print(f"✅ Odds data saved to: {filename}")
            return str(filename)

        except Exception as e:
            print(f"❌ Error saving CSV: {e}")
            return None

    def scrape_week(self, week, season=None, verbose=False):
        """
        Scrape odds for a specific week.

        Complete workflow: fetch data, parse odds, save to CSV, and provide summary.

        Args:
            week (int): NFL week number (1-18)
            season (int, optional): NFL season year. Defaults to current year
            verbose (bool): Whether to display detailed team-by-team odds

        Returns:
            str or None: Path to saved CSV file, or None if scrape failed
        """
        if season is None:
            season = datetime.now().year

        print(f"🏈 NFL ODDS SCRAPER - Week {week}, {season} Season")
        print("=" * 50)

        # Fetch data
        raw_data = self.fetch_odds_data(week, season)
        if not raw_data:
            return None

        # Parse DraftKings odds
        odds_data = self.parse_draftkings_odds(raw_data)

        if odds_data:
            if verbose:
                print(f"\n📊 Found DraftKings odds for {len(odds_data)} teams:")
                for entry in odds_data:
                    print(f"  {entry['team']} ({entry['abbr']}) - {entry['home_away'].upper()}: "
                          f"ML={entry['moneyline']}, Spread={entry['spread']}, O/U={entry['total']}")

            # Save to CSV
            output_file = self.save_to_csv(odds_data, week, season)

            if output_file:
                print(f"\n🎉 SUCCESS!")
                print(f"📁 File: {output_file}")
                print(f"📊 Teams: {len(odds_data)}")
                print(f"🏈 Games: {len(odds_data) // 2}")
                return output_file
            else:
                return None
        else:
            print("❌ No DraftKings odds data found")
            return None

def main():
    """
    Command line interface for the NFL odds scraper.

    Provides argument parsing and executes the scraping workflow
    based on provided command line arguments.
    """

    # TODO get from defaults or config
    parser = argparse.ArgumentParser(description='Scrape NFL DraftKings odds from Rotowire')
    parser.add_argument('--week', '-w', type=int, default=1, help='NFL week (1-18)')
    parser.add_argument('--season', '-s', type=int, help='NFL season year (default: current year)')
    parser.add_argument('--output-dir', '-o', help='Output directory (default: ~/Downloads)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')

    args = parser.parse_args()

    # Validate week
    if not MIN_NFL_WEEK <= args.week <= MAX_NFL_WEEK:
        print(f"❌ Week must be between {MIN_NFL_WEEK} and {MAX_NFL_WEEK}")
        return

    # Create scraper
    scraper = NFLOddsScraper(output_dir=args.output_dir)

    # Scrape the specified week
    result = scraper.scrape_week(
        week=args.week,
        season=args.season,
        verbose=args.verbose
    )

    if result:
        print(f"\n🔄 To scrape another week: python3 nfl_odds_scraper.py --week {args.week + 1}")
    else:
        print("\n💡 Try a different week or check your internet connection")

if __name__ == "__main__":
    main()