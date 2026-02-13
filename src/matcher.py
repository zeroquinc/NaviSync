"""
Fuzzy matching utilities for track and artist names.
Handles variations like & vs 'and', special characters, etc.
"""

import unicodedata
from thefuzz import fuzz
from typing import List, Dict, Optional, Tuple


def normalize_for_fuzzy_match(text: str) -> str:
    """
    Normalize text for fuzzy matching by handling common variations.
    
    Handles:
    - Unicode normalization (e.g., Próphecy -> Prophecy)
    - & vs 'and' 
    - Multiple spaces
    - Common punctuation variations
    """
    if not text:
        return ""
    
    # Normalize unicode characters (NFD = decompose, then remove accents)
    text = unicodedata.normalize('NFD', text)
    text = ''.join(char for char in text if unicodedata.category(char) != 'Mn')
    
    # Lowercase
    text = text.lower()
    
    # Replace & with 'and' for comparison
    text = text.replace(' & ', ' and ')
    
    # Remove extra punctuation and normalize spaces
    text = text.replace('  ', ' ').strip()
    
    return text


def find_fuzzy_matches_for_navidrome_track(
    navidrome_artist: str,
    navidrome_track: str,
    aggregated_scrobbles: Dict,
    threshold: int = 85
) -> List[Dict]:
    """
    Find potential fuzzy matches for a Navidrome track in Last.fm scrobbles.
    
    Args:
        navidrome_artist: Artist name from Navidrome
        navidrome_track: Track name from Navidrome
        aggregated_scrobbles: Dict of aggregated Last.fm scrobbles {key: {info}}
        threshold: Minimum similarity score (0-100)
    
    Returns:
        List of potential matches with similarity scores, sorted by score (highest first)
    """
    nav_artist_norm = normalize_for_fuzzy_match(navidrome_artist)
    nav_track_norm = normalize_for_fuzzy_match(navidrome_track)
    
    matches = []
    
    for key, scrobble_info in aggregated_scrobbles.items():
        lastfm_artist = scrobble_info['artist_orig']
        lastfm_track = scrobble_info['track_orig']
        
        lastfm_artist_norm = normalize_for_fuzzy_match(lastfm_artist)
        lastfm_track_norm = normalize_for_fuzzy_match(lastfm_track)
        
        # Calculate similarity scores
        artist_score = fuzz.ratio(nav_artist_norm, lastfm_artist_norm)
        track_score = fuzz.ratio(nav_track_norm, lastfm_track_norm)
        
        # Combined score (weighted average: track is more important)
        combined_score = (track_score * 0.7) + (artist_score * 0.3)
        
        # If both artist and track are reasonably similar
        if combined_score >= threshold and artist_score >= 70:
            matches.append({
                'lastfm_artist': lastfm_artist,
                'lastfm_track': lastfm_track,
                'scrobble_count': len(scrobble_info['timestamps']),
                'loved': scrobble_info['loved'],
                'artist_score': artist_score,
                'track_score': track_score,
                'combined_score': combined_score,
                'scrobble_info': scrobble_info
            })
    
    # Sort by combined score (highest first)
    matches.sort(key=lambda x: x['combined_score'], reverse=True)
    
    return matches


def prompt_user_for_lastfm_match(
    navidrome_artist: str,
    navidrome_track: str,
    matches: List[Dict]
) -> Optional[Dict]:
    """
    Prompt the user to select the correct Last.fm match for a Navidrome track.
    
    Returns:
        Selected Last.fm match dict with 'lastfm_artist', 'lastfm_track', 'scrobble_info', or None if user skips
    """
    print(f"\n🔍 Fuzzy match found for:")
    print(f"   Navidrome: {navidrome_artist} - {navidrome_track}")
    print(f"\n   Potential matches in Last.fm:")
    
    for idx, match in enumerate(matches, 1):
        score = match['combined_score']
        count = match['scrobble_count']
        loved_str = " ❤️" if match['loved'] else ""
        print(f"   [{idx}] {match['lastfm_artist']} - {match['lastfm_track']} ({count} scrobbles{loved_str}) (similarity: {score:.0f}%)")
    
    print(f"   [0] None of these match (skip this track)")
    
    while True:
        choice = input(f"\n   → Select match [0-{len(matches)}]: ").strip()
        
        if choice == '0':
            return None
        
        try:
            idx = int(choice)
            if 1 <= idx <= len(matches):
                selected = matches[idx - 1]
                print(f"   ✅ Matched to: {selected['lastfm_artist']} - {selected['lastfm_track']}")
                return selected
        except ValueError:
            pass
        
        print(f"   ⚠️  Invalid choice. Please enter a number between 0 and {len(matches)}")


def get_lastfm_match_for_navidrome_track(
    navidrome_track: Dict,
    aggregated_scrobbles: Dict,
    cache,
    fuzzy_threshold: int = 85,
    enable_fuzzy: bool = True,
    album_aware: bool = False,
    album_specific_keys: Optional[set] = None
) -> Optional[Dict]:
    """
    Get the best Last.fm match for a Navidrome track.
    Uses exact match first, then checks cache, then fuzzy matching with user prompt.
    
    If track was previously skipped but NEW Last.fm tracks are now available,
    it will re-prompt the user.
    
    Args:
        navidrome_track: Navidrome track dict with 'id', 'artist', 'title', 'album'
        aggregated_scrobbles: Dict of aggregated Last.fm scrobbles
        cache: ScrobbleCache instance
        fuzzy_threshold: Minimum score for fuzzy matching
        enable_fuzzy: Enable fuzzy matching (default: True)
        album_aware: Use album information in matching (default: False)
        album_specific_keys: Optional set of (artist_key, track_key) where Last.fm scrobbles have album info
    
    Returns:
        Dict with Last.fm scrobble info, or None if no match
    """
    from .utils import make_key_navidrome, make_key_lastfm
    
    navidrome_artist = navidrome_track['artist']
    navidrome_title = navidrome_track['title']
    navidrome_album = navidrome_track.get('album', '')
    navidrome_id = navidrome_track['id']
    
    # Check if we have a cached fuzzy match
    cached_match = cache.get_fuzzy_match_for_navidrome_track(navidrome_id)
    if cached_match:
        # Look up the scrobble info using the cached Last.fm artist/track
        # Note: fuzzy matches don't include album info, so use album_aware=False
        key = make_key_lastfm(cached_match['artist'], cached_match['track'], None, False)
        if key in aggregated_scrobbles:
            return aggregated_scrobbles[key]
        # If cached Last.fm track no longer exists in scrobbles, fall through
    
    # Try exact match first (with album awareness if enabled)
    exact_key = make_key_navidrome(navidrome_artist, navidrome_title, navidrome_album, album_aware)
    if exact_key in aggregated_scrobbles:
        return aggregated_scrobbles[exact_key]
    
    # If album-aware mode didn't find a match, be careful with fallbacks
    # Only fall back to album-agnostic matches when Last.fm provides no album info for this track
    if album_aware:
        nav_key_agnostic = make_key_navidrome(navidrome_artist, navidrome_title, None, False)
        nav_artist_key = nav_key_agnostic[0]
        nav_title_key = nav_key_agnostic[1]
        has_album_specific = False
        if album_specific_keys is not None:
            has_album_specific = (nav_artist_key, nav_title_key) in album_specific_keys

        empty_album_key = make_key_navidrome(navidrome_artist, navidrome_title, '', True)
        nav_album_clean = (navidrome_album or '').strip()

        # If Navidrome has no album, accept empty-album scrobbles
        if not nav_album_clean and empty_album_key in aggregated_scrobbles:
            return aggregated_scrobbles[empty_album_key]

        # If Last.fm has no album info for this artist/title, allow empty/agnostic fallbacks
        if not has_album_specific:
            if empty_album_key in aggregated_scrobbles:
                return aggregated_scrobbles[empty_album_key]

            album_agnostic_key = make_key_navidrome(navidrome_artist, navidrome_title, None, False)
            if album_agnostic_key in aggregated_scrobbles:
                return aggregated_scrobbles[album_agnostic_key]

        # Otherwise, don't force an album-agnostic match
        return None
    
    # Skip fuzzy matching if disabled
    if not enable_fuzzy:
        return None
    
    # Try fuzzy matching
    fuzzy_matches = find_fuzzy_matches_for_navidrome_track(
        navidrome_artist,
        navidrome_title,
        aggregated_scrobbles,
        threshold=fuzzy_threshold
    )
    
    if not fuzzy_matches:
        return None
    
    # Check if this track was previously skipped
    skipped_info = cache.get_skipped_track_info(navidrome_id)
    if skipped_info:
        # Check if there are NEW Last.fm tracks that weren't available when we skipped
        current_lastfm_keys = [(m['lastfm_artist'], m['lastfm_track']) for m in fuzzy_matches]
        previously_checked = skipped_info['checked_lastfm_tracks']
        
        # Convert to sets for comparison
        current_set = set(tuple(k) if isinstance(k, list) else k for k in current_lastfm_keys)
        previous_set = set(tuple(k) if isinstance(k, list) else k for k in previously_checked)
        
        new_matches = current_set - previous_set
        
        if not new_matches:
            # No new matches, keep it skipped
            return None
        
        # There are NEW matches! Re-prompt the user
        print(f"\n   ℹ️  New potential matches found for previously skipped track")
    
    # Prompt user to select match
    selected = prompt_user_for_lastfm_match(navidrome_artist, navidrome_title, fuzzy_matches)
    
    if selected:
        # Save the match for future runs
        cache.save_fuzzy_match(
            navidrome_track,
            selected['lastfm_artist'],
            selected['lastfm_track']
        )
        return selected['scrobble_info']
    else:
        # User skipped - remember this along with what was checked
        checked_keys = [(m['lastfm_artist'], m['lastfm_track']) for m in fuzzy_matches]
        cache.save_skipped_track(navidrome_track, checked_keys)
        return None
