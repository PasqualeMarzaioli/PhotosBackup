"""Verify authentication, retries and content-safe OneDrive uploads.

Mock Graph responses and use temporary files for integrity checks.
Author: Pasquale Marzaioli
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import onedrive  # noqa: E402


def response(status, payload=None, headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload or {}).encode()
    result._content_consumed = True
    result.headers.update(headers or {})
    result.url = "https://example.invalid/test"
    return result


class TestOneDrive(unittest.TestCase):
    def setUp(self):
        onedrive._folder_id_cache.clear()
        onedrive._access_token = None
        onedrive._token_expires_at = 0
        self.session = MagicMock()
        session_patch = patch.object(onedrive._http, "session", self.session, create=True)
        session_patch.start()
        self.addCleanup(session_patch.stop)

    @patch("onedrive._request")
    def test_ensure_folder_cached_and_created(self, request):
        request.side_effect = [response(404), response(201, {"id": "year"}), response(404), response(201, {"id": "month"})]
        self.assertEqual(onedrive._ensure_folder("token", "Year 2026/April"), "month")
        self.assertIn("Year%202026", request.call_args_list[0].args[1])
        request.reset_mock()
        self.assertEqual(onedrive._ensure_folder("token", "Year 2026/April"), "month")
        request.assert_not_called()

    def test_retry_honors_rate_limit(self):
        self.session.request.side_effect = [response(429, headers={"Retry-After": "3"}), response(200)]
        with patch("onedrive.time.sleep") as sleep:
            self.assertEqual(onedrive._request("GET", "https://example.invalid/test").status_code, 200)
        sleep.assert_called_once_with(3)

    def test_retry_network_failure_without_logging_credentials(self):
        self.session.request.side_effect = [requests.ConnectionError("secret-upload-url"), response(200)]
        with patch("onedrive.time.sleep"), self.assertLogs(onedrive.logger, level="WARNING") as logs:
            onedrive._request("PUT", "https://example.invalid/secret-upload-url", data=b"data")
        self.assertNotIn("secret-upload-url", " ".join(logs.output))
        self.assertEqual(self.session.request.call_count, 2)

    def test_unauthorized_graph_request_refreshes_once(self):
        self.session.request.side_effect = [response(401), response(200)]
        with patch("onedrive._get_token", return_value="fresh") as token:
            onedrive._request("GET", onedrive._GRAPH_BASE + "/me/drive", headers={"Authorization": "Bearer old"})
        token.assert_called_once_with(force_refresh=True)
        self.assertEqual(self.session.request.call_args.kwargs["headers"]["Authorization"], "Bearer fresh")

    def test_upload_url_never_receives_graph_token(self):
        self.session.request.return_value = response(401)
        with patch("onedrive._get_token") as token:
            self.assertEqual(onedrive._request("PUT", "https://example.invalid/upload", data=b"data").status_code, 401)
        token.assert_not_called()

    @patch("onedrive.os.path.exists", return_value=False)
    @patch("onedrive.msal.PublicClientApplication")
    @patch("onedrive._save_token_cache")
    def test_token_is_reused_until_refresh_needed(self, save, application, exists):
        app = application.return_value
        app.get_accounts.return_value = [{"account": "test"}]
        app.acquire_token_silent_with_error.return_value = {"access_token": "token", "expires_in": 3600}
        self.assertEqual(onedrive._get_token(), "token")
        self.assertEqual(onedrive._get_token(), "token")
        application.assert_called_once()
        onedrive._token_expires_at = 0
        self.assertEqual(onedrive._get_token(), "token")
        self.assertEqual(application.call_count, 2)

    @patch("onedrive.os.path.exists", return_value=False)
    @patch("onedrive.msal.PublicClientApplication")
    def test_refresh_error_explains_required_action(self, application, exists):
        app = application.return_value
        app.get_accounts.return_value = [{}]
        app.acquire_token_silent_with_error.return_value = {"error": "invalid_grant", "error_description": "Session revoked"}
        with self.assertRaisesRegex(RuntimeError, "Session revoked.*setup_auth"):
            onedrive._get_token()

    def test_token_cache_is_atomic_and_private(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "cache.json")
            cache = MagicMock()
            cache.serialize.return_value = '{"test": true}'
            with patch.object(onedrive.config, "TOKENS_DIR", root), patch.object(onedrive.config, "MICROSOFT_TOKEN_FILE", path):
                onedrive._save_token_cache(cache)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            with open(path) as fh:
                self.assertEqual(json.load(fh), {"test": True})
            self.assertEqual(os.listdir(root), ["cache.json"])

    @patch("onedrive.get_file_metadata_on_onedrive")
    def test_same_size_different_content_gets_stable_name(self, metadata):
        digest = hashlib.sha256(b"data").hexdigest()
        metadata.side_effect = [{"size": 4, "file": {"hashes": {"sha256Hash": "different"}}}, None]
        self.assertEqual(onedrive._select_remote_filename("token", "folder", "photo.jpg", 4, digest), f"photo ({digest[:16]}).jpg")

    @patch("onedrive.get_file_metadata_on_onedrive")
    def test_matching_collision_copy_is_reused(self, metadata):
        digest = hashlib.sha256(b"data").hexdigest()
        metadata.side_effect = [{"size": 5, "file": {}}, {"size": 4, "file": {"hashes": {"sha256Hash": digest.upper()}}}]
        self.assertIsNone(onedrive._select_remote_filename("token", "folder", "photo.jpg", 4, digest))

    @patch("onedrive._request")
    def test_missing_hash_streams_and_verifies_bytes(self, request):
        remote = response(200)
        remote._content = b"data"
        request.return_value = remote
        digest = hashlib.sha256(b"data").hexdigest()
        metadata = {"id": "item", "size": 4, "file": {}}
        self.assertTrue(onedrive._matches_content("token", metadata, 4, digest))
        remote._content = b"evil"
        self.assertFalse(onedrive._matches_content("token", metadata, 4, digest))

    @patch("onedrive._request")
    def test_file_exists_on_onedrive(self, request):
        request.return_value = response(200, {"id": "item"})
        self.assertTrue(onedrive.file_exists_on_onedrive("token", "folder", "Summer & Winter.jpg"))
        self.assertIn("Summer%20%26%20Winter.jpg", request.call_args.args[1])

    @patch("onedrive._request")
    def test_upload_requires_final_completion(self, request):
        with tempfile.NamedTemporaryFile() as fh:
            fh.write(b"data")
            fh.flush()
            for status, expected in ((202, False), (201, True), (200, True)):
                with self.subTest(status=status):
                    request.side_effect = [response(200, {"uploadUrl": "https://example.invalid/upload"}), response(status, {"size": 4})]
                    self.assertEqual(onedrive._upload_large("token", "folder", "photo.jpg", fh.name), expected)

    @patch("onedrive._request")
    def test_chunk_retry_resumes_at_server_offset(self, request):
        chunk_size = 10 * 1024 * 1024
        with tempfile.NamedTemporaryFile() as fh:
            fh.write(b"a" * chunk_size + b"end")
            fh.flush()
            request.side_effect = [response(200, {"uploadUrl": "https://example.invalid/upload"}), response(416), response(200, {"nextExpectedRanges": [f"{chunk_size}-"]}), response(201, {"size": chunk_size + 3})]
            self.assertTrue(onedrive._upload_large("token", "folder", "video.mov", fh.name))
            self.assertEqual(request.call_args.kwargs["data"], b"end")
            self.assertEqual(request.call_args.kwargs["headers"]["Content-Range"], f"bytes {chunk_size}-{chunk_size + 2}/{chunk_size + 3}")

    @patch("onedrive._get_token", return_value="token")
    @patch("onedrive._ensure_folder", return_value="folder")
    @patch("onedrive._upload_large", return_value=True)
    @patch("onedrive.get_file_metadata_on_onedrive")
    def test_upload_success_is_rejected_when_content_differs(self, metadata, upload, folder, token):
        with tempfile.NamedTemporaryFile() as fh:
            fh.write(b"data")
            fh.flush()
            metadata.side_effect = [None, {"size": 4, "file": {"hashes": {"sha256Hash": "wrong"}}}]
            self.assertFalse(onedrive.upload_file(fh.name, "Photos"))


if __name__ == "__main__":
    unittest.main()
