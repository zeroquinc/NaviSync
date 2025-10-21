# NaviSync

NaviSync is a Python tool that syncs play counts and loved track information from Last.fm to a Navidrome server. It ensures your local music library in Navidrome reflects your listening history on Last.fm.

## Features

- Fetches all recent tracks from Last.fm for a given user.
- Compares Last.fm scrobbles with Navidrome play counts.
- Detects tracks that are missing or have different play counts.
- Updates Navidrome play counts and loved/starred status.
- Generates JSON reports for missing or loved tracks.

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

**Before running, always make sure your Navidrome db file has a backup and is not running!**
**I am not responsible for any damage to your Navidrome server.**

Run the script:

`python main.py`

- Or run start.bat if on Windows.

The script will:

1. Load your Navidrome database and Last.fm scrobbles and loved tracks.
2. Compare the data and generate reports for missing and loved tracks.
3. Update Navidrome play counts and loved tracks from Last.fm.

Generated JSON files are saved in the `json/` folder:

- `json/missing_scrobbles.json` – tracks in Last.fm but missing from Navidrome.
- `json/missing_loved.json` – loved tracks in Last.fm but missing from Navidrome.

## Configuration

- Use the `.env` file for API keys, database path, and artist whitelist.
- The artist whitelist allows you to control which artists are treated as the “first artist” in collaborations.

## License

This project is licensed under the MIT License. See the LICENSE file for details.