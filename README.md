# NaviSync

Sync your Navidrome play counts and loved tracks from Last.fm. Two modes, one goal: keep Navidrome in sync with what you actually listened to.

## What it does

- Fetches your Last.fm scrobbles and loved tracks into a local SQLite cache
- Compares them with Navidrome
- Updates Navidrome either by writing to its DB (DB mode) or by calling its API (API-only mode)
- Generates JSON reports for tracks missing in Navidrome

## Modes

- DB mode
	- Directly updates the Navidrome SQLite database
	- Supports decreases and full conflict policies
    - Stars loved tracks
	- Requires stopping Navidrome while writing (recommended) and a DB backup
    - Much faster (almost instant) updates, recommended to run at first run

- API-only mode (default)
	- Never touches the Navidrome DB
    - It's recommend to disable any scrobbling to other services. I need more testers on this part to see if it actually forwards the scrobbles to other services. So be cautious with this.
	- Scrobbles only the missing plays (increases-only) via Subsonic/OpenSubsonic API
	- Stars loved tracks via API
	- Safe to run while Navidrome is online
    - Much slower with big libraries and a lot of scrobbles (much faster on second run and forward)

## Installation

1) Clone and enter the repo

```bash
git clone https://github.com/zeroquinc/NaviSync.git
cd NaviSync
```

2) Create and activate a virtualenv (optional but recommended)

```bash
python -m venv .venv
. .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
```

3) Install dependencies

```bash
pip install -r requirements.txt
```

4) Copy `env.example` to `.env` and fill in your values

```bash
cp env.example .env  # On Windows: copy env.example .env
```

## Configuration

Common (both modes):
- LASTFM_API_KEY, LASTFM_USER
- Matching controls:
	- SCROBBLED_FIRSTARTISTONLY=True/False
	- FIRST_ARTIST_WHITELIST=["Suzan & Freek", "Simon & Garfunkel", "AC/DC"]

DB mode (USE_NAVIDROME_API=False):
- NAVIDROME_DB_PATH must point to your Navidrome SQLite DB
- PLAYCOUNT_CONFLICT_RESOLUTION controls how differences are resolved:
	- ask | navidrome | lastfm | higher | increment

API-only mode (USE_NAVIDROME_API=True):
- NAVIDROME_URL, NAVIDROME_API_USER, NAVIDROME_API_PASSWORD
- Optional: NAVIDROME_API_VERSION, NAVIDROME_CLIENT_ID, API_RATE_LIMIT_MS
- PLAYCOUNT_CONFLICT_RESOLUTION is ignored in API mode (increases-only)

Refer to `env.example` for a complete, commented set of variables.

## Usage

First-time setup check:

```bash
python check_setup.py
```

Run the sync:

```bash
python main.py
```

Or on Windows you can run `start.bat`. This will automaticly create a virtual environment and installs the required packages.

### Running in DB mode

- Ensure `USE_NAVIDROME_API=False` and `NAVIDROME_DB_PATH` is set
- **Stop Navidrome and back up the DB before writing**!!
- Choose your `PLAYCOUNT_CONFLICT_RESOLUTION` policy
- The tool will:
	- Read tracks and annotations from the DB
	- Compare with your Last.fm cache
	- Apply updates (including decreases if your policy allows)
	- Write results back to the DB and mark loved

### Running in API-only mode

- Set `USE_NAVIDROME_API=True` (default) and provide API credentials/URL
- Navidrome can stay online; no DB file access is needed
- The tool will:
	- Resolve tracks by searching Navidrome via API (cached for speed)
	- Scrobble only the missing timestamps to increase play counts
	- Star loved tracks via API
	- Never decrease counts

## How it works (quick)

1) Caches your Last.fm scrobbles locally (incremental after first run)
2) Aggregates scrobbles by normalized Artist+Title
3) Compares to Navidrome (DB reads in DB mode; API lookups in API mode with local mapping cache)
4) Applies updates according to the selected mode

## Outputs

- `json/missing_scrobbles.json` – tracks scrobbled on Last.fm but missing in Navidrome
- `json/missing_loved.json` – loved tracks on Last.fm that aren’t in Navidrome
- `cache/scrobbles_cache.db` – local cache of your scrobbles and loved status

## Playcount conflict resolution (DB mode only)

- ask: prompt for each conflict
- navidrome: keep Navidrome when it’s higher
- lastfm: always use Last.fm
- higher: use whichever is higher
- increment: add Last.fm to Navidrome (Nav:20 + LFM:15 = 35)

Note: In API-only mode, decreases aren’t possible; conflicts where Navidrome > Last.fm are logged and skipped.

## Tips & troubleshooting

- First run fetches all scrobbles and can take a while; subsequent runs are incremental
- If matching merges collabs too aggressively, disable `SCROBBLED_FIRSTARTISTONLY` or set a whitelist
- If you hit API rate limits, increase `API_RATE_LIMIT_MS`
- To force a fresh sync comparison, use the cache utilities below

## Cache utilities

View cache info:

```bash
python cache_info.py --info
```

Interactive cache maintenance:

```bash
python cache_info.py
```

Reset all sync flags (forces re-sync):

```bash
python cache_info.py --reset
```

## License

MIT — see `LICENSE`.