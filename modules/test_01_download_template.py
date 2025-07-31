import os
import io
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from dotenv import load_dotenv

# Load credentials from .env
load_dotenv()
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Files to download: (Google Drive name → local filename)
TEMPLATES = {
    "2025_08_MarketOverview_Basic_MapTemplate.pptx": "downloaded_map_template.pptx",
    "2025_08_MarketOverview_Basic_IndustrySummary.pptx": "downloaded_summary_template.pptx"
}


def download_file_from_drive(service, drive_filename, local_filename):
    print(f"🔍 Searching for: {drive_filename}")
    results = service.files().list(
        q=f"name='{drive_filename}' and mimeType='application/vnd.openxmlformats-officedocument.presentationml.presentation'",
        spaces='drive',
        fields='files(id, name)').execute()

    files = results.get('files', [])
    if not files:
        print(f"❌ File not found: {drive_filename}")
        return

    file_id = files[0]['id']
    print(f"✅ Found: {drive_filename} (ID: {file_id})")

    request = service.files().get_media(fileId=file_id)
    fh = io.FileIO(local_filename, 'wb')
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
        print(f"⬇️ {drive_filename}: {int(status.progress() * 100)}% downloaded")

    print(f"✅ Downloaded to: {local_filename}")


def download_all_templates():
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    service = build('drive', 'v3', credentials=creds)

    for drive_name, local_name in TEMPLATES.items():
        download_file_from_drive(service, drive_name, local_name)


if __name__ == "__main__":
    download_all_templates()
