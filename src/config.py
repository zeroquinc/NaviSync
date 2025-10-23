import os
import json
import sys
from dotenv import load_dotenv

load_dotenv()

# Project root (main.py folder)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NAVIDROME_DB_PATH = os.getenv("NAVIDROME_DB_PATH")
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
LASTFM_USER = os.getenv("LASTFM_USER")
SCROBBLED_FIRSTARTISTONLY = os.getenv("SCROBBLED_FIRSTARTISTONLY", "True") == "True"

# Playcount conflict resolution setting
PLAYCOUNT_CONFLICT_RESOLUTION = os.getenv("PLAYCOUNT_CONFLICT_RESOLUTION", "ask").lower()

# Validate the conflict resolution setting
VALID_CONFLICT_MODES = ["ask", "navidrome", "lastfm", "higher", "increment"]
if PLAYCOUNT_CONFLICT_RESOLUTION not in VALID_CONFLICT_MODES:
    print(f"⚠️  Warning: Invalid PLAYCOUNT_CONFLICT_RESOLUTION '{PLAYCOUNT_CONFLICT_RESOLUTION}', using 'ask'")
    PLAYCOUNT_CONFLICT_RESOLUTION = "ask"

# Parse whitelist with error handling
try:
    FIRST_ARTIST_WHITELIST = json.loads(os.getenv("FIRST_ARTIST_WHITELIST", "[]"))
except json.JSONDecodeError:
    print("⚠️  Warning: Invalid JSON in FIRST_ARTIST_WHITELIST, using empty list")
    FIRST_ARTIST_WHITELIST = []

def validate_config():
    """Validate required configuration values.

    In DB mode (USE_NAVIDROME_API=False), NAVIDROME_DB_PATH must exist.
    In API-only mode (USE_NAVIDROME_API=True), NAVIDROME_DB_PATH is not required.
    """
    missing = []

    if not USE_NAVIDROME_API:
        if not NAVIDROME_DB_PATH:
            missing.append("NAVIDROME_DB_PATH")
        elif not os.path.exists(NAVIDROME_DB_PATH):
            print(f"❌ Error: Navidrome database not found at: {NAVIDROME_DB_PATH}")
            sys.exit(1)

    if not LASTFM_API_KEY:
        missing.append("LASTFM_API_KEY")

    if not LASTFM_USER:
        missing.append("LASTFM_USER")

    if missing:
        print(f"❌ Error: Missing required environment variables in .env file:")
        for var in missing:
            print(f"   - {var}")
        print("\nPlease check your .env file and ensure all required variables are set.")
        sys.exit(1)

# JSON output folder
JSON_FOLDER = os.path.join(PROJECT_ROOT, "json")
os.makedirs(JSON_FOLDER, exist_ok=True)  # create folder if it doesn't exist

# JSON output filenames (inside JSON_FOLDER)
MISSING_SCROBBLES = os.path.join(JSON_FOLDER, "missing_scrobbles.json")
MISSING_LOVED = os.path.join(JSON_FOLDER, "missing_loved.json")

# Cache database path
CACHE_FOLDER = os.path.join(PROJECT_ROOT, "cache")
os.makedirs(CACHE_FOLDER, exist_ok=True)  # create cache folder if it doesn't exist
CACHE_DB_PATH = os.path.join(CACHE_FOLDER, "scrobbles_cache.db")

# Navidrome API (Subsonic/OpenSubsonic) configuration
USE_NAVIDROME_API = os.getenv("USE_NAVIDROME_API", "False").lower() == "true"
NAVIDROME_URL = os.getenv("NAVIDROME_URL", "")  # e.g., https://navidrome.example.com
NAVIDROME_API_USER = os.getenv("NAVIDROME_API_USER", os.getenv("NAVIDROME_USERNAME", "")) or os.getenv("NAVIDROME_USER", "")
NAVIDROME_API_PASSWORD = os.getenv("NAVIDROME_API_PASSWORD", os.getenv("NAVIDROME_PASSWORD", ""))
NAVIDROME_API_VERSION = os.getenv("NAVIDROME_API_VERSION", "1.16.1")
NAVIDROME_CLIENT_ID = os.getenv("NAVIDROME_CLIENT_ID", "NaviSync")
API_RATE_LIMIT_MS = int(os.getenv("API_RATE_LIMIT_MS", "200"))