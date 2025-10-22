import sqlite3
import json
from datetime import datetime, timezone
from src.config import NAVIDROME_DB_PATH, MISSING_SCROBBLES, MISSING_LOVED, CACHE_DB_PATH, PLAYCOUNT_CONFLICT_RESOLUTION
from src.db import get_navidrome_user_id, get_all_tracks, get_annotation_playcount_starred, update_annotation
from src.lastfm import fetch_all_lastfm_scrobbles, fetch_loved_tracks
from src.utils import make_key, aggregate_scrobbles, group_missing_by_artist_album
from src.cache import ScrobbleCache

def main():
    # Validate configuration before starting
    from src.config import validate_config
    validate_config()
    
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
        
        # Fetch new scrobbles from Last.fm (only since last cached timestamp)
        latest_timestamp = cache.get_latest_scrobble_timestamp()
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
                if d['loved'] and not d['nav_starred']:
                    updated_loved += 1

                update_annotation(conn, d['id'], new_count, d['last_played'], d['loved'], NAVIDROME_USER_ID)

                # Mark this track as synced in cache
                cache.mark_scrobbles_synced(artist, title)

                # Log concise summary
                if new_count != nav:
                    if PLAYCOUNT_CONFLICT_RESOLUTION == "increment":
                        print(f"➕ Incremented playcount: {artist} - {title} ({nav} + {lastfm} = {new_count})")
                    else:
                        print(f"✅ Updated playcount: {artist} - {title} ({nav} → {new_count})")
                elif d['loved'] and not d['nav_starred']:
                    print(f"⭐ Starred: {artist} - {title}")
                elif PLAYCOUNT_CONFLICT_RESOLUTION != "ask" and nav > lastfm:
                    # Show when we kept Navidrome's higher count (non-interactive modes)
                    print(f"ℹ️  Kept Navidrome count: {artist} - {title} (Navidrome: {nav}, Last.fm: {lastfm})")
            
            # Update sync timestamp
            cache.set_metadata('last_sync_time', datetime.now(timezone.utc).isoformat())
            
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

if __name__ == "__main__":
    main()