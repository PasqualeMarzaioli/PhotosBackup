"""
Downloads photos from Google Photos using Playwright with a real Chrome instance.
The first time, manual login is required. Subsequent sessions reuse the saved session.

Supports:
  - on_downloaded callback for pipeline upload (upload starts as photos download)
  - Concurrent deletion using multiple browser tabs

Usage:
    python3 download_photos.py              # download current month's photos
    python3 download_photos.py 2026 4       # download photos for April 2026
"""

import glob
import os
import shutil
import sys
import time
from datetime import datetime
from typing import Callable, Optional

import config

# --- Configuration ------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_DIR = os.path.join(BASE_DIR, ".chrome_session")
TEMP_DIR = os.path.join(BASE_DIR, ".tmp_download")

# MONTHS is imported from config.py to ensure consistency across the project.


def download_photos_for_month(
    year: int,
    month: int,
    on_downloaded: Optional[Callable[[dict], None]] = None,
) -> list[dict]:
    """
    Downloads photos for the specified month from Google Photos.
    Returns a list of dicts with 'local_path' and 'google_url'.

    If on_downloaded is provided, it is called immediately after each photo
    is saved locally, enabling a pipeline where uploads start in parallel
    while remaining photos are still being downloaded.
    """
    from playwright.sync_api import sync_playwright

    month_name = config.MONTHS[month]
    download_dir = os.path.join(TEMP_DIR, f"{year}_{month:02d}")
    os.makedirs(download_dir, exist_ok=True)
    os.makedirs(SESSION_DIR, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  PHOTO DOWNLOAD: {month_name} {year}")
    print(f"{'='*60}")
    print(f"  Download folder: {download_dir}")

    with sync_playwright() as p:
        # Use real Chrome with persistent profile (keeps login state)
        context = p.chromium.launch_persistent_context(
            user_data_dir=SESSION_DIR,
            channel="chrome",
            headless=False,
            accept_downloads=True,
            args=["--disable-blink-features=AutomationControlled"],
        )

        page = context.pages[0] if context.pages else context.new_page()

        # Navigate to Google Photos
        print("\n  Opening Google Photos...")
        page.goto("https://photos.google.com/", wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)

        if "accounts.google.com" in page.url or "signin" in page.url.lower():
            print("\n  ⚠️  YOU ARE NOT LOGGED IN!")
            if sys.stdin.isatty():
                print("  -> Please login in the opened Chrome window.")
                print("  -> When you reach the Google Photos home, press ENTER here.")
                input("  Press ENTER to continue...")
                page.goto("https://photos.google.com/", wait_until="domcontentloaded", timeout=30000)
                time.sleep(2)
            else:
                print("  -> ERROR: Session expired and running in automatic mode. Failing preemptively.")
                context.close()
                raise RuntimeError("Not logged into Google Photos. Run 'python3 setup_auth.py' manually.")

        # Navigate to date search
        search_query = f"{month_name} {year}"
        import urllib.parse
        search_url = f"https://photos.google.com/search/{urllib.parse.quote(search_query)}"
        print(f"\n  Searching photos for: {search_query}...")
        page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)

        # Scroll the page to load all photos
        print("  Scrolling page to load all photos...")
        prev_count = 0
        scroll_attempts = 0
        max_scroll_attempts = 50

        while scroll_attempts < max_scroll_attempts:
            photo_links = page.query_selector_all('a[data-photo-id], div[data-media-key], a[href*="/photo/"]')
            current_count = len(photo_links)

            if current_count > 0 and current_count == prev_count:
                scroll_attempts += 1
                if scroll_attempts >= 3:
                    break
            else:
                scroll_attempts = 0

            prev_count = current_count
            page.keyboard.press("End")
            time.sleep(1.5)

        # Collect photo links
        photo_elements = page.query_selector_all('a[href*="/photo/"]')
        if not photo_elements:
            photo_elements = page.query_selector_all('[data-latest-bg]')

        photo_urls = []
        for el in photo_elements:
            href = el.get_attribute("href")
            if href and "/photo/" in href:
                full_url = href if href.startswith("http") else f"https://photos.google.com{href}"
                if full_url not in photo_urls:
                    photo_urls.append(full_url)

        if not photo_urls:
            print(f"\n  No photos found for {month_name} {year}.")
            print("  Check that there are photos in Google Photos for this period.")
            debug_path = os.path.join(BASE_DIR, "debug_screenshot.png")
            page.screenshot(path=debug_path)
            print(f"  Screenshot saved to: {debug_path}")
            context.close()
            return []

        print(f"\n  Found {len(photo_urls)} photos. Starting download...")
        downloaded_files = []

        for i, photo_url in enumerate(photo_urls, 1):
            try:
                print(f"  [{i}/{len(photo_urls)}] Downloading...", end=" ", flush=True)
                page.goto(photo_url, wait_until="domcontentloaded", timeout=20000)
                time.sleep(1)

                # Use Shift+D to download (Google Photos shortcut)
                with page.expect_download(timeout=30000) as download_info:
                    page.keyboard.down("Shift")
                    page.keyboard.press("d")
                    page.keyboard.up("Shift")

                download = download_info.value
                filename = download.suggested_filename
                save_path = os.path.join(download_dir, filename)

                # Avoid duplicates
                if os.path.exists(save_path):
                    print(f"SKIP (already present): {filename}")
                    download.delete()
                    item = {"local_path": save_path, "google_url": photo_url}
                    if not any(d["local_path"] == save_path for d in downloaded_files):
                        downloaded_files.append(item)
                        if on_downloaded:
                            on_downloaded(item)
                    continue

                download.save_as(save_path)
                item = {"local_path": save_path, "google_url": photo_url}
                downloaded_files.append(item)
                print(f"OK: {filename}")

                # Notify pipeline: this photo is ready for upload
                if on_downloaded:
                    on_downloaded(item)

            except Exception as e:
                print(f"ERROR: {e}")

        context.close()

    print(f"\n{'='*60}")
    print(f"  DOWNLOAD COMPLETED: {month_name} {year}")
    print(f"  Downloaded files: {len(downloaded_files)}")
    print(f"  Folder: {download_dir}")
    print(f"{'='*60}\n")

    return downloaded_files


def delete_photos_from_google(items: list[dict]) -> None:
    """
    Deletes photos from Google Photos using multiple browser tabs concurrently.
    Uses the '#' keyboard shortcut to move items to the trash.
    Processes CONCURRENT_DELETES photos at a time using separate tabs.
    """
    if not items:
        return

    from playwright.sync_api import sync_playwright

    concurrent = getattr(config, "CONCURRENT_DELETES", 3)

    print(f"\n{'='*60}")
    print(f"  DELETING FROM GOOGLE PHOTOS (to trash): {len(items)} photos")
    print(f"  Concurrent tabs: {concurrent}")
    print(f"{'='*60}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=SESSION_DIR,
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )

        deleted_count = 0
        error_count = 0

        # Process in batches of `concurrent` items
        for batch_start in range(0, len(items), concurrent):
            batch = items[batch_start : batch_start + concurrent]
            batch_num = batch_start // concurrent + 1
            total_batches = (len(items) + concurrent - 1) // concurrent
            print(f"\n  --- Batch {batch_num}/{total_batches} ({len(batch)} photos) ---")

            # Open a tab for each item in the batch and navigate
            tabs = []
            for item in batch:
                url = item.get("google_url")
                if not url:
                    continue
                tab = context.new_page()
                tabs.append((tab, item, url))

            # Navigate all tabs to their photo URLs concurrently
            for tab, item, url in tabs:
                try:
                    tab.goto(url, wait_until="domcontentloaded", timeout=20000)
                except Exception as e:
                    print(f"  Navigation error: {e}")

            # Wait for all pages to load
            time.sleep(2)

            # Now press # + Enter on each tab to delete
            for idx, (tab, item, url) in enumerate(tabs):
                i = batch_start + idx + 1
                try:
                    print(f"  [{i}/{len(items)}] Deleting...", end=" ", flush=True)
                    tab.keyboard.press("#")
                    time.sleep(0.5)
                    tab.keyboard.press("Enter")
                    time.sleep(1)
                    print("DONE")
                    deleted_count += 1
                except Exception as e:
                    print(f"ERROR: {e}")
                    error_count += 1

            # Close batch tabs
            for tab, _, _ in tabs:
                try:
                    tab.close()
                except Exception:
                    pass

            # Brief pause between batches
            time.sleep(0.5)

        context.close()

    print(f"\n  Successfully deleted: {deleted_count}/{len(items)}")
    if error_count > 0:
        print(f"  Errors: {error_count}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    if len(sys.argv) == 3:
        year = int(sys.argv[1])
        month = int(sys.argv[2])
    else:
        now = datetime.now()
        year = now.year
        month = now.month

    if not 1 <= month <= 12:
        print(f"Invalid month: {month}")
        sys.exit(1)

    files = download_photos_for_month(year, month)
    if files:
        print(f"Downloaded {len(files)} photos to .tmp_download/{year}_{month:02d}/")
