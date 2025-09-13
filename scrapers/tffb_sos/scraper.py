#!/usr/bin/env python3
"""
The Fantasy Footballers Strength of Schedule Scraper

Automates downloading Strength of Schedule data for all positions using the Arc browser
flow that already works for the Fantasy Footballers scraper. This avoids unreliable
ChromeDriver-to-Arc attachment and keeps you in Arc.

Flow per position:
1) Open the SOS page for the position in Arc
2) Provide clear manual steps
3) Detect the downloaded CSV and rename it

Usage:
    python3 scraper.py [--week WEEK] [--auto-skip]
"""

import argparse
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

# Configuration constants
BROWSER_WAIT_TIME = 15  # Seconds to wait for page load
FILE_CHECK_TIMEOUT = 10  # Seconds to wait for file download
BROWSER_AUTOMATION_DELAY = 3  # Seconds between automation attempts
DOWNLOAD_RECENT_WINDOW = 120  # Seconds window for recent file detection
TFFB_SOS_BASE_URL = "https://www.thefantasyfootballers.com/footclan/strength-of-schedule/"

# Position configurations
POSITIONS = {
    'QB': 'QB',
    'RB': 'RB',
    'WR': 'WR',
    'TE': 'TE',
    'D/ST': 'D'
}


def check_downloads():
    """
    Check for new CSV files in Downloads folder.

    Scans the ~/Downloads directory for CSV files modified in the last 2 minutes.

    Returns:
        list: List of Path objects for recent CSV files, sorted by modification time (newest first)
    """
    downloads_dir = Path.home() / "Downloads"
    recent_csvs = []
    now = time.time()

    for file in downloads_dir.glob("*.csv"):
        if now - file.stat().st_mtime < DOWNLOAD_RECENT_WINDOW:
            recent_csvs.append(file)

    return sorted(recent_csvs, key=lambda x: x.stat().st_mtime, reverse=True)


def simple_manual_approach(week_number: int) -> bool:
    """
    Simple manual approach - just provide clear instructions and wait for user.

    Opens the page, gives clear instructions, and waits for the user to complete.
    This mirrors the Fantasy Footballers scraper approach.
    """
    print(f"\n🎯 Manual step needed for week {week_number}:")
    print(f"   1) Page is open in Arc")
    print(f"   2) Click the week selector dropdown")
    print(f"   3) Select week {week_number}")
    print(f"   4) Click 'More' button")
    print(f"   5) Click 'Download CSV'")
    print()

    try:
        input("Press ENTER when the CSV download has started...")
        return True
    except EOFError:
        # Non-interactive mode, just wait briefly
        print("Running in non-interactive mode...")
        time.sleep(5)
        return False  # Return False in auto-skip since user can't interact


def scrape_position(position_name, position_code, week_number, auto_skip=False):
    """
    Arc-only flow for a specific position: open the page, guide manual fallback,
    then detect and rename the download.
    """
    print(f"🏈 Scraping {position_name} Strength of Schedule data in Arc...")

    # Build URL for this position
    url = f"{TFFB_SOS_BASE_URL}?position={position_code}"

    # Record initial download state
    initial_files = check_downloads()

    # Open the page in Arc
    print(f"🌐 Opening {position_name} SOS page in Arc...")
    webbrowser.open(url)
    time.sleep(BROWSER_WAIT_TIME)

    # Use simple manual approach
    manual_worked = simple_manual_approach(week_number)

    if not manual_worked and auto_skip:
        print(f"⚠️  Manual interaction required for {position_name} but running in auto-skip mode")
        print(f"   → Page was: {url}")

    # Check for new files
    if manual_worked:
        print(f"   ⏳ Waiting for {position_name} download to complete...")
        time.sleep(FILE_CHECK_TIMEOUT)
    else:
        time.sleep(2)  # Brief wait in auto-skip mode

    final_files = check_downloads()
    new_files = [f for f in final_files if f not in initial_files]

    # Close the Arc tab for this position
    print(f"🔄 Closing {position_name} Arc tab...")
    try:
        close_script = '''
        tell application "Arc"
            activate
            delay 0.5
        end tell

        tell application "System Events"
            tell process "Arc"
                keystroke "w" using command down
            end tell
        end tell
        '''
        result = subprocess.run(['osascript', '-e', close_script],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            print(f"✅ {position_name} tab closed")
        else:
            print(f"⚠️ Could not close {position_name} tab")
    except Exception as e:
        print(f"⚠️ Could not close {position_name} tab: {e}")

    if new_files:
        latest_file = new_files[0]

        # Sanitize position name for filesystem (e.g., D/ST → DST)
        safe_position = position_name.replace('/', '')
        new_name = f"TFFB_SOS_{safe_position}_Week{week_number}_{latest_file.name}"
        new_path = latest_file.parent / new_name

        try:
            latest_file.rename(new_path)
            print(f"✅ {position_name} download successful: {new_name}")
            return True
        except Exception as e:
            print(f"⚠️  {position_name} download successful but rename failed: {e}")
            print(f"   File: {latest_file.name}")
            return True
    else:
        if manual_worked:
            print(f"❌ {position_name} manual process completed but no download detected")
        else:
            print(f"❌ {position_name} skipped in auto-skip mode")
        return False


def main():
    """
    Main function for TFFB SOS scraper.

    Processes all positions (QB, RB, WR, TE, D/ST) and downloads their
    respective Strength of Schedule CSV files. Uses simple manual approach
    that mirrors Fantasy Footballers scraper.
    """
    parser = argparse.ArgumentParser(description='Download TFFB Strength of Schedule data for all positions')
    parser.add_argument('--week', '-w', type=int, default=1,
                        help='NFL week number (1-18)')
    parser.add_argument('--auto-skip', action='store_true',
                        help='Skip manual intervention prompts (for automated workflows)')

    args = parser.parse_args()

    # Validate week
    if not 1 <= args.week <= 18:
        print("❌ Week must be between 1 and 18")
        return False

    print(f"🏈 Starting TFFB Strength of Schedule scraping for Week {args.week}")
    print("=" * 60)

    # Auto-skip warning
    if args.auto_skip:
        print("\n⚠️ Auto-skip mode detected - this won't work for manual interactions")
        print("💡 Run without --auto-skip to use manual mode instead\n")

    results = []

    # Process each position
    for position_name, position_code in POSITIONS.items():
        success = scrape_position(position_name, position_code, args.week, args.auto_skip)
        results.append((position_name, success))

        # Skip remaining positions in auto-skip mode
        if args.auto_skip:
            print("\n⚠️ Skipping remaining positions in auto-skip mode")
            break

        # Add delay between positions
        if position_name != list(POSITIONS.keys())[-1]:  # Not the last position
            print(f"⏱️  Waiting before next position...")
            time.sleep(BROWSER_AUTOMATION_DELAY)
            print()

    # Print summary
    print("=" * 60)
    print("📊 TFFB SOS Scraping Summary:")
    successful = 0
    for position, success in results:
        status = "✅" if success else "❌"
        print(f"{status} {position} Strength of Schedule")
        if success:
            successful += 1

    print(f"\n🎯 Completed: {successful}/{len(results)} positions successful")

    return successful == len(results)


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)