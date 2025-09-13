#!/usr/bin/env python3
"""
Projections Scraper

Simple manual approach that mirrors other scrapers:
1. Opens page in Arc (uses your login & download preferences)
2. Provides clear manual instructions
3. Checks for successful download

Usage:
    python3 scraper.py [--auto-skip]
"""

import sys
import time
import webbrowser
from pathlib import Path

# Add utils to path for imports
sys.path.append(str(Path(__file__).parent.parent.parent / 'utils'))
from scraper_common import (
    check_downloads, close_arc_tab, simple_manual_approach,
    BROWSER_WAIT_TIME, FILE_CHECK_TIMEOUT
)

# Configuration constants
FANTASY_FOOTBALLERS_URL = "https://www.thefantasyfootballers.com/2025-ultimate-dfs-pass/dfs-pass-lineup-optimizer/"


def main():
    """
    Main function for Projections scraper.

    Opens the Projections optimizer page in Arc browser,
    provides manual instructions, and verifies successful download.

    Returns:
        bool: True if projections were successfully downloaded, False otherwise
    """
    print("🏈 Projections Scraper")
    print("=" * 44)

    # Record initial download state
    initial_files = check_downloads()

    print("🌐 Opening optimizer in Arc...")
    webbrowser.open(FANTASY_FOOTBALLERS_URL)
    time.sleep(BROWSER_WAIT_TIME)

    # Check for auto-skip mode (for automated workflows)
    auto_skip = "--auto-skip" in sys.argv

    # Use simple manual approach
    if auto_skip:
        print("⚠️  Manual interaction required but running in auto-skip mode")
        print(f"   → Page was: {FANTASY_FOOTBALLERS_URL}")
        manual_worked = False
        time.sleep(2)  # Brief wait in auto-skip mode
    else:
        instructions = [
            "Page is open in Arc",
            "Click the 'Projections' button (with download icon)",
            "Select 'Projections' from dropdown"
        ]
        manual_worked = simple_manual_approach(instructions)

    close_arc_tab("Arc tab")

    # Check for new files
    if manual_worked:
        print("   ⏳ Waiting for download to complete...")
        time.sleep(FILE_CHECK_TIMEOUT)
    else:
        time.sleep(2)  # Brief wait in auto-skip mode

    final_files = check_downloads()
    new_files = [f for f in final_files if f not in initial_files]

    if new_files:
        latest_file = new_files[0]
        print(f"✅ SUCCESS! Downloaded: {latest_file.name}")

        # Quick content check
        try:
            content = latest_file.read_text(encoding='utf-8')[:200]
            if 'ProjPts' in content:
                print("🎯 Confirmed: Projections data!")
            elif 'DraftKings' in content:
                print("⚠️  This looks like DraftKings data, not projections")
            else:
                print("💡 File downloaded - please verify content")
        except:
            print("💡 File downloaded - couldn't verify content")

        return True
    else:
        if manual_worked:
            print("❌ Manual process completed but no download detected")
        else:
            print("❌ Skipped in auto-skip mode")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)