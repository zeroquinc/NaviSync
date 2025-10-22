import requests
import time
from .config import LASTFM_API_KEY, LASTFM_USER

MAX_RETRIES = 5
RETRY_DELAY = 5
REQUEST_DELAY = 0.2

def fetch_lastfm_page(url):
    """Fetch a single page from Last.fm API with retry logic."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                # Check for Last.fm API errors
                if 'error' in data:
                    print(f"  ❌ Last.fm API error: {data.get('message', 'Unknown error')}")
                    return None
                return data
            elif r.status_code == 429:  # Rate limited
                wait_time = RETRY_DELAY * attempt
                print(f"  ⚠️  Rate limited, waiting {wait_time}s before retry...")
                time.sleep(wait_time)
                continue
            print(f"  ⚠️  Failed (status {r.status_code}), attempt {attempt}/{MAX_RETRIES}")
        except requests.RequestException as e:
            print(f"  ⚠️  Request error, attempt {attempt}/{MAX_RETRIES}: {e}")
        except ValueError as e:  # JSON decode error
            print(f"  ⚠️  Invalid JSON response, attempt {attempt}/{MAX_RETRIES}: {e}")
        
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)
    
    print(f"  ❌ Skipping page after {MAX_RETRIES} failed attempts.")
    return None

def fetch_all_lastfm_scrobbles(from_timestamp=0):
    """
    Fetch Last.fm scrobbles from a specific timestamp onwards.
    If from_timestamp > 0, fetches only new scrobbles since that time.
    This allows incremental updates instead of fetching everything.
    """
    if from_timestamp > 0:
        print(f"Fetching new Last.fm scrobbles since {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(from_timestamp))}...")
    else:
        print("Fetching all Last.fm scrobbles...")
    
    scrobbles = []
    page = 1
    total_pages = 1
    now = int(time.time())
    stop_fetching = False

    while page <= total_pages and not stop_fetching:
        url = (
            f"http://ws.audioscrobbler.com/2.0/?method=user.getRecentTracks"
            f"&user={LASTFM_USER}&api_key={LASTFM_API_KEY}"
            f"&from={from_timestamp}&to={now}&limit=200&page={page}&extended=1&format=json"
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
                timestamp = int(date_info['uts'])
                
                # If we've reached scrobbles older than our cache, stop
                if from_timestamp > 0 and timestamp <= from_timestamp:
                    stop_fetching = True
                    break
                
                scrobbles.append({
                    'artist': artist_name,
                    'album': album_name,
                    'track': t.get('name', ''),
                    'timestamp': timestamp,
                    'loved': loved
                })

        total_pages = int(data.get('recenttracks', {}).get('@attr', {}).get('totalPages', 1))
        print(f"  Fetched page {page}/{total_pages}, new scrobbles: {len(scrobbles)}")
        
        if stop_fetching:
            print(f"  Reached cached scrobbles, stopping early.")
            break
        
        page += 1
        time.sleep(REQUEST_DELAY)
    
    print(f"Finished fetching {len(scrobbles)} new scrobbles.\n")
    return scrobbles

def fetch_loved_tracks():
    """
    Fetch all loved tracks from Last.fm using the user.getLovedTracks method.
    This is more reliable for tracking loved status than relying on recenttracks.
    """
    print("Fetching loved tracks from Last.fm...")
    loved_tracks = []
    page = 1
    total_pages = 1

    while page <= total_pages:
        url = (
            f"http://ws.audioscrobbler.com/2.0/?method=user.getLovedTracks"
            f"&user={LASTFM_USER}&api_key={LASTFM_API_KEY}"
            f"&limit=200&page={page}&format=json"
        )
        data = fetch_lastfm_page(url)
        if not data:
            page += 1
            continue

        loved = data.get('lovedtracks', {}).get('track', [])
        if isinstance(loved, dict):
            loved = [loved]

        for t in loved:
            artist_info = t.get('artist', {})
            artist_name = artist_info.get('name') or artist_info.get('#text') if isinstance(artist_info, dict) else str(artist_info)
            
            date_info = t.get('date', {})
            timestamp = int(date_info.get('uts', 0)) if date_info else None
            
            loved_tracks.append({
                'artist': artist_name,
                'track': t.get('name', ''),
                'timestamp': timestamp
            })

        total_pages = int(data.get('lovedtracks', {}).get('@attr', {}).get('totalPages', 1))
        print(f"  Fetched page {page}/{total_pages}, total loved tracks: {len(loved_tracks)}")
        page += 1
        time.sleep(REQUEST_DELAY)
    
    print(f"Finished fetching {len(loved_tracks)} loved tracks.\n")
    return loved_tracks