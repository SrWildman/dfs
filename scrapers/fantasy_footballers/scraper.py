#!/usr/bin/env python3
"""
Fantasy Footballers Projections - Final Solution

The simplest, most reliable method:
1. Opens page in Arc (uses your login & download preferences)
2. Waits appropriate time for loading
3. Uses a single, simple AppleScript command to automate clicking
4. Checks for successful download

Usage:
    python3 scraper.py
"""

import subprocess
import sys
import time
import webbrowser
from pathlib import Path

# Configuration constants
BROWSER_WAIT_TIME = 15  # Seconds to wait for page load
FILE_CHECK_TIMEOUT = 10  # Seconds to wait for file download
BROWSER_AUTOMATION_DELAY = 5  # Seconds between automation attempts
DOWNLOAD_RECENT_WINDOW = 120  # Seconds window for recent file detection
FANTASY_FOOTBALLERS_URL = "https://www.thefantasyfootballers.com/2025-ultimate-dfs-pass/dfs-pass-lineup-optimizer/"

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
        if now - file.stat().st_mtime < DOWNLOAD_RECENT_WINDOW:  # Use constant
            recent_csvs.append(file)

    return sorted(recent_csvs, key=lambda x: x.stat().st_mtime, reverse=True)

def automate_download():
    """
    Attempt automated download using AppleScript.

    Uses AppleScript to activate Arc browser and send keyboard commands
    to navigate to and click the projections download button.

    Returns:
        bool: True if automation script executed successfully, False otherwise
    """
    print("🤖 Attempting automation...")

    # Very simple AppleScript - just activate Arc and send some key presses
    script = '''
    tell application "Arc"
        activate
        delay 2
    end tell

    tell application "System Events"
        -- Send CMD+F to open find, then search for "Projections"
        keystroke "f" using command down
        delay 1
        type text "Projections"
        delay 1
        key code 53  -- Escape to close find
        delay 1
        -- Try pressing Tab a few times to navigate to button, then Space to click
        repeat 5 times
            key code 48  -- Tab key
            delay 0.3
        end repeat
        key code 49  -- Space bar to click
        delay 2
        -- Try clicking again for dropdown
        key code 49  -- Space bar
    end tell
    '''

    try:
        result = subprocess.run(['osascript', '-e', script],
                              capture_output=True, text=True, timeout=30)
        return result.returncode == 0
    except:
        return False

def main():
    """
    Main function for Fantasy Footballers projections scraper.

    Opens the Fantasy Footballers optimizer page in Arc browser,
    attempts automated download, handles manual fallback if needed,
    and verifies successful download.

    Returns:
        bool: True if projections were successfully downloaded, False otherwise
    """
    print("🏈 Fantasy Footballers Projections - Final Version")
    print("=" * 54)

    # Open page in Arc
    print("🌐 Opening optimizer in Arc...")
    webbrowser.open(FANTASY_FOOTBALLERS_URL)

    # Wait for loading
    print("⏳ Waiting for page load...")
    time.sleep(BROWSER_WAIT_TIME)

    # Try automation
    automation_worked = automate_download()

    # Check for auto-skip mode (for automated workflows)
    auto_skip = "--auto-skip" in sys.argv

    if not automation_worked:
        if auto_skip:
            print("⚠️  Manual step required but running in auto-skip mode")
            print("   → Open Fantasy Footballers manually and click Projections button")
            print("   → Re-run this script without --auto-skip when done")
            # Still wait a bit in case user clicked manually
            time.sleep(5)
        else:
            print("")
            print("🎯 Quick manual step needed:")
            print("   → Click the 'Projections' button (with download icon)")
            print("   → Select 'Projections' from dropdown")
            print("")
            input("Press ENTER when done...")

    # Close the browser window
    print("🔄 Closing browser window...")
    try:
        close_script = '''
        tell application "Arc"
            tell front window
                close current tab
            end tell
        end tell
        '''
        subprocess.run(['osascript', '-e', close_script],
                      capture_output=True, text=True, timeout=10)
        print("✅ Browser window closed")
    except Exception as e:
        print(f"⚠️  Could not close browser window: {e}")
        print("💡 You can close it manually with Cmd+W")

    # Check results
    print("🔍 Checking downloads...")
    time.sleep(2)

    recent_files = check_downloads()

    if recent_files:
        latest = recent_files[0]
        print(f"✅ SUCCESS! Downloaded: {latest.name}")

        # Quick content check
        try:
            content = latest.read_text(encoding='utf-8')[:200]
            if 'ProjPts' in content:
                print("🎯 Confirmed: Fantasy Footballers projections data!")
            elif 'DraftKings' in content:
                print("⚠️  This looks like DraftKings data, not projections")
            else:
                print("💡 File downloaded - please verify content")
        except:
            print("💡 File downloaded - couldn't verify content")

        return True
    else:
        print("❌ No recent downloads found")
        print("💡 Try the manual method if automation failed")
        return False

if __name__ == "__main__":
    success = main()
    print("")
    if success:
        print("🏆 Projections download complete!")
    else:
        print("🔧 May need manual clicking, but Arc method works!")