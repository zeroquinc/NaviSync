import hashlib
import random
import string
import time
from typing import Dict, Optional

import requests

from .config import (
    NAVIDROME_URL,
    NAVIDROME_API_USER,
    NAVIDROME_API_PASSWORD,
    NAVIDROME_API_VERSION,
    NAVIDROME_CLIENT_ID,
    API_RATE_LIMIT_MS,
)

class NavidromeAPI:
    """Minimal Subsonic/OpenSubsonic client for Navidrome operations we need.

    Implements:
    - scrobble(): submit plays
    - star_track(): star a track (mark loved)
    - unstar_track(): unstar a track
    """

    def __init__(self, base_url: Optional[str] = None, user: Optional[str] = None, password: Optional[str] = None):
        self.base_url = (base_url or NAVIDROME_URL).rstrip('/')
        self.user = user or NAVIDROME_API_USER
        self.password = password or NAVIDROME_API_PASSWORD
        self.api_version = NAVIDROME_API_VERSION
        self.client_id = NAVIDROME_CLIENT_ID
        self.session = requests.Session()
        self._last_call = 0.0

    def _salt_and_token(self) -> Dict[str, str]:
        salt = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        token = hashlib.md5((self.password + salt).encode('utf-8')).hexdigest()
        return {"s": salt, "t": token}

    def _auth_params(self) -> Dict[str, str]:
        p = {"u": self.user, "v": self.api_version, "c": self.client_id, "f": "json"}
        p.update(self._salt_and_token())
        return p

    def _rate_limit(self):
        # Simple client-side rate limiting
        delay = max(0.0, (API_RATE_LIMIT_MS / 1000.0) - (time.time() - self._last_call))
        if delay > 0:
            time.sleep(delay)
        self._last_call = time.time()

    def scrobble(self, song_id: str, timestamp_ms: int, submission: bool = True) -> bool:
        """Submit a scrobble for a song id at a specific timestamp (ms).

        Returns True if Navidrome accepted the call (HTTP 200 and no error in response).
        """
        self._rate_limit()
        url = f"{self.base_url}/rest/scrobble.view"
        params = {
            **self._auth_params(),
            "id": song_id,
            "time": int(timestamp_ms),
            "submission": "true" if submission else "false",
        }
        try:
            r = self.session.get(url, params=params, timeout=15)
            if r.status_code != 200:
                print(f"  ⚠️  scrobble HTTP {r.status_code} for id={song_id}")
                return False
            data = r.json()
            if data.get('subsonic-response', {}).get('status') != 'ok':
                print(f"  ⚠️  scrobble API error: {data}")
                return False
            return True
        except requests.RequestException as e:
            print(f"  ⚠️  scrobble request failed: {e}")
            return False

    def update_play_count(self, song_id: str, play_count: int) -> bool:
        """
        Update the play count for a track directly in Navidrome's database.
        This does NOT trigger Last.fm scrobbling, unlike scrobble().
        """
        self._rate_limit()
        url = f"{self.base_url}/rest/updatePlayCount"
        params = {
            **self._auth_params(),
            "id": song_id,
            "count": play_count,
        }
        try:
            r = self.session.get(url, params=params, timeout=15)
            if r.status_code != 200:
                print(f"  ⚠️  updatePlayCount HTTP {r.status_code} for id={song_id}")
                return False
            data = r.json()
            if data.get('subsonic-response', {}).get('status') != 'ok':
                print(f"  ⚠️  updatePlayCount API error: {data}")
                return False
            return True
        except requests.RequestException as e:
            print(f"  ⚠️  updatePlayCount request failed: {e}")
            return False

    def star_track(self, song_id: str) -> bool:
        """Star (love) a track by its song id."""
        self._rate_limit()
        url = f"{self.base_url}/rest/star.view"
        params = {**self._auth_params(), "id": song_id}
        try:
            r = self.session.get(url, params=params, timeout=15)
            if r.status_code != 200:
                print(f"  ⚠️  star HTTP {r.status_code} for id={song_id}")
                return False
            data = r.json()
            if data.get('subsonic-response', {}).get('status') != 'ok':
                print(f"  ⚠️  star API error: {data}")
                return False
            return True
        except requests.RequestException as e:
            print(f"  ⚠️  star request failed: {e}")
            return False

    def unstar_track(self, song_id: str) -> bool:
        """Unstar a track by its song id."""
        self._rate_limit()
        url = f"{self.base_url}/rest/unstar.view"
        params = {**self._auth_params(), "id": song_id}
        try:
            r = self.session.get(url, params=params, timeout=15)
            if r.status_code != 200:
                print(f"  ⚠️  unstar HTTP {r.status_code} for id={song_id}")
                return False
            data = r.json()
            if data.get('subsonic-response', {}).get('status') != 'ok':
                print(f"  ⚠️  unstar API error: {data}")
                return False
            return True
        except requests.RequestException as e:
            print(f"  ⚠️  unstar request failed: {e}")
            return False

    def search_songs(self, query: str, song_count: int = 10, song_offset: int = 0):
        """Search for songs using Subsonic search2 API. Returns list of song dicts."""
        self._rate_limit()
        url = f"{self.base_url}/rest/search2.view"
        params = {**self._auth_params(), "query": query, "songCount": song_count, "songOffset": song_offset}
        try:
            r = self.session.get(url, params=params, timeout=15)
            if r.status_code != 200:
                print(f"  ⚠️  search2 HTTP {r.status_code} for query={query}")
                return []
            data = r.json()
            resp = data.get('subsonic-response', {})
            if resp.get('status') != 'ok':
                print(f"  ⚠️  search2 API error: {data}")
                return []
            songs = resp.get('searchResult2', {}).get('song', [])
            if isinstance(songs, dict):
                songs = [songs]
            return songs or []
        except requests.RequestException as e:
            print(f"  ⚠️  search2 request failed: {e}")
            return []

    def get_song(self, song_id: str):
        """Get a single song's details. Returns dict or None."""
        self._rate_limit()
        url = f"{self.base_url}/rest/getSong.view"
        params = {**self._auth_params(), "id": song_id}
        try:
            r = self.session.get(url, params=params, timeout=15)
            if r.status_code != 200:
                print(f"  ⚠️  getSong HTTP {r.status_code} for id={song_id}")
                return None
            data = r.json()
            resp = data.get('subsonic-response', {})
            if resp.get('status') != 'ok':
                print(f"  ⚠️  getSong API error: {data}")
                return None
            song = resp.get('song')
            return song
        except requests.RequestException as e:
            print(f"  ⚠️  getSong request failed: {e}")
            return None

    # --- Library listing helpers (for API-only preindex) ---
    def get_artists(self):
        """Return list of artist dicts using getArtists.view (OpenSubsonic)."""
        self._rate_limit()
        url = f"{self.base_url}/rest/getArtists.view"
        params = {**self._auth_params()}
        try:
            r = self.session.get(url, params=params, timeout=20)
            if r.status_code != 200:
                print(f"  ⚠️  getArtists HTTP {r.status_code}")
                return []
            data = r.json()
            resp = data.get('subsonic-response', {})
            if resp.get('status') != 'ok':
                print(f"  ⚠️  getArtists API error: {data}")
                return []
            artists_root = resp.get('artists', {})
            indexes = artists_root.get('index', [])
            artists = []
            if isinstance(indexes, dict):
                indexes = [indexes]
            for idx in indexes:
                lst = idx.get('artist', [])
                if isinstance(lst, dict):
                    lst = [lst]
                artists.extend(lst)
            return artists
        except requests.RequestException as e:
            print(f"  ⚠️  getArtists request failed: {e}")
            return []

    def get_artist(self, artist_id: str):
        """Return artist details including albums via getArtist.view."""
        self._rate_limit()
        url = f"{self.base_url}/rest/getArtist.view"
        params = {**self._auth_params(), "id": artist_id}
        try:
            r = self.session.get(url, params=params, timeout=20)
            if r.status_code != 200:
                print(f"  ⚠️  getArtist HTTP {r.status_code} for id={artist_id}")
                return None
            data = r.json()
            resp = data.get('subsonic-response', {})
            if resp.get('status') != 'ok':
                print(f"  ⚠️  getArtist API error: {data}")
                return None
            return resp.get('artist')
        except requests.RequestException as e:
            print(f"  ⚠️  getArtist request failed: {e}")
            return None

    def get_album(self, album_id: str):
        """Return album details including songs via getAlbum.view."""
        self._rate_limit()
        url = f"{self.base_url}/rest/getAlbum.view"
        params = {**self._auth_params(), "id": album_id}
        try:
            r = self.session.get(url, params=params, timeout=20)
            if r.status_code != 200:
                print(f"  ⚠️  getAlbum HTTP {r.status_code} for id={album_id}")
                return None
            data = r.json()
            resp = data.get('subsonic-response', {})
            if resp.get('status') != 'ok':
                print(f"  ⚠️  getAlbum API error: {data}")
                return None
            return resp.get('album')
        except requests.RequestException as e:
            print(f"  ⚠️  getAlbum request failed: {e}")
            return None

    def iterate_all_songs(self):
        """Generator yielding unique song dicts by walking artists -> albums -> songs.

        Some tracks can appear under multiple artists (e.g., collaborations or singles
        credited to multiple album artists). To avoid processing the same track multiple
        times in API mode, we de-duplicate by song id here.
        """
        artists = self.get_artists() or []
        seen_song_ids = set()
        for a in artists:
            a_id = a.get('id')
            if not a_id:
                continue
            art = self.get_artist(str(a_id))
            if not art:
                continue
            albums = art.get('album', [])
            if isinstance(albums, dict):
                albums = [albums]
            for al in albums:
                al_id = al.get('id')
                if not al_id:
                    continue
                alb = self.get_album(str(al_id))
                if not alb:
                    continue
                songs = alb.get('song', [])
                if isinstance(songs, dict):
                    songs = [songs]
                for s in songs or []:
                    sid = s.get('id')
                    if not sid:
                        continue
                    if sid in seen_song_ids:
                        continue
                    seen_song_ids.add(sid)
                    yield s
