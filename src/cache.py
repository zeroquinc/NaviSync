import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone


class ScrobbleCache:
    def __init__(self, cache_db_path):
        """Initialize the scrobble cache database."""
        self.cache_db_path = cache_db_path
        try:
            self._init_database()
        except sqlite3.Error as e:
            raise RuntimeError(f"Failed to initialize cache database: {e}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @contextmanager
    def _connect(self):
        """Open a SQLite connection, yield it, then close it."""
        conn = sqlite3.connect(self.cache_db_path)
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _normalize_lookup_key(value):
        """Normalize cache lookup keys to avoid case/whitespace mismatches across runs."""
        return (value or "").strip().lower()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_database(self):
        """Initialize the cache database with necessary tables, and run migrations."""
        with self._connect() as conn:
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

            # Indexes for faster queries
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON scrobbles(timestamp DESC)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_synced ON scrobbles(synced)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_artist_track ON scrobbles(artist, track)")

            # Table for loved tracks
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS loved_tracks (
                    artist TEXT NOT NULL,
                    track TEXT NOT NULL,
                    loved_timestamp INTEGER,
                    PRIMARY KEY (artist, track)
                )
            """)

            # Metadata table for tracking sync state / schema version
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

            # Table for tracking skipped Navidrome tracks
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

            # Table for storing duplicate track selections
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS duplicate_track_selections (
                    lastfm_artist TEXT NOT NULL,
                    lastfm_track TEXT NOT NULL,
                    selected_navidrome_track_ids TEXT NOT NULL,
                    selection_timestamp INTEGER NOT NULL,
                    PRIMARY KEY (lastfm_artist, lastfm_track)
                )
            """)

            # Table for storing loved-track duplicate selections
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS loved_duplicate_selections (
                    lastfm_artist TEXT NOT NULL,
                    lastfm_track TEXT NOT NULL,
                    selected_navidrome_track_ids TEXT NOT NULL,
                    selection_timestamp INTEGER NOT NULL,
                    PRIMARY KEY (lastfm_artist, lastfm_track)
                )
            """)

            conn.commit()
            self._run_migrations(conn)

    def _run_migrations(self, conn):
        """Apply any pending schema migrations."""
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM sync_metadata WHERE key = 'schema_version'")
        row = cursor.fetchone()
        current_version = int(row[0]) if row else 0

        # Migration 1: (placeholder for future column additions)
        # Example: if current_version < 1:
        #     cursor.execute("ALTER TABLE scrobbles ADD COLUMN new_col TEXT DEFAULT ''")
        #     current_version = 1

        if current_version < 1:
            # Bump version even if there is nothing structural to change yet,
            # so future migrations can build on this baseline.
            current_version = 1

        cursor.execute(
            "INSERT OR REPLACE INTO sync_metadata (key, value) VALUES ('schema_version', ?)",
            (str(current_version),)
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Scrobble access
    # ------------------------------------------------------------------

    def get_latest_scrobble_timestamp(self):
        """Get the timestamp of the most recent scrobble in cache."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(timestamp) FROM scrobbles")
            result = cursor.fetchone()[0]
        return result if result else 0

    def add_scrobbles(self, scrobbles):
        """Add new scrobbles to the cache. Returns count of new scrobbles added."""
        if not scrobbles:
            return 0

        added_count = 0
        with self._connect() as conn:
            cursor = conn.cursor()
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
                    pass  # duplicate scrobble
            conn.commit()
        return added_count

    def get_all_scrobbles(self):
        """Get all scrobbles from cache in the same format as Last.fm API."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT artist, album, track, timestamp, loved
                FROM scrobbles
                ORDER BY timestamp DESC
            """)
            return [
                {'artist': r[0], 'album': r[1] or '', 'track': r[2], 'timestamp': r[3], 'loved': bool(r[4])}
                for r in cursor.fetchall()
            ]

    def get_unsynced_scrobbles(self):
        """Get scrobbles that have not been synced yet."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT artist, album, track, timestamp, loved
                FROM scrobbles
                WHERE synced = 0
                ORDER BY timestamp DESC
            """)
            return [
                {'artist': r[0], 'album': r[1] or '', 'track': r[2], 'timestamp': r[3], 'loved': bool(r[4])}
                for r in cursor.fetchall()
            ]

    def mark_scrobbles_synced(self, artist, track):
        """Mark all scrobbles for a given artist/track as synced."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE scrobbles SET synced = 1 WHERE artist = ? AND track = ?", (artist, track))
            conn.commit()

    def get_scrobble_count(self, artist, track):
        """Get the total number of scrobbles for a given artist/track."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM scrobbles WHERE artist = ? AND track = ?", (artist, track))
            return cursor.fetchone()[0]

    def get_album_scrobble_counts(self, artist, track):
        """Get scrobble counts grouped by album for a given artist/track."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COALESCE(album, ''), COUNT(*)
                FROM scrobbles
                WHERE artist = ? AND track = ?
                GROUP BY COALESCE(album, '')
            """, (artist, track))
            return {album or "": count for album, count in cursor.fetchall()}

    # ------------------------------------------------------------------
    # Loved tracks
    # ------------------------------------------------------------------

    def update_loved_tracks(self, loved_tracks_list):
        """Replace the loved tracks table with the full list from Last.fm."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM loved_tracks")
            cursor.execute("UPDATE scrobbles SET loved = 0")

            for track in loved_tracks_list:
                cursor.execute("""
                    INSERT INTO loved_tracks (artist, track, loved_timestamp)
                    VALUES (?, ?, ?)
                """, (track['artist'], track['track'], track.get('timestamp')))

            for track in loved_tracks_list:
                cursor.execute(
                    "UPDATE scrobbles SET loved = 1 WHERE artist = ? AND track = ?",
                    (track['artist'], track['track'])
                )

            conn.commit()

    def is_track_loved(self, artist, track):
        """Check if a track is loved."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM loved_tracks WHERE artist = ? AND track = ?", (artist, track))
            return cursor.fetchone() is not None

    def get_loved_timestamp(self, artist, track):
        """Return the Unix timestamp when a track was loved on Last.fm, or None."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT loved_timestamp FROM loved_tracks WHERE artist=? AND track=?",
                (artist, track)
            )
            row = cursor.fetchone()
        return row[0] if row and row[0] else None

    def get_all_loved_tracks(self):
        """Get all loved tracks."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT artist, track, loved_timestamp FROM loved_tracks")
            return [
                {'artist': r[0], 'track': r[1], 'timestamp': r[2]}
                for r in cursor.fetchall()
            ]

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def set_metadata(self, key, value):
        """Set a metadata value."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO sync_metadata (key, value) VALUES (?, ?)",
                (key, value)
            )
            conn.commit()

    def get_metadata(self, key, default=None):
        """Get a metadata value."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM sync_metadata WHERE key = ?", (key,))
            result = cursor.fetchone()
        return result[0] if result else default

    def get_cache_stats(self):
        """Get statistics about the cache."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM scrobbles")
            total_scrobbles = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM scrobbles WHERE synced = 1")
            synced_scrobbles = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM loved_tracks")
            loved_count = cursor.fetchone()[0]
            cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM scrobbles")
            min_ts, max_ts = cursor.fetchone()

        return {
            'total_scrobbles': total_scrobbles,
            'synced_scrobbles': synced_scrobbles,
            'unsynced_scrobbles': total_scrobbles - synced_scrobbles,
            'loved_tracks': loved_count,
            'oldest_scrobble': datetime.fromtimestamp(min_ts, timezone.utc).strftime('%Y-%m-%d %H:%M:%S') if min_ts else None,
            'newest_scrobble': datetime.fromtimestamp(max_ts, timezone.utc).strftime('%Y-%m-%d %H:%M:%S') if max_ts else None,
        }

    def reset_sync_status(self):
        """Reset all scrobbles to unsynced status. Useful for a full re-sync."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE scrobbles SET synced = 0")
            conn.commit()

    # ------------------------------------------------------------------
    # Fuzzy match mappings
    # ------------------------------------------------------------------

    def get_fuzzy_match_for_navidrome_track(self, navidrome_track_id):
        """Get a previously saved fuzzy match for a Navidrome track.

        Returns dict with {'artist', 'track'}, or None if no mapping exists.
        """
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT lastfm_artist, lastfm_track
                FROM fuzzy_match_mappings
                WHERE navidrome_track_id = ?
            """, (navidrome_track_id,))
            result = cursor.fetchone()
        if result:
            return {'artist': result[0], 'track': result[1]}
        return None

    def save_fuzzy_match(self, navidrome_track, lastfm_artist, lastfm_track):
        """Save a fuzzy match mapping for future runs."""
        timestamp = int(datetime.now(timezone.utc).timestamp())
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM skipped_tracks WHERE navidrome_track_id = ?",
                (navidrome_track['id'],)
            )
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
                timestamp,
            ))
            conn.commit()

    def get_all_fuzzy_matches(self):
        """Get all saved fuzzy match mappings."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT navidrome_artist, navidrome_track, lastfm_artist, lastfm_track, matched_timestamp
                FROM fuzzy_match_mappings
                ORDER BY matched_timestamp DESC
            """)
            return [
                {
                    'navidrome_artist': r[0],
                    'navidrome_track': r[1],
                    'lastfm_artist': r[2],
                    'lastfm_track': r[3],
                    'matched_timestamp': r[4],
                }
                for r in cursor.fetchall()
            ]

    # ------------------------------------------------------------------
    # Skipped tracks
    # ------------------------------------------------------------------

    def get_skipped_track_info(self, navidrome_track_id):
        """Return info about a previously skipped track, or None."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT checked_lastfm_tracks FROM skipped_tracks WHERE navidrome_track_id = ?",
                (navidrome_track_id,)
            )
            result = cursor.fetchone()
        if result:
            return {'checked_lastfm_tracks': json.loads(result[0])}
        return None

    def save_skipped_track(self, navidrome_track, checked_lastfm_tracks):
        """Mark a Navidrome track as skipped with the Last.fm tracks that were checked."""
        timestamp = int(datetime.now(timezone.utc).timestamp())
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO skipped_tracks
                (navidrome_track_id, navidrome_artist, navidrome_track, checked_lastfm_tracks, skipped_timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (
                navidrome_track['id'],
                navidrome_track['artist'],
                navidrome_track['title'],
                json.dumps(checked_lastfm_tracks),
                timestamp,
            ))
            conn.commit()

    # ------------------------------------------------------------------
    # Duplicate track selections
    # ------------------------------------------------------------------

    def get_duplicate_selection(self, lastfm_artist, lastfm_track):
        """Get previously saved selection for duplicate tracks.

        Returns dict with keys {'mode', 'ids', 'distribution'}, or None.
        """
        artist_key = self._normalize_lookup_key(lastfm_artist)
        track_key = self._normalize_lookup_key(lastfm_track)

        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT selected_navidrome_track_ids FROM duplicate_track_selections
                WHERE LOWER(TRIM(lastfm_artist)) = ? AND LOWER(TRIM(lastfm_track)) = ?
            """, (artist_key, track_key))
            result = cursor.fetchone()

        if result:
            payload = json.loads(result[0])
            if isinstance(payload, list):
                return {'mode': 'select', 'ids': payload, 'distribution': None}
            if isinstance(payload, dict):
                return {
                    'mode': payload.get('mode', 'select'),
                    'ids': payload.get('ids', []),
                    'distribution': payload.get('distribution', None),
                }
        return None

    def save_duplicate_selection(self, lastfm_artist, lastfm_track, selected_track_ids, mode="select", distribution=None):
        """Save user's selection for duplicate tracks."""
        artist_key = self._normalize_lookup_key(lastfm_artist)
        track_key = self._normalize_lookup_key(lastfm_track)
        timestamp = int(datetime.now(timezone.utc).timestamp())
        payload = {'mode': mode, 'ids': selected_track_ids, 'distribution': distribution}

        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM duplicate_track_selections
                WHERE LOWER(TRIM(lastfm_artist)) = ? AND LOWER(TRIM(lastfm_track)) = ?
            """, (artist_key, track_key))
            cursor.execute("""
                INSERT OR REPLACE INTO duplicate_track_selections
                (lastfm_artist, lastfm_track, selected_navidrome_track_ids, selection_timestamp)
                VALUES (?, ?, ?, ?)
            """, (artist_key, track_key, json.dumps(payload), timestamp))
            conn.commit()

    # ------------------------------------------------------------------
    # Loved-track duplicate selections
    # ------------------------------------------------------------------

    def get_loved_selection(self, lastfm_artist, lastfm_track):
        """Get previously saved loved selection for duplicate tracks.

        Returns list of selected Navidrome track IDs, or None.
        """
        artist_key = self._normalize_lookup_key(lastfm_artist)
        track_key = self._normalize_lookup_key(lastfm_track)

        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT selected_navidrome_track_ids FROM loved_duplicate_selections
                WHERE LOWER(TRIM(lastfm_artist)) = ? AND LOWER(TRIM(lastfm_track)) = ?
            """, (artist_key, track_key))
            result = cursor.fetchone()

        if result:
            try:
                payload = json.loads(result[0])
            except json.JSONDecodeError:
                return None
            if isinstance(payload, list):
                return payload
        return None

    def save_loved_selection(self, lastfm_artist, lastfm_track, selected_track_ids):
        """Save user's selection for loved-track duplicates."""
        artist_key = self._normalize_lookup_key(lastfm_artist)
        track_key = self._normalize_lookup_key(lastfm_track)
        timestamp = int(datetime.now(timezone.utc).timestamp())

        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM loved_duplicate_selections
                WHERE LOWER(TRIM(lastfm_artist)) = ? AND LOWER(TRIM(lastfm_track)) = ?
            """, (artist_key, track_key))
            cursor.execute("""
                INSERT OR REPLACE INTO loved_duplicate_selections
                (lastfm_artist, lastfm_track, selected_navidrome_track_ids, selection_timestamp)
                VALUES (?, ?, ?, ?)
            """, (artist_key, track_key, json.dumps(selected_track_ids), timestamp))
            conn.commit()
