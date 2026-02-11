"""
NaviSync - Sync Last.fm scrobbles to Navidrome using direct database access.

This script syncs play counts and loved tracks from Last.fm to Navidrome
by directly modifying the Navidrome database. Navidrome must be stopped
before running this script.
"""

import sys
import sqlite3
import json
import time
from datetime import datetime, timezone
from src.config import (NAVIDROME_URL, NAVIDROME_DB_PATH, CACHE_DB_PATH, MISSING_SCROBBLES, 
                       MISSING_LOVED, DUPLICATE_TRACKS, PLAYCOUNT_CONFLICT_RESOLUTION, SYNC_LOVED_TO_LASTFM, 
                       ENABLE_FUZZY_MATCHING, ALBUM_MATCHING_MODE, DUPLICATE_RESOLUTION)
from src.lastfm import fetch_all_lastfm_scrobbles, fetch_loved_tracks, love_track, unlove_track
from src.utils import aggregate_scrobbles, group_missing_by_artist_album
from src.cache import ScrobbleCache
from src.db import (get_navidrome_user_id, get_all_tracks, 
                   get_annotation_playcount_starred, update_annotation, 
                   check_navidrome_active, update_artist_play_counts, 
                   update_album_play_counts)
from src.matcher import get_lastfm_match_for_navidrome_track

def print_header():
    print("\n NaviSync - Database Sync")
    print("=" * 60)
    print("⚠️  Requires Navidrome to be stopped!")


def show_cache_stats(cache: ScrobbleCache):
    stats = cache.get_cache_stats()
    if stats['total_scrobbles'] > 0:
        print("📊 Cache Statistics:")
        print(f"  Total cached scrobbles: {stats['total_scrobbles']:,}")
        print(f"  Synced: {stats['synced_scrobbles']:,} | Unsynced: {stats['unsynced_scrobbles']:,}")
        print(f"  Loved tracks: {stats['loved_tracks']:,}")
        print(f"  Date range: {stats['oldest_scrobble']} → {stats['newest_scrobble']}\n")
    else:
        print("📊 Cache is empty - this appears to be your first run.\n")


def fetch_and_update_cache(cache: ScrobbleCache):
    # Fetch new scrobbles from Last.fm (only since last cached timestamp)
    latest_timestamp = cache.get_latest_scrobble_timestamp()
    if latest_timestamp > 0:
        print(
            f"🔄 Checking for new scrobbles since {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(latest_timestamp))}..."
        )

    new_scrobbles = fetch_all_lastfm_scrobbles(from_timestamp=latest_timestamp)

    # Add new scrobbles to cache
    if new_scrobbles:
        added = cache.add_scrobbles(new_scrobbles)
        print(f"✅ Added {added} new scrobbles to cache.\n")
    else:
        print("ℹ️  No new scrobbles found.\n")

    # Fetch and update loved tracks
    print("Updating loved tracks...")
    loved_tracks = fetch_loved_tracks()
    cache.update_loved_tracks(loved_tracks)
    print(f"✅ Updated {len(loved_tracks)} loved tracks in cache.\n")

    # Get all scrobbles from cache
    all_scrobbles = cache.get_all_scrobbles()
    if not all_scrobbles:
        print("⚠️  No scrobbles found in cache. This might be your first run or your Last.fm account has no scrobbles.")
        print("   If this seems wrong, check your LASTFM_USER and LASTFM_API_KEY in .env file.\n")
    return all_scrobbles


def ensure_navidrome_stopped():
    print("🔍 Checking if Navidrome is stopped...")
    is_active, reason = check_navidrome_active(NAVIDROME_DB_PATH, navidrome_url=NAVIDROME_URL)
    if is_active:
        print(f"❌ ERROR: {reason}")
        print("⚠️  CANNOT proceed - Database appears to be in use!")
        print("    This prevents database corruption from simultaneous access.\n")
        sys.exit(1)
    print(f"✅ {reason}\n")


def get_navidrome_data():
    user_id = get_navidrome_user_id(NAVIDROME_DB_PATH)
    tracks = get_all_tracks(NAVIDROME_DB_PATH)
    if not tracks:
        print("⚠️  No tracks found in Navidrome database. Make sure your Navidrome library is scanned.\n")
        return user_id, []
    return user_id, tracks


def connect_db(db_path):
    if not db_path:
        print("❌ Error: NAVIDROME_DB_PATH is not configured")
        return None
    try:
        return sqlite3.connect(db_path)
    except sqlite3.Error as e:
        print(f"❌ Error connecting to Navidrome database: {e}")
        return None


def analyze_album_distribution(scrobble_info, duplicates):
    """
    Analyze which albums the Last.fm scrobbles belong to and suggest distribution.
    
    Args:
        scrobble_info: Dict with 'timestamps' and album data from Last.fm
        duplicates: List of Navidrome track versions with 'album' field
    
    Returns:
        Dict with album distribution data: {navidrome_album: {'lastfm_albums': [...], 'count': int, 'percentage': float}}
    """
    # Get the raw scrobbles to see their albums
    # This requires access to the aggregated scrobbles dict, which we'll pass through
    # For now, we'll return data based on analysis of available scrobbles
    
    # Try to read from the cache if we have timestamps
    # This is simplified - we'll enhance with actual album mapping
    distribution = {
        dup['album'] if dup['album'] else "(No Album)": {
            'suggested_count': len(scrobble_info['timestamps']),
            'original_count': len(scrobble_info['timestamps'])
        }
        for dup in duplicates
    }
    
    return distribution


def calculate_album_divide(duplicates, scrobble_info):
    """
    Calculate album-aware divide distribution WITHOUT interactive output.
    
    Used internally when applying cached selections or getting the distribution.
    
    Args:
        duplicates: List of Navidrome track versions with 'id', 'album' fields
        scrobble_info: Dict with 'timestamps' and 'album_orig' from Last.fm
    
    Returns:
        Dict mapping track_id to calculated playcount
    """
    total_scrobbles = len(scrobble_info['timestamps'])
    lastfm_album = scrobble_info.get('album_orig', '').strip()
    
    if not lastfm_album:
        # No album info from Last.fm, divide equally
        per_version = total_scrobbles // len(duplicates)
        remainder = total_scrobbles % len(duplicates)
        
        distribution = {}
        for idx, dup in enumerate(duplicates):
            # Distribute remainder to first few versions
            count = per_version + (1 if idx < remainder else 0)
            distribution[dup['id']] = count
        
        return distribution
    else:
        # Last.fm has album info - try to match to Navidrome albums
        # Find best matching Navidrome album
        exact_match = None
        for dup in duplicates:
            nav_album = (dup['album'] or '').strip()
            if nav_album.lower() == lastfm_album.lower():
                exact_match = dup
                break
        
        if exact_match:
            # Perfect match - give all scrobbles to this version
            distribution = {}
            for dup in duplicates:
                distribution[dup['id']] = total_scrobbles if dup['id'] == exact_match['id'] else 0
            
            return distribution
        else:
            # No exact match - divide equally as fallback
            per_version = total_scrobbles // len(duplicates)
            remainder = total_scrobbles % len(duplicates)
            
            distribution = {}
            for idx, dup in enumerate(duplicates):
                count = per_version + (1 if idx < remainder else 0)
                distribution[dup['id']] = count
            
            return distribution


def process_album_divide(duplicates, scrobble_info, cache, lastfm_artist, lastfm_track):
    """
    Process album-aware divide: distribute scrobbles across album versions based on Last.fm data.
    
    This function matches Last.fm albums to Navidrome album versions and divides the
    playcount proportionally. If a scrobble doesn't have album info or doesn't match,
    it's divided equally among versions.
    
    Args:
        duplicates: List of Navidrome track versions with 'id', 'album' fields
        scrobble_info: Dict with 'timestamps' and 'album_orig' from Last.fm
        cache: ScrobbleCache instance (for caching decisions)
        lastfm_artist: Last.fm artist name
        lastfm_track: Last.fm track name
    
    Returns:
        Dict mapping track_id to calculated playcount
    """
    total_scrobbles = len(scrobble_info['timestamps'])
    
    print(f"\n📀 Album-aware divide for: {lastfm_artist} - {lastfm_track}")
    print(f"   Total scrobbles: {total_scrobbles}")
    print(f"   Navidrome versions:")
    
    for idx, dup in enumerate(duplicates, 1):
        album_name = dup['album'] if dup['album'] else "(No Album)"
        print(f"      [{idx}] {album_name}")
    
    # Try to get album info from scrobble_info
    lastfm_album = scrobble_info.get('album_orig', '').strip()
    
    if not lastfm_album:
        # No album info from Last.fm, divide equally
        print(f"   ⚠️  Last.fm scrobbles don't have album information")
        print(f"   → Dividing {total_scrobbles} scrobbles equally among {len(duplicates)} versions")
        
        per_version = total_scrobbles // len(duplicates)
        remainder = total_scrobbles % len(duplicates)
        
        distribution = {}
        for idx, dup in enumerate(duplicates):
            # Distribute remainder to first few versions
            count = per_version + (1 if idx < remainder else 0)
            distribution[dup['id']] = count
            album_name = dup['album'] if dup['album'] else "(No Album)"
            print(f"      {album_name}: {count} scrobbles")
        
        return distribution
    else:
        # Last.fm has album info - try to match to Navidrome albums
        print(f"   Last.fm album: {lastfm_album}")
        
        # Find best matching Navidrome album
        exact_match = None
        for dup in duplicates:
            nav_album = (dup['album'] or '').strip()
            if nav_album.lower() == lastfm_album.lower():
                exact_match = dup
                break
        
        if exact_match:
            # Perfect match - give all scrobbles to this version
            print(f"   ✅ Matched to Navidrome album: {exact_match['album']}")
            distribution = {}
            for dup in duplicates:
                distribution[dup['id']] = total_scrobbles if dup['id'] == exact_match['id'] else 0
            
            for dup in duplicates:
                album_name = dup['album'] if dup['album'] else "(No Album)"
                print(f"      {album_name}: {distribution[dup['id']]} scrobbles")
            
            return distribution
        else:
            # No exact match - divide equally as fallback
            print(f"   ⚠️  No exact album match found in Navidrome")
            print(f"   → Dividing equally among {len(duplicates)} versions")
            
            per_version = total_scrobbles // len(duplicates)
            remainder = total_scrobbles % len(duplicates)
            
            distribution = {}
            for idx, dup in enumerate(duplicates):
                count = per_version + (1 if idx < remainder else 0)
                distribution[dup['id']] = count
                album_name = dup['album'] if dup['album'] else "(No Album)"
                print(f"      {album_name}: {count} scrobbles")
            
            return distribution


def prompt_user_for_duplicate_selection(duplicates, scrobble_info=None, album_maps=None):
    """
    Prompt user to select which album version(s) should receive the play count.
    
    Args:
        duplicates: List of dicts with Navidrome track info including 'id', 'album', 'artist', 'title'
        scrobble_info: Optional dict with Last.fm scrobble info including 'timestamps' 
        album_maps: Optional dict mapping navidrome albums to last.fm albums for album-aware divide
    
    Returns:
        Tuple of (selected_track_ids, distribution_dict) where distribution_dict is None unless album-divide is used
        selected_track_ids: List of selected track IDs, or None if user wants to skip all
        distribution_dict: For album-divide, dict of {track_id: playcount} for division, else None
    """
    print(f"\n⚠️  Multiple versions of the same track found in Navidrome:")
    print(f"   Track: {duplicates[0]['artist']} - {duplicates[0]['title']}")
    print(f"\n   Found in {len(duplicates)} different location(s):")
    
    def format_duration(seconds):
        """Format duration in seconds to MM:SS format."""
        if not seconds or seconds <= 0:
            return "--:--"
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}:{secs:02d}"
    
    for idx, dup in enumerate(duplicates, 1):
        album_info = dup['album'] if dup['album'] else "(No Album)"
        
        # Build additional info string
        info_parts = []
        if dup.get('track_number'):
            track_str = f"Track {dup['track_number']}"
            if dup.get('disc_number') and dup['disc_number'] > 1:
                track_str = f"Disc {dup['disc_number']}, {track_str}"
            info_parts.append(track_str)
        
        if dup.get('duration'):
            info_parts.append(f"({format_duration(dup['duration'])})")
        
        additional_info = f" - {' '.join(info_parts)}" if info_parts else ""
        print(f"   [{idx}] {album_info}{additional_info}")
    
    print(f"   [A] Apply to ALL versions")
    if scrobble_info and len(duplicates) > 1:
        total_scrobbles = len(scrobble_info.get('timestamps', []))
        print(f"   [B] Album-aware divide (divide {total_scrobbles} scrobbles by album)")
    print(f"   [0] Skip all versions")
    
    while True:
        options = f"1-{len(duplicates)}/A"
        if scrobble_info and len(duplicates) > 1:
            options += "/B"
        options += "/0"
        choice = input(f"\n   → Select which version(s) to update [{options}]: ").strip().upper()
        
        if choice == '0':
            print(f"   ⏭️  Skipped all versions")
            return None, None
        
        if choice == 'A':
            print(f"   ✅ Will update ALL versions")
            return [dup['id'] for dup in duplicates], None
        
        if choice == 'B' and scrobble_info and len(duplicates) > 1:
            # Album-aware divide mode
            print(f"   📀 Album-aware divide selected")
            return duplicates, scrobble_info  # Return as special marker
        
        try:
            idx = int(choice)
            if 1 <= idx <= len(duplicates):
                selected = duplicates[idx - 1]
                album_name = selected['album'] if selected['album'] else "(No Album)"
                print(f"   ✅ Selected: {album_name}")
                return [selected['id']], None
        except ValueError:
            pass
        
        print(f"   ⚠️  Invalid choice. Please enter a number between {options}")


def compute_differences(conn, tracks, aggregated_scrobbles, user_id, cache):
    differences = []
    navidrome_stars_to_sync = []  # Track Navidrome stars to sync TO Last.fm
    total_tracks = len(tracks)
    tracks_with_scrobbles = 0
    
    # Track potential duplicates: key = (lastfm_artist, lastfm_track), value = list of nav tracks
    potential_duplicates = {}

    print(f"\n🔍 Matching {total_tracks:,} Navidrome tracks with Last.fm scrobbles...\n")

    # Phase 1: Process all Navidrome tracks and find Last.fm matches
    track_matches = []  # Store all matches for later processing
    
    for i, nav_track in enumerate(tracks, 1):
        if i % 100 == 0 or i == total_tracks:
            percentage = (i / total_tracks) * 100
            print(f"[{i:,}/{total_tracks:,}] ({percentage:.1f}%) Processing tracks...", end='\r')

        # Try to find a Last.fm match for this Navidrome track
        scrobble_info = get_lastfm_match_for_navidrome_track(
            navidrome_track=nav_track,
            aggregated_scrobbles=aggregated_scrobbles,
            cache=cache,
            fuzzy_threshold=85,
            enable_fuzzy=ENABLE_FUZZY_MATCHING,
            album_aware=(ALBUM_MATCHING_MODE == "album_aware")
        )

        if not scrobble_info:
            continue  # No match found or was skipped

        tracks_with_scrobbles += 1
        
        # Store the match for later processing
        track_matches.append({
            'nav_track': nav_track,
            'scrobble_info': scrobble_info
        })
        
        # Track potential duplicates using the same key structure as matching
        # In album_aware mode, each album should be separate, so use full match key
        if ALBUM_MATCHING_MODE == "album_aware":
            # In album_aware mode, use the same key as the scrobble matching
            # This ensures each album version is treated separately
            duplicate_key = (scrobble_info['artist_orig'], scrobble_info['track_orig'], scrobble_info.get('album_orig', ''))
        else:
            # In other modes, group by artist/track only
            duplicate_key = (scrobble_info['artist_orig'], scrobble_info['track_orig'])
            
        if duplicate_key not in potential_duplicates:
            potential_duplicates[duplicate_key] = []
        potential_duplicates[duplicate_key].append(nav_track)
    
    print(f"\n✅ Matching complete!")
    print(f"   Matched tracks: {tracks_with_scrobbles:,}\n")
    
    # Write duplicate tracks log
    write_duplicate_log(potential_duplicates, album_aware=(ALBUM_MATCHING_MODE == "album_aware"))
    print()
    
    # Phase 2: Handle duplicates and create differences list
    processed_lastfm_keys = set()
    
    for match_info in track_matches:
        nav_track = match_info['nav_track']
        scrobble_info = match_info['scrobble_info']
        
        lastfm_artist = scrobble_info['artist_orig']
        lastfm_track = scrobble_info['track_orig']
        
        # Use the same key structure for processing as we used for tracking
        if ALBUM_MATCHING_MODE == "album_aware":
            processing_key = (lastfm_artist, lastfm_track, scrobble_info.get('album_orig', ''))
        else:
            processing_key = (lastfm_artist, lastfm_track)
        
        # Skip if we've already processed this Last.fm track
        if processing_key in processed_lastfm_keys:
            continue
        
        processed_lastfm_keys.add(processing_key)
        
        # Check for duplicates
        duplicates = potential_duplicates[processing_key]
        selected_track_ids = None
        
        # Debug info for troubleshooting
        # print(f"DEBUG: Mode={ALBUM_MATCHING_MODE}, Track={lastfm_artist}-{lastfm_track}, Duplicates={len(duplicates)}")
        
        # Determine if we need to prompt based on mode, duplicate resolution, and number of duplicates
        should_prompt = False
        auto_selection = None
        
        if len(duplicates) > 1:
            # Multiple versions exist, check duplicate resolution strategy
            if DUPLICATE_RESOLUTION == "ask":
                # Check album matching mode for specific prompting logic
                if ALBUM_MATCHING_MODE == "album_aware":
                    scrobble_album = scrobble_info.get('album_orig', '').strip()
                    if not scrobble_album:
                        should_prompt = True
                        print(f"\n⚠️  Album-aware mode: Last.fm scrobbles for '{lastfm_artist} - {lastfm_track}' lack album information.")
                        print(f"   Multiple album versions found in Navidrome. Please choose which should receive these {len(scrobble_info['timestamps'])} scrobbles.")
                    else:
                        should_prompt = True
                elif ALBUM_MATCHING_MODE == "prompt":
                    should_prompt = True
                else:  # album_agnostic
                    should_prompt = True
            elif DUPLICATE_RESOLUTION == "all":
                # Automatically select all versions
                auto_selection = [dup['id'] for dup in duplicates]
                print(f"   📀 Auto-selecting all {len(duplicates)} versions of '{lastfm_artist} - {lastfm_track}'")
            elif DUPLICATE_RESOLUTION == "first":
                # Automatically select first version
                auto_selection = [duplicates[0]['id']]
                album_name = duplicates[0]['album'] if duplicates[0]['album'] else "(No Album)"
                print(f"   📀 Auto-selecting first version: {album_name} - '{lastfm_artist} - {lastfm_track}'")
            elif DUPLICATE_RESOLUTION == "skip":
                # Skip this track entirely
                print(f"   ⏭️  Skipping '{lastfm_artist} - {lastfm_track}' (has {len(duplicates)} versions)")
                continue
        
        # Special case for album_agnostic mode when DUPLICATE_RESOLUTION is "ask"
        if ALBUM_MATCHING_MODE == "album_agnostic" and DUPLICATE_RESOLUTION == "ask" and len(duplicates) > 1:
            # In album_agnostic + ask mode, default to updating all versions
            should_prompt = False
            auto_selection = [dup['id'] for dup in duplicates]
            print(f"   📀 Album-agnostic: updating all {len(duplicates)} versions of '{lastfm_artist} - {lastfm_track}'")
        
        # Track for album-aware divide result
        album_divide_result = None
        
        if auto_selection:
            # Automatic selection based on DUPLICATE_RESOLUTION
            selected_track_ids = auto_selection
        elif should_prompt:
            # Multiple Navidrome tracks match the same Last.fm track
            # Check if user has already made a selection for this Last.fm track
            cached_selection = cache.get_duplicate_selection(lastfm_artist, lastfm_track)
            
            if cached_selection:
                # Use cached selection, but verify tracks still exist
                valid_ids = [t['id'] for t in duplicates]
                selected_track_ids = [tid for tid in cached_selection if tid in valid_ids]
                
                if selected_track_ids:
                    # Valid cached selection exists, check if it was an album-aware divide
                    # (all duplicates were selected, so recalculate the distribution for consistency)
                    if len(selected_track_ids) == len(duplicates) and len(duplicates) > 1:
                        # Likely an album-aware divide, recalculate silently
                        album_divide_result = calculate_album_divide(duplicates, scrobble_info)
                    # Otherwise use silently
                else:
                    # Cached selection no longer valid, prompt again
                    result, divide_info = prompt_user_for_duplicate_selection(duplicates, scrobble_info)
                    
                    # Check if user selected album-aware divide (result will be duplicates list)
                    if divide_info is not None and isinstance(result, list) and len(result) > 0 and isinstance(result[0], dict) and 'id' in result[0]:
                        # Album-aware divide selected
                        album_divide_result = process_album_divide(result, scrobble_info, cache, lastfm_artist, lastfm_track)
                        selected_track_ids = list(album_divide_result.keys())
                        cache.save_duplicate_selection(lastfm_artist, lastfm_track, selected_track_ids)
                    elif result:
                        selected_track_ids = result
                        cache.save_duplicate_selection(lastfm_artist, lastfm_track, selected_track_ids)
                    else:
                        selected_track_ids = None
            else:
                # No cached selection, prompt user to select which track(s) to update
                result, divide_info = prompt_user_for_duplicate_selection(duplicates, scrobble_info)
                
                # Check if user selected album-aware divide (result will be duplicates list)
                if divide_info is not None and isinstance(result, list) and len(result) > 0 and isinstance(result[0], dict) and 'id' in result[0]:
                    # Album-aware divide selected
                    album_divide_result = process_album_divide(result, scrobble_info, cache, lastfm_artist, lastfm_track)
                    selected_track_ids = list(album_divide_result.keys())
                    cache.save_duplicate_selection(lastfm_artist, lastfm_track, selected_track_ids)
                elif result:
                    selected_track_ids = result
                    cache.save_duplicate_selection(lastfm_artist, lastfm_track, selected_track_ids)
                else:
                    selected_track_ids = None
            
            if not selected_track_ids:
                # User chose to skip
                continue
        else:
            # Single track, use it
            selected_track_ids = [duplicates[0]['id']]
        
        # Now process only the selected track(s)
        for dup in duplicates:
            if dup['id'] not in selected_track_ids:
                continue
                
            track_id = dup['id']
            nav_count, nav_starred, nav_played_ts = get_annotation_playcount_starred(conn, track_id, user_id)
            
            track_scrobbles = scrobble_info['timestamps']
            
            # If album-aware divide was used, get the divided count instead of total
            if album_divide_result is not None:
                lastfm_count = album_divide_result.get(track_id, 0)
            else:
                lastfm_count = len(track_scrobbles)
            
            last_played = max(track_scrobbles) if track_scrobbles else None
            loved = scrobble_info['loved']

            # Check if Navidrome star needs to be synced TO Last.fm
            if SYNC_LOVED_TO_LASTFM and nav_starred and not loved:
                navidrome_stars_to_sync.append({
                    'artist': scrobble_info['artist_orig'],
                    'track': scrobble_info['track_orig'],
                    'nav_artist': dup['artist'],
                    'nav_track': dup['title']
                })

            if lastfm_count != nav_count or (loved and not nav_starred):
                differences.append({
                    'id': track_id,
                    'artist': dup['artist'],
                    'title': dup['title'],
                    'album': dup['album'],
                    'navidrome': nav_count,
                    'nav_starred': nav_starred,
                    'lastfm': lastfm_count,
                    'nav_played': nav_played_ts,
                    'last_played': last_played,
                    'loved': loved,
                    'lastfm_artist': scrobble_info['artist_orig'],
                    'lastfm_track': scrobble_info['track_orig']
                })

    print(f"\n✅ Processing complete!")
    if navidrome_stars_to_sync:
        print(f"   Navidrome stars to sync to Last.fm: {len(navidrome_stars_to_sync)}")
    print()
    return differences, navidrome_stars_to_sync


def write_duplicate_log(potential_duplicates, album_aware=False):
    """Write a log of duplicate tracks to help identify tagging issues.
    
    Args:
        potential_duplicates: Dict mapping Last.fm keys to lists of Navidrome tracks
        album_aware: Whether album information was included in duplicate detection
    """
    duplicate_log = {}
    
    for key, duplicates in potential_duplicates.items():
        if len(duplicates) <= 1:
            continue  # Skip non-duplicates
            
        # Extract artist and track from key
        if album_aware and len(key) == 3:
            artist, track, album = key
        else:
            artist = key[0]
            track = key[1]
            album = None
        
        # Create entry for this duplicate group
        entry = {
            "lastfm_artist": artist,
            "lastfm_track": track,
            "count": len(duplicates),
            "versions": []
        }
        
        if album:
            entry["lastfm_album"] = album
        
        for dup in duplicates:
            version_info = {
                "id": dup['id'],
                "navidrome_artist": dup['artist'],
                "navidrome_title": dup['title'],
                "navidrome_album": dup['album'] or "(No Album)"
            }
            if dup.get('path'):
                version_info["path"] = dup['path']
            if dup.get('duration'):
                version_info["duration_seconds"] = dup['duration']
            entry["versions"].append(version_info)
        
        # Use a unique key for the log
        log_key = f"{artist} - {track}"
        if album:
            log_key += f" [{album}]"
        duplicate_log[log_key] = entry
    
    if duplicate_log:
        with open(DUPLICATE_TRACKS, "w", encoding="utf-8") as f:
            json.dump(duplicate_log, f, indent=2, ensure_ascii=False)
        print(f"📀 Duplicate tracks log saved to {DUPLICATE_TRACKS} ({len(duplicate_log)} groups)")
    
    return len(duplicate_log)


def write_missing_reports(aggregated_scrobbles, tracks, cache, album_aware=False):
    print("💾 Generating missing tracks analysis from search results...")
    missing_scrobbles_grouped, missing_loved_grouped = group_missing_by_artist_album(aggregated_scrobbles, tracks, cache, album_aware)
    with open(MISSING_SCROBBLES, "w", encoding="utf-8") as f:
        json.dump(missing_scrobbles_grouped, f, indent=2, ensure_ascii=False)
    print(f"✅ Missing from scrobbles saved to {MISSING_SCROBBLES}")
    with open(MISSING_LOVED, "w", encoding="utf-8") as f:
        json.dump(missing_loved_grouped, f, indent=2, ensure_ascii=False)
    print(f"✅ Missing loved tracks saved to {MISSING_LOVED}")


def show_conflict_mode():
    conflict_mode_desc = {
        "ask": "interactive (will prompt for each conflict)",
        "navidrome": "always keep Navidrome when higher",
        "lastfm": "always use Last.fm",
        "higher": "always use higher count",
        "increment": "add Last.fm count to Navidrome count",
    }
    album_mode_desc = {
        "album_agnostic": "combine scrobbles for same artist/title regardless of album",
        "album_aware": "separate play counts per album based on scrobble album info",
        "prompt": "always prompt which album version(s) to update"
    }
    duplicate_mode_desc = {
        "ask": "interactive (will prompt for duplicates)",
        "all": "automatically update all versions",
        "first": "automatically update first version only",
        "skip": "skip tracks with multiple versions",
    }
    print(f"📋 Conflict resolution mode: {conflict_mode_desc.get(PLAYCOUNT_CONFLICT_RESOLUTION, PLAYCOUNT_CONFLICT_RESOLUTION)}")
    print(f"💽 Album matching mode: {album_mode_desc.get(ALBUM_MATCHING_MODE, ALBUM_MATCHING_MODE)}")
    print(f"📀 Duplicate resolution mode: {duplicate_mode_desc.get(DUPLICATE_RESOLUTION, DUPLICATE_RESOLUTION)}")
    
    if ALBUM_MATCHING_MODE == "album_aware":
        print(f"ℹ️  Album-aware mode: When scrobbles lack album info, you'll be prompted to choose which album version(s) should receive the play count.")
    print()


def resolve_playcount(nav: int, lastfm: int, artist: str, title: str, mode: str):
    """Return (new_count, conflict_resolved: bool, changed: bool)."""
    conflict = False
    changed = False

    if mode == "increment":
        new_count = nav + lastfm
        changed = True
        if nav != lastfm:
            conflict = True
        return new_count, conflict, changed

    if lastfm > nav:
        return lastfm, True, True

    if nav > lastfm:
        if mode == "ask":
            print(f"\n🎵 {artist} - {title}")
            print(f"   Navidrome: {nav} | Last.fm: {lastfm}")
            choice = input("   → Navidrome playcount is higher. Keep Navidrome (N) or use Last.fm (L)? [N/L, default=N]: ").strip().lower()
            new_count = nav if choice in ('', 'n') else lastfm
            return new_count, True, new_count != nav
        elif mode == "navidrome" or mode == "higher":
            return nav, True, False
        elif mode == "lastfm":
            return lastfm, True, True
        # Fallback
        return nav, False, False

    # Equal
    return nav, False, False


def prompt_yes_no(message: str, default: bool = False) -> bool:
    resp = input(message).strip().lower()
    if not resp:
        return default
    return resp in ("y", "yes")


def apply_updates(conn, cache: ScrobbleCache, differences, user_id: int):
    print(f"\nTracks with possible updates: {len(differences)}\n")
    show_conflict_mode()

    for d in differences:
        diff_str = f"{d['lastfm'] - d['navidrome']:+d}"
        album_info = f" [{d['album']}]" if d.get('album') else ""
        print(f"  - {d['artist']} - {d['title']}{album_info}")
        print(f"    Navidrome: {d['navidrome']} | Last.fm: {d['lastfm']} | Diff: {diff_str} | Loved: {d['loved']}")

    if not prompt_yes_no("\nProceed with reviewing and updating these tracks? [y/N]: ", default=False):
        print("🧪 Dry run complete. No changes made.")
        return

    updated_playcounts = 0
    updated_loved = 0
    conflicts_resolved = 0
    updated_track_ids = []  # Track which tracks were updated

    for d in differences:
        nav = d['navidrome']
        lastfm = d['lastfm']
        artist, title = d['artist'], d['title']

        new_count, conflict, changed = resolve_playcount(nav, lastfm, artist, title, PLAYCOUNT_CONFLICT_RESOLUTION)
        if conflict:
            conflicts_resolved += 1
        if changed:
            updated_playcounts += 1

        # Loved status
        will_update_loved = d['loved'] and not d['nav_starred']
        if will_update_loved:
            updated_loved += 1

        # Track if this record was actually modified
        track_was_updated = (new_count != nav) or will_update_loved
        if track_was_updated:
            updated_track_ids.append(d['id'])

        update_annotation(conn, d['id'], new_count, d['last_played'], d['loved'], user_id)

        # Mark this track as synced in cache using original Last.fm names
        lastfm_artist = d.get('lastfm_artist', d['artist'])
        lastfm_track = d.get('lastfm_track', d['title'])
        cache.mark_scrobbles_synced(lastfm_artist, lastfm_track)

        # Log concise summary
        if new_count != nav:
            if PLAYCOUNT_CONFLICT_RESOLUTION == "increment":
                print(f"➕ Incremented playcount: {artist} - {title} ({nav} + {lastfm} = {new_count})")
            else:
                print(f"✅ Updated playcount: {artist} - {title} ({nav} → {new_count})")
        elif will_update_loved:
            print(f"⭐ Starred: {artist} - {title}")
        elif PLAYCOUNT_CONFLICT_RESOLUTION != "ask" and nav > lastfm:
            # Show when we kept Navidrome's higher count (non-interactive modes)
            print(f"ℹ️  Kept Navidrome count: {artist} - {title} (Navidrome: {nav}, Last.fm: {lastfm})")

    # Update sync timestamp
    cache.set_metadata('last_sync_time', datetime.now(timezone.utc).isoformat())

    # Update artist and album play counts only for affected tracks
    print("\n🎨 Updating artist and album play counts...")
    artists_updated = update_artist_play_counts(conn, user_id, updated_track_ids)
    albums_updated = update_album_play_counts(conn, user_id, updated_track_ids)
    print(f"✅ Updated play counts for {artists_updated} artists and {albums_updated} albums")

    # Show summary
    print(f"\n{'='*60}")
    print(f"✅ Sync complete!")
    print(f"{'='*60}")
    print(f"   Updated playcounts: {updated_playcounts}")
    print(f"   Updated loved status: {updated_loved}")
    if conflicts_resolved > 0:
        print(f"   Conflicts resolved: {conflicts_resolved}")
    print(f"{'='*60}\n")


def close_db(conn):
    try:
        conn.close()
    except Exception:
        pass
    # Wait 2 seconds to ensure database connection is fully released
    print("🔒 Closing database connection...")
    time.sleep(2)


def sync_stars_to_lastfm(navidrome_stars_to_sync):
    """
    Sync Navidrome starred tracks TO Last.fm as loved tracks.
    
    Args:
        navidrome_stars_to_sync: List of dicts with 'artist', 'track', 'nav_artist', 'nav_track'
    """
    if not navidrome_stars_to_sync:
        return
    
    print(f"\n💝 Syncing {len(navidrome_stars_to_sync)} Navidrome stars to Last.fm...")
    print("   (Navidrome starred → Last.fm loved)\n")
    
    for track_info in navidrome_stars_to_sync:
        print(f"  - {track_info['nav_artist']} - {track_info['nav_track']}")
    
    if not prompt_yes_no("\nProceed with syncing these tracks to Last.fm? [y/N]: ", default=False):
        print("⏭️  Skipped syncing stars to Last.fm.")
        return
    
    synced_count = 0
    failed_count = 0
    
    for track_info in navidrome_stars_to_sync:
        artist = track_info['artist']
        track = track_info['track']
        
        if love_track(artist, track):
            synced_count += 1
            print(f"  ❤️  Loved on Last.fm: {track_info['nav_artist']} - {track_info['nav_track']}")
            time.sleep(0.5)  # Rate limiting
        else:
            failed_count += 1
    
    print(f"\n✅ Synced {synced_count} stars to Last.fm")
    if failed_count > 0:
        print(f"⚠️  Failed to sync {failed_count} tracks")


def main():
    """Main sync function using direct database access."""
    print_header()
    try:
        cache = ScrobbleCache(CACHE_DB_PATH)
        show_cache_stats(cache)
        all_scrobbles = fetch_and_update_cache(cache)
        ensure_navidrome_stopped()
        user_id, tracks = get_navidrome_data()
        if not tracks:
            return
        aggregated_scrobbles = aggregate_scrobbles(all_scrobbles, album_aware=(ALBUM_MATCHING_MODE == "album_aware"))
    except KeyboardInterrupt:
        print("\n\n⚠️  Sync cancelled by user.")
        return
    except Exception as e:
        print(f"\n❌ Error during initialization: {e}")
        return

    conn = connect_db(NAVIDROME_DB_PATH)
    if conn is None:
        return

    try:
        differences, navidrome_stars_to_sync = compute_differences(conn, tracks, aggregated_scrobbles, user_id, cache)
        write_missing_reports(aggregated_scrobbles, tracks, cache, (ALBUM_MATCHING_MODE == "album_aware"))
        
        # Sync Navidrome stars TO Last.fm if enabled
        if SYNC_LOVED_TO_LASTFM and navidrome_stars_to_sync:
            sync_stars_to_lastfm(navidrome_stars_to_sync)
        
        if differences:
            apply_updates(conn, cache, differences, user_id)
        else:
            print("\n✅ All tracks are already in sync!")
    finally:
        close_db(conn)

if __name__ == "__main__":
    main()