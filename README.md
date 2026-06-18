# Google Photos to OneDrive Backup

Author: Pasquale Marzaioli

Automated monthly backup of your Google Photos media to OneDrive.

The LaunchAgent runs a lightweight checker every day at **09:00**. Starting from the configured backup day, the checker runs the previous month's backup only if a successful run is not already present in `backup.log`. This means the backup can catch up if the Mac was asleep or turned off on the scheduled day.

Uploaded files are stored in this OneDrive folder:

```
Immagini/Memorie/{year}/{mm.MonthName year}
```

**Example:** `Immagini/Memorie/2026/04.Aprile 2026`

---

## Requirements

- macOS with Python 3.9+
- Google account with Google Photos
- Microsoft account with personal OneDrive
- A Microsoft app registration for OneDrive access
- Google Chrome for the browser-based Google Photos session

---

## Step 1 — Prepare Google Photos Access

The active downloader uses a real Chrome session because Google Photos browser downloads are more complete for personal-library backups than the Google Photos Library API.

Run:

```bash
python3 setup_auth.py
```

Chrome opens and asks you to sign in to Google Photos. Once the library is visible, return to the terminal and continue. The browser profile is saved in `.chrome_session/` and reused by future backups.

The optional `google_photos.py` helper can still use the Google Photos Library API for experiments, but it is not the primary backup path.

---

## Step 2 — Create Microsoft OneDrive Credentials

1. Go to [Azure Portal](https://portal.azure.com/)
2. Search for **"App registrations"** → **New registration**
3. Name: "PhotosBackup OneDrive"
4. Supported account types: **Personal Microsoft accounts only**
5. Redirect URI: select **Public client/native (mobile & desktop)** → `http://localhost:8080` or `http://localhost`
6. Copy the **Application (client) ID** into `config.py`:
   ```python
   MICROSOFT_CLIENT_ID = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
   ```
7. Menu **API permissions** → Add a permission → Microsoft Graph → Delegated permissions:
   - `Files.ReadWrite`
   - `offline_access`
8. Click **Grant admin consent**

---

## Configuration & Customization

### Language Customization
By default, the folder names created on OneDrive use **Italian month names** (e.g., `Aprile` instead of `April`). 

If you wish to use English or any other language, simply modify the `MONTHS` dictionary in `config.py`:

```python
# config.py
MONTHS = {
    1: "January",
    2: "February",
    # ... and so on
}
```

### Concurrency Settings
You can speed up the process by changing the parallelism settings in `config.py`:
- `CONCURRENT_UPLOADS`: Simultaneous uploads to OneDrive.
- `CONCURRENT_DELETES`: Number of browser tabs used for deletion.

### Reliability and Safety Settings
The browser downloader and OneDrive uploader are intentionally conservative:

- `MAX_SCROLL_ATTEMPTS`, `SCROLL_IDLE_ROUNDS`: Control how long Google Photos is scrolled before the media grid is considered fully loaded.
- `DOWNLOAD_RETRIES`: Retries per Google Photos media item.
- `REQUEST_RETRIES`: Retries for transient OneDrive and network errors.
- `MUTE_BROWSER_AUDIO`: Opens Google Photos media pages without video/audio playback sound.
- `DELETE_AFTER_UPLOAD`: Enables automatic deletion from Google Photos after a successful backup.
- `DELETE_ON_PARTIAL_SUCCESS`: Defaults to `False`; when a run has download or upload errors, Google Photos deletion is skipped.
- `CLEAN_RUNTIME_ARTIFACTS_AFTER_SUCCESS`: Removes temporary files and Python caches after a fully successful run.
- `CLEAN_CHROME_CACHE_AFTER_SUCCESS`: Removes Chrome cache folders while preserving the saved Google login session.

---

## Step 3 — Install and Configure

```bash
cd /Users/your_user/path/to/PhotosBackup

# Install dependencies and activate LaunchAgent
bash install.sh

# Authenticate (opens the browser)
python3 setup_auth.py
```

---

## Usage

| Command | Description |
|---------|-------------|
| `./backup.sh` | Backup for the previous month |
| `./backup.sh 2026 3` | Backup for March 2026 |
| `./check_and_backup.sh` | Run the catch-up checker |
| `python3 setup_auth.py` | Re-authentication (expired token) |

### LaunchAgent Management

```bash
# Check if it's active
launchctl list | grep photosbackup

# Start manually now
launchctl start com.pasquale.photosbackup

# Disable automatic backup
launchctl unload ~/Library/LaunchAgents/com.pasquale.photosbackup.plist

# Re-enable
launchctl load ~/Library/LaunchAgents/com.pasquale.photosbackup.plist
```

---

## File Structure

```
PhotosBackup/
├── backup.py          # Main script
├── config.py          # Configuration and credentials
├── download_photos.py # Active browser-based Google Photos downloader
├── google_photos.py  # Optional Google Photos Library API helper
├── onedrive.py        # Microsoft Graph API module
├── setup_auth.py      # Authentication setup
├── install.sh         # Installation script
├── check_and_backup.sh # Daily catch-up checker
├── requirements.txt   # Python dependencies
├── backup.log         # Operations log
├── README.md          # Project instructions
├── .tokens/           # OAuth Tokens (do not share!)
│   ├── google_token.json
│   └── microsoft_token.json
└── .tmp_download/     # Temporary files (auto-deleted)
```

---

## Logs and Monitoring

All backups are recorded in `backup.log`:

```
2026-04-02 09:00:01  INFO     BACKUP STARTED: April 2026
2026-04-02 09:00:03  INFO     Found 47 unique media links.
2026-04-02 09:01:22  INFO     BACKUP COMPLETED: April 2026
2026-04-02 09:01:22  INFO       Successfully uploaded : 47
2026-04-02 09:01:22  INFO       Upload errors         : 0
2026-04-02 09:01:22  INFO       Download errors       : 0
2026-04-02 09:01:22  INFO       Deletion errors       : 0
2026-04-02 09:01:22  INFO       Errors                : 0
```

---

## Important Notes

- Microsoft tokens are automatically renewed without requiring a new login.
- Files already present on OneDrive with the same name and size are skipped.
- Same-name files with different sizes are uploaded with a numbered suffix instead of being silently skipped.
- Google Photos URLs are canonicalized so the same media item is not downloaded repeatedly from different browser routes.
- Duplicate downloaded content is detected by SHA-256 and skipped before upload.
- Google Photos video/audio pages are opened muted when `MUTE_BROWSER_AUDIO = True`.
- If the Mac is turned off on the scheduled day, the daily checker catches up when the Mac is available again.
- Google Photos deletion is skipped when any download or upload error occurs, unless `DELETE_ON_PARTIAL_SUCCESS` is explicitly enabled.
- Temporary files are automatically deleted only after a fully successful run; failed runs keep them for inspection.
- After a fully successful run, runtime cleanup removes `.tmp_download`, Python bytecode caches, and Chrome cache directories without deleting tokens, logs, configuration, the virtual environment, or the browser login session.
