"""
Tests the main backup orchestration helpers.

Author: Pasquale Marzaioli
"""

import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import tempfile

# Add project path to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

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
                os.path.join(tmp_dir, ".tmp_download"),
                os.path.join(session_dir, "Default", "Cache"),
                os.path.join(session_dir, "Default", "Code Cache"),
            ]
            preserved_cookie = os.path.join(session_dir, "Default", "Cookies")

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

            self.assertGreaterEqual(cleaned_count, 5)
            for path in runtime_paths:
                self.assertFalse(os.path.exists(path))
            self.assertTrue(os.path.exists(preserved_cookie))

    @patch('onedrive.upload_file')
    @patch('os.remove')
    def test_upload_one_success(self, mock_remove, mock_upload):
        mock_upload.return_value = True

        item = {"local_path": "/tmp/test_photo.jpg", "google_url": "http://google/photo"}
        returned_item, success = backup._upload_one(item, "Immagini/Memorie/2026/04.Aprile 2026")

        self.assertTrue(success)
        self.assertEqual(returned_item, item)
        mock_upload.assert_called_once_with("/tmp/test_photo.jpg", "Immagini/Memorie/2026/04.Aprile 2026")
        mock_remove.assert_called_once_with("/tmp/test_photo.jpg")

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

if __name__ == '__main__':
    unittest.main()
