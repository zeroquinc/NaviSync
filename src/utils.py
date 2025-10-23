import re
from datetime import datetime, timezone

from .config import FIRST_ARTIST_WHITELIST, SCROBBLED_FIRSTARTISTONLY

def normalize(s):
    """Normalize a string for comparison (lowercase, trimmed, whitespace collapsed)."""
    if not s:
        return ""
    # Collapse whitespace and normalize case only
    result = " ".join(s.split())
    return result.strip().lower()

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

    # Fallback: split on common separators (feat., &, ',', '/', '-', with, etc.)
    sep_pattern = re.compile(
        r"\s+(feat\.?|ft\.?|featuring|&|;|,|/|-|mit|met|with)\s+",
        flags=re.IGNORECASE,
    )
    return sep_pattern.split(artist_clean)[0].strip()

def make_key(artist, title):
    """Create a normalized key for matching artist/title combinations."""
    return (normalize(first_artist(artist)), normalize(title))

def aggregate_scrobbles(scrobbles):
    """Aggregate scrobbles by artist/track key with timestamps and loved status."""
    aggregated = {}
    for s in scrobbles:
        key = make_key(s['artist'], s['track'])
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

def group_missing_by_artist_album(aggregated_scrobbles, tracks):
    """Group scrobbles that are missing from Navidrome by artist and album."""
    nav_keys = set(make_key(t['artist'], t['title']) for t in tracks)

    missing_scrobbles = {}
    missing_loved = {}

    for key, info in aggregated_scrobbles.items():
        if key not in nav_keys:
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

    return missing_scrobbles, missing_loved