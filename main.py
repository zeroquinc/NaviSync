"""
NaviSync - Sync Last.fm scrobbles to Navidrome using direct database access.

This script syncs play counts and loved tracks from Last.fm to Navidrome
by directly modifying the Navidrome database. Navidrome must be stopped
before running this script.
"""

import sys
import json
import time
from datetime import datetime, timezone
from src.config import (NAVIDROME_URL, NAVIDROME_DB_PATH, NAVIDROME_USER_ID, CACHE_DB_PATH, MISSING_SCROBBLES,
                        MISSING_LOVED, DUPLICATE_TRACKS, PLAYCOUNT_CONFLICT_RESOLUTION, SYNC_LOVED_TO_LASTFM,
                        SYNC_PLAYCOUNT, ENABLE_FUZZY_MATCHING, FUZZY_MATCHING_THRESHOLD,
                        FUZZY_MATCHING_AUTO_THRESHOLD, ALBUM_MATCHING_MODE, DUPLICATE_RESOLUTION,
                        AUTO_CONFIRM, validate_config)
from src.lastfm import fetch_all_lastfm_scrobbles, fetch_loved_tracks, love_track
from src.utils import aggregate_scrobbles, group_missing_by_artist_album
from src.cache import ScrobbleCache
from src.db import (connect_db, get_navidrome_user_id, get_all_tracks,
                    get_annotation_playcount_starred, update_annotation,
                    check_navidrome_active, update_artist_play_counts,
                    update_album_play_counts, insert_scrobbles, get_existing_scrobble_times)
from src.matcher import get_lastfm_match_for_navidrome_track
from src.duplicates import (
    recompute_manual_distribution,
    calculate_album_divide,
    resolve_album_divide_selection,
    prompt_user_for_loved_selection,
)

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


def warn_if_navidrome_id_migration_likely():
    """Warn users about Navidrome's ID migration when the DB looks post-migration."""
    try:
        conn = connect_db(NAVIDROME_DB_PATH)
        if conn is None:
            return
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='media_file'")
            if cursor.fetchone() is None:
                return

            cursor.execute("SELECT id FROM media_file LIMIT 5")
            sample_ids = [row[0] for row in cursor.fetchall() if row and row[0] is not None]
        finally:
            conn.close()

        if not sample_ids:
            return

        looks_like_new_ids = False
        for value in sample_ids:
            if isinstance(value, str):
                if len(value) == 22 and all(ch.isalnum() for ch in value):
                    looks_like_new_ids = True
                    break

        if looks_like_new_ids:
            print("⚠️  Navidrome appears to be using the new canonical ID format.")
            print("   If you upgraded Navidrome recently, consider clearing the local cache folders")
            print("   before the next run so stale cached matches do not persist.")
            print("   A backup of navidrome.db is still strongly recommended before any upgrade.\n")
    except Exception:
        return


def get_navidrome_data():
    user_id = get_navidrome_user_id(NAVIDROME_DB_PATH, preset_user_id=NAVIDROME_USER_ID)
    tracks = get_all_tracks(NAVIDROME_DB_PATH)
    if not tracks:
        print("⚠️  No tracks found in Navidrome database. Make sure your Navidrome library is scanned.\n")
        return user_id, []
    return user_id, tracks




def _make_duplicate_key(scrobble_info):
    """Build the key used to group duplicate Navidrome tracks for a given scrobble."""
    if ALBUM_MATCHING_MODE == "album_aware":
        return (scrobble_info["artist_orig"], scrobble_info["track_orig"], scrobble_info.get("album_orig", ""))
    return (scrobble_info["artist_orig"], scrobble_info["track_orig"])



def compute_differences(conn, tracks, aggregated_scrobbles, user_id, cache):
    differences = []
    navidrome_stars_to_sync = []  # Track Navidrome stars to sync TO Last.fm
    total_tracks = len(tracks)
    tracks_with_scrobbles = 0
    
    # Track potential duplicates: key = (lastfm_artist, lastfm_track [, album]), value = list of nav tracks
    potential_duplicates = {}
    # Always keep an album-agnostic map for loved handling in album-aware mode
    potential_duplicates_agnostic = {}

    # Precompute which artist/title pairs have album-specific Last.fm scrobbles
    album_specific_keys = None
    if ALBUM_MATCHING_MODE == "album_aware":
        album_specific_keys = {
            (key[0], key[1])
            for key in aggregated_scrobbles.keys()
            if isinstance(key, tuple) and len(key) == 3 and key[2]
        }

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
            fuzzy_threshold=FUZZY_MATCHING_THRESHOLD,
            auto_fuzzy_threshold=FUZZY_MATCHING_AUTO_THRESHOLD,
            enable_fuzzy=ENABLE_FUZZY_MATCHING,
            album_aware=(ALBUM_MATCHING_MODE == "album_aware"),
            album_specific_keys=album_specific_keys
        )

        if not scrobble_info:
            continue  # No match found or was skipped

        tracks_with_scrobbles += 1
        
        # Store the match for later processing
        track_matches.append({
            'nav_track': nav_track,
            'scrobble_info': scrobble_info
        })
        
        duplicate_key = _make_duplicate_key(scrobble_info)

        if duplicate_key not in potential_duplicates:
            potential_duplicates[duplicate_key] = []
        potential_duplicates[duplicate_key].append(nav_track)

        agnostic_key = (scrobble_info['artist_orig'], scrobble_info['track_orig'])
        if agnostic_key not in potential_duplicates_agnostic:
            potential_duplicates_agnostic[agnostic_key] = []
        potential_duplicates_agnostic[agnostic_key].append(nav_track)
    
    print(f"\n✅ Matching complete!")
    print(f"   Matched tracks: {tracks_with_scrobbles:,}\n")
    
    # Write duplicate tracks log
    write_duplicate_log(potential_duplicates, album_aware=(ALBUM_MATCHING_MODE == "album_aware"))
    print()
    
    # Phase 2: Handle duplicates and create differences list
    processed_lastfm_keys = set()
    love_selection_cache = {}
    for match_info in track_matches:
        nav_track = match_info['nav_track']
        scrobble_info = match_info['scrobble_info']
        
        lastfm_artist = scrobble_info['artist_orig']
        lastfm_track = scrobble_info['track_orig']
        
        processing_key = _make_duplicate_key(scrobble_info)
        
        # Skip if we've already processed this Last.fm track
        if processing_key in processed_lastfm_keys:
            continue
        
        processed_lastfm_keys.add(processing_key)
        
        # Check for duplicates
        duplicates = potential_duplicates[processing_key]
        selected_track_ids = None
        album_divide_result = None
        
        # Determine how to handle this track based on mode and duplicate count
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
                cached_ids = cached_selection.get("ids", [])
                cached_mode = cached_selection.get("mode", "select")
                cached_distribution = cached_selection.get("distribution", None)
                selected_track_ids = [tid for tid in cached_ids if tid in valid_ids]
                
                if selected_track_ids:
                    # Valid cached selection exists
                    if cached_mode == "select":
                        # Manual selection - use it silently
                        if cached_distribution and len(duplicates) > 1:
                            # Re-compute the manual distribution with fresh album counts
                            # so new scrobbles since last run are picked up.
                            current_album_counts = cache.get_album_scrobble_counts(lastfm_artist, lastfm_track)
                            refreshed = recompute_manual_distribution(
                                duplicates, cached_distribution, current_album_counts
                            ) if current_album_counts else None
                            album_divide_result = refreshed if refreshed is not None else cached_distribution
                        # Otherwise use silently (no distribution to update)
                    elif cached_mode == "divide" and cached_distribution:
                        # Re-compute distribution from current Last.fm data so new
                        # scrobbles are reflected (cached distribution has stale counts).
                        current_album_counts = cache.get_album_scrobble_counts(lastfm_artist, lastfm_track)
                        album_divide_result = calculate_album_divide(
                            duplicates, scrobble_info,
                            album_counts=current_album_counts if current_album_counts else None
                        )
                        # Persist refreshed counts so the cache stays up-to-date
                        cache.save_duplicate_selection(
                            lastfm_artist, lastfm_track,
                            list(album_divide_result.keys()),
                            mode="divide", distribution=album_divide_result
                        )
                else:
                    # Cached selection no longer valid — reprompt
                    selected_track_ids, album_divide_result, skip = resolve_album_divide_selection(
                        duplicates, scrobble_info, cache, lastfm_artist, lastfm_track
                    )
                    if skip:
                        continue
            else:
                # No cached selection — prompt fresh
                selected_track_ids, album_divide_result, skip = resolve_album_divide_selection(
                    duplicates, scrobble_info, cache, lastfm_artist, lastfm_track
                )
                if skip:
                    continue
            
            if not selected_track_ids:
                # User chose to skip
                continue
        else:
            # Single track, use it
            selected_track_ids = [duplicates[0]['id']]
        
        # Decide which duplicate(s) should receive loved status in album-aware mode
        loved_lastfm = scrobble_info['loved']
        love_allowed_ids = None
        if loved_lastfm:
            agnostic_key = (lastfm_artist, lastfm_track)
            agnostic_dups = potential_duplicates_agnostic.get(agnostic_key, [])
            if len(agnostic_dups) > 1:
                if agnostic_key not in love_selection_cache:
                    cached_selection = cache.get_loved_selection(lastfm_artist, lastfm_track)
                    need_prompt = False

                    if cached_selection is not None:
                        valid_ids = {t['id'] for t in agnostic_dups}
                        love_allowed_ids = [tid for tid in cached_selection if tid in valid_ids]
                        # If saved selection had IDs but all are now stale (e.g. Navidrome re-indexed), re-prompt
                        if cached_selection and not love_allowed_ids:
                            need_prompt = True
                    else:
                        need_prompt = True

                    if need_prompt:
                        starred_ids = set()
                        for dup_track in agnostic_dups:
                            _, nav_starred, _ = get_annotation_playcount_starred(conn, dup_track['id'], user_id)
                            if nav_starred:
                                starred_ids.add(dup_track['id'])

                        love_allowed_ids = prompt_user_for_loved_selection(agnostic_dups, starred_ids)
                        cache.save_loved_selection(lastfm_artist, lastfm_track, love_allowed_ids)

                    love_selection_cache[agnostic_key] = love_allowed_ids
                else:
                    love_allowed_ids = love_selection_cache[agnostic_key]

        # Decide which duplicates to process
        if album_divide_result is not None:
            process_track_ids = [dup['id'] for dup in duplicates]
        else:
            process_track_ids = selected_track_ids

        # Assign specific timestamps to each Navidrome track when using album-divide
        assigned_timestamps = {}
        # Extract raw timestamp ints and group by Last.fm album
        raw_ts = scrobble_info.get('timestamps', []) or []
        ts_by_album = {}
        for rec in raw_ts:
            # rec is {'timestamp': int, 'album': str}
            album_name = (rec.get('album') or '').strip()
            ts_by_album.setdefault(album_name, []).append(int(rec['timestamp']))

        # Helper: flattened list of all timestamps (ints), newest first
        all_ts_sorted = sorted((t for grp in ts_by_album.values() for t in grp), reverse=True)

        if album_divide_result is not None:
            # Prefer assigning timestamps that match the Last.fm album when possible
            remaining = {}
            for album, lst in ts_by_album.items():
                # sort newest-first per album
                remaining[album] = sorted(lst, reverse=True)

            # For each duplicate, try to match its album to Last.fm album group
            for dup in duplicates:
                assigned = []
                dup_album_norm = (dup.get('album') or '').strip().lower()
                # Find matching album key (case-insensitive)
                matched_key = None
                for a in remaining.keys():
                    if a.strip().lower() == dup_album_norm and remaining[a]:
                        matched_key = a
                        break

                if matched_key:
                    cnt = int(album_divide_result.get(dup['id'], 0))
                    assigned = remaining[matched_key][:cnt]
                    remaining[matched_key] = remaining[matched_key][cnt:]
                else:
                    # No direct album match — take from any remaining timestamps
                    cnt = int(album_divide_result.get(dup['id'], 0))
                    take = []
                    # Pull from album groups in arbitrary order until we have cnt
                    for a in list(remaining.keys()):
                        if not remaining[a]:
                            continue
                        need = cnt - len(take)
                        if need <= 0:
                            break
                        take.extend(remaining[a][:need])
                        remaining[a] = remaining[a][need:]
                    # If still short, pull from all_ts_sorted (shouldn't usually happen)
                    if len(take) < cnt:
                        extra_needed = cnt - len(take)
                        extra = []
                        for a in list(remaining.keys()):
                            if remaining[a]:
                                extra.append(remaining[a].pop(0))
                                if len(extra) >= extra_needed:
                                    break
                        take.extend(extra)
                    assigned = take

                assigned_timestamps[dup['id']] = assigned
        else:
            # If only a single target is selected, assign all timestamps to it;
            # avoid duplicating timestamps across multiple Navidrome versions.
            for dup in duplicates:
                if len(process_track_ids) == 1 and dup['id'] == process_track_ids[0]:
                    assigned_timestamps[dup['id']] = all_ts_sorted
                else:
                    assigned_timestamps[dup['id']] = []

        # Now process the intended track(s)
        for dup in duplicates:
            if dup['id'] not in process_track_ids:
                continue

            track_id = dup['id']
            nav_count, nav_starred, nav_played_ts = get_annotation_playcount_starred(conn, track_id, user_id)
            
            # track_scrobbles from aggregated data are dicts; count is length
            track_scrobbles = scrobble_info.get('timestamps', [])

            # If album-aware divide or manual distribution was used, use the assigned count
            if album_divide_result is not None:
                lastfm_count = album_divide_result.get(track_id, 0)
            else:
                lastfm_count = len(track_scrobbles)

            # Determine last_played timestamp (max of timestamps in this Last.fm grouping)
            last_played = None
            if track_scrobbles:
                last_played = max((t['timestamp'] for t in track_scrobbles))
            loved = loved_lastfm
            if love_allowed_ids is not None:
                loved = loved and (dup['id'] in love_allowed_ids)

            loved_at = cache.get_loved_timestamp(scrobble_info['artist_orig'], scrobble_info['track_orig']) if loved else None

            # Check if Navidrome star needs to be synced TO Last.fm
            if SYNC_LOVED_TO_LASTFM and nav_starred and not loved:
                navidrome_stars_to_sync.append({
                    'artist': scrobble_info['artist_orig'],
                    'track': scrobble_info['track_orig'],
                    'nav_artist': dup['artist'],
                    'nav_track': dup['title']
                })

            has_playcount_diff = SYNC_PLAYCOUNT and (lastfm_count != nav_count)
            has_loved_diff = loved and not nav_starred
            if has_playcount_diff or has_loved_diff:
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
                    'loved_at': loved_at,
                    'lastfm_artist': scrobble_info['artist_orig'],
                    'lastfm_track': scrobble_info['track_orig'],
                    'from_distribution': album_divide_result is not None,
                    'timestamps': assigned_timestamps.get(track_id, [])
                })

    print(f"\n✅ Processing complete!")
    if navidrome_stars_to_sync:
        print(f"   Navidrome stars to sync to Last.fm: {len(navidrome_stars_to_sync)}")
    print()
    return differences, navidrome_stars_to_sync, potential_duplicates, potential_duplicates_agnostic


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


def backfill_scrobbles(conn, cache, aggregated_scrobbles, tracks, user_id, potential_duplicates, potential_duplicates_agnostic):
    """Backfill scrobbles into Navidrome for tracks whose playcount already matches Last.fm.

    This will insert missing scrobble rows for Navidrome media_file IDs when the
    Navidrome annotation.play_count equals the Last.fm count — ensuring history is
    filled without changing play counts.
    """
    print("\n🔁 Backfilling scrobbles for already-matching playcounts...")
    inserted_total = 0

    # make_key_navidrome not needed here; use aggregated keys directly

    for key, scrobble_info in aggregated_scrobbles.items():
        # Determine duplicate key in same form as potential_duplicates
        if ALBUM_MATCHING_MODE == "album_aware":
            duplicate_key = key
        else:
            # key may be 3-tuple; normalize to 2-tuple
            duplicate_key = (key[0], key[1]) if isinstance(key, tuple) and len(key) >= 2 else key

        duplicates = potential_duplicates.get(duplicate_key)
        if not duplicates:
            # try agnostic fallback
            agnostic_key = (scrobble_info['artist_orig'], scrobble_info['track_orig'])
            duplicates = potential_duplicates_agnostic.get(agnostic_key, [])
        if not duplicates:
            continue

        # Build per-duplicate timestamp assignment similar to compute_differences
        # Prepare Last.fm keys and group timestamps by album
        lastfm_artist = scrobble_info['artist_orig']
        lastfm_track = scrobble_info['track_orig']

        raw_ts = scrobble_info.get('timestamps', []) or []
        ts_by_album = {}
        for rec in raw_ts:
            album_name = (rec.get('album') or '').strip()
            ts_by_album.setdefault(album_name, []).append(int(rec['timestamp']))
        all_ts_sorted = sorted((t for grp in ts_by_album.values() for t in grp), reverse=True)
        newest_ts = all_ts_sorted[0] if all_ts_sorted else 0

        # Consult cache to see if we've already backfilled this Last.fm track up to newest_ts
        try:
            marker = cache.get_backfill_marker(lastfm_artist, lastfm_track)
        except Exception:
            marker = None
        if marker and marker >= newest_ts:
            # Already backfilled up to this timestamp; skip
            continue

        album_divide_result = None
        if len(duplicates) > 1 and ALBUM_MATCHING_MODE == 'album_aware':
            album_counts = cache.get_album_scrobble_counts(scrobble_info['artist_orig'], scrobble_info['track_orig'])
            album_divide_result = calculate_album_divide(duplicates, scrobble_info, album_counts=album_counts if album_counts else None)

        # Assign timestamps per duplicate
        assigned_timestamps = {}
        if album_divide_result is not None:
            remaining = {a: sorted(lst, reverse=True) for a, lst in ts_by_album.items()}
            for dup in duplicates:
                assigned = []
                dup_album_norm = (dup.get('album') or '').strip().lower()
                matched_key = None
                for a in remaining.keys():
                    if a.strip().lower() == dup_album_norm and remaining[a]:
                        matched_key = a
                        break
                cnt = int(album_divide_result.get(dup['id'], 0))
                if matched_key:
                    assigned = remaining[matched_key][:cnt]
                    remaining[matched_key] = remaining[matched_key][cnt:]
                else:
                    take = []
                    for a in list(remaining.keys()):
                        if not remaining[a]:
                            continue
                        need = cnt - len(take)
                        if need <= 0:
                            break
                        take.extend(remaining[a][:need])
                        remaining[a] = remaining[a][need:]
                    if len(take) < cnt:
                        extra_needed = cnt - len(take)
                        extra = []
                        for a in list(remaining.keys()):
                            if remaining[a]:
                                extra.append(remaining[a].pop(0))
                                if len(extra) >= extra_needed:
                                    break
                        take.extend(extra)
                    assigned = take
                assigned_timestamps[dup['id']] = assigned
        else:
            for dup in duplicates:
                if len(duplicates) == 1:
                    assigned_timestamps[dup['id']] = all_ts_sorted
                else:
                    assigned_timestamps[dup['id']] = []

        # For each duplicate, if nav playcount equals target, insert missing timestamps
        for dup in duplicates:
            tid = dup['id']
            target_count = len(assigned_timestamps.get(tid, [])) if album_divide_result is not None else len(all_ts_sorted) if len(duplicates) == 1 else 0
            nav_count, nav_starred, nav_played_ts = get_annotation_playcount_starred(conn, tid, user_id)
            if nav_count != target_count or target_count == 0:
                continue

            # find missing timestamps
            existing = get_existing_scrobble_times(conn, tid, user_id)
            missing_ts = [t for t in assigned_timestamps.get(tid, []) if int(t) not in existing]
            if not missing_ts:
                # Nothing to insert; update marker so we skip this group next time
                try:
                    cache.set_backfill_marker(lastfm_artist, lastfm_track, newest_ts)
                except Exception:
                    pass
                continue

            inserted = insert_scrobbles(conn, tid, user_id, missing_ts)
            if inserted:
                inserted_total += inserted
                cache.mark_scrobbles_synced_timestamps(scrobble_info['artist_orig'], scrobble_info['track_orig'], missing_ts)
                print(f"➕ Backfilled {inserted} scrobble{'s' if inserted != 1 else ''} for {dup['artist']} - {dup['title']}")
                # Update backfill marker to newest timestamp for this Last.fm track
                try:
                    cache.set_backfill_marker(lastfm_artist, lastfm_track, newest_ts)
                except Exception:
                    pass

    print(f"✅ Backfill complete: {inserted_total} scrobbles added")


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
    print(f"� Play count sync: {'enabled' if SYNC_PLAYCOUNT else 'disabled (loved tracks only)'}")
    print(f"�📋 Conflict resolution mode: {conflict_mode_desc.get(PLAYCOUNT_CONFLICT_RESOLUTION, PLAYCOUNT_CONFLICT_RESOLUTION)}")
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


def apply_updates(conn, cache: ScrobbleCache, differences, user_id: int | str | None):
    print(f"\nTracks with possible updates: {len(differences)}\n")
    show_conflict_mode()

    for d in differences:
        diff_str = f"{d['lastfm'] - d['navidrome']:+d}"
        album_info = f" [{d['album']}]" if d.get('album') else ""
        print(f"  - {d['artist']} - {d['title']}{album_info}")
        print(f"    Navidrome: {d['navidrome']} | Last.fm: {d['lastfm']} | Diff: {diff_str} | Loved: {d['loved']}")

    if AUTO_CONFIRM:
        print("\n⚡ AUTO_CONFIRM is enabled, proceeding automatically.")
    elif not prompt_yes_no("\nProceed with reviewing and updating these tracks? [y/N]: ", default=False):
        print("🧪 Dry run complete. No changes made.")
        return

    updated_playcounts = 0
    updated_loved = 0
    conflicts_resolved = 0
    updated_track_ids = []  # Track which tracks were updated
    all_processed_track_ids = []  # Track all tracks processed (for aggregation)

    for d in differences:
        nav = d['navidrome']
        lastfm = d['lastfm']
        artist, title = d['artist'], d['title']
        
        all_processed_track_ids.append(d['id'])  # Track this for later aggregation

        # If play count sync is disabled, leave counts untouched
        if not SYNC_PLAYCOUNT:
            new_count = nav
            conflict = False
            changed = False
        # If this track came from an album distribution decision, use that count directly
        # without asking again (user already decided via album mismatch prompt)
        elif d.get('from_distribution', False):
            new_count = lastfm
            conflict = nav != lastfm
            changed = new_count != nav
        else:
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

        update_annotation(conn, d['id'], new_count, d['last_played'], d['loved'], user_id, loved_at=d.get('loved_at'))

        # Only insert scrobbles and mark cache when we actually updated playcount
        lastfm_artist = d.get('lastfm_artist', d['artist'])
        lastfm_track = d.get('lastfm_track', d['title'])

        if SYNC_PLAYCOUNT and (new_count != d['navidrome']):
            try:
                timestamps = d.get('timestamps', []) or []
                # Only attempt to insert timestamps that are not already present in Navidrome
                try:
                    existing = get_existing_scrobble_times(conn, d['id'], user_id)
                    missing_ts = [int(t) for t in timestamps if int(t) not in existing]
                except Exception:
                    missing_ts = [int(t) for t in timestamps]

                inserted = insert_scrobbles(conn, d['id'], user_id, missing_ts)
                if inserted:
                    print(f"➕ Added {inserted} scrobble row{'s' if inserted != 1 else ''} to Navidrome for {d['artist']} - {d['title']}")
                    # Mark only the timestamps we wrote as synced in the cache
                    try:
                        if timestamps:
                            # Mark only those cached timestamps that we wrote (or attempted)
                            marked = cache.mark_scrobbles_synced_timestamps(lastfm_artist, lastfm_track, missing_ts if missing_ts else timestamps)
                            if marked:
                                print(f"   ✅ Marked {marked} cached scrobble{'s' if marked != 1 else ''} as synced")
                    except Exception:
                        pass
                else:
                    # No scrobbles inserted (table missing or duplicates) — mark all as synced
                    cache.mark_scrobbles_synced(lastfm_artist, lastfm_track)
            except Exception:
                cache.mark_scrobbles_synced(lastfm_artist, lastfm_track)
        else:
            # Playcount was not changed — do not insert scrobbles or mark cache
            pass

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

    # Update artist and album play counts for all processed tracks (includes duplicates)
    # This ensures complete aggregation even if some duplicates didn't change
    print("\n🎨 Updating artist and album play counts...")
    artists_updated = update_artist_play_counts(conn, user_id, all_processed_track_ids)
    albums_updated = update_album_play_counts(conn, user_id, all_processed_track_ids)
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


def sync_stars_to_lastfm(navidrome_stars_to_sync, cache):
    """
    Sync Navidrome starred tracks TO Last.fm as loved tracks.
    Only syncs tracks that aren't already loved on Last.fm.
    
    Args:
        navidrome_stars_to_sync: List of dicts with 'artist', 'track', 'nav_artist', 'nav_track'
        cache: ScrobbleCache instance to check Last.fm loved tracks
    """
    if not navidrome_stars_to_sync:
        return
    
    # Get Last.fm loved tracks to avoid syncing ones already loved
    lastfm_loved = cache.get_all_loved_tracks()
    lastfm_loved_set = {(t['artist'], t['track']) for t in lastfm_loved}
    
    # Filter out tracks already loved on Last.fm
    to_sync = []
    already_loved = 0
    for track_info in navidrome_stars_to_sync:
        key = (track_info['artist'], track_info['track'])
        if key not in lastfm_loved_set:
            to_sync.append(track_info)
        else:
            already_loved += 1
    
    if already_loved > 0:
        print(f"\n   (Skipped {already_loved} duplicate loved track{'s' if already_loved != 1 else ''} since Last.fm can't have separate loved tracks per album)")
    
    if not to_sync:
        print("   No new tracks to sync.")
        return
    
    # Deduplicate by Last.fm artist/track (Last.fm only has one entry per track)
    # If multiple Navidrome duplicates are starred, we only need to sync once
    seen_tracks = set()
    deduplicated = []
    for track_info in to_sync:
        key = (track_info['artist'], track_info['track'])
        if key not in seen_tracks:
            deduplicated.append(track_info)
            seen_tracks.add(key)
    
    if len(deduplicated) < len(to_sync):
        print(f"   (Deduplicated: {len(to_sync)} Navidrome entries → {len(deduplicated)} unique Last.fm tracks)")
        to_sync = deduplicated
    
    print(f"\n💝 Syncing {len(to_sync)} Navidrome stars to Last.fm...")
    print("   (Navidrome starred → Last.fm loved)\n")
    
    for track_info in to_sync:
        print(f"  - {track_info['nav_artist']} - {track_info['nav_track']}")
    
    if not prompt_yes_no("\nProceed with syncing these tracks to Last.fm? [y/N]: ", default=False):
        print("⏭️  Skipped syncing stars to Last.fm.")
        return
    
    synced_count = 0
    failed_count = 0
    
    for track_info in to_sync:
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
    validate_config()
    print_header()
    try:
        cache = ScrobbleCache(CACHE_DB_PATH)
        show_cache_stats(cache)
        all_scrobbles = fetch_and_update_cache(cache)
        ensure_navidrome_stopped()
        warn_if_navidrome_id_migration_likely()
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
        differences, navidrome_stars_to_sync, potential_duplicates, potential_duplicates_agnostic = compute_differences(conn, tracks, aggregated_scrobbles, user_id, cache)
        write_missing_reports(aggregated_scrobbles, tracks, cache, (ALBUM_MATCHING_MODE == "album_aware"))
        
        # Sync Navidrome stars TO Last.fm if enabled
        if SYNC_LOVED_TO_LASTFM and navidrome_stars_to_sync:
            sync_stars_to_lastfm(navidrome_stars_to_sync, cache)
        
        if differences:
            apply_updates(conn, cache, differences, user_id)
        else:
            print("\n✅ All tracks are already in sync!")
            # Attempt to backfill scrobbles even when playcounts match
            backfill_scrobbles(conn, cache, aggregated_scrobbles, tracks, user_id, potential_duplicates, potential_duplicates_agnostic)
    finally:
        close_db(conn)

if __name__ == "__main__":
    main()