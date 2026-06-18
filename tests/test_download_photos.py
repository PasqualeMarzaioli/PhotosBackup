"""
Tests the Google Photos browser downloader helpers.

Author: Pasquale Marzaioli
"""

import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import tempfile

# Add project path to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import download_photos

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
