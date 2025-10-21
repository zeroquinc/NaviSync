import os
import json
from dotenv import load_dotenv

load_dotenv()

# Project root (main.py folder)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NAVIDROME_DB_PATH = os.getenv("NAVIDROME_DB_PATH")
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
LASTFM_USER = os.getenv("LASTFM_USER")
SCROBBLED_FIRSTARTISTONLY = os.getenv("SCROBBLED_FIRSTARTISTONLY", "True") == "True"
FIRST_ARTIST_WHITELIST = json.loads(os.getenv("FIRST_ARTIST_WHITELIST", "[]"))

# JSON output folder
JSON_FOLDER = os.path.join(PROJECT_ROOT, "json")
os.makedirs(JSON_FOLDER, exist_ok=True)  # create folder if it doesn't exist

# JSON output filenames (inside JSON_FOLDER)
MISSING_SCROBBLES = os.path.join(JSON_FOLDER, "missing_scrobbles.json")
MISSING_LOVED = os.path.join(JSON_FOLDER, "missing_loved.json")