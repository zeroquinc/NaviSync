from datetime import datetime, timezone
from .utils import make_key

def aggregate_scrobbles(scrobbles):
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