"""
NaviSync - Sync Last.fm scrobbles to Navidrome using API or Database access.

This script can sync play counts and loved tracks from Last.fm to Navidrome using:
1. Navidrome API (safer, works while Navidrome is running)
2. Direct Database (faster, requires Navidrome to be stopped)
"""

import sys
import sqlite3
import json
import time
from datetime import datetime, timezone
from tqdm import tqdm
from src.config import (NAVIDROME_URL, NAVIDROME_USER, NAVIDROME_PASSWORD, 
                       NAVIDROME_DB_PATH, CACHE_DB_PATH, MISSING_SCROBBLES, 
                       MISSING_LOVED, PLAYCOUNT_CONFLICT_RESOLUTION)
from src.api import NavidromeAPI
from src.lastfm import fetch_all_lastfm_scrobbles, fetch_loved_tracks
from src.utils import make_key, aggregate_scrobbles, group_missing_by_artist_album
from src.cache import ScrobbleCache

# Database imports (only used in database mode)
try:
    from src.db import (get_navidrome_user_id, get_all_tracks, 
                       get_annotation_playcount_starred, update_annotation, 
                       check_navidrome_active, update_artist_play_counts, 
                       update_album_play_counts)
    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False


def choose_sync_mode():
    """Let user choose between API and Database sync modes."""
    print("\n" + "="*60)
    print("🔄 NaviSync - Choose Sync Mode")
    print("="*60)
    print("1. API Mode (Recommended)")
    print("   ✅ Safe - Navidrome can stay running")
    print("   ✅ Uses Subsonic API")
    print("   ⚠️  Slower for large libraries")
    print()
    print("2. Database Mode")
    print("   ✅ Fast - Direct database access")
    print("   ✅ No client bugs")
    print("   ⚠️  Requires Navidrome to be stopped")
    print("   ⚠️  Direct database modification")
    
    if not DATABASE_AVAILABLE:
        print("\n❌ Database mode not available (src.db module missing)")
        return "api"
    
    while True:
        choice = input("\nChoose mode [1=API, 2=Database]: ").strip()
        if choice in ['1', 'api', 'API']:
            return "api"
        elif choice in ['2', 'database', 'db', 'Database']:
            return "database"
        else:
            print("Invalid choice. Please enter 1 or 2.")


def main_api_mode():
    """Main sync function using Navidrome API."""
    print("\n🌐 Using API Mode")
    print("="*60)
    
    try:
        # Initialize Navidrome API client
        print("🔗 Connecting to Navidrome...")
        
        # Type assertions for config values (validated by validate_config())
        assert NAVIDROME_URL is not None, "NAVIDROME_URL must be set"
        assert NAVIDROME_USER is not None, "NAVIDROME_USER must be set"
        assert NAVIDROME_PASSWORD is not None, "NAVIDROME_PASSWORD must be set"
        
        api = NavidromeAPI(NAVIDROME_URL, NAVIDROME_USER, NAVIDROME_PASSWORD)
        
        if not api.ping():
            print("❌ Error: Cannot connect to Navidrome!")
            print(f"   URL: {NAVIDROME_URL}")
            print("   Please check:")
            print("   - Navidrome is running")
            print("   - NAVIDROME_URL is correct")
            print("   - Username and password are correct\n")
            sys.exit(1)
        
        print(f"✅ Connected to Navidrome at {NAVIDROME_URL}\n")
        
        # Initialize cache
        cache = ScrobbleCache(CACHE_DB_PATH)
        
        # Show cache stats
        stats = cache.get_cache_stats()
        if stats['total_scrobbles'] > 0:
            print("📊 Cache Statistics:")
            print(f"  Total cached scrobbles: {stats['total_scrobbles']:,}")
            print(f"  Synced: {stats['synced_scrobbles']:,} | Unsynced: {stats['unsynced_scrobbles']:,}")
            print(f"  Loved tracks: {stats['loved_tracks']:,}")
            print(f"  Date range: {stats['oldest_scrobble']} → {stats['newest_scrobble']}\n")
        else:
            print("📊 Cache is empty - this appears to be your first run.\n")
        
        # Fetch new scrobbles from Last.fm
        latest_timestamp = cache.get_latest_scrobble_timestamp()
        if latest_timestamp > 0:
            print(f"🔄 Checking for new scrobbles since {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(latest_timestamp))}...")
        
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
        
        # Get unsynced scrobbles (ones we haven't submitted to Navidrome yet)
        unsynced = cache.get_unsynced_scrobbles()
        
        if not unsynced:
            print("✅ All scrobbles are already synced to Navidrome!\n")
            return
        
        # Aggregate unsynced scrobbles by artist/track
        aggregated = aggregate_scrobbles(unsynced)
        
        print(f"📝 Found {len(aggregated)} unique tracks with unsynced scrobbles")
        print(f"   Total unsynced plays: {len(unsynced):,}\n")
        
        # Step 1: Search for all tracks and check their current state
        print("🔍 Searching for tracks in Navidrome and checking current state...")
        from src.utils import first_artist
        tracks_to_search = [(first_artist(info['artist_orig']), info['track_orig']) 
                           for info in aggregated.values()]
        search_results = api.search_tracks_parallel(tracks_to_search, max_workers=3)
        
        found_tracks = sum(1 for tid in search_results.values() if tid)
        not_found_tracks = sum(1 for tid in search_results.values() if not tid)
        print(f"✅ Search complete: {found_tracks} found, {not_found_tracks} not found\n")
        
        # Generate missing tracks analysis using the search results we already have
        print("💾 Generating missing tracks analysis from search results...")
        
        # Get ALL scrobbles from cache for complete analysis
        all_scrobbles = cache.get_all_scrobbles()
        all_aggregated = aggregate_scrobbles(all_scrobbles)
        
        # Create a "fake" tracks list from our search results for the grouping function
        # This represents the tracks we know exist in Navidrome
        found_navidrome_tracks = []
        for (normalized_artist, title), track_id in search_results.items():
            if track_id:  # Only tracks that were found
                found_navidrome_tracks.append({
                    'id': track_id,
                    'artist': normalized_artist,
                    'title': title
                })
        
        # Generate missing tracks analysis (tracks in Last.fm but not found in search)
        missing_scrobbles_grouped, missing_loved_grouped = group_missing_by_artist_album(all_aggregated, found_navidrome_tracks)
        
        with open(MISSING_SCROBBLES, "w", encoding="utf-8") as f:
            json.dump(missing_scrobbles_grouped, f, indent=2, ensure_ascii=False)
        print(f"✅ Missing scrobbles saved to {MISSING_SCROBBLES}")
        with open(MISSING_LOVED, "w", encoding="utf-8") as f:
            json.dump(missing_loved_grouped, f, indent=2, ensure_ascii=False)
        print(f"✅ Missing loved tracks saved to {MISSING_LOVED}")
        print(f"ℹ️  Analysis based on {len(found_navidrome_tracks)} tracks found during sync search\n")
        
        # Step 2: Check current play counts for found tracks
        tracks_needing_sync = []
        tracks_already_synced = 0
        
        print("� Checking current play counts...")
        with tqdm(total=found_tracks, desc="Checking tracks", unit="track") as pbar:
            for key, info in aggregated.items():
                artist = info['artist_orig']
                title = info['track_orig']
                timestamps = info['timestamps']
                loved = info['loved']
                
                normalized_artist = first_artist(artist)
                track_id = search_results.get((normalized_artist, title))
                
                if not track_id:
                    continue  # Skip tracks not found in Navidrome
                
                # Check current state
                track_info = api.get_track_info(track_id)
                if track_info:
                    current_count = track_info['play_count']
                    lastfm_count = len(timestamps)
                    currently_starred = track_info['starred']
                    
                    # Check if sync is actually needed (match database mode logic)
                    # Only sync if counts don't match OR if track needs to be starred
                    needs_update = (current_count != lastfm_count) or (loved and not currently_starred)
                    
                    if needs_update:
                        tracks_needing_sync.append((key, info, track_id))
                    else:
                        # Already correct - mark as synced
                        cache.mark_scrobbles_synced(artist, title)
                        tracks_already_synced += 1
                
                pbar.update(1)
        
        print(f"✅ Analysis complete:")
        print(f"   Tracks needing sync: {len(tracks_needing_sync)}")
        print(f"   Tracks already correct: {tracks_already_synced}")
        print(f"   Tracks not found: {not_found_tracks}\n")
        
        if not tracks_needing_sync:
            print("✅ All found tracks are already in sync!\n")
            return
        
        # Calculate actual API calls needed
        total_scrobbles_needed = sum(len(info['timestamps']) for _, info, _ in tracks_needing_sync)
        print(f"⚠️  Will submit {total_scrobbles_needed:,} scrobbles for {len(tracks_needing_sync)} tracks.")
        print("   This is much more efficient than the initial estimate!\n")
        
        proceed = input("Proceed with API sync? [y/N]: ").strip().lower()
        if proceed != 'y':
            print("\n🧪 Dry run complete. No changes made.")
            return
        
        # Sync only the tracks that actually need it
        print("\n🔄 Starting sync...\n")
        total_tracks = len(tracks_needing_sync)
        successful_tracks = 0
        failed_tracks = 0
        total_scrobbles_submitted = 0
        total_scrobbles_failed = 0
        total_starred = 0
        
        # Submit scrobbles for tracks that need syncing
        print("📝 Submitting scrobbles...\n")
        
        with tqdm(total=total_tracks, desc="Syncing tracks", unit="track", 
                  bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]') as pbar:
            
            for key, info, track_id in tracks_needing_sync:
                artist = info['artist_orig']
                title = info['track_orig']
                timestamps = info['timestamps']
                loved = info['loved']
                
                # Truncate long track names for progress bar
                display_name = f"{artist} - {title}"
                if len(display_name) > 60:
                    display_name = display_name[:57] + "..."
                
                pbar.set_postfix_str(display_name)
                
                # Check what type of sync is needed
                track_info = api.get_track_info(track_id)
                if track_info:
                    current_count = track_info['play_count']
                    lastfm_count = len(timestamps)
                    currently_starred = track_info['starred']
                    
                    # Only submit scrobbles if we need to increase the count
                    if current_count < lastfm_count:
                        # Submit only the difference in scrobbles
                        scrobbles_to_submit = lastfm_count - current_count
                        timestamps_to_submit = timestamps[-scrobbles_to_submit:]  # Use most recent
                        success, failed = api.bulk_scrobble_track(track_id, timestamps_to_submit, rate_limit_delay=0.1)
                        total_scrobbles_submitted += success
                        total_scrobbles_failed += failed
                    else:
                        # Count is already correct or higher, no scrobbles needed
                        success = 0
                        failed = 0
                    
                    # Star the track if needed
                    if loved and not currently_starred:
                        if api.star_track(track_id):
                            total_starred += 1
                else:
                    # Fallback: submit all scrobbles if we can't get track info
                    success, failed = api.bulk_scrobble_track(track_id, timestamps, rate_limit_delay=0.1)
                    total_scrobbles_submitted += success
                    total_scrobbles_failed += failed
                
                if failed > 0:
                    failed_tracks += 1
                else:
                    successful_tracks += 1
                
                # Mark as synced in cache
                cache.mark_scrobbles_synced(artist, title)
                pbar.update(1)
        
        print()  # New line after progress bar
        
        # Update sync timestamp
        cache.set_metadata('last_sync_time', datetime.now(timezone.utc).isoformat())
        
        # Show summary
        print(f"\n{'='*60}")
        print(f"✅ Sync complete!")
        print(f"{'='*60}")
        print(f"   Tracks processed: {total_tracks}")
        print(f"   Successfully synced: {successful_tracks}")
        print(f"   Failed/Not found: {failed_tracks}")
        print(f"   Scrobbles submitted: {total_scrobbles_submitted}")
        if total_scrobbles_failed > 0:
            print(f"   Scrobbles failed: {total_scrobbles_failed}")
        if total_starred > 0:
            print(f"   Tracks starred: {total_starred}")
        print(f"{'='*60}")
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Sync cancelled by user.")
        return
    except Exception as e:
        print(f"\n❌ Error during sync: {e}")
        import traceback
        traceback.print_exc()
        return


def main_database_mode():
    """Main sync function using direct database access (original implementation)."""
    print("\n💾 Using Database Mode")
    print("="*60)
    print("⚠️  Requires Navidrome to be stopped!")
    
    try:
        # Initialize cache
        cache = ScrobbleCache(CACHE_DB_PATH)

        # Show cache stats
        stats = cache.get_cache_stats()
        if stats['total_scrobbles'] > 0:
            print("📊 Cache Statistics:")
            print(f"  Total cached scrobbles: {stats['total_scrobbles']:,}")
            print(f"  Synced: {stats['synced_scrobbles']:,} | Unsynced: {stats['unsynced_scrobbles']:,}")
            print(f"  Loved tracks: {stats['loved_tracks']:,}")
            print(f"  Date range: {stats['oldest_scrobble']} → {stats['newest_scrobble']}\n")
        else:
            print("📊 Cache is empty - this appears to be your first run.\n")

        # Fetch new scrobbles from Last.fm (only since last cached timestamp)
        latest_timestamp = cache.get_latest_scrobble_timestamp()
        if latest_timestamp > 0:
            print(f"🔄 Checking for new scrobbles since {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(latest_timestamp))}...")
        
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

        # CRITICAL: Check if Navidrome is running before accessing database
        print("🔍 Checking if Navidrome is stopped...")
        
        # First check: Try to ping Navidrome API to see if it's running
        if NAVIDROME_URL and NAVIDROME_USER and NAVIDROME_PASSWORD:
            print(f"   Testing API connection to {NAVIDROME_URL}...")
            try:
                api = NavidromeAPI(NAVIDROME_URL, NAVIDROME_USER, NAVIDROME_PASSWORD)
                if api.ping():
                    print(f"❌ ERROR: Navidrome is still running!")
                    print(f"   API responded successfully at {NAVIDROME_URL}")
                    print("⚠️  CANNOT proceed - Navidrome must be stopped before database sync!")
                    print("    This prevents database corruption from simultaneous access.")
                    print("\n   Please stop Navidrome and try again.\n")
                    sys.exit(1)
                else:
                    print("   ✅ API not responding - Navidrome appears to be stopped")
            except Exception:
                print("   ✅ API not responding - Navidrome appears to be stopped")
        else:
            print("   ⚠️  Cannot test API (missing connection details) - checking database lock only")
        
        # Second check: Database-level check
        is_active, reason = check_navidrome_active(NAVIDROME_DB_PATH, navidrome_api_url=None)

        if is_active:
            print(f"❌ ERROR: {reason}")
            print("⚠️  CANNOT proceed - Database appears to be in use!")
            print("    This prevents database corruption from simultaneous access.\n")
            sys.exit(1)

        print(f"✅ {reason}\n")

        # Get Navidrome data
        NAVIDROME_USER_ID = get_navidrome_user_id(NAVIDROME_DB_PATH)
        tracks = get_all_tracks(NAVIDROME_DB_PATH)

        if not tracks:
            print("⚠️  No tracks found in Navidrome database. Make sure your Navidrome library is scanned.\n")
            return

        # Aggregate scrobbles
        aggregated_scrobbles = aggregate_scrobbles(all_scrobbles)

    except KeyboardInterrupt:
        print("\n\n⚠️  Sync cancelled by user.")
        return
    except Exception as e:
        print(f"\n❌ Error during initialization: {e}")
        return

    if not NAVIDROME_DB_PATH:
        print("❌ Error: NAVIDROME_DB_PATH is not configured")
        return

    try:
        conn = sqlite3.connect(NAVIDROME_DB_PATH)
    except sqlite3.Error as e:
        print(f"❌ Error connecting to Navidrome database: {e}")
        return

    differences = []
    total = len(tracks)
    tracks_with_scrobbles = 0

    print(f"\n🔍 Comparing {total:,} tracks with Last.fm data...\n")

    for i, t in enumerate(tracks, 1):
        track_id = t['id']
        artist = t['artist']
        title = t['title']
        key = make_key(artist, title)
        agg = aggregated_scrobbles.get(key, {'timestamps': [], 'loved': False})

        # Show progress indicator
        if i % 100 == 0 or i == total:
            percentage = (i / total) * 100
            print(f"[{i:,}/{total:,}] ({percentage:.1f}%) Processing tracks...", end='\r')

        nav_count, nav_starred, nav_played_ts = get_annotation_playcount_starred(conn, track_id, NAVIDROME_USER_ID)
        track_scrobbles = agg['timestamps']
        lastfm_count = len(track_scrobbles)
        last_played = max(track_scrobbles) if track_scrobbles else None
        loved = agg['loved']

        if lastfm_count > 0:
            tracks_with_scrobbles += 1

        if lastfm_count != nav_count or (loved and not nav_starred):
            differences.append({
                'id': track_id,
                'artist': artist,
                'title': title,
                'navidrome': nav_count,
                'nav_starred': nav_starred,
                'lastfm': lastfm_count,
                'nav_played': nav_played_ts,
                'last_played': last_played,
                'loved': loved
            })

    print(f"\n✅ Processing complete! Found {tracks_with_scrobbles:,} tracks with Last.fm scrobbles.\n")

    print("💾 Generating missing tracks analysis from search results...")
    missing_scrobbles_grouped, missing_loved_grouped = group_missing_by_artist_album(aggregated_scrobbles, tracks)
    with open(MISSING_SCROBBLES, "w", encoding="utf-8") as f:
        json.dump(missing_scrobbles_grouped, f, indent=2, ensure_ascii=False)
    print(f"✅ Missing from scrobbles saved to {MISSING_SCROBBLES}")
    with open(MISSING_LOVED, "w", encoding="utf-8") as f:
        json.dump(missing_loved_grouped, f, indent=2, ensure_ascii=False)
    print(f"✅ Missing loved tracks saved to {MISSING_LOVED}")

    if differences:
        print(f"\nTracks with possible updates: {len(differences)}\n")

        # Show conflict resolution mode
        conflict_mode_desc = {
            "ask": "interactive (will prompt for each conflict)",
            "navidrome": "always keep Navidrome when higher",
            "lastfm": "always use Last.fm",
            "higher": "always use higher count",
            "increment": "add Last.fm count to Navidrome count"
        }
        print(f"📋 Conflict resolution mode: {conflict_mode_desc.get(PLAYCOUNT_CONFLICT_RESOLUTION, PLAYCOUNT_CONFLICT_RESOLUTION)}\n")

        for d in differences:
            diff_str = f"{d['lastfm'] - d['navidrome']:+d}"
            print(f"  - {d['artist']} - {d['title']}")
            print(f"    Navidrome: {d['navidrome']} | Last.fm: {d['lastfm']} | Diff: {diff_str} | Loved: {d['loved']}")

        proceed = input("\nProceed with reviewing and updating these tracks? [y/N]: ").strip().lower()
        if proceed == 'y':
            updated_playcounts = 0
            updated_loved = 0
            conflicts_resolved = 0
            updated_track_ids = []  # Track which tracks were updated

            for d in differences:
                nav = d['navidrome']
                lastfm = d['lastfm']
                artist, title = d['artist'], d['title']

                # Decide which playcount to use based on configuration
                if PLAYCOUNT_CONFLICT_RESOLUTION == "increment":
                    # Always increment: add Last.fm count to Navidrome count
                    new_count = nav + lastfm
                    updated_playcounts += 1
                    if nav != lastfm:  # Only count as conflict if they're different
                        conflicts_resolved += 1
                elif lastfm > nav:
                    # Last.fm count is higher - always use it (except in increment mode)
                    new_count = lastfm
                    updated_playcounts += 1
                elif nav > lastfm:
                    # Navidrome count is higher - use conflict resolution strategy
                    if PLAYCOUNT_CONFLICT_RESOLUTION == "ask":
                        # Interactive mode: ask user
                        print(f"\n🎵 {artist} - {title}")
                        print(f"   Navidrome: {nav} | Last.fm: {lastfm}")
                        choice = input("   → Navidrome playcount is higher. Keep Navidrome (N) or use Last.fm (L)? [N/L, default=N]: ").strip().lower()
                        new_count = nav if choice in ('', 'n') else lastfm
                        conflicts_resolved += 1
                    elif PLAYCOUNT_CONFLICT_RESOLUTION == "navidrome":
                        # Always keep Navidrome when it's higher
                        new_count = nav
                        conflicts_resolved += 1
                    elif PLAYCOUNT_CONFLICT_RESOLUTION == "lastfm":
                        # Always use Last.fm (overwrite Navidrome)
                        new_count = lastfm
                        conflicts_resolved += 1
                        updated_playcounts += 1
                    elif PLAYCOUNT_CONFLICT_RESOLUTION == "higher":
                        # Use higher count (which is Navidrome in this branch)
                        new_count = nav
                        conflicts_resolved += 1
                    else:
                        # Fallback to Navidrome if somehow invalid
                        new_count = nav

                    if new_count != nav:
                        updated_playcounts += 1
                else:
                    # Counts are equal - no change needed (unless increment mode handled it above)
                    new_count = nav

                # Check if loved status will be updated
                will_update_loved = d['loved'] and not d['nav_starred']
                if will_update_loved:
                    updated_loved += 1

                # Track if this record was actually modified
                track_was_updated = (new_count != nav) or will_update_loved
                if track_was_updated:
                    updated_track_ids.append(d['id'])

                update_annotation(conn, d['id'], new_count, d['last_played'], d['loved'], NAVIDROME_USER_ID)

                # Mark this track as synced in cache
                cache.mark_scrobbles_synced(artist, title)

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
            artists_updated = update_artist_play_counts(conn, NAVIDROME_USER_ID, updated_track_ids)
            albums_updated = update_album_play_counts(conn, NAVIDROME_USER_ID, updated_track_ids)
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
        else:
            print("🧪 Dry run complete. No changes made.")
    else:
        print("\n✅ All tracks are already in sync!")

    conn.close()

    # Wait 2 seconds to ensure database connection is fully released
    print("🔒 Closing database connection...")
    time.sleep(2)


def main():
    """Main entry point - choose sync mode and execute."""
    # Validate configuration
    from src.config import validate_config
    validate_config()
    
    mode = choose_sync_mode()
    
    if mode == "api":
        main_api_mode()
    elif mode == "database":
        main_database_mode()


if __name__ == "__main__":
    main()