# Google Photos to OneDrive Backup

Automated monthly backup of your photos: every **2nd of the month** the script downloads the previous month's photos from Google Photos, deletes them from Google Photos, and uploads them to OneDrive in the following folder:

```
Memorie/immagine/{year}/{mm.MonthName year}
```

**Example:** `Memorie/immagine/2026/04.Aprile 2026`

---

## Requirements

- macOS with Python 3.9+
- Google account with Google Photos
- Microsoft account with personal OneDrive

---

## Step 1 — Create Google Photos API Credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (e.g., "PhotosBackup")
3. Sidebar menu → **APIs & Services** → **Library**
4. Search for **"Photos Library API"** and enable it
5. Go to **Credentials** → **Create Credentials** → **OAuth client ID**
6. Application type: **Desktop app**
7. Download the JSON → copy the values into `config.py`:
   ```python
   GOOGLE_CLIENT_ID = "xxxxx.apps.googleusercontent.com"
   GOOGLE_CLIENT_SECRET = "GOCSPX-xxxxx"
   ```
8. In **OAuth consent screen** → add your email as a test user

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
You can speed up the process by increasing the number of parallel uploads or browser tabs in `config.py`:
- `CONCURRENT_UPLOADS`: Simultaneous uploads to OneDrive.
- `CONCURRENT_DELETES`: Number of browser tabs used for deletion.

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
| `python3 backup.py` | Backup for the previous month |
| `python3 backup.py 2026 3` | Backup for March 2026 |
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
├── google_photos.py  # Google Photos API module
├── onedrive.py        # Microsoft Graph API module
├── setup_auth.py      # Authentication setup
├── install.sh         # Installation script
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
2026-04-02 09:00:03  INFO     Found 47 photos.
2026-04-02 09:01:22  INFO     BACKUP COMPLETED: April 2026
2026-04-02 09:01:22  INFO       Successfully uploaded : 47
2026-04-02 09:01:22  INFO       Already present (skip): 0
2026-04-02 09:01:22  INFO       Errors                : 0
```

---

## Important Notes

- Tokens are automatically renewed without requiring a new login
- Photos already present on OneDrive are skipped (no duplicates)
- If the Mac is turned off on the 2nd of the month, the backup is not executed and will resume the following month
- Temporary files are automatically deleted after upload
