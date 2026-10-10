"""
Tests the main backup orchestration helpers.

Author: Pasquale Marzaioli
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

# Add project path to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config  # noqa: E402

# Simulated backup summaries must never mark a real month as completed.
with patch.object(config, "LOG_FILE", os.devnull):
    import backup


class TestBackup(unittest.TestCase):

    def test_onedrive_path_for_month(self):
        # Test folder name mapping
        path = backup.onedrive_path_for_month(2026, 4)
        self.assertEqual(path, "Immagini/Memorie/2026/04.Aprile 2026")

    def test_cleanup_runtime_artifacts_preserves_session_data(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            session_dir = os.path.join(tmp_dir, ".chrome_session")
            runtime_paths = [
                os.path.join(tmp_dir, "__pycache__"),
                os.path.join(tmp_dir, "tests", "__pycache__"),
                os.path.join(session_dir, "Default", "Cache"),
                os.path.join(session_dir, "Default", "Code Cache"),
            ]
            preserved_cookie = os.path.join(session_dir, "Default", "Cookies")
            pending_downloads = os.path.join(tmp_dir, ".tmp_download")
            os.makedirs(pending_downloads)

            for path in runtime_paths:
                os.makedirs(path, exist_ok=True)
                with open(os.path.join(path, "cache-file"), "w", encoding="utf-8") as fh:
                    fh.write("cache")

            os.makedirs(os.path.dirname(preserved_cookie), exist_ok=True)
            with open(preserved_cookie, "w", encoding="utf-8") as fh:
                fh.write("login-state")

            with patch.object(backup.config, "BASE_DIR", tmp_dir), \
                 patch.object(backup.config, "TEMP_DIR", os.path.join(tmp_dir, ".tmp_download")), \
                 patch.object(backup.config, "CLEAN_RUNTIME_ARTIFACTS_AFTER_SUCCESS", True), \
                 patch.object(backup.config, "CLEAN_CHROME_CACHE_AFTER_SUCCESS", True), \
                 patch.object(backup.download_photos, "SESSION_DIR", session_dir):
                cleaned_count = backup.cleanup_runtime_artifacts()

            self.assertGreaterEqual(cleaned_count, 4)
            for path in runtime_paths:
                self.assertFalse(os.path.exists(path))
            self.assertTrue(os.path.exists(preserved_cookie))
            self.assertTrue(os.path.exists(pending_downloads))

    @patch('onedrive.upload_file')
    @patch('os.remove')
    def test_upload_one_success(self, mock_remove, mock_upload):
        mock_upload.return_value = True

        item = {"local_path": "/tmp/test_photo.jpg", "google_url": "http://google/photo"}
        returned_item, success = backup._upload_one(item, "Immagini/Memorie/2026/04.Aprile 2026")

        self.assertTrue(success)
        self.assertEqual(returned_item, item)
        mock_upload.assert_called_once_with("/tmp/test_photo.jpg", "Immagini/Memorie/2026/04.Aprile 2026")
        mock_remove.assert_not_called()

    @patch('onedrive.upload_file')
    @patch('os.remove')
    def test_upload_one_failed(self, mock_remove, mock_upload):
        mock_upload.return_value = False

        item = {"local_path": "/tmp/test_photo.jpg", "google_url": "http://google/photo"}
        returned_item, success = backup._upload_one(item, "Immagini/Memorie/2026/04.Aprile 2026")

        self.assertFalse(success)
        self.assertEqual(returned_item, item)
        mock_upload.assert_called_once_with("/tmp/test_photo.jpg", "Immagini/Memorie/2026/04.Aprile 2026")
        mock_remove.assert_not_called()

class TestBackupWorkflow(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = self.stack.enter_context(tempfile.TemporaryDirectory())
        for name, value in (("BASE_DIR", self.root), ("TEMP_DIR", os.path.join(self.root, "downloads")), ("TOKENS_DIR", os.path.join(self.root, "tokens")), ("DELETE_AFTER_UPLOAD", True), ("DELETE_ON_PARTIAL_SUCCESS", False)):
            self.stack.enter_context(patch.object(backup.config, name, value))
        self.verify = self.stack.enter_context(patch.object(backup.onedrive, "verify_connection"))
        self.download = self.stack.enter_context(patch.object(backup.download_photos, "download_photos_for_month", return_value=[]))
        self.errors = self.stack.enter_context(patch.object(backup.download_photos, "get_last_download_errors", return_value=[]))
        self.delete = self.stack.enter_context(patch.object(backup.download_photos, "delete_photos_from_google", return_value={"requested": 1, "deleted": 1, "errors": 0}))
        self.cleanup = self.stack.enter_context(patch.object(backup.download_photos, "cleanup_completed_downloads"))
        self.stack.enter_context(patch.object(backup, "cleanup_runtime_artifacts"))
        self.stack.enter_context(patch.object(backup, "send_telegram"))
        self.item = {"local_path": os.path.join(self.root, "photo.jpg"), "google_url": "https://photos.google.com/photo/test"}

        def download(year, month, on_downloaded):
            on_downloaded(self.item)
            return [self.item]

        self.download.side_effect = download

    def test_authentication_failure_stops_before_download(self):
        self.verify.side_effect = RuntimeError("Session revoked")
        with self.assertRaisesRegex(RuntimeError, "Session revoked"):
            backup.run_backup(2026, 4)
        self.download.assert_not_called()
        self.delete.assert_not_called()

    @patch("backup._upload_one")
    def test_upload_failure_preserves_downloads_and_google_originals(self, upload):
        upload.return_value = (self.item, False)
        self.assertFalse(backup.run_backup(2026, 4))
        self.delete.assert_not_called()
        self.cleanup.assert_not_called()

    @patch("backup._upload_one")
    def test_incomplete_listing_prevents_deletion(self, upload):
        upload.return_value = (self.item, True)
        self.errors.return_value = ["Scrolling limit reached"]
        self.assertFalse(backup.run_backup(2026, 4))
        self.delete.assert_not_called()
        self.cleanup.assert_not_called()

    @patch("backup._upload_one")
    def test_success_cleans_only_registered_downloads(self, upload):
        upload.return_value = (self.item, True)
        self.assertTrue(backup.run_backup(2026, 4))
        self.delete.assert_called_once_with([self.item])
        self.cleanup.assert_called_once_with(2026, 4, [self.item])

    @patch("backup._upload_one")
    def test_deletion_failure_preserves_downloads(self, upload):
        upload.return_value = (self.item, True)
        self.delete.return_value = {"requested": 1, "deleted": 0, "errors": 1}
        self.assertFalse(backup.run_backup(2026, 4))
        self.cleanup.assert_not_called()

    @patch("backup._upload_one")
    def test_download_exception_joins_workers_and_releases_lock(self, upload):
        started = threading.Event()

        def uploaded(item, folder):
            started.set()
            return item, True

        upload.side_effect = uploaded

        def interrupted(year, month, on_downloaded):
            on_downloaded(self.item)
            self.assertTrue(started.wait(timeout=5))
            raise RuntimeError("Browser closed")

        self.download.side_effect = interrupted
        with self.assertRaisesRegex(RuntimeError, "Browser closed"):
            backup.run_backup(2026, 4)
        self.assertEqual(upload.call_count, 1)
        self.delete.assert_not_called()
        with backup.download_photos.runtime_lock():
            pass

    def test_empty_failed_download_is_not_success(self):
        self.download.side_effect = None
        self.errors.return_value = ["Download timeout"]
        self.assertFalse(backup.run_backup(2026, 4))

    @patch("backup.run_backup", return_value=False)
    def test_cli_reports_nonzero_exit_on_partial_failure(self, run):
        with patch.object(sys, "argv", ["backup.py", "2026", "4"]), self.assertRaises(SystemExit) as result:
            backup.main()
        self.assertEqual(result.exception.code, 1)


class TestShellChecker(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source = Path(__file__).resolve().parents[1]
        shutil.copy(source / "check_and_backup.sh", self.root)
        now = datetime.now()
        self.month = now.month - 1 if now.month > 1 else 12
        self.year = now.year if now.month > 1 else now.year - 1
        (self.root / "config.py").write_text(
            "# Checker fixture with a fixed month label.\n# Author: Pasquale Marzaioli\n"
            f"BACKUP_NOT_BEFORE_DAY = 1\nMONTHS = {{{self.month}: 'Target'}}\n"
        )
        (self.root / "backup.py").write_text(
            "# Record checker arguments without contacting cloud services.\n# Author: Pasquale Marzaioli\n"
            "import json, sys\nfrom pathlib import Path\n"
            "Path(__file__).with_name('called.json').write_text(json.dumps(sys.argv[1:]))\nsys.exit(7)\n"
        )

    def run_checker(self, log):
        (self.root / "backup.log").write_text(log)
        return subprocess.run(
            ["/bin/bash", str(self.root / "check_and_backup.sh")],
            env={**os.environ, "PYTHON_BIN": sys.executable},
            capture_output=True, text=True, check=False,
        )

    def test_duplicate_long_success_summary_skips_backup(self):
        heading = f"INFO BACKUP COMPLETED: Target {self.year}\n"
        log = heading * 2 + "INFO Successfully uploaded: 5\n" * 12 + "INFO Errors : 0\n" * 2
        result = self.run_checker(log)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "called.json").exists())

    def test_failed_month_is_not_validated_by_another_month(self):
        log = f"INFO BACKUP COMPLETED: Target {self.year}\nINFO Upload errors : 0\nINFO Errors : 1\nINFO BACKUP COMPLETED: Other {self.year}\nINFO Errors : 0\n"
        result = self.run_checker(log)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(json.loads((self.root / "called.json").read_text()), [str(self.year), str(self.month)])

    def test_missing_success_runs_backup_and_propagates_failure(self):
        result = self.run_checker("")
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertTrue((self.root / "called.json").exists())

    def test_installer_serializes_paths_with_xml_characters(self):
        source = Path(__file__).resolve().parents[1]
        project = self.root / "Space & Folder"
        project.mkdir()
        name = "com.pasquale.photosbackup.plist"
        shutil.copy(source / (name + ".example"), project)
        installer = (source / "install.sh").read_text()
        script = installer.split('<<\'PY\'\n', 1)[1].split('\nPY\n', 1)[0]
        with patch.object(sys, "argv", ["-", str(project), name]):
            exec(compile(script, "install.sh", "exec"), {})
        import plistlib
        contents = plistlib.loads((project / name).read_bytes())
        self.assertEqual(contents["WorkingDirectory"], str(project))
        self.assertEqual(contents["EnvironmentVariables"]["PYTHON_BIN"], str(project / "venv/bin/python"))
        self.assertEqual(contents["StandardOutPath"], str(project / "backup_stdout.log"))


class TestAuthenticationSetup(unittest.TestCase):
    def test_failed_microsoft_verification_is_reported(self):
        import setup_auth
        with patch.object(backup.onedrive, "verify_connection", side_effect=RuntimeError("Access denied")):
            self.assertFalse(setup_auth.verify_setup())

    @patch("playwright.sync_api.sync_playwright")
    def test_incomplete_google_login_closes_browser(self, playwright):
        import setup_auth
        context = playwright.return_value.__enter__.return_value.chromium.launch_persistent_context.return_value
        page = context.new_page.return_value
        page.url = "https://accounts.google.com/signin"
        context.pages = []
        with tempfile.TemporaryDirectory() as root, patch.object(backup.download_photos, "SESSION_DIR", root), patch("builtins.input", return_value=""):
            with self.assertRaisesRegex(RuntimeError, "not completed"):
                setup_auth.setup_google()
        context.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
