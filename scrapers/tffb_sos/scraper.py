#!/usr/bin/env python3
"""
The Strength of Schedule Scraper

Automates downloading Strength of Schedule data for all positions using the Arc browser
flow that already works for the Projections scraper. This avoids unreliable
ChromeDriver-to-Arc attachment and keeps you in Arc.

Flow per position:
1) Open the SOS page for the position in Arc
2) Provide clear manual steps
3) Detect the downloaded CSV and rename it

Usage:
    python3 scraper.py [--week WEEK] [--auto-skip]
"""

import argparse
import sys
import time
import webbrowser
from pathlib import Path

# Add utils to path for imports
sys.path.append(str(Path(__file__).parent.parent.parent / 'utils'))
from scraper_common import (
    check_downloads, close_arc_tab, simple_manual_approach,
    BROWSER_WAIT_TIME, FILE_CHECK_TIMEOUT, get_current_nfl_week
)

# Configuration constants
BROWSER_AUTOMATION_DELAY = 1  # Seconds between automation attempts
SOS_BASE_URL = "https://www.thefantasyfootballers.com/footclan/strength-of-schedule/"

# Position configurations
POSITIONS = {
    'QB': 'QB',
    'RB': 'RB',
    'WR': 'WR',
    'TE': 'TE',
    'D/ST': 'D'
}


def scrape_position(position_name, position_code, week_number, auto_skip=False):
    """
    Arc-only flow for a specific position: opens a new page for each position.
    """
    print(f"🏈 Scraping {position_name} Strength of Schedule data...")

    url = f"{SOS_BASE_URL}?position={position_code}"

    # Record initial download state
    initial_files = check_downloads()

    print(f"🌐 Opening SOS page in Arc...")
    webbrowser.open(url)
    time.sleep(BROWSER_WAIT_TIME)

    # Use simple manual approach
    if auto_skip:
        print(f"⚠️  Manual interaction required for {position_name} but running in auto-skip mode")
        print(f"   → Page was: {url}")
        manual_worked = False
    else:
        instructions = [
            "Page is open in Arc",
            f"Select position: {position_name}",
            f"Select week: {week_number}",
            "Click 'More' → 'Download CSV'"
        ]
        manual_worked = simple_manual_approach(instructions, f"for {position_name}")

    # Check for new files
    if manual_worked:
        print(f"   ⏳ Waiting for {position_name} download to complete...")
        time.sleep(FILE_CHECK_TIMEOUT)
    else:
        time.sleep(2)  # Brief wait in auto-skip mode

    final_files = check_downloads()
    new_files = [f for f in final_files if f not in initial_files]

    if new_files:
        latest_file = new_files[0]

        # Sanitize position name for filesystem (e.g., D/ST → DST)
        safe_position = position_name.replace('/', '')
        new_name = f"SOS_{safe_position}_Week{week_number}_{latest_file.name}"
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
    Main function for Strength of Schedule scraper.

    Processes all positions (QB, RB, WR, TE, D/ST) and downloads their
    respective Strength of Schedule CSV files. Uses simple manual approach
    that mirrors Projections scraper.
    """
    parser = argparse.ArgumentParser(description='Download Strength of Schedule data for all positions')
    parser.add_argument('--week', '-w', type=int, default=None,
                        help='NFL week number (1-18, defaults to current week)')
    parser.add_argument('--auto-skip', action='store_true',
                        help='Skip manual intervention prompts (for automated workflows)')

    args = parser.parse_args()

    # Use current week if not specified
    week = args.week if args.week is not None else get_current_nfl_week()

    # Validate week
    if not 1 <= week <= 18:
        print("❌ Week must be between 1 and 18")
        return False

    if args.week is None:
        print(f"🏈 Starting Strength of Schedule scraping for Week {week} (auto-detected)")
    else:
        print(f"🏈 Starting Strength of Schedule scraping for Week {week}")
    print("=" * 60)

    # Auto-skip warning
    if args.auto_skip:
        print("\n⚠️ Auto-skip mode detected - this won't work for manual interactions")
        print("💡 Run without --auto-skip to use manual mode instead\n")

    results = []

    # Process each position with separate windows
    for i, (position_name, position_code) in enumerate(POSITIONS.items()):
        success = scrape_position(position_name, position_code, week, args.auto_skip)
        results.append((position_name, success))

        # In auto-skip mode, continue to next position instead of breaking
        # This allows all positions to be attempted even if manual interaction fails

        # Close Arc tab after each position
        close_arc_tab(f"{position_name} Arc tab")

        # Add brief delay between positions (except after last)
        if i < len(POSITIONS) - 1:
            time.sleep(BROWSER_AUTOMATION_DELAY)
            print()

    print("=" * 60)
    print("📊 Strength of Schedule Scraping Summary:")
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