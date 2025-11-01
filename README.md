# NaviSync

Sync your Last.fm play counts and loved tracks to Navidrome with intelligent caching and two sync modes.

## Quick Start

1. **Install dependencies:**
   ```bash
   git clone https://github.com/zeroquinc/NaviSync.git
   cd NaviSync
   pip install -r requirements.txt
   ```

2. **Configure:** Copy `env.example` to `.env` and fill in your details:
   ```env
   # Navidrome connection
   NAVIDROME_URL=http://localhost:4533
   NAVIDROME_USER=your_username
   NAVIDROME_PASSWORD=your_password
   
   # Last.fm credentials
   LASTFM_API_KEY=your_api_key
   LASTFM_USER=your_username
   ```

3. **Run:** `python main.py`

## Features

- **Keep your play counts in sync** - Never lose track of your listening history by updating Navidrome play counts
- **Sync your loved tracks** - Last.fm hearts become Navidrome stars  
- **Fast after first run** - Only processes new plays, not your entire history

## Configuration Options

### Artist Handling
```env
# Extract first artist from collaborations (e.g. "Artist A feat. Artist B" → "Artist A")
SCROBBLED_FIRSTARTISTONLY=True
FIRST_ARTIST_WHITELIST=["Simon & Garfunkel", "AC/DC"]  # Keep these exact
```

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

## Cache Management

View cache status: `python cache_info.py --info`

Reset sync status: `python cache_info.py --reset`

## Troubleshooting

**Setup issues:** `python check_setup.py`

**First run slow:** Normal - fetches all historical scrobbles. Subsequent runs are fast!

## License

MIT License - see LICENSE file for details.