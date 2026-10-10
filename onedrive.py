"""
Uploads backup files to Microsoft OneDrive with Microsoft Graph.

Author: Pasquale Marzaioli

Module for uploading to Microsoft OneDrive via Microsoft Graph API.
Handles MSAL authentication and file uploads (including large files with upload sessions).
"""

import hashlib
import logging
import os
import tempfile
import threading
import time
import urllib.parse
from typing import Optional

import msal
import requests

import config

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_AUTHORITY = f"https://login.microsoftonline.com/{config.MICROSOFT_TENANT_ID}"

# Cache for folder IDs to prevent race conditions and excessive API calls
_folder_id_cache = {}
_folder_id_cache_lock = threading.Lock()
_folder_creation_lock = threading.Lock()
_token_lock = threading.Lock()
_access_token = None
_token_expires_at = 0
_http = threading.local()

_RETRY_STATUSES = {408, 429, 500, 502, 503, 504}


def _request(method: str, url: str, **kwargs) -> requests.Response:
    """Run an HTTP request with retries for transient Graph/network failures."""
    attempts = max(1, int(getattr(config, "REQUEST_RETRIES", 5)))
    backoff = float(getattr(config, "REQUEST_RETRY_BACKOFF_SECONDS", 2.0))
    if not hasattr(_http, "session"):
        _http.session = requests.Session()
    refreshed = False

    for attempt in range(1, attempts + 1):
        try:
            resp = _http.session.request(method, url, **kwargs)
        except requests.RequestException as exc:
            if attempt == attempts:
                raise RuntimeError(f"{method} request failed after {attempts} attempts ({type(exc).__name__})") from None
            sleep_for = min(backoff * attempt, 30)
            # Upload URLs contain credentials; never include them in logs.
            logger.warning(f"{method} request failed ({type(exc).__name__}); retrying in {sleep_for:.1f}s")
            time.sleep(sleep_for)
            continue

        if resp.status_code == 401 and url.startswith(_GRAPH_BASE + "/") and not refreshed:
            resp.close()
            kwargs["headers"] = {**kwargs.get("headers", {}), **_headers(_get_token(force_refresh=True))}
            refreshed = True
            resp = _http.session.request(method, url, **kwargs)

        if resp.status_code not in _RETRY_STATUSES or attempt == attempts:
            return resp

        retry_after = resp.headers.get("Retry-After")
        try:
            sleep_for = max(0, float(retry_after)) if retry_after else min(backoff * attempt, 30)
        except ValueError:
            sleep_for = min(backoff * attempt, 30)
        logger.warning(
            f"{method} request returned {resp.status_code}; retrying in {sleep_for:.1f}s"
        )
        resp.close()
        time.sleep(sleep_for)

    raise RuntimeError(f"{method} request failed without a response")


def _save_token_cache(cache) -> None:
    """Replace the credential cache atomically with owner-only permissions."""
    os.makedirs(config.TOKENS_DIR, mode=0o700, exist_ok=True)
    fd, path = tempfile.mkstemp(dir=config.TOKENS_DIR, prefix=".microsoft-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(cache.serialize())
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(path, config.MICROSOFT_TOKEN_FILE)
    finally:
        if os.path.exists(path):
            os.remove(path)


def _get_token(force_refresh: bool = False) -> str:
    """Retrieve a valid Microsoft access token using MSAL with persistent cache."""
    global _access_token, _token_expires_at
    with _token_lock:
        if not force_refresh and _access_token and time.time() < _token_expires_at:
            return _access_token
        cache = msal.SerializableTokenCache()

        if os.path.exists(config.MICROSOFT_TOKEN_FILE):
            with open(config.MICROSOFT_TOKEN_FILE, "r") as f:
                cache.deserialize(f.read())

        app = msal.PublicClientApplication(
            client_id=config.MICROSOFT_CLIENT_ID,
            authority=_AUTHORITY,
            token_cache=cache,
            timeout=30,
        )

        accounts = app.get_accounts()
        result = None

        if accounts:
            result = app.acquire_token_silent_with_error(
                config.MICROSOFT_SCOPES, account=accounts[0], force_refresh=force_refresh,
            )

        if not result or "access_token" not in result:
            _access_token = None
            reason = (result or {}).get("error_description") or (result or {}).get("error") or "No cached account or refresh token"
            raise RuntimeError(f"Microsoft authentication failed: {reason}. Run venv/bin/python setup_auth.py manually.")

        # Save updated cache
        if cache.has_state_changed:
            _save_token_cache(cache)

        _access_token = result["access_token"]
        _token_expires_at = time.time() + max(0, int(result.get("expires_in", 300)) - 60)
        return _access_token


def verify_connection() -> dict:
    """Check authentication and drive access before downloading any media."""
    resp = _request("GET", f"{_GRAPH_BASE}/me/drive", headers=_headers(_get_token()), timeout=30)
    resp.raise_for_status()
    return resp.json()


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _ensure_folder(token: str, folder_path: str) -> str:
    """
    Recursively creates the folder structure on OneDrive if it does not exist.
    Returns the leaf folder ID.
    Thread-safe and uses in-memory caching to avoid API calls and race conditions.
    """
    with _folder_creation_lock:
        with _folder_id_cache_lock:
            if folder_path in _folder_id_cache:
                return _folder_id_cache[folder_path]

        parts = [p for p in folder_path.split("/") if p]
        parent_id = "root"
        path_so_far = ""

        for part in parts:
            path_so_far = f"{path_so_far}/{part}" if path_so_far else part

            with _folder_id_cache_lock:
                if path_so_far in _folder_id_cache:
                    parent_id = _folder_id_cache[path_so_far]
                    continue

            encoded_part = urllib.parse.quote(part, safe="")
            url = f"{_GRAPH_BASE}/me/drive/items/{parent_id}:/{encoded_part}"
            resp = _request("GET", url, headers=_headers(token), timeout=30)

            found_id = None
            if resp.status_code == 200:
                item = resp.json()
                if "folder" not in item:
                    raise RuntimeError(f"OneDrive path segment exists but is not a folder: {path_so_far}")
                found_id = item["id"]
            elif resp.status_code != 404:
                resp.raise_for_status()

            if found_id:
                parent_id = found_id
            else:
                body = {"name": part, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}
                resp = _request(
                    "POST",
                    f"{_GRAPH_BASE}/me/drive/items/{parent_id}/children",
                    headers={**_headers(token), "Content-Type": "application/json"},
                    json=body,
                    timeout=30,
                )
                if resp.status_code == 409 or (resp.status_code == 400 and "alreadyExists" in resp.text):
                    for retry in range(5):
                        resp = _request("GET", url, headers=_headers(token), timeout=30)
                        if resp.status_code == 200:
                            item = resp.json()
                            if "folder" not in item:
                                raise RuntimeError(f"OneDrive path is not a folder: {path_so_far}")
                            parent_id = item["id"]
                            break
                        time.sleep(0.5 * (retry + 1))
                    else:
                        resp.raise_for_status()
                else:
                    resp.raise_for_status()
                    parent_id = resp.json()["id"]
                    logger.info(f"Folder created: {part}")

            with _folder_id_cache_lock:
                _folder_id_cache[path_so_far] = parent_id

        return parent_id


def _upload_large(token: str, folder_id: str, filename: str, filepath: str) -> bool:
    """Use conflict-safe upload sessions for both photos and large videos."""
    file_size = os.path.getsize(filepath)

    # Create upload session
    encoded_filename = urllib.parse.quote(filename, safe="")
    url = f"{_GRAPH_BASE}/me/drive/items/{folder_id}:/{encoded_filename}:/createUploadSession"
    body = {"item": {"@microsoft.graph.conflictBehavior": "fail", "name": filename}}
    resp = _request(
        "POST",
        url,
        headers={**_headers(token), "Content-Type": "application/json"},
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    upload_url = resp.json()["uploadUrl"]

    chunk_size = 10 * 1024 * 1024  # 10 MB per chunk
    uploaded = 0

    with open(filepath, "rb") as f:
        while uploaded < file_size:
            chunk = f.read(chunk_size)
            if not chunk:
                raise RuntimeError("Local file changed during upload")
            end = uploaded + len(chunk) - 1
            headers = {
                "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {uploaded}-{end}/{file_size}",
            }
            resp = _request("PUT", upload_url, headers=headers, data=chunk, timeout=120)

            if resp.status_code == 416:
                # A timed-out request may have committed its chunk before a retry.
                status = _request("GET", upload_url, timeout=30)
                if status.status_code != 200:
                    return False
                ranges = status.json().get("nextExpectedRanges", [])
                if not ranges:
                    return False
                offset = min(int(value.split("-", 1)[0]) for value in ranges)
                if not uploaded < offset < file_size:
                    return False
                uploaded = offset
                f.seek(uploaded)
                continue

            if resp.status_code not in (200, 201, 202):
                logger.error(f"Chunk upload failed ({resp.status_code})")
                return False

            uploaded += len(chunk)
            if resp.status_code in (200, 201):
                return uploaded == file_size and resp.json().get("size") == file_size
            logger.debug(f"Upload {filename}: {uploaded}/{file_size} bytes")

    logger.error("Upload session did not confirm completion")
    return False


def get_file_metadata_on_onedrive(token: str, folder_id: str, filename: str) -> Optional[dict]:
    """Return OneDrive file metadata for filename, or None when it is absent."""
    encoded_filename = urllib.parse.quote(filename, safe="")
    url = f"{_GRAPH_BASE}/me/drive/items/{folder_id}:/{encoded_filename}"
    resp = _request("GET", url, headers=_headers(token), timeout=15)
    if resp.status_code == 200:
        return resp.json()
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return None


def file_exists_on_onedrive(token: str, folder_id: str, filename: str) -> bool:
    """Checks if the file already exists on OneDrive."""
    return get_file_metadata_on_onedrive(token, folder_id, filename) is not None


def _candidate_filename(filename: str, suffix: int) -> str:
    root, ext = os.path.splitext(filename)
    return f"{root} ({suffix}){ext}"


def _matches_content(token: str, metadata: dict, file_size: int, sha256: str) -> bool:
    """Compare content, streaming it when Graph does not provide SHA-256."""
    if metadata.get("size") != file_size or "file" not in metadata:
        return False
    remote_hash = metadata["file"].get("hashes", {}).get("sha256Hash")
    if remote_hash:
        return remote_hash.lower() == sha256.lower()
    digest = hashlib.sha256()
    count = 0
    url = f"{_GRAPH_BASE}/me/drive/items/{metadata['id']}/content"
    with _request("GET", url, headers=_headers(token), stream=True, timeout=120) as resp:
        if resp.status_code != 200:
            raise RuntimeError(f"OneDrive content download failed (HTTP {resp.status_code})")
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            digest.update(chunk)
            count += len(chunk)
    return count == file_size and digest.hexdigest() == sha256.lower()


def _select_remote_filename(token: str, folder_id: str, filename: str, file_size: int, sha256: str) -> Optional[str]:
    """Reuse only verified content; give collisions a stable content suffix."""
    root, ext = os.path.splitext(filename)
    collision_name = f"{root} ({sha256[:16]}){ext}"
    candidates = [filename, collision_name]
    candidates.extend(_candidate_filename(collision_name, suffix) for suffix in range(2, 100))
    for candidate in candidates:
        metadata = get_file_metadata_on_onedrive(token, folder_id, candidate)
        if metadata is None:
            return candidate
        if _matches_content(token, metadata, file_size, sha256):
            logger.info(f"Verified existing OneDrive copy: {candidate}")
            return None
    raise RuntimeError(f"Too many OneDrive filename collisions: {filename}")


def upload_file(filepath: str, onedrive_folder_path: str, target_filename: Optional[str] = None) -> bool:
    """
    Uploads a file to OneDrive in the specified folder.
    Automatically creates intermediate folders if needed.
    Returns success only after verifying the remote file's content.
    """
    token = _get_token()
    if not os.path.exists(filepath):
        logger.error(f"Local file does not exist: {filepath}")
        return False

    filename = os.path.basename(target_filename or filepath)
    file_size = os.path.getsize(filepath)
    if file_size == 0:
        logger.error(f"Refusing to upload an empty file: {filename}")
        return False
    digest = hashlib.sha256()
    with open(filepath, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    sha256 = digest.hexdigest()

    folder_id = _ensure_folder(token, onedrive_folder_path)

    remote_filename = _select_remote_filename(token, folder_id, filename, file_size, sha256)
    if remote_filename is None:
        return True

    logger.info(f"Uploading: {remote_filename} ({file_size / 1024 / 1024:.1f} MB)")

    try:
        # A lost final acknowledgement is harmless if the committed content matches.
        _upload_large(token, folder_id, remote_filename, filepath)
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 409:
            raise
        # A concurrent writer may have completed the identical file first.
    metadata = get_file_metadata_on_onedrive(_get_token(), folder_id, remote_filename)
    verified = metadata is not None and _matches_content(_get_token(), metadata, file_size, sha256)
    if not verified:
        logger.error(f"OneDrive content verification failed: {remote_filename}")
    return verified
