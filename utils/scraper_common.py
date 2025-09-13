#!/usr/bin/env python3
"""
Common Scraper Utilities

Shared functions used across multiple scrapers to reduce code duplication
and maintain consistency.
"""

import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

# Configuration constants
DOWNLOAD_RECENT_WINDOW = 120  # Seconds window for recent file detection
BROWSER_WAIT_TIME = 5  # Seconds to wait for page load
FILE_CHECK_TIMEOUT = 3  # Seconds to wait for file download
DEFAULT_TIMEOUT = 30  # Default timeout for API requests

# NFL Season constants - update each year
NFL_SEASON_START_DATE = datetime(2025, 9, 5)  # First Thursday night game of Week 1
NFL_WEEKS_IN_SEASON = 18


def check_downloads() -> List[Path]:
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


def get_current_nfl_week() -> int:
    """
    Automatically detect the current NFL week based on the date.

    Uses Monday as the start of each NFL week since DFS preparation
    focuses on the upcoming Sunday slate.

    Week logic:
    - Monday starts prep for upcoming Sunday games
    - Thursday-Sunday are game days for that week
    - Monday after games = start of next week's prep

    Returns:
        int: Current NFL week (1-18), defaults to 1 if before season or 18 if after
    """
    now = datetime.now()

    # If we're before the season starts, default to week 1
    if now < NFL_SEASON_START_DATE:
        return 1

    # For DFS purposes, each Monday starts the prep for upcoming Sunday games
    # Week 1 games are around Sept 7-8 (first Sunday), so Week 1 prep starts Sept 2 (Monday)
    # Week 2 games are around Sept 14-15, so Week 2 prep starts Sept 9 (Monday)
    # Week 3 games are around Sept 21-22, so Week 3 prep starts Sept 16 (Monday)

    # Find the first Monday of the season (Monday before first games)
    # If first game is around Sept 7-8, then first Monday is Sept 2
    first_sunday = NFL_SEASON_START_DATE + timedelta(days=(6 - NFL_SEASON_START_DATE.weekday()) % 7)
    if first_sunday == NFL_SEASON_START_DATE:  # If season starts on Sunday
        first_sunday = NFL_SEASON_START_DATE + timedelta(days=7)

    week1_monday = first_sunday - timedelta(days=6)  # Monday before first Sunday

    # Calculate days since Week 1 Monday
    days_since_week1_monday = (now - week1_monday).days

    # Each week starts on Monday, so divide by 7 and add 1
    current_week = (days_since_week1_monday // 7) + 1

    # Cap at max weeks in season
    if current_week > NFL_WEEKS_IN_SEASON:
        return NFL_WEEKS_IN_SEASON

    return current_week


def close_arc_tab(context_name: str = "tab") -> bool:
    """
    Close the current Arc browser tab using AppleScript.

    Args:
        context_name: Description for the tab being closed (for logging)

    Returns:
        bool: True if tab was closed successfully, False otherwise
    """
    print(f"🔄 Closing {context_name}...")

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
            print(f"✅ {context_name.title()} closed")
            return True
        else:
            print(f"⚠️ Could not close {context_name}")
            return False
    except Exception as e:
        print(f"⚠️ Could not close {context_name}: {e}")
        return False


def simple_manual_approach(instructions: List[str], context: str = "") -> bool:
    """
    Simple manual approach - provide clear instructions and wait for user.

    Args:
        instructions: List of instruction strings to display
        context: Optional context string for the first line

    Returns:
        bool: True if user completed the task, False if running in non-interactive mode
    """
    if context:
        print(f"\n🎯 Manual step needed {context}:")
    else:
        print(f"\n🎯 Manual step needed:")

    for i, instruction in enumerate(instructions, 1):
        print(f"   {i}) {instruction}")
    print()

    try:
        input("Press ENTER when done...")
        print("   ⏳ Checking for download...")
        return True
    except EOFError:
        # Non-interactive mode, just wait briefly
        print("Running in non-interactive mode...")
        time.sleep(2)
        return False  # In auto-skip since user can't interact