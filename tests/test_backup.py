import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Add project path to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import backup

class TestBackup(unittest.TestCase):

    def test_onedrive_path_for_month(self):
        # Test folder name mapping
        path = backup.onedrive_path_for_month(2026, 4)
        self.assertEqual(path, "Immagini/Memorie/2026/04.Aprile 2026")

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
