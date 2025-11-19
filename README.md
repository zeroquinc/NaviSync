# NaviSync

Sync your Last.fm play counts and loved tracks to Navidrome with intelligent caching.

## Quick Start

1. **Install dependencies:**
   ```bash
   git clone https://github.com/zeroquinc/NaviSync.git
   cd NaviSync
   pip install -r requirements.txt
   ```

2. **Configure:** Copy `env.example` to `.env` and fill in your details:
   ```env
      NAVIDROME_DB_PATH=Z:/navidrome/navidrome.db
      NAVIDROME_URL=http://192.168.0.50:4533

      LASTFM_API_KEY=lastfmapikey
      LASTFM_USER=Username

      SCROBBLED_FIRSTARTISTONLY=True
      FIRST_ARTIST_WHITELIST=["Suzan & Freek", "Simon & Garfunkel", "AC/DC"]
      ENABLE_FUZZY_MATCHING=True
      PLAYCOUNT_CONFLICT_RESOLUTION=ask
      SYNC_LOVED_TO_LASTFM=False
   ```

3. **Run:** `python main.py`

4. It is important to delete the cache and json folders when updating from this repo, as they may contain some changes.

## Features

- **Keep your play counts in sync** - Never lose track of your listening history by updating Navidrome play counts
- **Sync your loved tracks** - Last.fm hearts become Navidrome stars  
- **Reverse sync (optional)** - Sync Navidrome stars TO Last.fm as loved tracks (requires authentication)
- **Fast after first run** - Only processes new plays, not your entire history
- **Intelligent fuzzy matching** - Finds potential matches for track name variations:
  - Handles `&` vs `and`, special characters, accents, and minor differences
  - **Always prompts you to confirm** - no automatic matching to prevent errors
  - Shows similarity scores to help you decide
  - **Remembers your choices** - confirmed matches are saved and used automatically in future runs
  - You have full control over which tracks get matched

## Configuration Options

### Artist Handling
```env
# Extract first artist from collaborations (e.g. "Artist A feat. Artist B" → "Artist A")
SCROBBLED_FIRSTARTISTONLY=True
FIRST_ARTIST_WHITELIST=["Simon & Garfunkel", "AC/DC"]  # Keep these exact
```

### Fuzzy Matching

```env
ENABLE_FUZZY_MATCHING=True  # Default: True
```

**Options:**
- `True` - Enable fuzzy matching with prompts for similar tracks (recommended for accuracy)
- `False` - Only exact matches, no prompts (faster but fewer matches)

When enabled, the script intelligently finds potential matches for track name variations and prompts you to confirm. When disabled, only 100% exact matches are synced.

### Conflict Resolution

```env
PLAYCOUNT_CONFLICT_RESOLUTION=ask  # Options: ask, navidrome, lastfm, higher, increment
```

**Conflict Options:**
- `ask` - Prompt for each conflict (default)
- `navidrome` - Keep Navidrome when higher
- `lastfm` - Always use Last.fm
- `higher` - Use whichever is higher
- `increment` - Add counts together

### Reverse Sync (Optional)

Sync Navidrome starred tracks TO Last.fm as loved tracks:

```env
SYNC_LOVED_TO_LASTFM=True  # Default: False
```

**Setup (one-time):**

1. Get your API Secret from [Last.fm API Account](https://www.last.fm/api/account/create)

2. Add to `.env`:
   ```env
   LASTFM_API_SECRET=yourapisecret
   ```

3. Get session key (interactive):
   ```bash
   python -c "from src.lastfm import get_session_key; get_session_key()"
   ```

4. Follow the prompts, authorize in browser, then add the session key to `.env`:
   ```env
   LASTFM_SESSION_KEY=yoursessionkey
   ```

5. Enable reverse sync:
   ```env
   SYNC_LOVED_TO_LASTFM=True
   ```

## Cache Management

View cache status: `python cache_info.py --info`

View fuzzy match mappings: `python cache_info.py --fuzzy`

Reset sync status: `python cache_info.py --reset`

**Fuzzy Match Mappings:** Once you confirm a fuzzy match (e.g., "The Great Hall and The Prophecy" → "The Great Hall & The Prophecy"), it's saved in the cache. Future runs will automatically use this mapping without prompting you again.

## Troubleshooting

**Setup issues:** `python check_setup.py`

**First run slow:** Normal - fetches all historical scrobbles. Subsequent runs are fast!

## License

MIT License - see LICENSE file for details.