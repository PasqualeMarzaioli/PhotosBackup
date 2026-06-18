"""
Downloads Google Photos media through a persistent browser session.

Author: Pasquale Marzaioli

Downloads media from Google Photos using Playwright with a persistent Chrome
profile. The first run requires a manual login; later runs reuse the saved
browser session.

The browser route is intentionally kept as the active downloader because the
Google Photos Library API is not reliable for a full personal-library backup.
"""

import calendar
import hashlib
import os
import sys
import time
import urllib.parse
from datetime import datetime
from typing import Callable, Optional

import config

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_DIR = os.path.join(BASE_DIR, ".chrome_session")
TEMP_DIR = getattr(config, "TEMP_DIR", os.path.join(BASE_DIR, ".tmp_download"))

_LAST_DOWNLOAD_ERRORS: list[str] = []


def get_last_download_errors() -> list[str]:
    """Return errors collected during the most recent download run."""
    return list(_LAST_DOWNLOAD_ERRORS)


def _cfg(name: str, default):
    return getattr(config, name, default)


def _browser_args() -> list[str]:
    args = ["--disable-blink-features=AutomationControlled"]
    if bool(_cfg("MUTE_BROWSER_AUDIO", True)):
        args.append("--mute-audio")
    return args


def _wait(page, seconds: float) -> None:
    try:
        page.wait_for_timeout(int(seconds * 1000))
    except Exception:
        time.sleep(seconds)


def _search_queries(year: int, month: int) -> list[str]:
    queries = []
    localized = f"{config.MONTHS[month]} {year}"
    english = f"{calendar.month_name[month]} {year}"
    for query in (localized, english):
        if query and query not in queries:
            queries.append(query)
    return queries


def _normalise_photo_url(url: str) -> Optional[str]:
    if not url or "/photo/" not in url:
        return None

    parsed = urllib.parse.urlparse(url if url.startswith("http") else f"https://photos.google.com{url}")
    photo_marker = "/photo/"
    marker_index = parsed.path.find(photo_marker)
    if marker_index < 0:
        return None

    photo_id = parsed.path[marker_index + len(photo_marker) :].split("/", 1)[0]
    photo_id = urllib.parse.unquote(photo_id).strip()
    if not photo_id:
        return None

    return f"https://photos.google.com/photo/{urllib.parse.quote(photo_id, safe='')}"


def _photo_key(url: str) -> str:
    canonical_url = _normalise_photo_url(url)
    return canonical_url or url.split("#", 1)[0].split("?", 1)[0]


def _photo_urls_from_page(page) -> list[str]:
    script = """
    () => {
        const urls = new Set();
        const add = (href) => {
            if (!href || !href.includes('/photo/')) return;
            urls.add(new URL(href, window.location.origin).href.split('#')[0].split('?')[0]);
        };

        document.querySelectorAll('a[href*="/photo/"]').forEach((el) => add(el.href));
        document.querySelectorAll('[data-photo-id], [data-media-key], [data-latest-bg]').forEach((el) => {
            const anchor = el.closest('a[href*="/photo/"]');
            if (anchor) add(anchor.href);
        });

        return Array.from(urls);
    }
    """
    urls: list[str] = []

    try:
        result = page.evaluate(script)
        if isinstance(result, list):
            urls.extend(result)
    except Exception:
        pass

    if urls:
        return list(dict.fromkeys(filter(None, (_normalise_photo_url(url) for url in urls))))

    try:
        elements = page.query_selector_all('a[href*="/photo/"]')
    except Exception:
        elements = []

    for element in elements:
        href = element.get_attribute("href")
        full_url = _normalise_photo_url(href)
        if full_url:
            urls.append(full_url)

    return list(dict.fromkeys(urls))


def _scroll_metrics(page) -> Optional[dict]:
    script = """
    () => ({
        scrollY: window.scrollY,
        innerHeight: window.innerHeight,
        scrollHeight: document.documentElement.scrollHeight || document.body.scrollHeight || 0
    })
    """
    try:
        metrics = page.evaluate(script)
    except Exception:
        return None
    return metrics if isinstance(metrics, dict) else None


def _scroll_down(page) -> None:
    try:
        page.evaluate("() => window.scrollBy(0, Math.max(window.innerHeight * 0.9, 700))")
    except Exception:
        page.keyboard.press("PageDown")


def _mute_media_on_page(page) -> None:
    if not bool(_cfg("MUTE_BROWSER_AUDIO", True)):
        return

    script = """
    () => {
        for (const media of document.querySelectorAll('video, audio')) {
            media.muted = true;
            media.volume = 0;
        }
    }
    """
    try:
        page.evaluate(script)
    except Exception:
        pass


def _collect_urls_while_scrolling(page) -> list[str]:
    max_scrolls = int(_cfg("MAX_SCROLL_ATTEMPTS", 120))
    idle_rounds = int(_cfg("SCROLL_IDLE_ROUNDS", 8))
    pause_seconds = float(_cfg("SCROLL_PAUSE_SECONDS", 1.0))

    seen: set[str] = set()
    ordered: list[str] = []
    stable_rounds = 0
    previous_scroll_y = None
    previous_scroll_height = None

    for _ in range(max_scrolls):
        before_count = len(seen)
        for url in _photo_urls_from_page(page):
            if url not in seen:
                seen.add(url)
                ordered.append(url)

        metrics = _scroll_metrics(page)
        if metrics:
            scroll_y = float(metrics.get("scrollY", 0) or 0)
            inner_height = float(metrics.get("innerHeight", 0) or 0)
            scroll_height = float(metrics.get("scrollHeight", 0) or 0)
            at_bottom = scroll_y + inner_height >= scroll_height - 40
            did_not_move = (
                previous_scroll_y == scroll_y
                and previous_scroll_height == scroll_height
            )
            previous_scroll_y = scroll_y
            previous_scroll_height = scroll_height
        else:
            at_bottom = True
            did_not_move = True

        if len(seen) == before_count and (at_bottom or did_not_move):
            stable_rounds += 1
        else:
            stable_rounds = 0

        if stable_rounds >= idle_rounds:
            break

        _scroll_down(page)
        _wait(page, pause_seconds)

    return ordered


def _next_unique_filename(filename: str, used_names: set[str]) -> str:
    safe_name = os.path.basename(filename or "").strip() or "google-photo"
    root, ext = os.path.splitext(safe_name)
    candidate = safe_name
    counter = 2

    while candidate.casefold() in used_names:
        candidate = f"{root} ({counter}){ext}"
        counter += 1

    return candidate


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_one(page, photo_url: str, index: int, total: int, download_dir: str, used_names: set[str]) -> dict:
    print(f"  [{index}/{total}] Downloading...", end=" ", flush=True)

    page.goto(
        photo_url,
        wait_until="domcontentloaded",
        timeout=int(_cfg("PHOTO_NAVIGATION_TIMEOUT_MS", 60000)),
    )
    _wait(page, 1.0)
    _mute_media_on_page(page)

    with page.expect_download(timeout=int(_cfg("DOWNLOAD_TIMEOUT_MS", 120000))) as download_info:
        page.keyboard.press("Shift+D")

    download = download_info.value
    original_filename = os.path.basename(download.suggested_filename or f"google-photo-{index}")
    upload_filename = _next_unique_filename(original_filename, used_names)
    save_path = os.path.join(download_dir, upload_filename)

    download.save_as(save_path)
    if not os.path.exists(save_path) or os.path.getsize(save_path) == 0:
        raise RuntimeError(f"Downloaded file is empty or missing: {upload_filename}")

    used_names.add(upload_filename.casefold())
    item = {
        "local_path": save_path,
        "google_url": photo_url,
        "original_filename": original_filename,
        "upload_filename": upload_filename,
        "size": os.path.getsize(save_path),
    }
    print(f"OK: {upload_filename}")
    return item


def download_photos_for_month(
    year: int,
    month: int,
    on_downloaded: Optional[Callable[[dict], None]] = None,
) -> list[dict]:
    """
    Download media for the specified month from Google Photos.

    Returns dictionaries with local_path, google_url, original_filename,
    upload_filename, and size. If on_downloaded is provided, it is called after
    each file is saved locally so uploads can start immediately.
    """
    from playwright.sync_api import sync_playwright

    global _LAST_DOWNLOAD_ERRORS
    _LAST_DOWNLOAD_ERRORS = []

    month_name = config.MONTHS[month]
    download_root = os.path.join(TEMP_DIR, f"{year}_{month:02d}")
    download_dir = os.path.join(download_root, datetime.now().strftime("run_%Y%m%d_%H%M%S"))
    os.makedirs(download_dir, exist_ok=True)
    os.makedirs(SESSION_DIR, exist_ok=True)

    print(f"\n{'=' * 60}")
    print(f"  PHOTO DOWNLOAD: {month_name} {year}")
    print(f"{'=' * 60}")
    print(f"  Download folder: {download_dir}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=SESSION_DIR,
            channel="chrome",
            headless=bool(_cfg("BROWSER_HEADLESS", False)),
            accept_downloads=True,
            args=_browser_args(),
        )

        try:
            page = context.pages[0] if context.pages else context.new_page()

            print("\n  Opening Google Photos...")
            page.goto(
                "https://photos.google.com/",
                wait_until="domcontentloaded",
                timeout=int(_cfg("GOOGLE_PHOTOS_TIMEOUT_MS", 60000)),
            )
            _wait(page, 2.0)

            if "accounts.google.com" in page.url or "signin" in page.url.lower():
                print("\n  WARNING: You are not logged in.")
                if sys.stdin.isatty():
                    print("  Please log in in the opened Chrome window.")
                    print("  When Google Photos is loaded, press ENTER here.")
                    input("  Press ENTER to continue...")
                    page.goto(
                        "https://photos.google.com/",
                        wait_until="domcontentloaded",
                        timeout=int(_cfg("GOOGLE_PHOTOS_TIMEOUT_MS", 60000)),
                    )
                    _wait(page, 2.0)
                else:
                    raise RuntimeError("Not logged into Google Photos. Run 'python3 setup_auth.py' manually.")

            all_urls: list[str] = []
            seen_photo_keys: set[str] = set()

            for search_query in _search_queries(year, month):
                search_url = f"https://photos.google.com/search/{urllib.parse.quote(search_query, safe='')}"
                print(f"\n  Searching Google Photos for: {search_query}...")
                page.goto(
                    search_url,
                    wait_until="domcontentloaded",
                    timeout=int(_cfg("GOOGLE_PHOTOS_TIMEOUT_MS", 60000)),
                )
                _wait(page, float(_cfg("SEARCH_SETTLE_SECONDS", 3.0)))

                print("  Scrolling until the media grid stops loading new items...")
                found_urls = _collect_urls_while_scrolling(page)
                new_urls = []
                for url in found_urls:
                    photo_key = _photo_key(url)
                    if photo_key in seen_photo_keys:
                        continue
                    seen_photo_keys.add(photo_key)
                    new_urls.append(url)
                    all_urls.append(url)
                print(f"  Query result: {len(found_urls)} links, {len(new_urls)} new.")

            if not all_urls:
                print(f"\n  No media links found for {month_name} {year}.")
                debug_path = os.path.join(BASE_DIR, "debug_screenshot.png")
                page.screenshot(path=debug_path)
                print(f"  Screenshot saved to: {debug_path}")
                return []

            print(f"\n  Found {len(all_urls)} unique media links. Starting download...")
            downloaded_files: list[dict] = []
            used_upload_names: set[str] = set()
            downloaded_hashes: set[str] = set()
            retries = max(1, int(_cfg("DOWNLOAD_RETRIES", 3)))

            for index, photo_url in enumerate(all_urls, 1):
                last_error = None
                for attempt in range(1, retries + 1):
                    try:
                        item = _download_one(page, photo_url, index, len(all_urls), download_dir, used_upload_names)
                        file_hash = _file_sha256(item["local_path"])
                        if file_hash in downloaded_hashes:
                            print(f"SKIP DUPLICATE CONTENT: {item['original_filename']}")
                            try:
                                os.remove(item["local_path"])
                            except OSError:
                                pass
                            last_error = None
                            break

                        item["sha256"] = file_hash
                        downloaded_hashes.add(file_hash)
                        downloaded_files.append(item)
                        if on_downloaded:
                            on_downloaded(item)
                        last_error = None
                        break
                    except Exception as exc:
                        last_error = exc
                        if attempt < retries:
                            print(f"RETRY {attempt}/{retries}: {exc}")
                            _wait(page, min(2.0 * attempt, 10.0))
                        else:
                            print(f"ERROR: {exc}")

                if last_error is not None:
                    _LAST_DOWNLOAD_ERRORS.append(f"{photo_url}: {last_error}")

        finally:
            context.close()

    print(f"\n{'=' * 60}")
    print(f"  DOWNLOAD COMPLETED: {month_name} {year}")
    print(f"  Downloaded files: {len(downloaded_files)}")
    print(f"  Download errors: {len(_LAST_DOWNLOAD_ERRORS)}")
    print(f"  Folder: {download_dir}")
    print(f"{'=' * 60}\n")

    return downloaded_files


def delete_photos_from_google(items: list[dict]) -> dict:
    """
    Move Google Photos items to trash using the browser shortcut.

    Processes CONCURRENT_DELETES items at a time using separate tabs.
    """
    if not items:
        return {"requested": 0, "deleted": 0, "errors": 0}

    from playwright.sync_api import sync_playwright

    concurrent = max(1, int(getattr(config, "CONCURRENT_DELETES", 3)))

    print(f"\n{'=' * 60}")
    print(f"  DELETING FROM GOOGLE PHOTOS (to trash): {len(items)} items")
    print(f"  Concurrent tabs: {concurrent}")
    print(f"{'=' * 60}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=SESSION_DIR,
            channel="chrome",
            headless=bool(_cfg("BROWSER_HEADLESS", False)),
            args=_browser_args(),
        )

        try:
            deleted_count = 0
            error_count = 0

            for batch_start in range(0, len(items), concurrent):
                batch = items[batch_start : batch_start + concurrent]
                batch_num = batch_start // concurrent + 1
                total_batches = (len(items) + concurrent - 1) // concurrent
                print(f"\n  --- Batch {batch_num}/{total_batches} ({len(batch)} items) ---")

                tabs = []
                for item in batch:
                    url = item.get("google_url")
                    if not url:
                        error_count += 1
                        continue
                    tab = context.new_page()
                    tabs.append((tab, item, url))

                for tab, item, url in tabs:
                    try:
                        tab.goto(
                            url,
                            wait_until="domcontentloaded",
                            timeout=int(_cfg("PHOTO_NAVIGATION_TIMEOUT_MS", 60000)),
                        )
                        _mute_media_on_page(tab)
                    except Exception as exc:
                        print(f"  Navigation error: {exc}")

                if tabs:
                    _wait(tabs[0][0], 2.0)

                for idx, (tab, item, url) in enumerate(tabs):
                    item_num = batch_start + idx + 1
                    try:
                        print(f"  [{item_num}/{len(items)}] Deleting...", end=" ", flush=True)
                        tab.keyboard.press("#")
                        _wait(tab, 0.5)
                        tab.keyboard.press("Enter")

                        deletion_verified = False
                        for _ in range(5):
                            _wait(tab, 1.0)
                            if tab.url != url:
                                deletion_verified = True
                                break

                        if deletion_verified:
                            print("DONE")
                            deleted_count += 1
                        else:
                            print("FAILED (URL did not change)")
                            error_count += 1
                    except Exception as exc:
                        print(f"ERROR: {exc}")
                        error_count += 1

                for tab, _, _ in tabs:
                    try:
                        tab.close()
                    except Exception:
                        pass

                time.sleep(0.5)

        finally:
            context.close()

    print(f"\n  Successfully deleted: {deleted_count}/{len(items)}")
    if error_count > 0:
        print(f"  Errors: {error_count}")
    print(f"{'=' * 60}\n")
    return {"requested": len(items), "deleted": deleted_count, "errors": error_count}


if __name__ == "__main__":
    if len(sys.argv) == 3:
        year = int(sys.argv[1])
        month = int(sys.argv[2])
    else:
        now = datetime.now()
        month = now.month - 1 if now.month > 1 else 12
        year = now.year if now.month > 1 else now.year - 1

    if not 1 <= month <= 12:
        print(f"Invalid month: {month}")
        sys.exit(1)

    files = download_photos_for_month(year, month)
    if files:
        print(f"Downloaded {len(files)} files to .tmp_download/{year}_{month:02d}/")
