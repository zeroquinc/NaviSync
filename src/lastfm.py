import requests
import time
import hashlib
from tqdm import tqdm
from .config import LASTFM_API_KEY, LASTFM_API_SECRET, LASTFM_SESSION_KEY, LASTFM_USER

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
    pbar = None

    while page <= total_pages and not stop_fetching:
        # Initialize progress bar once we know total pages
        if pbar is None and total_pages > 1:
            pbar = tqdm(total=total_pages, desc="Fetching scrobble pages", unit="page",
                       bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]')
        
        url = (
            f"http://ws.audioscrobbler.com/2.0/?method=user.getRecentTracks"
            f"&user={LASTFM_USER}&api_key={LASTFM_API_KEY}"
            f"&from={from_timestamp}&to={now}&limit=200&page={page}&extended=1&format=json"
        )
        data = fetch_lastfm_page(url)
        if not data:
            if pbar:
                pbar.update(1)
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
        
        # Update progress bar
        if pbar:
            pbar.set_postfix_str(f"{len(scrobbles)} scrobbles")
            pbar.update(1)
        elif total_pages == 1:
            # Single page - show simple progress
            print(f"  Fetched {len(scrobbles)} scrobbles from single page")
        
        if stop_fetching:
            if pbar:
                pbar.set_postfix_str(f"Reached cached data, stopped early")
            else:
                print(f"  Reached cached scrobbles, stopping early.")
            break
        
        page += 1
        time.sleep(REQUEST_DELAY)
    
    if pbar:
        pbar.close()
    
    print(f"✅ Fetched {len(scrobbles)} new scrobbles.\n")
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
    pbar = None

    while page <= total_pages:
        # Initialize progress bar once we know total pages
        if pbar is None and total_pages > 1:
            pbar = tqdm(total=total_pages, desc="Fetching loved tracks", unit="page",
                       bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]')
        
        url = (
            f"http://ws.audioscrobbler.com/2.0/?method=user.getLovedTracks"
            f"&user={LASTFM_USER}&api_key={LASTFM_API_KEY}"
            f"&limit=200&page={page}&format=json"
        )
        data = fetch_lastfm_page(url)
        if not data:
            if pbar:
                pbar.update(1)
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
        
        # Update progress bar
        if pbar:
            pbar.set_postfix_str(f"{len(loved_tracks)} loved tracks")
            pbar.update(1)
        elif total_pages == 1:
            # Single page - show simple progress
            print(f"  Fetched {len(loved_tracks)} loved tracks from single page")
        
        page += 1
        time.sleep(REQUEST_DELAY)
    
    if pbar:
        pbar.close()
    
    print(f"✅ Fetched {len(loved_tracks)} loved tracks.\n")
    return loved_tracks


def generate_api_signature(params, api_secret):
    """
    Generate API signature for authenticated Last.fm requests.
    
    Args:
        params: Dict of parameters (excluding format and api_sig)
        api_secret: Last.fm API secret
    
    Returns:
        MD5 hash signature string
    """
    # Sort parameters alphabetically and concatenate
    sorted_params = sorted(params.items())
    signature_string = ''.join(f'{k}{v}' for k, v in sorted_params)
    signature_string += api_secret
    
    # Return MD5 hash
    return hashlib.md5(signature_string.encode('utf-8')).hexdigest()


def get_session_key():
    """
    Interactive helper to obtain a Last.fm session key.
    This only needs to be run once to get the session key for your .env file.
    
    Steps:
    1. Get a token
    2. User authorizes the token via browser
    3. Exchange token for session key
    """
    if not LASTFM_API_KEY or not LASTFM_API_SECRET:
        print("❌ Error: LASTFM_API_KEY and LASTFM_API_SECRET must be set in .env file")
        return
    
    # Step 1: Get token
    print("📝 Step 1: Getting authentication token...")
    params = {
        'method': 'auth.getToken',
        'api_key': LASTFM_API_KEY
    }
    params['api_sig'] = generate_api_signature(params, LASTFM_API_SECRET)
    params['format'] = 'json'
    
    response = requests.get('http://ws.audioscrobbler.com/2.0/', params=params)
    data = response.json()
    
    if 'error' in data:
        print(f"❌ Error getting token: {data.get('message')}")
        return
    
    token = data['token']
    print(f"✅ Token obtained: {token}")
    
    # Step 2: User authorization
    auth_url = f"http://www.last.fm/api/auth/?api_key={LASTFM_API_KEY}&token={token}"
    print(f"\n📝 Step 2: Authorize this application")
    print(f"   Open this URL in your browser:")
    print(f"   {auth_url}")
    input("\n   Press Enter after you have authorized the application...")
    
    # Step 3: Get session key
    print("\n📝 Step 3: Getting session key...")
    params = {
        'method': 'auth.getSession',
        'api_key': LASTFM_API_KEY,
        'token': token
    }
    params['api_sig'] = generate_api_signature(params, LASTFM_API_SECRET)
    params['format'] = 'json'
    
    response = requests.get('http://ws.audioscrobbler.com/2.0/', params=params)
    data = response.json()
    
    if 'error' in data:
        print(f"❌ Error getting session: {data.get('message')}")
        return
    
    session_key = data['session']['key']
    print(f"\n✅ Session key obtained!")
    print(f"\n   Add this to your .env file:")
    print(f"   LASTFM_SESSION_KEY={session_key}")
    return session_key


def love_track(artist, track):
    """
    Mark a track as loved on Last.fm.
    
    Args:
        artist: Artist name
        track: Track name
    
    Returns:
        True if successful, False otherwise
    """
    if not LASTFM_API_KEY or not LASTFM_API_SECRET or not LASTFM_SESSION_KEY:
        return False
    
    params = {
        'method': 'track.love',
        'api_key': LASTFM_API_KEY,
        'sk': LASTFM_SESSION_KEY,
        'artist': artist,
        'track': track
    }
    params['api_sig'] = generate_api_signature(params, LASTFM_API_SECRET)
    params['format'] = 'json'
    
    try:
        response = requests.post('http://ws.audioscrobbler.com/2.0/', data=params, timeout=10)
        data = response.json()
        
        if 'error' in data:
            print(f"  ⚠️  Failed to love track on Last.fm: {data.get('message')}")
            return False
        
        return True
    except Exception as e:
        print(f"  ⚠️  Error loving track on Last.fm: {e}")
        return False


def unlove_track(artist, track):
    """
    Remove loved status from a track on Last.fm.
    
    Args:
        artist: Artist name
        track: Track name
    
    Returns:
        True if successful, False otherwise
    """
    if not LASTFM_API_KEY or not LASTFM_API_SECRET or not LASTFM_SESSION_KEY:
        return False
    
    params = {
        'method': 'track.unlove',
        'api_key': LASTFM_API_KEY,
        'sk': LASTFM_SESSION_KEY,
        'artist': artist,
        'track': track
    }
    params['api_sig'] = generate_api_signature(params, LASTFM_API_SECRET)
    params['format'] = 'json'
    
    try:
        response = requests.post('http://ws.audioscrobbler.com/2.0/', data=params, timeout=10)
        data = response.json()
        
        if 'error' in data:
            print(f"  ⚠️  Failed to unlove track on Last.fm: {data.get('message')}")
            return False
        
        return True
    except Exception as e:
        print(f"  ⚠️  Error unloving track on Last.fm: {e}")
        return False