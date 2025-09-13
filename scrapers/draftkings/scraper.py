#!/usr/bin/env python3
"""
DraftKings NFL Salary Scraper

Automatically finds the current week's main NFL slate and downloads player salary data.
Uses your existing browser login (Arc, Chrome, Safari, etc.) for authentication.

Usage:
    python scraper.py
"""

import subprocess
import sys
import time
import webbrowser
import re
import platform
from pathlib import Path
from datetime import datetime

import requests

# Add utils to path for imports
sys.path.append(str(Path(__file__).parent.parent.parent / 'utils'))
from scraper_common import DEFAULT_TIMEOUT, BROWSER_WAIT_TIME

# Configuration constants
DRAFTKINGS_API_URL = "https://api.draftkings.com/draftgroups/v1/"
DRAFTKINGS_CSV_BASE_URL = "https://www.draftkings.com/lineup/getavailableplayerscsv"
DEFAULT_CSV_FILENAME = "DraftKings NFL Salaries.csv"


def get_unique_filename(downloads_dir, base_filename):
    """
    Generate a unique filename to avoid overwriting existing files.

    Args:
        downloads_dir (Path): Directory where file will be saved
        base_filename (str): Base filename to use

    Returns:
        Path: Unique file path that doesn't conflict with existing files
    """
    filepath = downloads_dir / base_filename
    if not filepath.exists():
        return filepath

    # If file exists, add timestamp to make it unique
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name_parts = base_filename.rsplit('.', 1)
    if len(name_parts) == 2:
        unique_filename = f"{name_parts[0]}_{timestamp}.{name_parts[1]}"
    else:
        unique_filename = f"{base_filename}_{timestamp}"

    return downloads_dir / unique_filename


def _is_sunday_afternoon_game(start_time):
    """
    Check if a game start time represents a Sunday afternoon game (12 PM - 5 PM ET).

    Uses dynamic date parsing to identify Sunday afternoon games regardless of the week.
    Excludes Sunday Night Football (typically 8 PM ET or later).

    Args:
        start_time (str): Game start time string from DraftKings API

    Returns:
        bool: True if the game is on Sunday between 12 PM and 5 PM ET, False otherwise
    """
    if not start_time:
        return False

    # Try to parse various date formats and check if it's a Sunday afternoon game
    date_formats = [
        '%Y-%m-%dT%H:%M:%S.%fZ',  # ISO format with microseconds
        '%Y-%m-%dT%H:%M:%SZ',     # ISO format
        '%Y-%m-%d %H:%M:%S',      # Standard datetime
        '%m/%d/%Y %H:%M:%S',      # US date format
        '%m-%d-%Y %H:%M:%S',      # US date format with dashes
    ]

    for date_format in date_formats:
        try:
            parsed_datetime = datetime.strptime(start_time, date_format)
            # Check if it's Sunday (weekday == 6)
            if parsed_datetime.weekday() != 6:
                return False

            # Check if it's afternoon time (12 PM - 5 PM ET)
            # Assuming input times are already in ET
            hour = parsed_datetime.hour
            return 12 <= hour <= 17  # 12 PM (noon) to 5 PM

        except ValueError:
            continue

    # If no datetime format matches, try extracting date and look for time patterns
    try:
        # Look for patterns like "09/07/2025 01:00PM ET" or "09/07/2025 04:25PM ET"
        time_match = re.search(r'(\d{1,2}):(\d{2})(AM|PM)\s*ET', start_time, re.IGNORECASE)
        date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', start_time)

        if time_match and date_match:
            # Parse the date
            parsed_date = datetime.strptime(date_match.group(1), '%m/%d/%Y')
            if parsed_date.weekday() != 6:  # Not Sunday
                return False

            # Parse the time
            hour = int(time_match.group(1))
            am_pm = time_match.group(3).upper()

            # Convert to 24-hour format
            if am_pm == 'PM' and hour != 12:
                hour += 12
            elif am_pm == 'AM' and hour == 12:
                hour = 0

            # Check if it's afternoon time (12 PM - 5 PM ET)
            return 12 <= hour <= 17

        # Look for YYYY-MM-DD pattern and check if it's Sunday (fallback)
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', start_time)
        if date_match:
            parsed_date = datetime.strptime(date_match.group(1), '%Y-%m-%d')
            # If no time info, assume it's an afternoon game if it's Sunday
            return parsed_date.weekday() == 6

    except (ValueError, AttributeError):
        pass

    # If all parsing fails, return False
    return False


def get_current_nfl_contest():
    """
    Get the Fantasy Football Millionaire contest (Sunday afternoon games only) from DraftKings API.

    Specifically looks for Fantasy Football Millionaire contests (type 21) that contain
    ONLY Sunday afternoon games (12 PM - 5 PM ET) and selects the LARGEST slate by game count.
    This excludes Sunday Night Football (8 PM+ ET), Monday Night Football, Thursday games, etc.
    Typically 12+ games representing the main Sunday afternoon slate.

    Returns:
        str or None: CSV download URL for the Sunday afternoon Millionaire contest, or None if not found
    """
    try:
        print("🔍 Fetching current NFL contests from DraftKings API...")
        print("🎯 Looking for Fantasy Football Millionaire contest with Sunday afternoon games (12-5 PM ET)...")

        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }

        response = requests.get(
            DRAFTKINGS_API_URL,
            headers=headers,
            timeout=DEFAULT_TIMEOUT
        )
        response.raise_for_status()

        data = response.json()
        nfl_contests = [
            contest for contest in data['draftGroups']
            if contest['contestType']['sport'] == 'NFL'
        ]

        # Find Fantasy Football Millionaire contests (type 21) that start on Sunday
        millionaire_contests = []

        for contest in nfl_contests:
            if (contest.get('draftGroupState') == 'Upcoming' and
                contest['contestType']['contestTypeId'] == 21):

                game_count = len(contest.get('games', []))
                if game_count > 0:
                    # Check if contest starts on Sunday by looking at game times
                    games = contest.get('games', [])
                    sunday_games = 0

                    non_sunday_games = 0
                    for i, game in enumerate(games):

                        # Try different possible field names for game start time
                        start_time = (game.get('startTime', '') or
                                    game.get('startDate', '') or
                                    game.get('gameTime', '') or
                                    game.get('date', '') or
                                    game.get('startDateTime', ''))

                        # Check if this game is on Sunday afternoon (12 PM - 5 PM ET)
                        is_sunday_afternoon = _is_sunday_afternoon_game(start_time)

                        if is_sunday_afternoon:
                            sunday_games += 1
                        else:
                            non_sunday_games += 1

                    # Only consider contests with ONLY Sunday afternoon games (no Sunday Night Football, Monday, Thursday, etc.)
                    is_sunday_afternoon_only_slate = (sunday_games > 0 and non_sunday_games == 0)
                    millionaire_contests.append({
                        'contest': contest,
                        'game_count': game_count,
                        'contest_type_id': contest['contestType']['contestTypeId'],
                        'sunday_games': sunday_games,
                        'non_sunday_games': non_sunday_games,
                        'is_sunday_afternoon_only_slate': is_sunday_afternoon_only_slate,
                        'draft_group_id': contest['draftGroupId']
                    })

        if not millionaire_contests:
            print("❌ No Fantasy Football Millionaire contests found")
            return None

        # Filter to ONLY contests with Sunday afternoon games (12-5 PM ET)
        sunday_afternoon_only_contests = [c for c in millionaire_contests if c['is_sunday_afternoon_only_slate']]

        if not sunday_afternoon_only_contests:
            print("❌ No Sunday afternoon Fantasy Football Millionaire contests found")
            print("💡 All contests include non-afternoon games (Sunday Night Football, Monday Night, etc.)")
            return None

        # Sort Sunday afternoon contests by game count to find the largest (main slate)
        sorted_contests = sorted(
            sunday_afternoon_only_contests,
            key=lambda x: x['game_count'],
            reverse=True
        )

        # Select the largest Sunday afternoon slate (the main slate)
        main_contest_data = sorted_contests[0]
        main_contest = main_contest_data['contest']
        game_count = main_contest_data['game_count']

        draft_group_id = main_contest['draftGroupId']
        contest_type_id = main_contest['contestType']['contestTypeId']
        csv_url = (
            f"{DRAFTKINGS_CSV_BASE_URL}"
            f"?contestTypeId={contest_type_id}&draftGroupId={draft_group_id}"
        )

        print(f"\n✅ Selected Fantasy Football Millionaire contest:")
        print(f"   📊 {game_count} games (Sunday afternoon)")
        print(f"   🆔 Draft Group: {draft_group_id}")

        return csv_url

    except requests.RequestException as e:
        print(f"❌ Network error fetching NFL contests: {e}")
        return None
    except (KeyError, ValueError, TypeError) as e:
        print(f"❌ Data parsing error: {e}")
        return None


def test_direct_download(csv_url):
    """
    Test if CSV can be downloaded directly (usually requires authentication).

    Attempts to download the CSV directly without browser authentication.
    Checks for locked/placeholder data to determine if authentication is needed.

    Args:
        csv_url (str): The CSV download URL

    Returns:
        bool: True if real salary data was downloaded, False if authentication required
    """
    print("🔄 Testing direct download (usually requires authentication)...")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    }

    try:
        response = requests.get(csv_url, headers=headers, timeout=DEFAULT_TIMEOUT)

        if response.status_code == 200:
            # Check if we got locked/placeholder data
            if "(LOCKED)" in response.text:
                print("⚠️  Direct download returned locked data - authentication required")
                return False
            else:
                # Real data - save it
                downloads_dir = Path.home() / "Downloads"
                filepath = get_unique_filename(downloads_dir, DEFAULT_CSV_FILENAME)

                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(response.text)

                print(f"✅ Success! Real salary data downloaded")
                print(f"📁 Saved to: {filepath}")
                return True
        else:
            print(f"⚠️  Direct download failed (HTTP {response.status_code})")
            return False

    except (requests.RequestException, IOError, OSError) as e:
        print(f"⚠️  Direct download failed: {e}")
        return False


def open_in_browser(csv_url):
    """
    Open the CSV URL in the user's default browser.

    Opens the DraftKings CSV download URL in the default browser,
    waits for download completion, and attempts to close the browser window.

    Args:
        csv_url (str): The CSV download URL

    Returns:
        bool: True if browser opened successfully, False otherwise
    """
    print("\n🌐 Opening CSV URL in your default browser...")

    try:
        webbrowser.open(csv_url)
        print("✅ Opened in browser!")

        print("\n" + "="*60)
        print("📱 If you're logged into DraftKings:")
        print("   → CSV should download as 'DraftKings NFL Salaries.csv'")
        print("   → Players should have normal salaries ($3000-$8000+)")
        print("")
        print("🔐 If you're NOT logged in:")
        print("   → Log into DraftKings, then the CSV will download")
        print("="*60)

        print(f"\n⏳ Waiting {BROWSER_WAIT_TIME} seconds for download...")
        try:
            time.sleep(BROWSER_WAIT_TIME)
        except KeyboardInterrupt:
            print("⏹️  Stopped by user")

        print("🔄 Closing browser window...")
        try:
            system = platform.system().lower()
            if system == 'darwin':  # macOS
                subprocess.run([
                    'osascript', '-e',
                    'tell application "System Events" to tell process of (get name of first application process whose frontmost is true) to keystroke "w" using command down'
                ], check=False, capture_output=True)
            elif system == 'windows':
                # Windows: Use Alt+F4 to close active window
                import pyautogui
                pyautogui.hotkey('alt', 'f4')
            elif system == 'linux':
                # Linux: Use Ctrl+W to close browser tab
                import pyautogui
                pyautogui.hotkey('ctrl', 'w')
            else:
                print(f"⚠️  Unsupported platform: {system}")
                raise OSError(f"Platform {system} not supported")
            print("✅ Browser window closed")
        except ImportError:
            print("⚠️  pyautogui not available for non-macOS platforms")
            print("💡 You can close the browser window manually")
        except (OSError, subprocess.SubprocessError) as e:
            print(f"⚠️  Could not close browser window automatically: {e}")
            print("💡 You can close the browser window manually")

        return True

    except (OSError, webbrowser.Error) as e:
        print(f"❌ Failed to open browser: {e}")
        return False


def main():
    """
    Main function to run the DraftKings scraper.

    Complete workflow: finds current NFL contest, attempts direct download,
    falls back to browser method if authentication is required.

    Returns:
        bool: True if scraper completed successfully, False otherwise
    """
    print("🏈 DraftKings NFL Salary Scraper")
    print("="*50)

    # Step 1: Get the current contest URL
    csv_url = get_current_nfl_contest()
    if not csv_url:
        print("❌ Could not find current NFL contest")
        return False

    print(f"\n🎯 CSV URL: {csv_url}")

    # Step 2: Try direct download (rarely works without auth)
    if test_direct_download(csv_url):
        print("\n🎉 Success! CSV downloaded directly.")
        return True

    # Step 3: Open in browser (main method)
    print("\n🔄 Opening in browser for authenticated download...")
    if open_in_browser(csv_url):
        print("\n✅ Browser method completed!")
        print("\n💡 Check your Downloads folder for:")
        print("   📁 'DraftKings NFL Salaries.csv'")
        print("\n🔍 If download didn't work:")
        print("   1. Make sure you're logged into DraftKings")
        print("   2. Try pasting this URL manually:")
        print(f"      {csv_url}")
        return True

    # Fallback: Provide manual instructions
    print("\n❌ Could not open browser automatically")
    print("🔗 Manual steps:")
    print("   1. Copy this URL:")
    print(f"      {csv_url}")
    print("   2. Open your browser and log into DraftKings")
    print("   3. Paste the URL - CSV should download automatically")

    return False


if __name__ == "__main__":
    success = main()
    if success:
        print("\n🏆 Scraper completed successfully!")
    else:
        print("\n⚠️  Scraper completed with issues - check instructions above")