import re

from .config import FIRST_ARTIST_WHITELIST, SCROBBLED_FIRSTARTISTONLY

def normalize(s):
    return s.strip().lower() if s else ""

def first_artist(artist):
    if not artist:
        return ""
    artist_clean = artist.strip()
    for whitelisted in FIRST_ARTIST_WHITELIST:
        if artist_clean.lower() == whitelisted.lower():
            return whitelisted
    if not SCROBBLED_FIRSTARTISTONLY:
        return artist_clean
    sep_pattern = re.compile(
        r"\s+(feat\.?|ft\.?|featuring|&|;|,|/|-|mit|met|with)\s+", flags=re.IGNORECASE
    )
    return sep_pattern.split(artist_clean)[0].strip()

def make_key(artist, title):
    """Create a normalized key for matching artist/title combinations."""
    return (normalize(first_artist(artist)), normalize(title))