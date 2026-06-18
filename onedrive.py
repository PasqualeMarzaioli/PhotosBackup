"""
Uploads backup files to Microsoft OneDrive with Microsoft Graph.

Author: Pasquale Marzaioli

Module for uploading to Microsoft OneDrive via Microsoft Graph API.
Handles MSAL authentication and file uploads (including large files with upload sessions).
"""

import os
import logging
import requests
import threading
import time
import urllib.parse
from typing import Optional

import msal

import config

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_AUTHORITY = f"https://login.microsoftonline.com/{config.MICROSOFT_TENANT_ID}"

# Cache for folder IDs to prevent race conditions and excessive API calls
_folder_id_cache = {}
_folder_id_cache_lock = threading.Lock()
_folder_creation_lock = threading.Lock()
_token_lock = threading.Lock()

# Threshold to use upload session (files > 4 MB)
_LARGE_FILE_THRESHOLD = 4 * 1024 * 1024
_RETRY_STATUSES = {408, 429, 500, 502, 503, 504}


def _request(method: str, url: str, **kwargs) -> requests.Response:
    """Run an HTTP request with retries for transient Graph/network failures."""
    attempts = max(1, int(getattr(config, "REQUEST_RETRIES", 5)))
    backoff = float(getattr(config, "REQUEST_RETRY_BACKOFF_SECONDS", 2.0))
    request_func = getattr(requests, method.lower())
    last_exc = None

    for attempt in range(1, attempts + 1):
        try:
            resp = request_func(url, **kwargs)
        except requests.RequestException as exc:
            last_exc = exc
            if attempt == attempts:
                raise
            sleep_for = min(backoff * attempt, 30)
            logger.warning(f"{method} {url} failed ({exc}); retrying in {sleep_for:.1f}s")
            time.sleep(sleep_for)
            continue

        if resp.status_code not in _RETRY_STATUSES or attempt == attempts:
            return resp

        retry_after = resp.headers.get("Retry-After")
        try:
            sleep_for = float(retry_after) if retry_after else min(backoff * attempt, 30)
        except ValueError:
            sleep_for = min(backoff * attempt, 30)
        logger.warning(
            f"{method} {url} returned {resp.status_code}; retrying in {sleep_for:.1f}s"
        )
        time.sleep(sleep_for)

    if last_exc:
        raise last_exc
    raise RuntimeError(f"{method} {url} failed without a response")


def _get_token() -> str:
    """Retrieve a valid Microsoft access token using MSAL with persistent cache."""
    with _token_lock:
        cache = msal.SerializableTokenCache()

        if os.path.exists(config.MICROSOFT_TOKEN_FILE):
            with open(config.MICROSOFT_TOKEN_FILE, "r") as f:
                cache.deserialize(f.read())

        app = msal.PublicClientApplication(
            client_id=config.MICROSOFT_CLIENT_ID,
            authority=_AUTHORITY,
            token_cache=cache,
        )

        accounts = app.get_accounts()
        result = None

        if accounts:
            result = app.acquire_token_silent(config.MICROSOFT_SCOPES, account=accounts[0])

        if not result or "access_token" not in result:
            raise RuntimeError(
                "Microsoft token not found or expired. "
                "Run first: python setup_auth.py"
            )

        # Save updated cache
        if cache.has_state_changed:
            os.makedirs(config.TOKENS_DIR, exist_ok=True)
            with open(config.MICROSOFT_TOKEN_FILE, "w") as f:
                f.write(cache.serialize())

        return result["access_token"]


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
                            parent_id = resp.json()["id"]
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


def _upload_small(token: str, folder_id: str, filename: str, filepath: str) -> bool:
    """Simple upload for files <= 4 MB."""
    encoded_filename = urllib.parse.quote(filename, safe="")
    url = f"{_GRAPH_BASE}/me/drive/items/{folder_id}:/{encoded_filename}:/content"
    with open(filepath, "rb") as f:
        data = f.read()
    resp = _request(
        "PUT",
        url,
        headers={**_headers(token), "Content-Type": "application/octet-stream"},
        data=data,
        timeout=120,
    )
    if resp.status_code in (200, 201):
        return True
    logger.error(f"Upload failed ({resp.status_code}): {resp.text[:200]}")
    return False


def _upload_large(token: str, folder_id: str, filename: str, filepath: str) -> bool:
    """Upload session for files > 4 MB."""
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
            end = uploaded + len(chunk) - 1
            headers = {
                "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {uploaded}-{end}/{file_size}",
            }
            resp = _request("PUT", upload_url, headers=headers, data=chunk, timeout=120)

            if resp.status_code not in (200, 201, 202):
                logger.error(f"Chunk upload failed ({resp.status_code}): {resp.text[:200]}")
                return False

            uploaded += len(chunk)
            logger.debug(f"Upload {filename}: {uploaded}/{file_size} bytes")

    return True


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


def _select_remote_filename(token: str, folder_id: str, filename: str, file_size: int) -> Optional[str]:
    """
    Return the remote filename to upload, or None when the same name and size
    already exist and can be treated as backed up.
    """
    metadata = get_file_metadata_on_onedrive(token, folder_id, filename)
    if metadata is None:
        return filename

    remote_size = metadata.get("size")
    if remote_size == file_size:
        logger.info(f"Already on OneDrive with same size, skipping: {filename}")
        return None

    for suffix in range(2, 100):
        candidate = _candidate_filename(filename, suffix)
        if get_file_metadata_on_onedrive(token, folder_id, candidate) is None:
            logger.warning(
                f"Name collision on OneDrive for {filename}; uploading as {candidate}"
            )
            return candidate

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    root, ext = os.path.splitext(filename)
    candidate = f"{root} ({timestamp}){ext}"
    logger.warning(f"Many OneDrive name collisions for {filename}; uploading as {candidate}")
    return candidate


def upload_file(filepath: str, onedrive_folder_path: str, target_filename: Optional[str] = None) -> bool:
    """
    Uploads a file to OneDrive in the specified folder.
    Automatically creates intermediate folders if needed.
    Skips the file if the same name and size already exist.
    """
    token = _get_token()
    if not os.path.exists(filepath):
        logger.error(f"Local file does not exist: {filepath}")
        return False

    filename = os.path.basename(target_filename or filepath)
    file_size = os.path.getsize(filepath)

    folder_id = _ensure_folder(token, onedrive_folder_path)

    remote_filename = _select_remote_filename(token, folder_id, filename, file_size)
    if remote_filename is None:
        return True

    logger.info(f"Uploading: {remote_filename} ({file_size / 1024 / 1024:.1f} MB)")

    if file_size <= _LARGE_FILE_THRESHOLD:
        return _upload_small(token, folder_id, remote_filename, filepath)
    else:
        return _upload_large(token, folder_id, remote_filename, filepath)
