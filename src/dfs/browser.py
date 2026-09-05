"""Shared Playwright browser profile for sites that need a login.

One persistent context per site under data/profiles/<site>/, so you log in
once (`dfs auth tffb`, `dfs auth dk`) -- including any 2FA -- and every
later run reuses those cookies headlessly. Replaces the old approach of
`webbrowser.open()` + an AppleScript Cmd+W sent to "whatever application
process is frontmost" (scraper_common.close_arc_tab, draftkings/scraper.py's
open_in_browser), which could close an unrelated window if focus had moved.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from playwright.sync_api import BrowserContext, sync_playwright

from dfs.log import get_logger
from dfs.paths import PROFILES_DIR

log = get_logger("browser")


def profile_dir(site: str) -> Path:
    d = PROFILES_DIR / site
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextmanager
def persistent_context(site: str, *, headless: bool = True) -> Iterator[BrowserContext]:
    """A browser context whose cookies/local-storage persist across runs."""
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir(site)),
            headless=headless,
        )
        try:
            yield context
        finally:
            context.close()


def interactive_login(site: str, url: str, *, success_check: str | None = None) -> None:
    """Open a real (headed) browser window at `url` for the user to log into
    by hand, then wait for them to close the tab/window before returning.

    `success_check` is an optional human-readable description shown to the
    user of how to know they're done (e.g. "the optimizer page, not a login
    form"). This module doesn't try to detect login success itself --
    sites vary too much -- so the human is the judge.
    """
    with persistent_context(site, headless=False) as context:
        page = context.new_page()
        page.goto(url)
        print(f"\nLog into {site} in the window that just opened.")
        if success_check:
            print(f"You're done when you see: {success_check}")
        print("Close the browser window (or press Ctrl+C here) when finished.\n")
        try:
            page.wait_for_event("close", timeout=0)
        except KeyboardInterrupt:
            pass
        except Exception:  # noqa: BLE001 - context/browser closed is expected
            pass
    log.info("saved %s login session to %s", site, profile_dir(site))
