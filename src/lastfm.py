import requests
import time
from .config import LASTFM_API_KEY, LASTFM_USER

MAX_RETRIES = 5
RETRY_DELAY = 5
REQUEST_DELAY = 0.2

def fetch_lastfm_page(url):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                return r.json()
            print(f"  Failed page {url} (status {r.status_code}), attempt {attempt}/{MAX_RETRIES}")
        except requests.RequestException as e:
            print(f"  Request exception on page {url}, attempt {attempt}/{MAX_RETRIES}: {e}")
        time.sleep(RETRY_DELAY)
    print(f"  Skipping page {url} after {MAX_RETRIES} failed attempts.")
    return None

def fetch_all_lastfm_scrobbles():
    print("Fetching all Last.fm scrobbles...")
    scrobbles = []
    page = 1
    total_pages = 1
    now = int(time.time())

    while page <= total_pages:
        url = (
            f"http://ws.audioscrobbler.com/2.0/?method=user.getRecentTracks"
            f"&user={LASTFM_USER}&api_key={LASTFM_API_KEY}"
            f"&from=0&to={now}&limit=200&page={page}&extended=1&format=json"
        )
        data = fetch_lastfm_page(url)
        if not data:
            page += 1
            continue

        recent = data.get('recenttracks', {}).get('track', [])
        if isinstance(recent, dict):
            recent = [recent]

        for t in recent:
            date_info = t.get('date', {})
            artist_info = t.get('artist', {})
            album_info = t.get('album', {})
            artist_name = artist_info.get('name') or artist_info.get('#text') if isinstance(artist_info, dict) else str(artist_info)
            album_name = album_info.get('#text', '') if isinstance(album_info, dict) else ''
            loved = str(t.get('loved', '0')) == '1'

            if 'uts' in date_info:
                scrobbles.append({
                    'artist': artist_name,
                    'album': album_name,
                    'track': t.get('name', ''),
                    'timestamp': int(date_info['uts']),
                    'loved': loved
                })

        total_pages = int(data.get('recenttracks', {}).get('@attr', {}).get('totalPages', 1))
        print(f"  Fetched page {page}/{total_pages}, total scrobbles: {len(scrobbles)}")
        page += 1
        time.sleep(REQUEST_DELAY)
    print(f"Finished fetching {len(scrobbles)} scrobbles.\n")
    return scrobbles