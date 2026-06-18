"""
Sets up browser and Microsoft authentication for the backup tools.

Author: Pasquale Marzaioli

Setup script: authenticates the user on Google Photos and Microsoft OneDrive
and securely saves the tokens for future automated backups.

Run ONCE:
    python setup_auth.py
"""

import json
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import msal
import requests
import config


def setup_google() -> None:
    print("\n" + "=" * 55)
    print("  GOOGLE PHOTOS SETUP")
    print("=" * 55)

    import download_photos
    from playwright.sync_api import sync_playwright

    os.makedirs(download_photos.SESSION_DIR, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=download_photos.SESSION_DIR,
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://photos.google.com/")
        print("\n  A Chrome window has opened.")
        print("  Log in to your Google account if requested.")
        print("  When you see your photo library, come back here and press ENTER.")
        input("  Press ENTER to continue...")
        context.close()
    
    print("\nGoogle Photos: AUTHENTICATION COMPLETED")


def setup_microsoft() -> None:
    print("\n" + "=" * 55)
    print("  MICROSOFT ONEDRIVE SETUP")
    print("=" * 55)

    cache = msal.SerializableTokenCache()
    app = msal.PublicClientApplication(
        client_id=config.MICROSOFT_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{config.MICROSOFT_TENANT_ID}",
        token_cache=cache,
    )

    # Device code flow (compatible with all environments)
    flow = app.initiate_device_flow(scopes=config.MICROSOFT_SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"Unable to start device flow: {flow.get('error_description')}")

    print(f"\n1. Go to: {flow['verification_uri']}")
    print(f"2. Enter code: {flow['user_code']}")
    print("\nWaiting for authentication...")
    webbrowser.open(flow["verification_uri"])

    result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        raise RuntimeError(f"Microsoft authentication failed: {result.get('error_description')}")

    os.makedirs(config.TOKENS_DIR, exist_ok=True)
    with open(config.MICROSOFT_TOKEN_FILE, "w") as f:
        f.write(cache.serialize())

    print(f"\nMicrosoft token saved to: {config.MICROSOFT_TOKEN_FILE}")
    print("Microsoft OneDrive: AUTHENTICATION COMPLETED")


def verify_setup() -> None:
    print("\n" + "=" * 55)
    print("  VERIFY CONNECTIONS ")
    print("=" * 55)

    # Verify Google
    import download_photos
    if os.path.exists(download_photos.SESSION_DIR):
        print("  Google Photos : OK (Playwright session found)")
    else:
        print("  Google Photos : ERROR (No session found)")

    # Verify Microsoft
    try:
        import onedrive
        token = onedrive._get_token()
        resp = requests.get(
            "https://graph.microsoft.com/v1.0/me/drive",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        drive_name = resp.json().get("name", "OneDrive")
        print(f"  Microsoft     : OK  ({drive_name})")
    except Exception as e:
        print(f"  Microsoft     : ERROR - {e}")

    print()


if __name__ == "__main__":
    print("\nWelcome to the Google Photos -> OneDrive Backup Setup")

    # Check for placeholder Microsoft credentials
    if "YOUR_" in config.MICROSOFT_CLIENT_ID or "IL_TUO" in config.MICROSOFT_CLIENT_ID:
        print("WARNING: Microsoft API credentials are not yet configured.")
        print("Edit the config.py file with your Client ID.")
        exit(1)

    try:
        setup_google()
    except Exception as e:
        print(f"\nGoogle Error: {e}")
        exit(1)

    try:
        setup_microsoft()
    except Exception as e:
        print(f"\nMicrosoft Error: {e}")
        exit(1)

    verify_setup()
    print("Setup completed! The daily checker can now run the monthly backup when needed.")
