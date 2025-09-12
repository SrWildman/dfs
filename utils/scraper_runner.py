#!/usr/bin/env python3
"""
Shared utilities for running DFS scrapers consistently across workflows.

This module provides common functionality for executing scrapers with proper
error handling, timeout management, and consistent logging.
"""

import subprocess
from pathlib import Path


def run_scraper(scraper_path, scraper_file, description, args=None):
    """
    Run a specific scraper and report results.

    Args:
        scraper_path (Path): Directory containing the scraper script
        scraper_file (str): Name of the scraper script to run
        description (str): Human-readable description for logging
        args (list, optional): Additional arguments to pass to the scraper

    Returns:
        bool: True if scraper completed successfully, False otherwise
    """
    print(f"🔄 {description}...")
    try:
        # Add auto-skip flag for Fantasy Footballers to prevent interactive prompt
        cmd = ['python3', scraper_file]
        if 'fantasy_footballers' in str(scraper_path):
            cmd.append('--auto-skip')
        
        # Add any additional arguments passed to the scraper
        if args:
            cmd.extend(args)

        result = subprocess.run(
            cmd,
            cwd=scraper_path,
            capture_output=True,
            text=True,
            timeout=300
        )

        if result.returncode == 0:
            print(f"✅ {description} completed successfully")
            return True
        else:
            print(f"❌ {description} failed: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        print(f"⏰ {description} timed out")
        return False
    except Exception as e:
        print(f"❌ {description} error: {e}")
        return False


def run_scrapers(scrapers, args=None):
    """
    Run multiple scrapers and collect results.

    Args:
        scrapers (list): List of tuples (scraper_path, scraper_file, description)
        args (list, optional): Additional arguments to pass to all scrapers

    Returns:
        list: List of (description, success) tuples
    """
    results = []

    for scraper_path, scraper_file, description in scrapers:
        if scraper_path.exists() and (scraper_path / scraper_file).exists():
            success = run_scraper(scraper_path, scraper_file, description, args)
            results.append((description, success))
        else:
            print(f"⚠️  {description} scraper not found at {scraper_path}")
            results.append((description, False))

        print()  # Add spacing between scrapers

    return results


def print_results_summary(results, title="Collection Summary"):
    """
    Print summary of scraper results.

    Args:
        results (list): List of (description, success) tuples
        title (str): Title for the summary section

    Returns:
        tuple: (successful_count, total_count)
    """
    print(f"📊 {title}:")
    print("-" * len(title))

    successful = sum(1 for _, success in results if success)
    total = len(results)

    for description, success in results:
        status = "✅" if success else "❌"
        print(f"{status} {description}")

    print(f"\n🎯 Completed: {successful}/{total} scrapers successful")
    print()

    return successful, total