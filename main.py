import sqlite3
import json
from src.config import NAVIDROME_DB_PATH, MISSING_SCROBBLES, MISSING_LOVED
from src.db import get_navidrome_user_id, get_all_tracks, get_annotation_playcount_starred, update_annotation
from src.lastfm import fetch_all_lastfm_scrobbles
from src.aggregation import aggregate_scrobbles, group_missing_by_artist_album
from src.utils import make_key

def main():
    NAVIDROME_USER_ID = get_navidrome_user_id(NAVIDROME_DB_PATH)
    tracks = get_all_tracks(NAVIDROME_DB_PATH)
    all_scrobbles = fetch_all_lastfm_scrobbles()
    aggregated_scrobbles = aggregate_scrobbles(all_scrobbles)

    conn = sqlite3.connect(NAVIDROME_DB_PATH)
    differences = []
    total = len(tracks)

    for i, t in enumerate(tracks, 1):
        track_id = t['id']
        artist = t['artist']
        title = t['title']
        key = make_key(artist, title)
        agg = aggregated_scrobbles.get(key, {'timestamps': [], 'loved': False})

        print(f"[{i}/{total}] Processing: {artist} - {title}")

        nav_count, nav_starred, nav_played_ts = get_annotation_playcount_starred(conn, track_id, NAVIDROME_USER_ID)
        track_scrobbles = agg['timestamps']
        lastfm_count = len(track_scrobbles)
        last_played = max(track_scrobbles) if track_scrobbles else None
        loved = agg['loved']

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

    missing_scrobbles_grouped, missing_loved_grouped = group_missing_by_artist_album(aggregated_scrobbles, tracks)
    with open(MISSING_SCROBBLES, "w", encoding="utf-8") as f:
        json.dump(missing_scrobbles_grouped, f, indent=2, ensure_ascii=False)
    print(f"✅ Missing from scrobbles saved to {MISSING_SCROBBLES}")
    with open(MISSING_LOVED, "w", encoding="utf-8") as f:
        json.dump(missing_loved_grouped, f, indent=2, ensure_ascii=False)
    print(f"✅ Missing loved tracks saved to {MISSING_LOVED}")

    if differences:
        print(f"\nTracks with possible updates: {len(differences)}\n")
        for d in differences:
            diff_str = f"{d['lastfm'] - d['navidrome']:+d}"
            print(f"  - {d['artist']} - {d['title']}")
            print(f"    Navidrome: {d['navidrome']} | Last.fm: {d['lastfm']} | Diff: {diff_str} | Loved: {d['loved']}")

        proceed = input("\nProceed with reviewing and updating these tracks? [y/N]: ").strip().lower()
        if proceed == 'y':
            for d in differences:
                nav = d['navidrome']
                lastfm = d['lastfm']
                artist, title = d['artist'], d['title']

                # Decide which playcount to use
                if lastfm > nav:
                    new_count = lastfm  # Auto update upwards
                elif nav > lastfm:
                    print(f"\n🎵 {artist} - {title}")
                    print(f"   Navidrome: {nav} | Last.fm: {lastfm}")
                    choice = input("   → Navidrome playcount is higher. Keep Navidrome (N) or use Last.fm (L)? [N/L, default=N]: ").strip().lower()
                    new_count = nav if choice in ('', 'n') else lastfm
                else:
                    new_count = nav  # Equal — no change

                update_annotation(conn, d['id'], new_count, d['last_played'], d['loved'], NAVIDROME_USER_ID)

                # Log concise summary
                if new_count != nav:
                    print(f"✅ Updated playcount for: {artist} - {title}")
                elif d['loved'] and not d['nav_starred']:
                    print(f"⭐ Updated loved status for: {artist} - {title}")
            print("\n✅ Sync complete!")
        else:
            print("🧪 Dry run complete. No changes made.")

    conn.close()

if __name__ == "__main__":
    main()