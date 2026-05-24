import unittest
from unittest.mock import patch, mock_open, MagicMock
import os
import sys

# Add project path to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import onedrive

class TestOneDrive(unittest.TestCase):

    def setUp(self):
        # Clear the cache before each test
        onedrive._folder_id_cache.clear()

    @patch('onedrive._get_token')
    @patch('requests.get')
    @patch('requests.post')
    def test_ensure_folder_cached_and_created(self, mock_post, mock_get, mock_token):
        mock_token.return_value = "fake_ms_token"

        # Mock GET response for non-existent folder
        mock_get_resp = MagicMock()
        mock_get_resp.status_code = 404
        mock_get.return_value = mock_get_resp

        # Mock POST response for folder creation
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 201
        mock_post_resp.json.return_value = {"id": "sub_folder_id"}
        mock_post.return_value = mock_post_resp

        # Call _ensure_folder for path "Year 2026/April"
        folder_id = onedrive._ensure_folder("fake_ms_token", "Year 2026/April")
        self.assertEqual(folder_id, "sub_folder_id")

        # Verify that URL encoding was used for paths
        # First segment: GET /me/drive/items/root:/Year%202026
        mock_get.assert_any_call(
            "https://graph.microsoft.com/v1.0/me/drive/items/root:/Year%202026",
            headers={"Authorization": "Bearer fake_ms_token"},
            timeout=30
        )

        # Let's clear mock calls and run again - it should hit the cache and make NO API calls
        mock_get.reset_mock()
        mock_post.reset_mock()

        folder_id_cached = onedrive._ensure_folder("fake_ms_token", "Year 2026/April")
        self.assertEqual(folder_id_cached, "sub_folder_id")
        mock_get.assert_not_called()
        mock_post.assert_not_called()

    @patch('onedrive._get_token')
    @patch('requests.put')
    def test_upload_small_file(self, mock_put, mock_token):
        mock_token.return_value = "fake_ms_token"
        
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_put.return_value = mock_resp

        with patch('builtins.open', mock_open(read_data=b"hello small file")):
            success = onedrive._upload_small("fake_ms_token", "folder123", "My Photo #1.jpg", "/path/to/photo.jpg")
            self.assertTrue(success)

            # Check that URL encoding was applied to filename (My Photo #1.jpg -> My%20Photo%20%231.jpg)
            mock_put.assert_called_once_with(
                "https://graph.microsoft.com/v1.0/me/drive/items/folder123:/My%20Photo%20%231.jpg:/content",
                headers={"Authorization": "Bearer fake_ms_token", "Content-Type": "application/octet-stream"},
                data=b"hello small file",
                timeout=120
            )

    @patch('onedrive._get_token')
    @patch('requests.get')
    def test_file_exists_on_onedrive(self, mock_get, mock_token):
        mock_token.return_value = "fake_ms_token"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        exists = onedrive.file_exists_on_onedrive("fake_ms_token", "folder123", "Summer & Winter.jpg")
        self.assertTrue(exists)

        # Check URL encoding (Summer & Winter.jpg -> Summer%20%26%20Winter.jpg)
        mock_get.assert_called_once_with(
            "https://graph.microsoft.com/v1.0/me/drive/items/folder123:/Summer%20%26%20Winter.jpg",
            headers={"Authorization": "Bearer fake_ms_token"},
            timeout=15
        )

if __name__ == '__main__':
    unittest.main()
