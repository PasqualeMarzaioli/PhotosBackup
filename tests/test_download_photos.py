import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Add project path to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import download_photos

class TestDownloadPhotos(unittest.TestCase):

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
        items = download_photos.download_photos_for_month(2026, 4)
        
        self.assertEqual(items, [])
        # Verify it navigated to google photos and search URL
        mock_page.goto.assert_any_call("https://photos.google.com/", wait_until="domcontentloaded", timeout=30000)
        mock_page.goto.assert_any_call("https://photos.google.com/search/Aprile%202026", wait_until="domcontentloaded", timeout=30000)

if __name__ == '__main__':
    unittest.main()
