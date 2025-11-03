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

    # Exact or prefix match against whitelist (case-insensitive)
    artist_lower = artist_clean.lower()
    for whitelisted in FIRST_ARTIST_WHITELIST:
        wl = (whitelisted or "").strip()
        if not wl:
            continue
        wl_lower = wl.lower()
        if artist_lower == wl_lower or artist_lower.startswith(wl_lower):
            return wl  # preserve canonical casing from whitelist

    # Fallback: split on common separators (feat., &, ',', '/', '-', with, bullet point, etc.)
    # The bullet point (•) is handled separately as it may not have spaces around it
    sep_pattern = re.compile(
        r"\s+(feat\.?|ft\.?|featuring|&|;|,|/|-|x|vs\.?|and|mit|met|with)\s+|•",
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

def make_key_lastfm(artist, title):
    """Create a normalized key for Last.fm scrobbles.
    
    Does NOT apply first_artist() logic - preserves full artist name from Last.fm.
    """
    return (normalize(artist), normalize(title))

def make_key_navidrome(artist, title):
    """Create a normalized key for Navidrome tracks.
    
    Applies first_artist() logic if SCROBBLED_FIRSTARTISTONLY=True.
    This normalizes Navidrome collaborations (e.g., "2Pac feat Damascus" → "2Pac")
    to match Last.fm scrobbles where user only scrobbles first artist.
    """
    return (normalize(first_artist(artist)), normalize(title))

def aggregate_scrobbles(scrobbles):
    """Aggregate scrobbles by artist/track key with timestamps and loved status."""
    aggregated = {}
    for s in scrobbles:
        key = make_key_lastfm(s['artist'], s['track'])
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

def group_missing_by_artist_album(aggregated_scrobbles, tracks, cache):
    """Group scrobbles that are missing from Navidrome by artist and album.
    
    Args:
        aggregated_scrobbles: Dict of Last.fm scrobbles aggregated by artist/track
        tracks: List of Navidrome tracks
        cache: ScrobbleCache instance to check for fuzzy match mappings
    
    Returns:
        Tuple of (missing_scrobbles_grouped, missing_loved_grouped)
    """
    nav_keys = set(make_key_navidrome(t['artist'], t['title']) for t in tracks)
    
    # Get all fuzzy match mappings to check if Last.fm tracks are matched
    fuzzy_matches = cache.get_all_fuzzy_matches()
    
    # Build a set of Last.fm keys that have been fuzzy-matched to Navidrome tracks
    # Format: (normalized_lastfm_artist, normalized_lastfm_track)
    fuzzy_matched_lastfm_keys = set()
    for match in fuzzy_matches:
        lastfm_key = make_key_lastfm(match['lastfm_artist'], match['lastfm_track'])
        fuzzy_matched_lastfm_keys.add(lastfm_key)

    missing_scrobbles = {}
    missing_loved = {}

    for key, info in aggregated_scrobbles.items():
        # Skip if track exists in Navidrome (exact match) OR has been fuzzy-matched
        if key in nav_keys or key in fuzzy_matched_lastfm_keys:
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