import re
from datetime import datetime, timezone

from .config import FIRST_ARTIST_WHITELIST, SCROBBLED_FIRSTARTISTONLY

def normalize(s):
    """Normalize a string for comparison (lowercase, trimmed)."""
    return s.strip().lower() if s else ""

def first_artist(artist):
    """Extract the primary artist from a collaboration string."""
    if not artist:
        return ""
    artist_clean = artist.strip()

    # Short-circuit: if not applying first-artist-only logic, return as-is
    if not SCROBBLED_FIRSTARTISTONLY:
        return artist_clean

    # Exact match against whitelist (case-insensitive)
    # The whitelist is for artists you want preserved with specific casing
    # Only exact matches should trigger preservation (no prefix matching)
    artist_lower = artist_clean.lower()
    for whitelisted in FIRST_ARTIST_WHITELIST:
        wl = (whitelisted or "").strip()
        if not wl:
            continue
        wl_lower = wl.lower()
        if artist_lower == wl_lower:
            return wl  # preserve canonical casing from whitelist

    # Fallback: split on common separators (feat., &, +, ',', '/', '-', with, bullet point, etc.)
    # Use word boundaries for multi-letter separators to prevent matching inside artist names
    # Patterns ordered by actual frequency in database: &(112), feat(95), featuring(15), ft(10), and(8)
    sep_pattern = re.compile(
        r"\s+(\bfeat\.?|\bft\.?|\bfeaturing\b|&|\+|;|,|/|\-|\bvs\.?|\band\b|\bwith\b|"
        r"\bmit\b|\bmet\b|\bx\b|\bremix\b|\bversus\b)\s+",
        flags=re.IGNORECASE,
    )
    return sep_pattern.split(artist_clean)[0].strip()

def make_key(artist, title):
    """Create a normalized key for matching artist/title combinations.
    
    This applies first_artist() logic to Last.fm data only.
    Use make_key_lastfm() for Last.fm scrobbles.
    Use make_key_navidrome() for Navidrome tracks.
    """
    return (normalize(first_artist(artist)), normalize(title))

def make_key_lastfm(artist, title, album=None, album_aware=False):
    """Create a normalized key for Last.fm scrobbles.
    
    Does NOT apply first_artist() logic - preserves the actual artist name from Last.fm.
    This is important because Last.fm scrobbles should be matched as they were actually scrobbled,
    even if SCROBBLED_FIRSTARTISTONLY is enabled (which only affects future scrobbles).
    
    Args:
        artist: Artist name from Last.fm
        title: Track title from Last.fm
        album: Album name from Last.fm (optional)
        album_aware: If True, include album in key for album-specific matching
    """
    if album_aware:
        # Always use 3-tuple in album_aware mode, even for empty albums
        # This ensures consistent key structure
        return (normalize(artist), normalize(title), normalize(album or ''))
    return (normalize(artist), normalize(title))

def make_key_navidrome(artist, title, album=None, album_aware=False):
    """Create a normalized key for Navidrome tracks.
    
    Applies first_artist() logic if SCROBBLED_FIRSTARTISTONLY=True.
    This normalizes Navidrome collaborations (e.g., "2Pac feat Damascus" → "2Pac")
    to match Last.fm scrobbles where user only scrobbles first artist.
    
    Args:
        artist: Artist name from Navidrome
        title: Track title from Navidrome
        album: Album name from Navidrome (optional)
        album_aware: If True, include album in key for album-specific matching
    """
    if album_aware:
        # Always use 3-tuple in album_aware mode, even for empty albums
        # This ensures consistent key structure
        return (normalize(first_artist(artist)), normalize(title), normalize(album or ''))
    return (normalize(first_artist(artist)), normalize(title))

def aggregate_scrobbles(scrobbles, album_aware=False):
    """Aggregate scrobbles by artist/track key with timestamps and loved status.
    
    Args:
        scrobbles: List of scrobble dicts from Last.fm
        album_aware: If True, aggregate by artist/track/album instead of just artist/track
    """
    aggregated = {}
    for s in scrobbles:
        key = make_key_lastfm(s['artist'], s['track'], s.get('album', ''), album_aware)
        aggregated.setdefault(key, {
            'timestamps': [],
            'loved': False,
            'artist_orig': s['artist'],
            'track_orig': s['track'],
            'album_orig': s.get('album', '')
        })
        aggregated[key]['timestamps'].append(s['timestamp'])
        if s['loved']:
            aggregated[key]['loved'] = True
    return aggregated

def group_missing_by_artist_album(aggregated_scrobbles, tracks, cache, album_aware=False):
    """Group scrobbles that are missing from Navidrome by artist and album.
    
    Args:
        aggregated_scrobbles: Dict of Last.fm scrobbles aggregated by artist/track or artist/track/album
        tracks: List of Navidrome tracks
        cache: ScrobbleCache instance to check for fuzzy match mappings
        album_aware: Whether album information was used in aggregation keys
    
    Returns:
        Tuple of (missing_scrobbles_grouped, missing_loved_grouped)
    """
    nav_keys = set(make_key_navidrome(t['artist'], t['title'], t.get('album'), album_aware) for t in tracks)
    
    # Pre-compute album-agnostic keys once for performance (avoid O(n²) in loop)
    nav_keys_album_agnostic = set(
        make_key_navidrome(t['artist'], t['title'], None, False) for t in tracks
    ) if album_aware else set()
    
    # Get all fuzzy match mappings to check if Last.fm tracks are matched
    fuzzy_matches = cache.get_all_fuzzy_matches()
    
    # Build a set of Last.fm keys that have been fuzzy-matched to Navidrome tracks
    fuzzy_matched_lastfm_keys = set()
    for match in fuzzy_matches:
        # For fuzzy matches, we don't have album info stored, so use album_aware=False
        lastfm_key = make_key_lastfm(match['lastfm_artist'], match['lastfm_track'], None, False)
        fuzzy_matched_lastfm_keys.add(lastfm_key)
        
        # Also check album-aware variant if we're in album-aware mode
        if album_aware:
            album_aware_key = make_key_lastfm(match['lastfm_artist'], match['lastfm_track'], "", album_aware)
            fuzzy_matched_lastfm_keys.add(album_aware_key)

    missing_scrobbles = {}
    missing_loved = {}

    for key, info in aggregated_scrobbles.items():
        # For album-aware mode, check both exact match and album-agnostic match
        exact_match = key in nav_keys
        album_agnostic_match = False
        
        if album_aware and not exact_match:
            # Check if there's a match ignoring album (using pre-computed set)
            album_agnostic_key = make_key_navidrome(info['artist_orig'], info['track_orig'], None, False)
            album_agnostic_match = album_agnostic_key in nav_keys_album_agnostic
        
        # Also check fuzzy matches
        fuzzy_match = key in fuzzy_matched_lastfm_keys
        
        # Skip if track exists in Navidrome (exact or album-agnostic match) OR has been fuzzy-matched
        if exact_match or album_agnostic_match or fuzzy_match:
            continue
            
        artist = info['artist_orig']
        track = info['track_orig']
        album = info['album_orig'] or ""
        scrobble_count = len(info['timestamps'])
        last_played_ts = max(info['timestamps'])
        last_played_str = datetime.fromtimestamp(
            last_played_ts, timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S")

        track_entry = {
            "track": track,
            "scrobbled": scrobble_count,
            "loved": info["loved"],
            "lastplayed": last_played_str,
        }

        missing_scrobbles.setdefault(artist, {}).setdefault(album, []).append(track_entry)

        if info["loved"]:
            missing_loved.setdefault(artist, {}).setdefault(album, []).append(track_entry)

    # Sort by artist name alphabetically, and albums within each artist
    missing_scrobbles = {artist: dict(sorted(albums.items())) 
                         for artist, albums in sorted(missing_scrobbles.items())}
    missing_loved = {artist: dict(sorted(albums.items())) 
                     for artist, albums in sorted(missing_loved.items())}

    return missing_scrobbles, missing_loved