"""
Module for uploading to Microsoft OneDrive via Microsoft Graph API.
Handles MSAL authentication and file uploads (including large files with upload sessions).
"""

import os
import logging
import requests
import threading
import urllib.parse

import msal

import config

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_AUTHORITY = f"https://login.microsoftonline.com/{config.MICROSOFT_TENANT_ID}"

# Cache for folder IDs to prevent race conditions and excessive API calls
_folder_id_cache = {}
_folder_id_cache_lock = threading.Lock()

# Threshold to use upload session (files > 4 MB)
_LARGE_FILE_THRESHOLD = 4 * 1024 * 1024


def _get_token() -> str:
    """Retrieve a valid Microsoft access token using MSAL with persistent cache."""
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

        encoded_part = urllib.parse.quote(part)
        url = f"{_GRAPH_BASE}/me/drive/items/{parent_id}:/{encoded_part}"
        resp = requests.get(url, headers=_headers(token), timeout=30)
        
        found_id = None
        if resp.status_code == 200:
            item = resp.json()
            if "folder" in item:
                found_id = item["id"]
        elif resp.status_code != 404:
            resp.raise_for_status()

        if found_id:
            parent_id = found_id
        else:
            # Create folder (with fail conflict behavior, handles race condition via try-get fallback)
            body = {"name": part, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}
            resp = requests.post(
                f"{_GRAPH_BASE}/me/drive/items/{parent_id}/children",
                headers={**_headers(token), "Content-Type": "application/json"},
                json=body,
                timeout=30,
            )
            if resp.status_code == 409 or (resp.status_code == 400 and "alreadyExists" in resp.text):
                # Retrieve the ID of the folder that was created concurrently
                resp = requests.get(url, headers=_headers(token), timeout=30)
                resp.raise_for_status()
                parent_id = resp.json()["id"]
            else:
                resp.raise_for_status()
                parent_id = resp.json()["id"]
                logger.info(f"Folder created: {part}")

        with _folder_id_cache_lock:
            _folder_id_cache[path_so_far] = parent_id

    return parent_id


def _upload_small(token: str, folder_id: str, filename: str, filepath: str) -> bool:
    """Simple upload for files <= 4 MB."""
    encoded_filename = urllib.parse.quote(filename)
    url = f"{_GRAPH_BASE}/me/drive/items/{folder_id}:/{encoded_filename}:/content"
    with open(filepath, "rb") as f:
        data = f.read()
    resp = requests.put(
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
    encoded_filename = urllib.parse.quote(filename)
    url = f"{_GRAPH_BASE}/me/drive/items/{folder_id}:/{encoded_filename}:/createUploadSession"
    body = {"item": {"@microsoft.graph.conflictBehavior": "replace", "name": filename}}
    resp = requests.post(
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
            resp = requests.put(upload_url, headers=headers, data=chunk, timeout=120)

            if resp.status_code not in (200, 201, 202):
                logger.error(f"Chunk upload failed ({resp.status_code}): {resp.text[:200]}")
                return False

            uploaded += len(chunk)
            logger.debug(f"Upload {filename}: {uploaded}/{file_size} bytes")

    return True


def file_exists_on_onedrive(token: str, folder_id: str, filename: str) -> bool:
    """Checks if the file already exists on OneDrive (avoids duplicates)."""
    encoded_filename = urllib.parse.quote(filename)
    url = f"{_GRAPH_BASE}/me/drive/items/{folder_id}:/{encoded_filename}"
    resp = requests.get(url, headers=_headers(token), timeout=15)
    return resp.status_code == 200


def upload_file(filepath: str, onedrive_folder_path: str) -> bool:
    """
    Uploads a file to OneDrive in the specified folder.
    Automatically creates intermediate folders if needed.
    Skips the file if already present (deduplication by name).
    """
    token = _get_token()
    filename = os.path.basename(filepath)
    file_size = os.path.getsize(filepath)

    folder_id = _ensure_folder(token, onedrive_folder_path)

    if file_exists_on_onedrive(token, folder_id, filename):
        logger.debug(f"Already on OneDrive, skipping: {filename}")
        return True

    logger.info(f"Uploading: {filename} ({file_size / 1024 / 1024:.1f} MB)")

    if file_size <= _LARGE_FILE_THRESHOLD:
        return _upload_small(token, folder_id, filename, filepath)
    else:
        return _upload_large(token, folder_id, filename, filepath)
