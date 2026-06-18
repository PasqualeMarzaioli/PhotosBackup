"""
Tests the optional Google Photos Library API helpers.

Author: Pasquale Marzaioli
"""

import unittest
from unittest.mock import patch, mock_open, MagicMock
import os
import sys

# Add project path to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import google_photos

class TestGooglePhotos(unittest.TestCase):

    @patch('google_photos._get_credentials')
    @patch('requests.post')
    def test_get_photos_for_month_february(self, mock_post, mock_creds):
        # Setup mock credentials
        mock_credentials = MagicMock()
        mock_credentials.token = "fake_token"
        mock_creds.return_value = mock_credentials

        # Setup mock API response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "mediaItems": [
                {"id": "id1", "filename": "photo1.jpg", "baseUrl": "http://example.com/p1", "mimeType": "image/jpeg"}
            ]
        }
        mock_post.return_value = mock_response

        # February 2024 is a leap year (29 days)
        photos = google_photos.get_photos_for_month(2024, 2)
        self.assertEqual(len(photos), 1)
        self.assertEqual(photos[0]["filename"], "photo1.jpg")

        # Verify payload content: endDate day should be 29
        _, kwargs = mock_post.call_args
        payload = kwargs["json"]
        self.assertEqual(payload["filters"]["dateFilter"]["ranges"][0]["endDate"]["day"], 29)

    @patch('google_photos._get_credentials')
    @patch('requests.post')
    def test_get_photos_for_month_april(self, mock_post, mock_creds):
        # Setup mock credentials
        mock_credentials = MagicMock()
        mock_credentials.token = "fake_token"
        mock_creds.return_value = mock_credentials

        # Setup mock API response
        mock_response = MagicMock()
        mock_response.json.return_value = {"mediaItems": []}
        mock_post.return_value = mock_response

        # April has 30 days
        google_photos.get_photos_for_month(2026, 4)
        
        _, kwargs = mock_post.call_args
        payload = kwargs["json"]
        self.assertEqual(payload["filters"]["dateFilter"]["ranges"][0]["endDate"]["day"], 30)

    @patch('requests.get')
    @patch('os.makedirs')
    @patch('os.path.exists')
    def test_download_photo_success(self, mock_exists, mock_makedirs, mock_get):
        mock_exists.return_value = False
        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = [b"data1", b"data2"]
        mock_get.return_value = mock_resp

        photo = {"filename": "test.jpg", "baseUrl": "http://example.com/test"}
        
        with patch('builtins.open', mock_open()) as m_open:
            dest = google_photos.download_photo(photo, "/tmp/backup")
            self.assertEqual(dest, "/tmp/backup/test.jpg")
            mock_get.assert_called_once_with("http://example.com/test=d", timeout=60, stream=True)
            m_open.assert_called_once_with("/tmp/backup/test.jpg", "wb")
            m_open().write.assert_any_call(b"data1")
            m_open().write.assert_any_call(b"data2")

if __name__ == '__main__':
    unittest.main()
