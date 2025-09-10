#!/usr/bin/env python3
"""
The Fantasy Footballers Strength of Schedule Scraper

Automates downloading Strength of Schedule data for all positions from TFFB FootClan.
Handles week selection and downloads CSV files for QB, RB, WR, TE, and D/ST positions.

Steps:
1. Opens TFFB SOS page for each position
2. Selects the specified week from dropdown
3. Downloads CSV via "More" dropdown
4. Repeats for all positions (QB, RB, WR, TE, D/ST)

Usage:
    python3 scraper.py [--week WEEK] [--auto-skip]
"""

import argparse
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options import Options
    from webdriver_manager.chrome import ChromeDriverManager
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

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
    'D/ST': 'D%2FST'  # URL encoded
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


def is_arc_running():
    """Check if Arc browser is currently running."""
    try:
        result = subprocess.run(['pgrep', '-f', 'Arc'], capture_output=True, text=True)
        return result.returncode == 0
    except:
        return False


def launch_arc_with_debugging():
    """Launch Arc browser with remote debugging enabled."""
    try:
        arc_executable = "/Applications/Arc.app/Contents/MacOS/Arc"
        arc_user_data = os.path.expanduser("~/Library/Application Support/Arc")
        
        print("🚀 Launching Arc with remote debugging...")
        
        # Launch Arc with debugging port
        process = subprocess.Popen([
            arc_executable,
            "--remote-debugging-port=9222",
            f"--user-data-dir={arc_user_data}"
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Wait a moment for Arc to start
        time.sleep(3)
        return True
    except Exception as e:
        print(f"❌ Failed to launch Arc with debugging: {e}")
        return False


def setup_arc_for_selenium():
    """Set up Arc browser for Selenium automation."""
    import os
    
    arc_executable = "/Applications/Arc.app/Contents/MacOS/Arc"
    if not os.path.exists(arc_executable):
        print("❌ Arc browser not found at expected location")
        return False
    
    print("🔍 Detecting Arc browser status...")
    
    if is_arc_running():
        print("📱 Arc is currently running")
        print("🔄 Need to restart Arc with debugging enabled...")
        print("   This will:")
        print("   1. Quit the current Arc session")
        print("   2. Relaunch Arc with debugging enabled")
        print("   3. Preserve all your login sessions")
        
        try:
            response = input("   Continue? (y/N): ").lower().strip()
            if response != 'y':
                print("❌ User cancelled Arc setup")
                return False
        except EOFError:
            # Running in non-interactive mode, auto-proceed
            print("   Proceeding automatically...")
            response = 'y'
        
        print("⏹️  Quitting Arc...")
        subprocess.run(['pkill', '-f', 'Arc'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
    
    # Launch Arc with debugging
    if launch_arc_with_debugging():
        print("✅ Arc launched with debugging enabled")
        return True
    else:
        return False


def setup_browser_driver():
    """
    Set up WebDriver to work with Arc browser using remote debugging.
    
    Returns:
        webdriver: Chrome WebDriver instance connected to Arc or None if setup fails
    """
    if not SELENIUM_AVAILABLE:
        print("❌ Selenium not available. Install with: pip install selenium webdriver-manager")
        return None
    
    import os
    
    # First, set up Arc for Selenium if needed
    if os.path.exists("/Applications/Arc.app/Contents/MacOS/Arc"):
        if not setup_arc_for_selenium():
            print("🔄 Arc setup failed, falling back to Chrome...")
        else:
            # Try to connect to Arc via remote debugging
            try:
                print("🔗 Connecting to Arc browser...")
                chrome_options = Options()
                chrome_options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
                
                driver = webdriver.Chrome(
                    service=webdriver.chrome.service.Service(ChromeDriverManager().install()),
                    options=chrome_options
                )
                
                print("✅ Successfully connected to Arc browser with your login session!")
                return driver
                
            except Exception as e:
                print(f"⚠️  Arc connection failed: {e}")
                print("🔄 Falling back to Chrome...")
    
    # Fallback to Chrome
    try:
        print("🔄 Using Chrome browser...")
        chrome_options = Options()
        chrome_options.add_argument("--no-first-run")
        
        driver = webdriver.Chrome(
            service=webdriver.chrome.service.Service(ChromeDriverManager().install()),
            options=chrome_options
        )
        
        print("⚠️  Using Chrome - you'll need to log in to TFFB manually")
        return driver
        
    except Exception as e:
        print(f"❌ WebDriver setup completely failed: {e}")
        return None


def select_week_and_download_selenium(driver, week_number):
    """
    Use Selenium to select week and download CSV.
    
    Args:
        driver: Selenium WebDriver instance
        week_number (int): NFL week number to select
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        wait = WebDriverWait(driver, 10)
        
        print(f"   🎯 Selecting week {week_number}...")
        
        # Wait for page to load and find week selector
        week_selector = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, ".ffb-filters--range-picker.week-selector .ffb-filters--button"))
        )
        week_selector.click()
        time.sleep(1)
        
        # Click the specific week button
        week_button_selector = f'[data-week="{week_number}"]'
        week_button = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, week_button_selector))
        )
        week_button.click()
        print(f"   ✅ Selected week {week_number}")
        time.sleep(2)
        
        # Click the More button
        print("   🎯 Clicking More button...")
        more_button = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "#more .ffb-filters--button"))
        )
        more_button.click()
        time.sleep(1)
        
        # Click Download CSV
        print("   🎯 Clicking Download CSV...")
        download_button = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, ".dt-button.buttons-csv"))
        )
        download_button.click()
        print("   ✅ CSV download initiated")
        time.sleep(3)
        
        return True
        
    except Exception as e:
        print(f"   ❌ Selenium automation failed: {e}")
        return False


def select_week_and_download(week_number):
    """
    Automate week selection and CSV download using Selenium WebDriver.
    
    Args:
        week_number (int): NFL week number to select
        
    Returns:
        bool: True if automation script executed successfully, False otherwise
    """
    print(f"🤖 Using Selenium WebDriver to automate week {week_number} selection and CSV download...")
    
    if not SELENIUM_AVAILABLE:
        print("❌ Selenium not available - falling back to AppleScript (unreliable)")
        return False
    
    driver = setup_browser_driver()
    if not driver:
        return False
    
    try:
        # Note: Don't navigate to URL here - it should already be open from scrape_position
        success = select_week_and_download_selenium(driver, week_number)
        return success
        
    except Exception as e:
        print(f"❌ WebDriver automation error: {e}")
        return False
    finally:
        try:
            driver.quit()
        except:
            pass


def scrape_position_selenium(position_name, position_code, week_number, auto_skip=False):
    """
    Scrape SOS data for a specific position using Selenium WebDriver.
    
    Args:
        position_name (str): Human readable position name (e.g., 'QB')
        position_code (str): URL-encoded position code 
        week_number (int): NFL week number
        auto_skip (bool): Skip manual intervention prompts
        
    Returns:
        bool: True if scraping was successful, False otherwise
    """
    print(f"🏈 Scraping {position_name} Strength of Schedule data with Selenium...")
    
    # Build URL for this position
    url = f"{TFFB_SOS_BASE_URL}?position={position_code}"
    
    # Record initial download state
    initial_files = check_downloads()
    
    # Set up WebDriver
    driver = setup_browser_driver()
    if not driver:
        print(f"❌ {position_name} scraping failed: Could not set up WebDriver")
        return False
    
    try:
        # Navigate to the page
        print(f"🌐 Navigating to {position_name} SOS page...")
        driver.get(url)
        time.sleep(3)  # Wait for page load
        
        # Attempt automation
        automation_worked = select_week_and_download_selenium(driver, week_number)
        
        if not automation_worked:
            if auto_skip:
                print(f"⚠️  Manual step required for {position_name} but running in auto-skip mode")
                print(f"   → Page open at: {url}")
                print(f"   → Select week {week_number} and download CSV")
                time.sleep(5)  # Brief wait in case user clicks manually
            else:
                print(f"🔧 Manual intervention needed for {position_name}:")
                print(f"   1. Page should be open at: {url}")
                print(f"   2. Select week {week_number} from the week dropdown") 
                print("   3. Click 'More' dropdown and select download/export CSV")
                print("   4. Files will be checked automatically...")
                time.sleep(10)  # Give time for manual action
        
        # Check for new files
        time.sleep(FILE_CHECK_TIMEOUT)
        final_files = check_downloads()
        new_files = [f for f in final_files if f not in initial_files]
        
        if new_files:
            latest_file = new_files[0]
            
            # Rename file to include position information
            new_name = f"TFFB_SOS_{position_name}_Week{week_number}_{latest_file.name}"
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
            print(f"❌ {position_name} download failed or file not detected")
            return False
            
    except Exception as e:
        print(f"❌ {position_name} scraping error: {e}")
        return False
    finally:
        try:
            driver.quit()
        except:
            pass


def scrape_position(position_name, position_code, week_number, auto_skip=False):
    """
    Scrape SOS data for a specific position - tries Selenium first, falls back to webbrowser.
    
    Args:
        position_name (str): Human readable position name (e.g., 'QB')
        position_code (str): URL-encoded position code 
        week_number (int): NFL week number
        auto_skip (bool): Skip manual intervention prompts
        
    Returns:
        bool: True if scraping was successful, False otherwise
    """
    # Try Selenium approach first
    if SELENIUM_AVAILABLE:
        return scrape_position_selenium(position_name, position_code, week_number, auto_skip)
    
    # Fallback to original webbrowser approach
    print(f"🏈 Scraping {position_name} Strength of Schedule data (fallback mode)...")
    
    # Build URL for this position
    url = f"{TFFB_SOS_BASE_URL}?position={position_code}"
    
    # Record initial download state
    initial_files = check_downloads()
    
    # Open the page
    print(f"🌐 Opening {position_name} SOS page...")
    webbrowser.open(url)
    time.sleep(BROWSER_WAIT_TIME)
    
    # Attempt full automation (week selection + download)
    automation_worked = select_week_and_download(week_number)
    
    if not automation_worked:
        if auto_skip:
            print(f"⚠️  Manual step required for {position_name} but running in auto-skip mode")
            print(f"   → Open {url} manually")
            print(f"   → Select week {week_number} and download CSV")
            print("   → Re-run this script without --auto-skip when done")
            time.sleep(5)  # Brief wait in case user clicked manually
        else:
            print(f"🔧 Manual intervention needed for {position_name}:")
            print(f"   1. Ensure the page is loaded: {url}")
            print(f"   2. Select week {week_number} from the week dropdown")
            print("   3. Click 'More' dropdown and select download/export CSV")
            print("   4. Files will be checked automatically...")
            time.sleep(10)  # Give time for manual action if user is watching
    
    # Check for new files
    time.sleep(FILE_CHECK_TIMEOUT)
    final_files = check_downloads()
    new_files = [f for f in final_files if f not in initial_files]
    
    if new_files:
        latest_file = new_files[0]
        
        # Rename file to include position information
        new_name = f"TFFB_SOS_{position_name}_Week{week_number}_{latest_file.name}"
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
        print(f"❌ {position_name} download failed or file not detected")
        return False


def main():
    """
    Main function for TFFB SOS scraper.
    
    Processes all positions (QB, RB, WR, TE, D/ST) and downloads their
    respective Strength of Schedule CSV files.
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
    
    results = []
    
    # Process each position
    for position_name, position_code in POSITIONS.items():
        success = scrape_position(position_name, position_code, args.week, args.auto_skip)
        results.append((position_name, success))
        
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