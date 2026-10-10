"""
Tests the Google Photos browser downloader helpers.

Author: Pasquale Marzaioli
"""

import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

# Add project path to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import download_photos  # noqa: E402


class TestDownloadPhotos(unittest.TestCase):

    def test_normalise_photo_url_canonicalises_route_variants(self):
        urls = [
            "https://photos.google.com/search/Aprile%202026/photo/AF1QipSame?pli=1",
            "https://photos.google.com/u/0/photo/AF1QipSame#details",
            "/search/April%202026/photo/AF1QipSame?foo=bar",
        ]

        normalised = [download_photos._normalise_photo_url(url) for url in urls]

        self.assertEqual(
            normalised,
            [
                "https://photos.google.com/photo/AF1QipSame",
                "https://photos.google.com/photo/AF1QipSame",
                "https://photos.google.com/photo/AF1QipSame",
            ],
        )

    def test_file_sha256_matches_identical_content(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            first = os.path.join(tmp_dir, "first.bin")
            second = os.path.join(tmp_dir, "second.bin")

            with open(first, "wb") as fh:
                fh.write(b"same photo bytes")
            with open(second, "wb") as fh:
                fh.write(b"same photo bytes")

            self.assertEqual(download_photos._file_sha256(first), download_photos._file_sha256(second))

    def test_photo_url_rejects_external_hosts(self):
        self.assertIsNone(download_photos._normalise_photo_url("https://example.invalid/photo/test"))

    def test_manifest_rejects_paths_outside_month(self):
        with tempfile.TemporaryDirectory() as root:
            url = "https://photos.google.com/photo/test"
            download_photos._save_download_manifest(root, {url: {"local_path": "/outside/photo.jpg"}})
            with self.assertRaisesRegex(RuntimeError, "outside"):
                download_photos._load_download_manifest(root)

    def test_cleanup_preserves_untracked_files_and_other_months(self):
        with tempfile.TemporaryDirectory() as root:
            month_root = os.path.join(root, "2026_04")
            os.makedirs(month_root)
            complete = os.path.join(month_root, "complete.jpg")
            pending = os.path.join(month_root, "pending.jpg")
            other = os.path.join(root, "2026_03")
            os.makedirs(other)
            for path in (complete, pending):
                with open(path, "wb") as fh:
                    fh.write(b"data")
            url = "https://photos.google.com/photo/test"
            item = {"local_path": complete, "google_url": url, "sha256": download_photos._file_sha256(complete), "upload_filename": "complete.jpg"}
            download_photos._save_download_manifest(month_root, {url: item})
            with patch.object(download_photos, "TEMP_DIR", root):
                download_photos.cleanup_completed_downloads(2026, 4, [item])
            self.assertFalse(os.path.exists(complete))
            self.assertTrue(os.path.exists(pending))
            self.assertTrue(os.path.exists(other))

    def test_runtime_lock_excludes_other_processes(self):
        import subprocess
        code = "import fcntl,sys; f=open(sys.argv[1],'a'); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"
        with tempfile.TemporaryDirectory() as root, patch.object(download_photos.config, "BASE_DIR", root):
            with download_photos.runtime_lock():
                blocked = subprocess.run([sys.executable, "-c", code, os.path.join(root, ".backup.lock")], capture_output=True)
                self.assertNotEqual(blocked.returncode, 0)
            available = subprocess.run([sys.executable, "-c", code, os.path.join(root, ".backup.lock")], capture_output=True)
            self.assertEqual(available.returncode, 0)

    def test_scroll_limit_reports_incomplete_listing(self):
        download_photos._LAST_DOWNLOAD_ERRORS.clear()
        page = MagicMock()
        with patch.object(download_photos.config, "MAX_SCROLL_ATTEMPTS", 2), patch.object(download_photos, "_photo_urls_from_page", return_value=["https://photos.google.com/photo/test"]), patch.object(download_photos, "_scroll_metrics", return_value={"scrollY": 0, "innerHeight": 100, "scrollHeight": 1000}):
            self.assertEqual(len(download_photos._collect_urls_while_scrolling(page)), 1)
        self.assertIn("incomplete", download_photos.get_last_download_errors()[0])

    @patch('playwright.sync_api.sync_playwright')
    def test_restart_reuses_verified_download(self, playwright):
        with ExitStack() as stack:
            root = stack.enter_context(tempfile.TemporaryDirectory())
            stack.enter_context(patch.object(download_photos, "TEMP_DIR", root))
            stack.enter_context(patch.object(download_photos, "SESSION_DIR", os.path.join(root, "session")))
            stack.enter_context(patch.object(download_photos, "_wait"))
            url = "https://photos.google.com/photo/test"
            stack.enter_context(patch.object(download_photos, "_collect_urls_while_scrolling", return_value=[url]))
            download = stack.enter_context(patch.object(download_photos, "_download_one"))
            month_root = os.path.join(root, "2026_04")
            os.makedirs(month_root)
            path = os.path.join(month_root, "photo.jpg")
            with open(path, "wb") as fh:
                fh.write(b"data")
            item = {"local_path": path, "google_url": url, "upload_filename": "photo.jpg", "sha256": download_photos._file_sha256(path)}
            download_photos._save_download_manifest(month_root, {url: item})
            context = playwright.return_value.__enter__.return_value.chromium.launch_persistent_context.return_value
            page = MagicMock()
            page.url = "https://photos.google.com/"
            context.pages = [page]
            callback = MagicMock()
            self.assertEqual(download_photos.download_photos_for_month(2026, 4, callback), [item])
            download.assert_not_called()
            callback.assert_called_once_with(item)

    @patch('playwright.sync_api.sync_playwright')
    def test_browser_closure_stops_retries_and_preserves_downloads(self, playwright):
        with ExitStack() as stack:
            root = stack.enter_context(tempfile.TemporaryDirectory())
            stack.enter_context(patch.object(download_photos, "TEMP_DIR", root))
            stack.enter_context(patch.object(download_photos, "SESSION_DIR", os.path.join(root, "session")))
            wait = stack.enter_context(patch.object(download_photos, "_wait"))
            urls = ["https://photos.google.com/photo/first", "https://photos.google.com/photo/second"]
            stack.enter_context(patch.object(download_photos, "_collect_urls_while_scrolling", return_value=urls))
            download = stack.enter_context(patch.object(download_photos, "_download_one", side_effect=RuntimeError("Target closed")))
            month_root = os.path.join(root, "2026_04")
            os.makedirs(month_root)
            path = os.path.join(month_root, "pending.jpg")
            with open(path, "wb") as fh:
                fh.write(b"pending photo")
            item = {"local_path": path, "google_url": urls[1], "upload_filename": "pending.jpg", "sha256": download_photos._file_sha256(path)}
            download_photos._save_download_manifest(month_root, {urls[1]: item})
            context = playwright.return_value.__enter__.return_value.chromium.launch_persistent_context.return_value
            page = MagicMock()
            page.url = "https://photos.google.com/"
            page.is_closed.return_value = True
            context.pages = [page]
            callback = MagicMock()
            with self.assertRaisesRegex(RuntimeError, "preserved"):
                download_photos.download_photos_for_month(2026, 4, callback)
            download.assert_called_once()
            callback.assert_not_called()
            self.assertEqual(wait.call_count, 3)
            self.assertEqual(download_photos._load_download_manifest(month_root), {urls[1]: item})
            self.assertTrue(os.path.isfile(path))
            context.close.assert_called_once()

    @patch('playwright.sync_api.sync_playwright')
    def test_failed_navigation_never_attempts_deletion(self, playwright):
        context = playwright.return_value.__enter__.return_value.chromium.launch_persistent_context.return_value
        tab = context.new_page.return_value
        tab.goto.side_effect = RuntimeError("Navigation failed")
        with patch.object(download_photos, "_wait"), patch.object(download_photos.time, "sleep"):
            result = download_photos.delete_photos_from_google([{"google_url": "https://photos.google.com/photo/test"}])
        self.assertEqual(result, {"requested": 1, "deleted": 0, "errors": 1})
        tab.keyboard.press.assert_not_called()

    @patch('playwright.sync_api.sync_playwright')
    def test_download_photos_for_month_empty(self, mock_sync_playwright):
        # Mock Playwright structure
        mock_p = MagicMock()
        mock_sync_playwright.return_value.__enter__.return_value = mock_p
        
        mock_context = MagicMock()
        mock_p.chromium.launch_persistent_context.return_value = mock_context
        
        mock_page = MagicMock()
        mock_context.pages = [mock_page]
        
        # Mock scroll logic returns no links
        mock_page.query_selector_all.return_value = []
        mock_page.evaluate.return_value = {"scrollY": 0, "innerHeight": 100, "scrollHeight": 100}
        mock_page.url = "https://photos.google.com/"
        
        # Running the downloader
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.object(download_photos, "TEMP_DIR", tmp_dir):
                items = download_photos.download_photos_for_month(2026, 4)
        
        self.assertEqual(items, [])
        # Verify it navigated to google photos and search URL
        mock_page.goto.assert_any_call("https://photos.google.com/", wait_until="domcontentloaded", timeout=60000)
        mock_page.goto.assert_any_call("https://photos.google.com/search/Aprile%202026", wait_until="domcontentloaded", timeout=60000)

if __name__ == '__main__':
    unittest.main()
