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
                       MISSING_LOVED, PLAYCOUNT_CONFLICT_RESOLUTION)
from src.lastfm import fetch_all_lastfm_scrobbles, fetch_loved_tracks
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


def compute_differences(conn, tracks, aggregated_scrobbles, user_id, cache):
    differences = []
    total_tracks = len(tracks)
    tracks_with_scrobbles = 0

    print(f"\n🔍 Matching {total_tracks:,} Navidrome tracks with Last.fm scrobbles...\n")

    # Process Navidrome tracks and find Last.fm matches
    for i, nav_track in enumerate(tracks, 1):
        if i % 100 == 0 or i == total_tracks:
            percentage = (i / total_tracks) * 100
            print(f"[{i:,}/{total_tracks:,}] ({percentage:.1f}%) Processing tracks...", end='\r')

        # Try to find a Last.fm match for this Navidrome track
        scrobble_info = get_lastfm_match_for_navidrome_track(
            navidrome_track=nav_track,
            aggregated_scrobbles=aggregated_scrobbles,
            cache=cache,
            fuzzy_threshold=85
        )

        if not scrobble_info:
            continue  # No match found or was skipped

        tracks_with_scrobbles += 1
        
        track_id = nav_track['id']
        nav_count, nav_starred, nav_played_ts = get_annotation_playcount_starred(conn, track_id, user_id)
        
        track_scrobbles = scrobble_info['timestamps']
        lastfm_count = len(track_scrobbles)
        last_played = max(track_scrobbles) if track_scrobbles else None
        loved = scrobble_info['loved']

        if lastfm_count != nav_count or (loved and not nav_starred):
            differences.append({
                'id': track_id,
                'artist': nav_track['artist'],
                'title': nav_track['title'],
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
    print(f"   Matched tracks: {tracks_with_scrobbles:,}")
    print()
    return differences


def write_missing_reports(aggregated_scrobbles, tracks):
    print("💾 Generating missing tracks analysis from search results...")
    missing_scrobbles_grouped, missing_loved_grouped = group_missing_by_artist_album(aggregated_scrobbles, tracks)
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
    print(f"📋 Conflict resolution mode: {conflict_mode_desc.get(PLAYCOUNT_CONFLICT_RESOLUTION, PLAYCOUNT_CONFLICT_RESOLUTION)}\n")


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
        print(f"  - {d['artist']} - {d['title']}")
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
        aggregated_scrobbles = aggregate_scrobbles(all_scrobbles)
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
        differences = compute_differences(conn, tracks, aggregated_scrobbles, user_id, cache)
        write_missing_reports(aggregated_scrobbles, tracks)
        if differences:
            apply_updates(conn, cache, differences, user_id)
        else:
            print("\n✅ All tracks are already in sync!")
    finally:
        close_db(conn)

if __name__ == "__main__":
    main()