"""
Navidrome API client for scrobbling and managing play counts.
Uses the Subsonic API that Navidrome implements.
"""

import requests
from requests.adapters import HTTPAdapter
import hashlib
import time
from typing import Optional, List, Dict, Tuple, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

class NavidromeAPI:
    def __init__(self, base_url: str, username: str, password: str):
        """
        Initialize Navidrome API client.
        
        Args:
            base_url: Base URL of Navidrome server (e.g., "http://localhost:4533")
            username: Navidrome username
            password: Navidrome password (will be hashed)
        """
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.client_name = "NaviSync"
        self.api_version = "1.16.1"  # Subsonic API version
        
        # Create a session for connection reuse
        self.session = requests.Session()
        # Limit connection pool size to prevent port exhaustion
        adapter = HTTPAdapter(
            pool_connections=5,
            pool_maxsize=5,
            max_retries=3
        )
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        
    def _get_auth_params(self) -> dict:
        """Generate authentication parameters for API requests."""
        # Subsonic uses token-based auth: token = md5(password + salt)
        salt = str(int(time.time() * 1000))  # Use timestamp as salt
        token = hashlib.md5(f"{self.password}{salt}".encode()).hexdigest()
        
        return {
            'u': self.username,
            't': token,
            's': salt,
            'v': self.api_version,
            'c': self.client_name,
            'f': 'json'
        }
    
    def _make_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> dict:
        """Make a request to the Navidrome API."""
        if params is None:
            params = {}
        
        # Add authentication
        params.update(self._get_auth_params())
        
        url = f"{self.base_url}/rest/{endpoint}"
        
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            # Check for Subsonic API errors
            if 'subsonic-response' in data:
                subsonic_data = data['subsonic-response']
                if subsonic_data.get('status') == 'failed':
                    error = subsonic_data.get('error', {})
                    raise Exception(f"API Error {error.get('code')}: {error.get('message')}")
                return subsonic_data
            
            return data
            
        except requests.RequestException as e:
            raise Exception(f"Request failed: {e}")
    
    def ping(self) -> bool:
        """Test connection to Navidrome server."""
        try:
            result = self._make_request('ping.view')
            return result.get('status') == 'ok'
        except Exception:
            return False
    
    def search_track(self, artist: str, title: str) -> Optional[str]:
        """
        Search for a track by artist and title.
        Returns the track ID if found, None otherwise.
        """
        try:
            # Search using search3 endpoint (more precise)
            params = {
                'query': f"{artist} {title}",
            }
            result = self._make_request('search3.view', params)
            
            # Look through song results
            search_result = result.get('searchResult3', {})
            songs = search_result.get('song', [])
            
            if not songs:
                return None
            
            # Try to find exact match
            artist_lower = artist.lower().strip()
            title_lower = title.lower().strip()
            
            for song in songs:
                song_artist = song.get('artist', '').lower().strip()
                song_title = song.get('title', '').lower().strip()
                
                # Check for exact match
                if song_artist == artist_lower and song_title == title_lower:
                    return song['id']
            
            # If no exact match, return first result as best guess
            return songs[0]['id']
            
        except Exception as e:
            print(f"  ⚠️  Search error for '{artist} - {title}': {e}")
            return None
    
    def scrobble(self, track_id: str, timestamp: int) -> bool:
        """
        Submit a scrobble for a track.
        
        Args:
            track_id: Navidrome track ID
            timestamp: Unix timestamp (seconds) when the track was played
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Convert to milliseconds (Subsonic API uses milliseconds)
            timestamp_ms = timestamp * 1000
            
            params = {
                'id': track_id,
                'time': timestamp_ms,
                'submission': 'false'  # Don't forward to Last.fm, just update internal play count
            }
            
            result = self._make_request('scrobble.view', params)
            return result.get('status') == 'ok'
            
        except Exception as e:
            print(f"  ❌ Scrobble error: {e}")
            return False
    
    def star_track(self, track_id: str) -> bool:
        """
        Star (love) a track.
        
        Args:
            track_id: Navidrome track ID
            
        Returns:
            True if successful, False otherwise
        """
        try:
            params = {'id': track_id}
            result = self._make_request('star.view', params)
            return result.get('status') == 'ok'
        except Exception as e:
            print(f"  ❌ Star error: {e}")
            return False
    
    def get_track_info(self, track_id: str) -> Optional[dict]:
        """
        Get information about a track including play count.
        
        Args:
            track_id: Navidrome track ID
            
        Returns:
            Dictionary with track info or None if not found
        """
        try:
            params = {'id': track_id}
            result = self._make_request('getSong.view', params)
            song = result.get('song', {})
            
            return {
                'id': song.get('id'),
                'title': song.get('title'),
                'artist': song.get('artist'),
                'album': song.get('album'),
                'play_count': song.get('playCount', 0),
                'starred': song.get('starred') is not None,
            }
        except Exception:
            return None
    
    def bulk_scrobble_track(self, track_id: str, timestamps: List[int], 
                           rate_limit_delay: float = 0.1) -> Tuple[int, int]:
        """
        Submit multiple scrobbles for a single track.
        
        Args:
            track_id: Navidrome track ID
            timestamps: List of Unix timestamps (seconds) 
            rate_limit_delay: Delay between requests in seconds
            
        Returns:
            Tuple of (successful_count, failed_count)
        """
        successful = 0
        failed = 0
        
        for timestamp in timestamps:
            if self.scrobble(track_id, timestamp):
                successful += 1
            else:
                failed += 1
            
            # Rate limiting
            if rate_limit_delay > 0:
                time.sleep(rate_limit_delay)
        
        return successful, failed
    
    def search_tracks_parallel(self, tracks: List[Tuple[str, str]], 
                               max_workers: int = 3) -> Dict[Tuple[str, str], Optional[str]]:
        """
        Search for multiple tracks in parallel.
        
        Args:
            tracks: List of (artist, title) tuples
            max_workers: Maximum number of concurrent threads
            
        Returns:
            Dictionary mapping (artist, title) to track_id (or None if not found)
        """
        results = {}
        
        def search_single(artist: str, title: str) -> Tuple[Tuple[str, str], Optional[str]]:
            track_id = self.search_track(artist, title)
            # Small delay to prevent overwhelming the server
            time.sleep(0.05)
            return ((artist, title), track_id)
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all search tasks
            future_to_track = {
                executor.submit(search_single, artist, title): (artist, title)
                for artist, title in tracks
            }
            
            # Collect results as they complete with progress bar
            with tqdm(total=len(tracks), desc="Searching tracks", unit="track",
                     bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]') as pbar:
                
                for future in as_completed(future_to_track):
                    try:
                        (artist, title), track_id = future.result()
                        results[(artist, title)] = track_id
                    except Exception as e:
                        artist, title = future_to_track[future]
                        print(f"  ⚠️  Search error for '{artist} - {title}': {e}")
                        results[(artist, title)] = None
                    
                    pbar.update(1)
        
        return results
