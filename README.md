# NaviSync

Sync your Last.fm play counts and loved tracks including timestamps to Navidrome with intelligent caching.

## ⚠️ Always make a backup of your Navidrome database first before using this! ⚠️

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
      FUZZY_MATCHING_THRESHOLD=85  # Minimum similarity score to consider a fuzzy candidate
      FUZZY_MATCHING_AUTO_THRESHOLD=95  # Automatically accept a fuzzy match at or above this score
      PLAYCOUNT_CONFLICT_RESOLUTION=ask
      SYNC_LOVED_TO_LASTFM=False
      ALBUM_MATCHING_MODE=album_agnostic
      DUPLICATE_RESOLUTION=ask
      AUTO_CONFIRM=False
   ```

   Navidrome must be stopped while sync runs so the database can be updated safely.

3. **Run:** `python main.py`

4. It is important to delete the cache and json folders when updating from this repo, as they may contain some changes.

## Features

- **Keep your play counts in sync** - Never lose track of your listening history by updating Navidrome play counts
- **Sync your loved tracks** - Last.fm hearts become Navidrome stars  
- **Reverse sync (optional)** - Sync Navidrome stars TO Last.fm as loved tracks (requires authentication)
- **Fast after first run** - Only processes new plays, not your entire history
- **Intelligent fuzzy matching** - Finds potential matches for track name variations:
  - Handles `&` vs `and`, special characters, accents, and minor differences
   - Prompts you to confirm uncertain matches and can auto-accept high-confidence matches
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
FUZZY_MATCHING_THRESHOLD=85  # Minimum similarity score to consider a fuzzy candidate
FUZZY_MATCHING_AUTO_THRESHOLD=95  # Automatically accept a fuzzy match at or above this score
```

**Options:**
- `True` - Enable fuzzy matching with prompts for similar tracks (recommended for accuracy)
- `False` - Only exact matches, no prompts (faster but fewer matches)

**Threshold settings:**
- `FUZZY_MATCHING_THRESHOLD` controls the minimum combined similarity score used to find fuzzy candidates
- `FUZZY_MATCHING_AUTO_THRESHOLD` enables automatic acceptance of the top fuzzy match when its score meets or exceeds this percentage

When enabled, the script intelligently finds potential matches for track name variations. With `FUZZY_MATCHING_AUTO_THRESHOLD` set, high-confidence matches are accepted automatically, while lower-confidence matches still prompt for confirmation.

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

### Album Matching

```env
ALBUM_MATCHING_MODE=album_agnostic  # Options: album_agnostic, album_aware, prompt
```

**Album Handling Options:**
- `album_agnostic` - Combine scrobbles for same artist/title regardless of album (default, current behavior)
  - Example: "Track A" on "Album X" and "Compilation Y" both get 100 plays total from Last.fm
  - When multiple album versions exist, prompts user to choose which to update
- `album_aware` - Match by artist/title/album, allowing different play counts per album
  - Example: "Track A" on "Album X" gets 60 plays, same track on "Compilation Y" gets 40 plays
  - Ideal for mixed albums, compilations, and avoiding duplicate play counts in smart playlists
- `prompt` - Like album_agnostic, but always asks which album version(s) to update (no auto-selection)

### Duplicate Resolution

```env
DUPLICATE_RESOLUTION=ask  # Options: ask, all, first, skip
```

**Duplicate Options:**
- `ask` - Prompt for each duplicate track version (default)
- `all` - Update all duplicate versions automatically
- `first` - Update only the first version found
- `skip` - Skip tracks that have duplicates

### Auto Confirm

```env
AUTO_CONFIRM=False  # Default: False
```

**Options:**
- `False` - Show final confirmation prompt before applying updates (default)
- `True` - Skip final confirmation and apply updates immediately

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

Clear album cache/fuzzy matches entries: `python clear_album_cache.py`

**Fuzzy Match Mappings:** Once you confirm a fuzzy match (e.g., "The Great Hall and The Prophecy" → "The Great Hall & The Prophecy"), it's saved in the cache. Future runs will automatically use this mapping without prompting you again.

## Troubleshooting

**Setup issues:** `python check_setup.py`

**First run slow:** Normal - fetches all historical scrobbles. Subsequent runs are fast!

## License

MIT License - see LICENSE file for details.
