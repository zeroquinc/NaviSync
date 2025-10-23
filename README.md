# NaviSync

NaviSync is a Python tool that syncs play counts and loved track information from Last.fm to a Navidrome server. It ensures your local music library in Navidrome reflects your listening history on Last.fm.

⚠️ **WARNING:**

**This script writes to the Navidrome database, always make sure Navidrome is NOT running and make a backup of the db file!** 

**I am not responsible for any damage to your Navidrome server.**

The script has a built-in check to see if Navidrome is running and if the database file is locked, but better be safe than sorry.

## Features

- **Intelligent Caching**: Local SQLite cache stores scrobbles to minimize Last.fm API calls and speed up syncs.
- **Incremental Sync**: Only fetches new scrobbles since the last sync, dramatically reducing sync time.
- **Loved Tracks Support**: Uses the dedicated `user.getLovedTracks` API endpoint for reliable loved track tracking.
- Compares Last.fm scrobbles with Navidrome play counts.
- Detects tracks that are missing or have different play counts.
- Updates Navidrome play counts and loved/starred status.
- Generates JSON reports for missing or loved tracks.
- Health checks to see if Navidrome is running.

## Installation

1. Clone the repository:

`git clone https://github.com/zeroquinc/NaviSync.git`
`cd NaviSync`

2. Install dependencies:

`pip install -r requirements.txt`

3. Create a `.env` (or rename the env.example to `.env`) file in the project root with the following variables:

```
NAVIDROME_DB_PATH=path/to/navidrome.db
LASTFM_API_KEY=your_lastfm_api_key
LASTFM_USER=your_lastfm_username
SCROBBLED_FIRSTARTISTONLY=True
FIRST_ARTIST_WHITELIST=["Suzan & Freek", "Simon & Garfunkel", "AC/DC"]
```

4. For updates simply run `git pull`.

## Usage

⚠️ **Before running, always make sure your Navidrome db file has a backup and is not running!** ⚠️

⚠️ **I am not responsible for any damage to your Navidrome server.** ⚠️

### First-time Setup Check

Run the diagnostic tool to verify your setup:

`python check_setup.py`

This will check:
- Python version
- Required dependencies
- .env configuration
- Database file existence

### Running the Sync

`python main.py`

- Or run start.bat if on Windows.

The script will:

1. Initialize or update the local scrobble cache (stored in `cache/scrobbles_cache.db`).
2. Fetch only new scrobbles from Last.fm since the last sync (much faster after first run!).
3. Update loved tracks using Last.fm's dedicated loved tracks API.
4. Load your Navidrome database and compare with cached Last.fm data.
5. Generate reports for missing and loved tracks.
6. Update Navidrome play counts and loved tracks from Last.fm.

### First Run vs. Subsequent Runs

- **First run**: Fetches all historical scrobbles (may take a few minutes depending on your scrobble count).
- **Subsequent runs**: Only fetches new scrobbles since last sync (typically seconds!).

Generated files:

- `json/missing_scrobbles.json` – tracks in Last.fm but missing from Navidrome.
- `json/missing_loved.json` – loved tracks in Last.fm but missing from Navidrome.
- `cache/scrobbles_cache.db` – local cache of all your Last.fm scrobbles (auto-managed).

## Configuration

- Use the `.env` file for API keys, database path, and artist whitelist.
- The artist whitelist allows you to control which artists are treated as the "first artist" in collaborations.

### Playcount Conflict Resolution

When Navidrome has a higher play count than Last.fm, you can control how NaviSync handles this conflict using the `PLAYCOUNT_CONFLICT_RESOLUTION` setting in your `.env` file:

- **`ask`** (default): Interactively prompt for each conflict - gives you full control
- **`navidrome`**: Always keep Navidrome's count when it's higher - useful if you play music offline or use other clients
- **`lastfm`**: Always use Last.fm's count - overwrites Navidrome completely  
- **`higher`**: Always use whichever count is higher - best of both worlds
- **`increment`**: Add Last.fm count to Navidrome count - combines both sources (e.g., Navidrome:20 + Last.fm:15 = 35)

**Example configuration:**
```env
# Keep Navidrome counts when they're higher (no prompts)
PLAYCOUNT_CONFLICT_RESOLUTION=navidrome

# Combine playcounts from both sources
PLAYCOUNT_CONFLICT_RESOLUTION=increment
```

**When to use each mode:**
- Use **`navidrome`** if you listen offline or use multiple music players and want to preserve those plays
- Use **`lastfm`** if Last.fm is your single source of truth
- Use **`higher`** if you want the maximum count from both sources
- Use **`increment`** if you have separate scrobbling sources (e.g., mobile app not scrobbling to Last.fm, or manual plays in Navidrome)
- Use **`ask`** if you want to manually review conflicts (good for first sync)

## Cache Management

The cache is stored in `cache/scrobbles_cache.db` and is automatically managed. The cache includes:

- All your Last.fm scrobbles with timestamps
- Loved track status
- Sync state for each track

### Utility Commands

View cache information:
```bash
python cache_info.py --info
```

Interactive cache management menu:
```bash
python cache_info.py
```

Reset sync status (force full re-sync with Navidrome):
```bash
python cache_info.py --reset
```

If you ever need to completely rebuild the cache, simply delete the `cache/` folder and run the script again.

## License

This project is licensed under the MIT License. See the LICENSE file for details.
