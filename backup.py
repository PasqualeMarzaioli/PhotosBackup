"""
Runs the monthly Google Photos to OneDrive backup workflow.

Author: Pasquale Marzaioli

Main backup script: Google Photos -> OneDrive.
Executed manually or by the daily macOS LaunchAgent checker.

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

import argparse
import logging
import os
import shutil
import sys
import threading
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
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


def _remove_runtime_path(path: str) -> bool:
    """Remove a generated runtime path if it exists."""
    if not os.path.exists(path):
        return False

    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        logger.info(f"Cleaned runtime artifact: {path}")
        return True
    except Exception as exc:
        logger.warning(f"Could not clean runtime artifact {path}: {exc}")
        return False


def cleanup_runtime_artifacts() -> int:
    """
    Remove generated cache files after a fully successful backup run.

    This intentionally preserves credentials, browser login state, logs,
    configuration, and the virtual environment.
    """
    if not bool(getattr(config, "CLEAN_RUNTIME_ARTIFACTS_AFTER_SUCCESS", True)):
        logger.info("Post-success runtime cleanup is disabled by configuration.")
        return 0

    base_dir = getattr(config, "BASE_DIR", os.path.dirname(os.path.abspath(__file__)))
    paths = [
        os.path.join(base_dir, "__pycache__"),
        os.path.join(base_dir, "tests", "__pycache__"),
    ]

    if bool(getattr(config, "CLEAN_CHROME_CACHE_AFTER_SUCCESS", True)):
        session_dir = getattr(download_photos, "SESSION_DIR", os.path.join(base_dir, ".chrome_session"))
        cache_paths = [
            "Default/Cache",
            "Default/Code Cache",
            "Default/GPUCache",
            "Default/DawnGraphiteCache",
            "Default/DawnWebGPUCache",
            "GrShaderCache",
            "GraphiteDawnCache",
            "ShaderCache",
            "GPUPersistentCache/GPUCache",
            "optimization_guide_model_store",
        ]
        paths.extend(os.path.join(session_dir, cache_path) for cache_path in cache_paths)

    cleaned_count = 0
    for path in paths:
        if _remove_runtime_path(path):
            cleaned_count += 1

    logger.info(f"Post-success runtime cleanup completed. Removed paths: {cleaned_count}")
    return cleaned_count


def send_telegram(message: str) -> None:
    token = getattr(config, "TELEGRAM_BOT_TOKEN", "")
    chat_id = getattr(config, "TELEGRAM_CHAT_ID", "")
    if not token or not chat_id or token.startswith("YOUR_") or chat_id.startswith("YOUR_"):
        logger.debug("Telegram credentials are not configured; notification skipped.")
        return

    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
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
    filename = item.get("upload_filename") or os.path.basename(local_path)
    try:
        if item.get("upload_filename"):
            ok = onedrive.upload_file(local_path, onedrive_path, target_filename=filename)
        else:
            ok = onedrive.upload_file(local_path, onedrive_path)
        if ok:
            # Keep the download until the whole run succeeds so restart needs no download.
            logger.info(f"  ✅ Uploaded: {filename}")
        else:
            logger.error(f"  ❌ Upload failed: {filename}")
        return (item, ok)
    except Exception as e:
        logger.error(f"  ❌ Upload error {filename}: {e}")
        return (item, False)


def run_backup(year: int, month: int) -> bool:
    """Protect the shared browser profile and downloads across all entry points."""
    datetime(year, month, 1)
    with download_photos.runtime_lock():
        return _run_backup(year, month)


def _run_backup(year: int, month: int) -> bool:
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
    onedrive.verify_connection()

    # ── Pipeline: concurrent uploads start as photos are downloaded ──────────
    executor = ThreadPoolExecutor(
        max_workers=max(1, int(config.CONCURRENT_UPLOADS)),
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
    except BaseException as e:
        logger.error(f"Error downloading from Google Photos: {e}")
        # Workers must finish before releasing the lock or closing the process.
        executor.shutdown(wait=True, cancel_futures=True)
        raise

    if not all_items:
        download_errors = download_photos.get_last_download_errors()
        if download_errors:
            logger.error(f"No files downloaded, but {len(download_errors)} download errors occurred.")
            for error in download_errors[:5]:
                logger.error(f"  Download error: {error}")
        else:
            logger.info("No photos found or downloaded for this month.")
        executor.shutdown()
        return not download_errors

    logger.info(f"All {len(all_items)} photos downloaded. Waiting for remaining uploads...")

    # 2. Wait for all upload futures to complete
    success_count = 0
    error_count = 0
    successful_items = []

    with futures_lock:
        pending = list(futures)

    item_by_future = {future: item for future, item in pending}
    for future in as_completed(item_by_future):
        item = item_by_future[future]
        try:
            returned_item, ok = future.result()
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

    download_errors = download_photos.get_last_download_errors()
    download_error_count = len(download_errors)
    if download_error_count:
        logger.error(f"Download errors: {download_error_count}")
        for error in download_errors[:10]:
            logger.error(f"  Download error: {error}")
        if download_error_count > 10:
            logger.error(f"  ... plus {download_error_count - 10} more download errors")

    pre_delete_error_count = error_count + download_error_count
    deletion_error_count = 0

    # 3. Delete from Google Photos (concurrent tabs)
    should_delete = bool(getattr(config, "DELETE_AFTER_UPLOAD", True))
    partial_delete_allowed = bool(getattr(config, "DELETE_ON_PARTIAL_SUCCESS", False))
    if successful_items and should_delete and (pre_delete_error_count == 0 or partial_delete_allowed):
        logger.info(f"Deleting {len(successful_items)} items from Google Photos...")
        try:
            delete_result = download_photos.delete_photos_from_google(successful_items)
            deletion_error_count = int(delete_result.get("errors", 0))
            deleted_count = int(delete_result.get("deleted", 0))
            requested_delete_count = int(delete_result.get("requested", len(successful_items)))
            if deleted_count != requested_delete_count:
                deletion_error_count += max(0, requested_delete_count - deleted_count - deletion_error_count)
            if deletion_error_count:
                logger.error(f"Google Photos deletion errors: {deletion_error_count}")
        except Exception as e:
            logger.error(f"Error deleting from Google Photos: {e}")
            deletion_error_count = len(successful_items)
    elif successful_items and should_delete:
        logger.warning(
            "Google Photos deletion skipped because the run had errors. "
            "Uploaded files remain backed up on OneDrive."
        )
    elif successful_items:
        logger.info("Google Photos deletion is disabled by configuration.")

    total_error_count = pre_delete_error_count + deletion_error_count

    # Clean up generated runtime files only after a fully successful run.
    if total_error_count == 0:
        download_photos.cleanup_completed_downloads(year, month, all_items)
        cleanup_runtime_artifacts()
    elif os.path.exists(temp_dir):
        logger.warning(f"Temporary files kept for inspection: {temp_dir}")

    logger.info("=" * 60)
    logger.info(f"BACKUP COMPLETED: {month_name} {year}")
    logger.info(f"  Successfully uploaded : {success_count}")
    logger.info(f"  Upload errors         : {error_count}")
    logger.info(f"  Download errors       : {download_error_count}")
    logger.info(f"  Deletion errors       : {deletion_error_count}")
    logger.info(f"  Errors                : {total_error_count}")
    logger.info("=" * 60)

    if total_error_count == 0:
        deletion_text = (
            "Google Photos items were moved to trash automatically."
            if should_delete
            else "Google Photos deletion is disabled."
        )
        send_telegram(
            f"*Backup completed*\n\n"
            f"{success_count} files from *{month_name} {year}* are stored on OneDrive.\n\n"
            f"{deletion_text}"
        )
    else:
        send_telegram(
            f"*Backup for {month_name} {year} completed with errors*\n\n"
            f"Uploaded: {success_count}\n"
            f"Upload errors: {error_count}\n"
            f"Download errors: {download_error_count}\n\n"
            f"Deletion errors: {deletion_error_count}\n\n"
            f"Google Photos deletion was skipped unless DELETE_ON_PARTIAL_SUCCESS is enabled."
        )
    return total_error_count == 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up Google Photos to OneDrive")
    parser.add_argument("year", nargs="?", type=int)
    parser.add_argument("month", nargs="?", type=int)
    args = parser.parse_args()
    if (args.year is None) != (args.month is None):
        parser.error("Provide both year and month, or neither")
    if args.year is not None:
        year, month = args.year, args.month
    else:
        # Default: previous month
        now = datetime.now()
        month = now.month - 1 if now.month > 1 else 12
        year = now.year if now.month > 1 else now.year - 1

    try:
        datetime(year, month, 1)
        if not run_backup(year, month):
            sys.exit(1)
    except (ValueError, RuntimeError, OSError, requests.RequestException) as exc:
        logger.error(f"Backup failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
