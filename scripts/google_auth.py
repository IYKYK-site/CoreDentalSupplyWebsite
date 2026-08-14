from pathlib import Path
import os
from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/calendar"]
client_file = os.getenv("GOOGLE_CLIENT_SECRET_FILE", "client_secret.json")
token_file = os.getenv("GOOGLE_TOKEN_FILE", "token.json")

if not Path(client_file).exists():
    raise SystemExit(
        f"{client_file} not found. Download an OAuth Desktop App credential JSON "
        "from Google Cloud Console and save it with that filename."
    )

flow = InstalledAppFlow.from_client_secrets_file(client_file, SCOPES)
creds = flow.run_local_server(port=0)
Path(token_file).write_text(creds.to_json())
print(f"Google Calendar OAuth complete. Saved {token_file}.")
