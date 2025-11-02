import sqlite3
import os
from datetime import datetime, timezone

class ScrobbleCache:
    def __init__(self, cache_db_path):
        """Initialize the scrobble cache database."""
        self.cache_db_path = cache_db_path
        try:
            self._init_database()
        except sqlite3.Error as e:
            raise RuntimeError(f"Failed to initialize cache database: {e}")

    def _init_database(self):
        """Initialize the cache database with necessary tables."""
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        
        # Table for scrobbles
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scrobbles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artist TEXT NOT NULL,
                album TEXT,
                track TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                loved INTEGER DEFAULT 0,
                synced INTEGER DEFAULT 0,
                UNIQUE(artist, track, timestamp)
            )
        """)
        
        # Index for faster queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp ON scrobbles(timestamp DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_synced ON scrobbles(synced)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_artist_track ON scrobbles(artist, track)
        """)
        
        # Table for loved tracks
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS loved_tracks (
                artist TEXT NOT NULL,
                track TEXT NOT NULL,
                loved_timestamp INTEGER,
                PRIMARY KEY (artist, track)
            )
        """)
        
        # Metadata table for tracking sync state
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sync_metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        
        # Table for fuzzy match mappings (Navidrome -> Last.fm)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fuzzy_match_mappings (
                navidrome_track_id TEXT NOT NULL,
                navidrome_artist TEXT NOT NULL,
                navidrome_track TEXT NOT NULL,
                lastfm_artist TEXT NOT NULL,
                lastfm_track TEXT NOT NULL,
                matched_timestamp INTEGER NOT NULL,
                PRIMARY KEY (navidrome_track_id)
            )
        """)
        
        # Table for tracking skipped Navidrome tracks (so we don't ask again)
        # We store the Last.fm tracks that were checked when skipping
        # so we can re-prompt if new Last.fm tracks appear later
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS skipped_tracks (
                navidrome_track_id TEXT NOT NULL,
                navidrome_artist TEXT NOT NULL,
                navidrome_track TEXT NOT NULL,
                checked_lastfm_tracks TEXT NOT NULL,
                skipped_timestamp INTEGER NOT NULL,
                PRIMARY KEY (navidrome_track_id)
            )
        """)
        
        conn.commit()
        conn.close()

    def get_latest_scrobble_timestamp(self):
        """Get the timestamp of the most recent scrobble in cache."""
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(timestamp) FROM scrobbles")
        result = cursor.fetchone()[0]
        conn.close()
        return result if result else 0

    def add_scrobbles(self, scrobbles):
        """Add new scrobbles to the cache. Returns count of new scrobbles added."""
        if not scrobbles:
            return 0
        
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        added_count = 0
        
        for s in scrobbles:
            try:
                cursor.execute("""
                    INSERT INTO scrobbles (artist, album, track, timestamp, loved)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    s['artist'],
                    s.get('album', ''),
                    s['track'],
                    s['timestamp'],
                    1 if s.get('loved', False) else 0
                ))
                added_count += 1
            except sqlite3.IntegrityError:
                # Duplicate scrobble, skip
                pass
        
        conn.commit()
        conn.close()
        return added_count

    def get_all_scrobbles(self):
        """Get all scrobbles from cache in the same format as Last.fm API."""
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT artist, album, track, timestamp, loved
            FROM scrobbles
            ORDER BY timestamp DESC
        """)
        
        scrobbles = []
        for row in cursor.fetchall():
            scrobbles.append({
                'artist': row[0],
                'album': row[1] or '',
                'track': row[2],
                'timestamp': row[3],
                'loved': bool(row[4])
            })
        
        conn.close()
        return scrobbles

    def get_unsynced_scrobbles(self):
        """Get scrobbles that haven't been synced yet."""
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT artist, album, track, timestamp, loved
            FROM scrobbles
            WHERE synced = 0
            ORDER BY timestamp DESC
        """)
        
        scrobbles = []
        for row in cursor.fetchall():
            scrobbles.append({
                'artist': row[0],
                'album': row[1] or '',
                'track': row[2],
                'timestamp': row[3],
                'loved': bool(row[4])
            })
        
        conn.close()
        return scrobbles

    def mark_scrobbles_synced(self, artist, track):
        """Mark all scrobbles for a given artist/track as synced."""
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE scrobbles
            SET synced = 1
            WHERE artist = ? AND track = ?
        """, (artist, track))
        conn.commit()
        conn.close()

    def get_scrobble_count(self, artist, track):
        """Get the total number of scrobbles for a given artist/track."""
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM scrobbles
            WHERE artist = ? AND track = ?
        """, (artist, track))
        count = cursor.fetchone()[0]
        conn.close()
        return count

    def update_loved_tracks(self, loved_tracks_list):
        """Update the loved tracks table with the full list from Last.fm."""
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        
        # Clear existing loved tracks
        cursor.execute("DELETE FROM loved_tracks")
        
        # Insert new loved tracks
        for track in loved_tracks_list:
            cursor.execute("""
                INSERT INTO loved_tracks (artist, track, loved_timestamp)
                VALUES (?, ?, ?)
            """, (track['artist'], track['track'], track.get('timestamp')))
        
        # Also update loved status in scrobbles table
        for track in loved_tracks_list:
            cursor.execute("""
                UPDATE scrobbles
                SET loved = 1
                WHERE artist = ? AND track = ?
            """, (track['artist'], track['track']))
        
        conn.commit()
        conn.close()

    def is_track_loved(self, artist, track):
        """Check if a track is loved."""
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 1 FROM loved_tracks
            WHERE artist = ? AND track = ?
        """, (artist, track))
        result = cursor.fetchone()
        conn.close()
        return result is not None

    def get_all_loved_tracks(self):
        """Get all loved tracks."""
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT artist, track, loved_timestamp FROM loved_tracks")
        loved = []
        for row in cursor.fetchall():
            loved.append({
                'artist': row[0],
                'track': row[1],
                'timestamp': row[2]
            })
        conn.close()
        return loved

    def set_metadata(self, key, value):
        """Set a metadata value."""
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO sync_metadata (key, value)
            VALUES (?, ?)
        """, (key, value))
        conn.commit()
        conn.close()

    def get_metadata(self, key, default=None):
        """Get a metadata value."""
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM sync_metadata WHERE key = ?", (key,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else default

    def get_cache_stats(self):
        """Get statistics about the cache."""
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM scrobbles")
        total_scrobbles = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM scrobbles WHERE synced = 1")
        synced_scrobbles = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM loved_tracks")
        loved_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM scrobbles")
        min_ts, max_ts = cursor.fetchone()
        
        conn.close()
        
        return {
            'total_scrobbles': total_scrobbles,
            'synced_scrobbles': synced_scrobbles,
            'unsynced_scrobbles': total_scrobbles - synced_scrobbles,
            'loved_tracks': loved_count,
            'oldest_scrobble': datetime.fromtimestamp(min_ts, timezone.utc).strftime('%Y-%m-%d %H:%M:%S') if min_ts else None,
            'newest_scrobble': datetime.fromtimestamp(max_ts, timezone.utc).strftime('%Y-%m-%d %H:%M:%S') if max_ts else None
        }

    def reset_sync_status(self):
        """Reset all scrobbles to unsynced status. Useful for full re-sync."""
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE scrobbles SET synced = 0")
        conn.commit()
        conn.close()

    def get_fuzzy_match_for_navidrome_track(self, navidrome_track_id):
        """Get a previously saved fuzzy match for a Navidrome track.
        
        Args:
            navidrome_track_id: The Navidrome track ID
            
        Returns:
            Dict with Last.fm track info {'artist', 'track'}, or None if no mapping exists
        """
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT lastfm_artist, lastfm_track
            FROM fuzzy_match_mappings
            WHERE navidrome_track_id = ?
        """, (navidrome_track_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return {
                'artist': result[0],
                'track': result[1]
            }
        return None

    def get_skipped_track_info(self, navidrome_track_id):
        """Get information about a previously skipped track.
        
        Args:
            navidrome_track_id: The Navidrome track ID
            
        Returns:
            Dict with 'checked_lastfm_tracks' (list of Last.fm track keys), or None if not skipped
        """
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT checked_lastfm_tracks FROM skipped_tracks
            WHERE navidrome_track_id = ?
        """, (navidrome_track_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            import json
            return {
                'checked_lastfm_tracks': json.loads(result[0])
            }
        return None

    def save_fuzzy_match(self, navidrome_track, lastfm_artist, lastfm_track):
        """Save a fuzzy match mapping for future runs.
        
        Args:
            navidrome_track: Navidrome track dict with 'id', 'artist', 'title'
            lastfm_artist: Artist name from Last.fm
            lastfm_track: Track name from Last.fm
        """
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        
        timestamp = int(datetime.now(timezone.utc).timestamp())
        
        # Remove from skipped tracks if it was there
        cursor.execute("DELETE FROM skipped_tracks WHERE navidrome_track_id = ?", 
                      (navidrome_track['id'],))
        
        cursor.execute("""
            INSERT OR REPLACE INTO fuzzy_match_mappings 
            (navidrome_track_id, navidrome_artist, navidrome_track, lastfm_artist, lastfm_track, matched_timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            navidrome_track['id'],
            navidrome_track['artist'],
            navidrome_track['title'],
            lastfm_artist, 
            lastfm_track,
            timestamp
        ))
        
        conn.commit()
        conn.close()

    def save_skipped_track(self, navidrome_track, checked_lastfm_tracks):
        """Mark a Navidrome track as skipped with the Last.fm tracks that were checked.
        
        Args:
            navidrome_track: Navidrome track dict with 'id', 'artist', 'title'
            checked_lastfm_tracks: List of Last.fm track keys (tuples of artist/track) that were checked
        """
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        
        timestamp = int(datetime.now(timezone.utc).timestamp())
        
        import json
        checked_json = json.dumps(checked_lastfm_tracks)
        
        cursor.execute("""
            INSERT OR REPLACE INTO skipped_tracks 
            (navidrome_track_id, navidrome_artist, navidrome_track, checked_lastfm_tracks, skipped_timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (
            navidrome_track['id'],
            navidrome_track['artist'],
            navidrome_track['title'],
            checked_json,
            timestamp
        ))
        
        conn.commit()
        conn.close()

    def get_all_fuzzy_matches(self):
        """Get all saved fuzzy match mappings."""
        conn = sqlite3.connect(self.cache_db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT navidrome_artist, navidrome_track, lastfm_artist, lastfm_track, matched_timestamp
            FROM fuzzy_match_mappings
            ORDER BY matched_timestamp DESC
        """)
        
        mappings = []
        for row in cursor.fetchall():
            mappings.append({
                'navidrome_artist': row[0],
                'navidrome_track': row[1],
                'lastfm_artist': row[2],
                'lastfm_track': row[3],
                'matched_timestamp': row[4]
            })
        
        conn.close()
        return mappings
        for row in cursor.fetchall():
            mappings.append({
                'lastfm_artist': row[0],
                'lastfm_track': row[1],
                'navidrome_artist': row[2],
                'navidrome_track': row[3],
                'matched_timestamp': row[4]
            })
        
        conn.close()
        return mappings