import sqlite3
import re
import json
from datetime import datetime, timezone
from src.config import NAVIDROME_DB_PATH, MISSING_SCROBBLES, MISSING_LOVED, CACHE_DB_PATH, PLAYCOUNT_CONFLICT_RESOLUTION
from src.config import USE_NAVIDROME_API
from src.db import get_navidrome_user_id, get_all_tracks, get_annotation_playcount_starred, update_annotation
from src.lastfm import fetch_all_lastfm_scrobbles, fetch_loved_tracks
from src.utils import make_key, aggregate_scrobbles, group_missing_by_artist_album
from src.cache import ScrobbleCache
from src.api import NavidromeAPI

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
        
        # Aggregate scrobbles
        aggregated_scrobbles = aggregate_scrobbles(all_scrobbles)
        
        # In DB mode, get Navidrome data from DB; in API mode we skip this entirely
        if not USE_NAVIDROME_API:
            NAVIDROME_USER_ID = get_navidrome_user_id(NAVIDROME_DB_PATH)
            tracks = get_all_tracks(NAVIDROME_DB_PATH)
            
            if not tracks:
                print("⚠️  No tracks found in Navidrome database. Make sure your Navidrome library is scanned.\n")
                return
        else:
            # API mode: no DB reads needed; we resolve on-the-fly
            tracks = []
            NAVIDROME_USER_ID = None
    
    except KeyboardInterrupt:
        print("\n\n⚠️  Sync cancelled by user.")
        return
    except Exception as e:
        print(f"\n❌ Error during initialization: {e}")
        return

    # Prepare differences depending on mode
    differences = []
    # Track all indexed Navidrome tracks for missing reports (API mode needs this)
    indexed_tracks = []
    
    if USE_NAVIDROME_API:
        # Pure API mode: don't open or read Navidrome DB; resolve via API search and song details
        api_client = NavidromeAPI()
        total_tracks = len(aggregated_scrobbles)
        print(f"\n🔍 Resolving {total_tracks:,} tracks via API...\n")
        
        def _strip_status_suffix(title: str, status) -> str:
            """Strip trailing (status) or [status] from a title using explicitStatus.
            Only removes the exact status token (e.g., explicit, clean), case-insensitive.
            """
            if not title:
                return ""
            st = (str(status or "").strip())
            if not st:
                return title
            pat_paren = re.compile(r"\s*\(" + re.escape(st) + r"\)\s*$", re.IGNORECASE)
            pat_brack = re.compile(r"\s*\[" + re.escape(st) + r"\]\s*$", re.IGNORECASE)
            title2 = pat_paren.sub("", title)
            title2 = pat_brack.sub("", title2)
            return title2
        
        # Pre-index Navidrome library via API if mapping cache is empty (or very small)
        mapping_count = cache.get_mapping_count()
        if mapping_count < 100:  # heuristic threshold
            print("📚 Building Navidrome library index via API (one-time)...")
            indexed = 0
            api_error = 0
            for s in api_client.iterate_all_songs():
                try:
                    artist = s.get('artist', '')
                    # Use API title and strip explicit/clean suffix if explicitStatus present (no sortName)
                    display_title = s.get('title', '')
                    title = _strip_status_suffix(display_title, s.get('explicitStatus')).strip()
                    song_id = s.get('id')
                    play_count = int(s.get('playCount', 0) or 0)
                    starred = bool(s.get('starred', False))
                    if not song_id or not artist or not title:
                        continue
                    key = make_key(artist, title)
                    key_str = f"{key[0]}||{key[1]}"
                    cache.set_mapped_song_state(key_str, str(song_id), play_count, starred, artist, display_title)
                    # Store track for matching using clean title; keep display_title for printing
                    indexed_tracks.append({
                        'id': song_id,
                        'artist': artist,
                        'title': title,
                        'display_title': display_title,
                        'display_artist': artist,
                        'explicit_status': s.get('explicitStatus')
                    })
                    indexed += 1
                    if indexed % 200 == 0:
                        print(f"  Indexed {indexed:,} songs...", end='\r')
                except Exception:
                    api_error += 1
            print(f"\n✅ Library index complete. Songs indexed: {indexed:,}; errors: {api_error}\n")
        else:
            print("ℹ️  Using existing Navidrome index from local cache.\n")
            # Rebuild indexed_tracks from cache, using normalized key for matching and title_orig for display
            cached_rows = cache.get_all_indexed_tracks()
            indexed_tracks = []
            for row in cached_rows:
                key_str = row.get('key') or ''
                if '||' in key_str:
                    artist_norm, title_norm = key_str.split('||', 1)
                else:
                    # Fallback to recomputing from stored artist/title
                    artist_norm, title_norm = make_key(row.get('artist',''), row.get('title',''))
                indexed_tracks.append({
                    'id': row['id'],
                    'artist': artist_norm,
                    'title': title_norm,
                    'display_title': row.get('title',''),
                    'display_artist': row.get('artist','')
                })
            if not indexed_tracks:
                print("⚠️  No original track data in cache. JSON reports require a fresh index (delete cache to regenerate).\n")

        # De-duplicate any duplicate Navidrome entries by song id (can occur via API traversal)
        if indexed_tracks:
            deduped = []
            seen_ids = set()
            skipped_dupes = 0
            for t in indexed_tracks:
                tid = t.get('id')
                if not tid:
                    continue
                if tid in seen_ids:
                    skipped_dupes += 1
                    continue
                seen_ids.add(tid)
                deduped.append(t)
            if skipped_dupes:
                print(f"ℹ️  Skipped {skipped_dupes:,} duplicate track entries (same Navidrome id).")
            indexed_tracks = deduped

        # Mirror DB mode logic: iterate through NAVIDROME tracks, look up Last.fm data
        print(f"\n🔍 Comparing {len(indexed_tracks):,} Navidrome tracks with Last.fm data...\n")
        processed = 0
        tracks_with_scrobbles = 0
        
        for t in indexed_tracks:
            processed += 1
            if processed % 100 == 0 or processed == len(indexed_tracks):
                percentage = (processed / len(indexed_tracks)) * 100
                print(f"[{processed:,}/{len(indexed_tracks):,}] ({percentage:.1f}%) Processing tracks...", end='\r')
            
            track_id = t['id']
            artist = t['artist']
            title = t['title']  # normalized match title (sortName when available)
            key = make_key(artist, title)
            key_str = f"{key[0]}||{key[1]}"

            # Build candidate keys to handle cases where Last.fm includes suffixes like (Explicit)
            candidate_titles = []
            base_titles = [title]
            disp_title = t.get('display_title')
            if disp_title:
                base_titles.append(disp_title)
            # Ensure uniqueness and non-empty
            base_titles = [bt for i, bt in enumerate(base_titles) if bt and bt not in base_titles[:i]]

            # Always try the clean and display titles first
            for bt in base_titles:
                candidate_titles.append(bt)

            # If we know explicit status, generate bracket/paren variants; also try generic explicit/clean variants
            explicit_status = (t.get('explicit_status') or '').strip().lower()
            status_variants = []
            if explicit_status in ('explicit', 'clean'):
                status_variants = [explicit_status]
            else:
                # Try both common markers as fallback when status unknown
                status_variants = ['explicit', 'clean']

            for bt in base_titles:
                for st in status_variants:
                    candidate_titles.append(f"{bt} ({st})")
                    candidate_titles.append(f"{bt} [{st}]")

            # Deduplicate candidate titles while preserving order
            seen_ct = set()
            unique_candidate_titles = []
            for ct in candidate_titles:
                if ct not in seen_ct:
                    seen_ct.add(ct)
                    unique_candidate_titles.append(ct)

            # Look up this Navidrome track in Last.fm aggregated data using the first candidate that matches
            best_agg = None
            best_key = None
            best_count = -1
            for ct in unique_candidate_titles:
                k = make_key(artist, ct)
                agg_try = aggregated_scrobbles.get(k)
                if agg_try and len(agg_try.get('timestamps', [])) > best_count:
                    best_agg = agg_try
                    best_key = k
                    best_count = len(agg_try.get('timestamps', []))

            # Default if nothing matched
            if best_agg is None:
                best_agg = {'timestamps': [], 'loved': False, 'artist_orig': artist, 'track_orig': title}
                best_key = key
            
            # Get cached Navidrome state
            cached_state = cache.get_mapped_song_state(key_str)
            if not cached_state:
                # Should not happen if we just indexed, but handle gracefully
                continue
            
            nav_count = cached_state['play_count']
            nav_starred = cached_state['starred']
            
            track_scrobbles = best_agg['timestamps']
            lastfm_count = len(track_scrobbles)
            last_played = max(track_scrobbles) if track_scrobbles else None
            loved = best_agg['loved']
            
            if lastfm_count > 0:
                tracks_with_scrobbles += 1
            
            if lastfm_count != nav_count or (loved and not nav_starred):
                differences.append({
                    'id': track_id,
                    'artist': artist,
                    'display_artist': t.get('display_artist', artist),
                    'title': title,
                    'display_title': disp_title or title,
                    'navidrome': nav_count,
                    'nav_starred': nav_starred,
                    'lastfm': lastfm_count,
                    'nav_played': None,
                    'last_played': last_played,
                    'loved': loved,
                    'match_key': best_key
                })

        print(f"\n✅ Processing complete! Found {tracks_with_scrobbles:,} tracks with Last.fm scrobbles.\n")
        
        # Generate missing reports in API mode (only if we have indexed_tracks from preindex)
        if indexed_tracks:
            missing_scrobbles_grouped, missing_loved_grouped = group_missing_by_artist_album(aggregated_scrobbles, indexed_tracks)
            with open(MISSING_SCROBBLES, "w", encoding="utf-8") as f:
                json.dump(missing_scrobbles_grouped, f, indent=2, ensure_ascii=False)
            print(f"✅ Missing scrobbles saved to {MISSING_SCROBBLES}")
            with open(MISSING_LOVED, "w", encoding="utf-8") as f:
                json.dump(missing_loved_grouped, f, indent=2, ensure_ascii=False)
            print(f"✅ Missing loved tracks saved to {MISSING_LOVED}\n")
    else:
        if not NAVIDROME_DB_PATH:
            print("❌ Error: NAVIDROME_DB_PATH is not configured")
            return
            
        try:
            conn = sqlite3.connect(NAVIDROME_DB_PATH)
        except sqlite3.Error as e:
            print(f"❌ Error connecting to Navidrome database: {e}")
            return
        
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
        
        # Mode info
        if not USE_NAVIDROME_API:
            conflict_mode_desc = {
                "ask": "interactive (will prompt for each conflict)",
                "navidrome": "always keep Navidrome when higher",
                "lastfm": "always use Last.fm",
                "higher": "always use higher count",
                "increment": "add Last.fm count to Navidrome count"
            }
            print(f"📋 Conflict resolution mode: {conflict_mode_desc.get(PLAYCOUNT_CONFLICT_RESOLUTION, PLAYCOUNT_CONFLICT_RESOLUTION)}\n")
        else:
            print("📋 API mode: will scrobble only missing plays to match Last.fm (no decreases).\n")
        
        for d in differences:
            diff_str = f"{d['lastfm'] - d['navidrome']:+d}"
            to_show = d.get('display_title', d['title'])
            artist_show = d.get('display_artist', d['artist'])
            print(f"  - {artist_show} - {to_show}")
            print(f"    Navidrome: {d['navidrome']} | Last.fm: {d['lastfm']} | Diff: {diff_str} | Loved: {d['loved']}")

        proceed = input("\nProceed with reviewing and updating these tracks? [y/N]: ").strip().lower()
        if proceed == 'y':
            updated_playcounts = 0
            updated_loved = 0
            conflicts_resolved = 0
            api_scrobbled_tracks = 0
            api_scrobbles_sent = 0
            
            # If using API-only mode, we'll scrobble only when Last.fm is higher (no decreases)
            api_client = NavidromeAPI() if USE_NAVIDROME_API else None

            for d in differences:
                nav = d['navidrome']
                lastfm = d['lastfm']
                artist, title = d['artist'], d['title']
                key = make_key(artist, title)

                if USE_NAVIDROME_API:
                    assert api_client is not None
                    # API mode: only increase Navidrome to match Last.fm (no decreases)
                    delta = max(0, lastfm - nav)
                    new_nav_count = nav  # Track the updated count
                    if delta > 0:
                        match_key = d.get('match_key') or key
                        ts_list = sorted(aggregated_scrobbles.get(match_key, {'timestamps': []})['timestamps'])
                        to_send = ts_list[-delta:] if delta <= len(ts_list) else ts_list
                        planned = len(to_send)
                        sent_ok = 0
                        for ts in to_send:
                            ok = api_client.scrobble(str(d['id']), int(ts) * 1000, submission=True)
                            if ok:
                                sent_ok += 1
                                api_scrobbles_sent += 1
                        if sent_ok:
                            api_scrobbled_tracks += 1
                            updated_playcounts += 1
                            new_nav_count = nav + sent_ok
                            agg_for_key = aggregated_scrobbles.get(match_key, {})
                            orig_artist = agg_for_key.get('artist_orig', artist)
                            orig_title = agg_for_key.get('track_orig', d.get('display_title', title))
                            cache.mark_scrobbles_synced(orig_artist, orig_title)
                            print(f"📤 API scrobbled: {d.get('display_artist', artist)} - {d.get('display_title', title)} (+{sent_ok}/{planned})")
                        else:
                            print(f"⚠️  API scrobble failed: {artist} - {title}")
                    
                    # Star loved tracks via API when needed
                    new_starred = d['nav_starred']
                    if d['loved'] and not d['nav_starred']:
                        ok = api_client.star_track(str(d['id']))
                        if ok:
                            updated_loved += 1
                            new_starred = True
                            print(f"⭐ Starred via API: {d.get('display_artist', artist)} - {d.get('display_title', title)}")
                        else:
                            print(f"⚠️  Failed to star via API: {artist} - {title}")
                    
                    # Update cache with final state (both playcount and starred)
                    key_str = f"{key[0]}||{key[1]}"
                    try:
                        cache.set_mapped_song_state(key_str, str(d['id']), new_nav_count, new_starred, artist, title)
                    except Exception:
                        pass
                    
                    # Skip DB update path entirely when in API mode
                    continue

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
            if USE_NAVIDROME_API:
                print(f"   API scrobbled tracks: {api_scrobbled_tracks}")
                print(f"   API scrobbles sent: {api_scrobbles_sent}")
            if conflicts_resolved > 0:
                print(f"   Conflicts resolved: {conflicts_resolved}")
            print(f"{'='*60}\n")
        else:
            print("🧪 Dry run complete. No changes made.")
    else:
        print("\n✅ All tracks are already in sync!")

    # Close DB connection only in DB mode
    try:
        if not USE_NAVIDROME_API and 'conn' in locals():
            conn.close()
    except Exception:
        pass

if __name__ == "__main__":
    main()