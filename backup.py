"""
Main backup script: Google Photos -> OneDrive.
Executed automatically every 2nd of the month via macOS LaunchAgent.

Uses a pipeline architecture for maximum speed:
  1. Photos are downloaded one by one from Google Photos (Playwright)
  2. As each photo finishes downloading, it is immediately submitted
     to a thread pool that uploads CONCURRENT_UPLOADS files in parallel
  3. After all uploads succeed, photos are deleted from Google Photos
     using CONCURRENT_DELETES browser tabs simultaneously

Manual usage:
    python backup.py               # backup for previous month
    python backup.py 2026 3        # backup for March 2026
"""

import logging
import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime

import requests

import config
import download_photos
import onedrive

# --- Logging ------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def send_telegram(message: str) -> None:
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": config.TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Telegram not reachable: {e}")


def onedrive_path_for_month(year: int, month: int) -> str:
    """
    Builds the OneDrive path for the given month.
    Ex: Immagini/Memorie/2026/04.April 2026
    """
    month_name = config.MONTHS[month]
    month_folder = f"{month:02d}.{month_name} {year}"
    return f"{config.ONEDRIVE_ROOT_FOLDER}/{year}/{month_folder}"


def _upload_one(item: dict, onedrive_path: str) -> tuple[dict, bool]:
    """
    Upload a single file to OneDrive. Returns (item, success).
    Thread-safe: each call gets its own token from MSAL cache.
    """
    local_path = item["local_path"]
    filename = os.path.basename(local_path)
    try:
        ok = onedrive.upload_file(local_path, onedrive_path)
        if ok:
            # Remove temp file after successful upload
            try:
                os.remove(local_path)
            except OSError:
                pass
            logger.info(f"  ✅ Uploaded: {filename}")
        else:
            logger.error(f"  ❌ Upload failed: {filename}")
        return (item, ok)
    except Exception as e:
        logger.error(f"  ❌ Upload error {filename}: {e}")
        return (item, False)


def run_backup(year: int, month: int) -> None:
    month_name = config.MONTHS[month]
    logger.info("=" * 60)
    logger.info(f"BACKUP STARTED: {month_name} {year}")
    logger.info(f"  Concurrent uploads: {config.CONCURRENT_UPLOADS}")
    logger.info(f"  Concurrent deletes: {config.CONCURRENT_DELETES}")
    logger.info("=" * 60)

    temp_dir = os.path.join(config.TEMP_DIR, f"{year}_{month:02d}")
    os.makedirs(temp_dir, exist_ok=True)
    os.makedirs(config.TOKENS_DIR, exist_ok=True)

    onedrive_path = onedrive_path_for_month(year, month)
    logger.info(f"OneDrive Destination: {onedrive_path}")

    # ── Pipeline: concurrent uploads start as photos are downloaded ──────────
    executor = ThreadPoolExecutor(
        max_workers=config.CONCURRENT_UPLOADS,
        thread_name_prefix="upload",
    )
    futures: list[tuple[Future, dict]] = []
    futures_lock = threading.Lock()

    def on_photo_downloaded(item: dict) -> None:
        """Callback: submit upload as soon as a photo finishes downloading."""
        future = executor.submit(_upload_one, item, onedrive_path)
        with futures_lock:
            futures.append((future, item))

    # 1. Download from Google Photos (uploads fire in background via callback)
    logger.info("Downloading photos from Google Photos (Playwright)...")
    logger.info("  Uploads will start in parallel as photos are downloaded.")
    try:
        all_items = download_photos.download_photos_for_month(
            year, month, on_downloaded=on_photo_downloaded,
        )
    except Exception as e:
        logger.error(f"Error downloading from Google Photos: {e}")
        executor.shutdown(wait=False)
        raise

    if not all_items:
        logger.info("No photos found or downloaded for this month.")
        executor.shutdown()
        return

    logger.info(f"All {len(all_items)} photos downloaded. Waiting for remaining uploads...")

    # 2. Wait for all upload futures to complete
    success_count = 0
    error_count = 0
    successful_items = []

    with futures_lock:
        pending = list(futures)

    for future, item in pending:
        try:
            returned_item, ok = future.result(timeout=300)  # 5 min max per file
            if ok:
                success_count += 1
                successful_items.append(returned_item)
            else:
                error_count += 1
        except Exception as e:
            filename = os.path.basename(item["local_path"])
            logger.error(f"  ❌ Upload exception {filename}: {e}")
            error_count += 1

    executor.shutdown()

    # 3. Delete from Google Photos (concurrent tabs)
    if successful_items:
        logger.info(f"Deleting {len(successful_items)} items from Google Photos...")
        try:
            download_photos.delete_photos_from_google(successful_items)
        except Exception as e:
            logger.error(f"Error deleting from Google Photos: {e}")

    # Clean up temporary directory
    if os.path.exists(temp_dir):
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass

    logger.info("=" * 60)
    logger.info(f"BACKUP COMPLETED: {month_name} {year}")
    logger.info(f"  Successfully uploaded : {success_count}")
    logger.info(f"  Errors                : {error_count}")
    logger.info("=" * 60)

    if error_count == 0:
        send_telegram(
            f"✅ *Backup completed!*\n\n"
            f"📁 {success_count} photos of *{month_name} {year}* are safely stored on OneDrive.\n\n"
            f"🗑 Photos deleted from Google Photos automatically."
        )
    else:
        send_telegram(
            f"⚠️ *Backup for {month_name} {year} completed with errors*\n\n"
            f"✅ Uploaded: {success_count}\n"
            f"❌ Errors: {error_count}\n\n"
            f"Check the logs before deleting photos from Google Photos."
        )


def main() -> None:
    if len(sys.argv) == 3:
        year = int(sys.argv[1])
        month = int(sys.argv[2])
    else:
        # Default: previous month
        now = datetime.now()
        month = now.month - 1 if now.month > 1 else 12
        year = now.year if now.month > 1 else now.year - 1

    if not 1 <= month <= 12:
        logger.error(f"Invalid month: {month}")
        sys.exit(1)

    run_backup(year, month)


if __name__ == "__main__":
    main()
