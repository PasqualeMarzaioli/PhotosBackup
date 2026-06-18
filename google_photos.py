"""
Provides optional Google Photos Library API helpers.

Author: Pasquale Marzaioli

Module for accessing the Google Photos Library API.
Handles OAuth2 authentication and retrieval of media items per month.
"""

import os
import logging
import requests
import calendar
from typing import Optional

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

import config

logger = logging.getLogger(__name__)


def _get_credentials() -> Credentials:
    """Loads or refreshes the Google OAuth2 credentials."""
    creds = None

    if os.path.exists(config.GOOGLE_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(
            config.GOOGLE_TOKEN_FILE, config.GOOGLE_SCOPES
        )

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing Google Photos token...")
            creds.refresh(Request())
        else:
            raise RuntimeError(
                "Google token not found or invalid. "
                "Run first: python setup_auth.py"
            )

        with open(config.GOOGLE_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return creds


def get_photos_for_month(year: int, month: int) -> list[dict]:
    """
    Retrieves all media items for a specific month from Google Photos.
    Returns a list of dicts returning 'id', 'filename', 'baseUrl', 'mimeType'.
    """
    creds = _get_credentials()
    headers = {"Authorization": f"Bearer {creds.token}"}

    url = "https://photoslibrary.googleapis.com/v1/mediaItems:search"
    last_day = calendar.monthrange(year, month)[1]
    payload = {
        "pageSize": 100,
        "filters": {
            "dateFilter": {
                "ranges": [
                    {
                        "startDate": {"year": year, "month": month, "day": 1},
                        "endDate": {"year": year, "month": month, "day": last_day},
                    }
                ]
            },
            "mediaTypeFilter": {"mediaTypes": ["PHOTO", "VIDEO"]},
        },
    }

    media_items = []
    page_token = None

    while True:
        if page_token:
            payload["pageToken"] = page_token

        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()

        items = data.get("mediaItems", [])
        media_items.extend(
            {
                "id": item["id"],
                "filename": item["filename"],
                "baseUrl": item["baseUrl"],
                "mimeType": item.get("mimeType", "image/jpeg"),
            }
            for item in items
        )

        logger.info(f"Retrieved {len(media_items)} media items so far...")
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    logger.info(f"Total media items found for {month}/{year}: {len(media_items)}")
    return media_items


def download_photo(photo: dict, dest_dir: str) -> Optional[str]:
    """
    Downloads a single media item into the dest_dir.
    Returns the local path of the downloaded file, or None in case of an error.
    """
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, photo["filename"])

    if os.path.exists(dest_path):
        logger.debug(f"Already present: {photo['filename']}, skip.")
        return dest_path

    # Google Photos Library API uses a different download suffix for videos.
    suffix = "=dv" if photo.get("mimeType", "").startswith("video/") else "=d"
    download_url = photo["baseUrl"] + suffix

    try:
        response = requests.get(download_url, timeout=60, stream=True)
        response.raise_for_status()

        with open(dest_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)

        logger.debug(f"Downloaded: {photo['filename']}")
        return dest_path

    except Exception as e:
        logger.error(f"Error downloading {photo['filename']}: {e}")
        return None
