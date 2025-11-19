import os
import json
import sys
from dotenv import load_dotenv

load_dotenv()

# Project root (main.py folder)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Database Mode Configuration
NAVIDROME_DB_PATH = os.getenv("NAVIDROME_DB_PATH")
NAVIDROME_URL = os.getenv("NAVIDROME_URL")

# Last.fm Configuration
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
LASTFM_API_SECRET = os.getenv("LASTFM_API_SECRET")
LASTFM_SESSION_KEY = os.getenv("LASTFM_SESSION_KEY")
LASTFM_USER = os.getenv("LASTFM_USER")
SCROBBLED_FIRSTARTISTONLY = os.getenv("SCROBBLED_FIRSTARTISTONLY", "True") == "True"

# Sync Configuration
# Enable syncing Navidrome starred tracks TO Last.fm as loved tracks
# Requires LASTFM_API_SECRET and LASTFM_SESSION_KEY to be configured
SYNC_LOVED_TO_LASTFM = os.getenv("SYNC_LOVED_TO_LASTFM", "False") == "True"

# Fuzzy matching configuration
# Enable fuzzy matching for track names (default: True)
# When False, only exact matches between Navidrome and Last.fm will be synced
# When True, prompts user to confirm fuzzy matches
ENABLE_FUZZY_MATCHING = os.getenv("ENABLE_FUZZY_MATCHING", "True") == "True"

# Playcount conflict resolution strategy (for database mode)
# Options: "ask", "navidrome", "lastfm", "higher", "increment"
# - "ask": Prompt user for each conflict (default, interactive)
# - "navidrome": Always keep Navidrome count when it's higher
# - "lastfm": Always use Last.fm count
# - "higher": Always use the higher count between Navidrome and Last.fm
# - "increment": Add Last.fm count to Navidrome count (useful for combining separate scrobbling sources)
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

# JSON output folder
JSON_FOLDER = os.path.join(PROJECT_ROOT, "json")
os.makedirs(JSON_FOLDER, exist_ok=True)  # create folder if it doesn't exist

# JSON output filenames (inside JSON_FOLDER)
MISSING_SCROBBLES = os.path.join(JSON_FOLDER, "missing_scrobbles.json")
MISSING_LOVED = os.path.join(JSON_FOLDER, "missing_loved.json")

# Cache database path
CACHE_FOLDER = os.path.join(PROJECT_ROOT, "cache")
os.makedirs(CACHE_FOLDER, exist_ok=True)  # create cache folder if it doesn't exist
CACHE_DB_PATH = os.path.join(CACHE_FOLDER, "scrobbles.db")

def validate_config():
    """Validate required configuration values."""
    missing = []
    
    # Last.fm is always required
    if not LASTFM_API_KEY:
        missing.append("LASTFM_API_KEY")
    if not LASTFM_USER:
        missing.append("LASTFM_USER")
    
    # API mode requires connection details
    if not NAVIDROME_URL:
        missing.append("NAVIDROME_URL")
    
    # Check if reverse sync is enabled but authentication is missing
    if SYNC_LOVED_TO_LASTFM:
        if not LASTFM_API_SECRET:
            missing.append("LASTFM_API_SECRET (required when SYNC_LOVED_TO_LASTFM=True)")
        if not LASTFM_SESSION_KEY:
            missing.append("LASTFM_SESSION_KEY (required when SYNC_LOVED_TO_LASTFM=True)")
    
    if missing:
        print(f"❌ Error: Missing required environment variables in .env file:")
        for var in missing:
            print(f"   - {var}")
        print("\nPlease check your .env file and ensure all required variables are set.")
        sys.exit(1)