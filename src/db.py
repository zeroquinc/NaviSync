import sqlite3
import os
import time
from datetime import datetime, timezone
import requests

def is_database_locked(db_path, timeout=1):
    """
    Check if database is locked by attempting exclusive access.
    Returns True if locked/in-use, False if accessible.
    
    Args:
        db_path: Path to Navidrome database file
        timeout: How long to wait before giving up (in seconds)
    
    Returns:
        bool: True if database is locked, False if accessible
    """
    try:
        conn = sqlite3.connect(db_path, timeout=timeout)
        conn.execute("BEGIN EXCLUSIVE")
        conn.rollback()
        conn.close()
        return False  # Successfully locked, so no one else is using it
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e):
            return True  # Database is in use
        raise
    except Exception:
        # If we can't determine status, assume it's safe
        return False

def is_database_recently_modified(db_path, seconds=5):
    """
    Check if database was modified within the last N seconds.
    This indicates Navidrome is actively writing.
    
    Args:
        db_path: Path to Navidrome database file
        seconds: Threshold in seconds (default 5)
    
    Returns:
        bool: True if recently modified, False otherwise
    """
    try:
        last_modified = os.path.getmtime(db_path)
        time_since_modified = time.time() - last_modified
        return time_since_modified < seconds
    except (OSError, FileNotFoundError):
        return False

def check_navidrome_active(db_path, check_lock=True, check_mtime=True, navidrome_url=None):
    """
    Comprehensive check if Navidrome is actively using the database.
    
    CRITICAL: We MUST NOT open the database while Navidrome is running!
    
    Args:
        db_path: Path to Navidrome database
        check_lock: Check if database file is locked
        check_mtime: Check if database was recently modified
        navidrome_url: Optional Navidrome endpoint to ping
    
    Returns:
        (is_active, reason) - tuple of (bool, str)
    """

    if navidrome_url:
        try:
            response = requests.get(navidrome_url, timeout=3)
            if response.status_code == 200:
                return True, "Navidrome is responding - server is active"
        except (requests.ConnectionError, requests.Timeout):
            pass
        except Exception:
            pass
    
    # Check if database is locked
    if check_lock and is_database_locked(db_path):
        return True, "Database file is locked (Navidrome is actively accessing it)"
    
    # Check if database was recently modified
    if check_mtime and is_database_recently_modified(db_path):
        return True, "Database was recently modified (Navidrome is writing)"
    
    return False, "Navidrome appears to be inactive - safe to proceed"

def get_navidrome_user_id(db_path):
    """Get the Navidrome user ID from the database."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM user")
        users = [row[0] for row in cursor.fetchall()]
        conn.close()
    except sqlite3.Error as e:
        raise RuntimeError(f"Error reading Navidrome database: {e}")
    
    if not users:
        raise ValueError("No users found in Navidrome user table.")
    elif len(users) == 1:
        print(f"Auto-selected user ID: {users[0]}")
        return users[0]
    else:
        print("Multiple user IDs found:")
        for i, uid in enumerate(users, 1):
            print(f"{i}: {uid}")
        choice = input("Select the user ID to use [1]: ").strip()
        if not choice:
            choice = "1"
        try:
            index = int(choice) - 1
            if 0 <= index < len(users):
                return users[index]
            else:
                raise ValueError("Invalid selection")
        except (ValueError, IndexError):
            print("⚠️  Invalid selection, using first user.")
            return users[0]

def get_all_tracks(db_path):
    """Get all tracks from Navidrome database.

    This function is robust to invalid UTF-8 sequences in text columns. Some Navidrome
    databases may contain malformed bytes for fields like `artist` or `title`. To
    avoid crashing with a UnicodeDecodeError, we fetch TEXT columns as raw bytes and
    decode them per-column, using 'utf-8' and falling back to 'replace' on errors.
    When replacements occur we emit a short warning pointing to the affected record.
    """
    try:
        conn = sqlite3.connect(db_path)
        # Fetch text as bytes so we can handle decoding errors explicitly
        conn.text_factory = bytes
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, title, artist, album, track_number, disc_number, duration
            FROM media_file
        """)

        tracks = []
        for row in cursor.fetchall():
            raw_id = row[0]
            # Normalize id so it's JSON-serializable (prefer int when possible)
            if isinstance(raw_id, (bytes, bytearray)):
                try:
                    id_decoded = raw_id.decode('utf-8', 'replace')
                except Exception:
                    id_decoded = raw_id.decode('latin-1', 'replace')
                # If it looks numeric, convert to int; otherwise keep as string
                try:
                    track_id = int(id_decoded) if id_decoded.isdigit() else id_decoded
                except Exception:
                    track_id = id_decoded
            else:
                track_id = raw_id

            def _decode_field(val, colname):
                # If the value is None, keep it None
                if val is None:
                    return None
                # If sqlite returned bytes, decode safely
                if isinstance(val, (bytes, bytearray)):
                    try:
                        return val.decode('utf-8')
                    except UnicodeDecodeError:
                        # Fall back to replacement so we don't crash; log a short warning
                        try:
                            decoded = val.decode('utf-8', 'replace')
                        except Exception:
                            # As a last resort, decode using latin-1 so we get a str
                            decoded = val.decode('latin-1', 'replace')
                        print(f"⚠️  Non-UTF-8 data in column '{colname}' for track id {track_id}; invalid sequences replaced.")
                        return decoded
                # Already a str
                return val

            title = _decode_field(row[1], 'title')
            artist = _decode_field(row[2], 'artist')
            album = _decode_field(row[3], 'album')

            tracks.append({
                'id': track_id,
                'title': title,
                'artist': artist,
                'album': album,
                'track_number': row[4],
                'disc_number': row[5],
                'duration': row[6]
            })

        conn.close()
        print(f"Fetched {len(tracks):,} tracks from Navidrome database.")
        return tracks
    except sqlite3.Error as e:
        raise RuntimeError(f"Error reading tracks from Navidrome database: {e}")

def get_annotation_playcount_starred(conn, track_id, user_id):
    cursor = conn.cursor()
    cursor.execute("""
        SELECT play_count, starred, play_date
        FROM annotation
        WHERE user_id=? AND item_id=? AND item_type='media_file'
    """, (user_id, track_id))
    row = cursor.fetchone()
    if row:
        play_date_ts = None
        if row[2]:
            try:
                dt = datetime.strptime(row[2], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                play_date_ts = int(dt.timestamp())
            except ValueError:
                pass
        return row[0] or 0, bool(row[1]), play_date_ts
    return 0, False, None

def update_annotation(conn, track_id, new_count, new_last_played, loved, user_id):
    """Update or insert annotation for a track."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT play_date FROM annotation
        WHERE user_id=? AND item_id=? AND item_type='media_file'
    """, (user_id, track_id))
    row = cursor.fetchone()
    existing_play_date = None
    if row and row[0]:
        try:
            dt = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            existing_play_date = int(dt.timestamp())
        except ValueError:
            pass

    # Only update play_date if newer or None
    if new_last_played and (existing_play_date is None or new_last_played > existing_play_date):
        play_date_str = datetime.fromtimestamp(new_last_played, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    else:
        play_date_str = row[0] if row else None

    cursor.execute("""
        SELECT 1 FROM annotation
        WHERE user_id=? AND item_id=? AND item_type='media_file'
    """, (user_id, track_id))
    exists = cursor.fetchone()

    if exists:
        if loved:
            cursor.execute("""
                UPDATE annotation
                SET play_count=?, play_date=?, starred=1
                WHERE user_id=? AND item_id=? AND item_type='media_file'
            """, (new_count, play_date_str, user_id, track_id))
        else:
            cursor.execute("""
                UPDATE annotation
                SET play_count=?, play_date=?
                WHERE user_id=? AND item_id=? AND item_type='media_file'
            """, (new_count, play_date_str, user_id, track_id))
    else:
        starred_val = 1 if loved else 0
        cursor.execute("""
            INSERT INTO annotation(user_id, item_id, item_type, play_count, play_date, starred)
            VALUES (?, ?, 'media_file', ?, ?, ?)
        """, (user_id, track_id, new_count, play_date_str, starred_val))
    conn.commit()

def update_artist_play_counts(conn, user_id, updated_track_ids=None):
    """
    Recalculate and update artist play counts by aggregating track play counts.
    This ensures the Artist tab shows correct total plays without requiring a library scan.
    Handles both primary artists and additional artists (multi-artist tracks).
    
    Args:
        conn: SQLite connection to Navidrome database
        user_id: Navidrome user ID
        updated_track_ids: Optional list of track IDs that were updated. If provided, only
                          recalculates counts for artists of these tracks. If None, updates all artists.
    """
    cursor = conn.cursor()
    
    # If specific track IDs provided, get only affected artist IDs
    affected_artist_ids = set()
    if updated_track_ids:
        # Check if there's a media_file_artists table for multi-artist support
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='media_file_artists'
        """)
        has_multi_artist_table = cursor.fetchone() is not None
        
        placeholders = ','.join('?' * len(updated_track_ids))
        
        if has_multi_artist_table:
            # Get all artist IDs from media_file_artists for updated tracks
            cursor.execute(f"""
                SELECT DISTINCT mfa.artist_id
                FROM media_file_artists mfa
                WHERE mfa.media_file_id IN ({placeholders})
                    AND mfa.role = 'artist'
                    AND mfa.artist_id IS NOT NULL
            """, updated_track_ids)
            affected_artist_ids.update(row[0] for row in cursor.fetchall())
        
        # Also get primary artist_id from media_file
        cursor.execute(f"""
            SELECT DISTINCT artist_id
            FROM media_file
            WHERE id IN ({placeholders})
                AND artist_id IS NOT NULL
        """, updated_track_ids)
        affected_artist_ids.update(row[0] for row in cursor.fetchall())
    
    # Check if there's a media_file_artists table for multi-artist support
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name='media_file_artists'
    """)
    
    has_multi_artist_table = cursor.fetchone() is not None
    artist_play_counts = {}
    
    if has_multi_artist_table:
        # Build query with optional WHERE clause for specific artists
        where_clause = ""
        params = [user_id]
        if affected_artist_ids:
            placeholders = ','.join('?' * len(affected_artist_ids))
            where_clause = f"AND mfa.artist_id IN ({placeholders})"
            params.extend(affected_artist_ids)
        
        # Use media_file_artists table, filtering by role='artist' to exclude albumartist
        cursor.execute(f"""
            SELECT 
                mfa.artist_id,
                SUM(COALESCE(a.play_count, 0)) as total_plays
            FROM media_file_artists mfa
            JOIN media_file mf ON mfa.media_file_id = mf.id
            LEFT JOIN annotation a ON a.item_id = mf.id 
                AND a.item_type = 'media_file' 
                AND a.user_id = ?
            WHERE mfa.artist_id IS NOT NULL
                AND mfa.role = 'artist'
                {where_clause}
            GROUP BY mfa.artist_id
        """, params)
        
        for artist_id, total_plays in cursor.fetchall():
            artist_play_counts[artist_id] = total_plays
    else:
        # Build query with optional WHERE clause for specific artists
        where_clause = ""
        params = [user_id]
        if affected_artist_ids:
            placeholders = ','.join('?' * len(affected_artist_ids))
            where_clause = f"AND mf.artist_id IN ({placeholders})"
            params.extend(affected_artist_ids)
        
        # Fallback to primary artist only (artist_id) if no multi-artist table exists
        cursor.execute(f"""
            SELECT 
                mf.artist_id,
                SUM(COALESCE(a.play_count, 0)) as total_plays
            FROM media_file mf
            LEFT JOIN annotation a ON a.item_id = mf.id 
                AND a.item_type = 'media_file' 
                AND a.user_id = ?
            WHERE mf.artist_id IS NOT NULL
                {where_clause}
            GROUP BY mf.artist_id
        """, params)
        
        for artist_id, total_plays in cursor.fetchall():
            artist_play_counts[artist_id] = total_plays
    
    # Update each artist's play count in the annotation table
    for artist_id, total_plays in artist_play_counts.items():
        # Check if artist annotation exists
        cursor.execute("""
            SELECT 1 FROM annotation
            WHERE user_id=? AND item_id=? AND item_type='artist'
        """, (user_id, artist_id))
        
        exists = cursor.fetchone()
        
        if exists:
            cursor.execute("""
                UPDATE annotation
                SET play_count=?
                WHERE user_id=? AND item_id=? AND item_type='artist'
            """, (total_plays, user_id, artist_id))
        else:
            # Only insert if total_plays > 0
            if total_plays > 0:
                cursor.execute("""
                    INSERT INTO annotation(user_id, item_id, item_type, play_count)
                    VALUES (?, ?, 'artist', ?)
                """, (user_id, artist_id, total_plays))
    
    conn.commit()
    return len(artist_play_counts)

def update_album_play_counts(conn, user_id, updated_track_ids=None):
    """
    Recalculate and update album play counts by aggregating track play counts.
    This ensures the Album tab shows correct total plays without requiring a library scan.
    
    Args:
        conn: SQLite connection to Navidrome database
        user_id: Navidrome user ID
        updated_track_ids: Optional list of track IDs that were updated. If provided, only
                          recalculates counts for albums of these tracks. If None, updates all albums.
    """
    cursor = conn.cursor()
    
    # If specific track IDs provided, get only affected album IDs
    affected_album_ids = set()
    if updated_track_ids:
        placeholders = ','.join('?' * len(updated_track_ids))
        cursor.execute(f"""
            SELECT DISTINCT album_id
            FROM media_file
            WHERE id IN ({placeholders})
                AND album_id IS NOT NULL
        """, updated_track_ids)
        affected_album_ids.update(row[0] for row in cursor.fetchall())
    
    # Build query with optional WHERE clause for specific albums
    where_clause = ""
    params = [user_id]
    if affected_album_ids:
        placeholders = ','.join('?' * len(affected_album_ids))
        where_clause = f"AND mf.album_id IN ({placeholders})"
        params.extend(affected_album_ids)
    
    # Get albums and their total play counts from tracks
    cursor.execute(f"""
        SELECT 
            mf.album_id,
            SUM(COALESCE(a.play_count, 0)) as total_plays
        FROM media_file mf
        LEFT JOIN annotation a ON a.item_id = mf.id 
            AND a.item_type = 'media_file' 
            AND a.user_id = ?
        WHERE mf.album_id IS NOT NULL
            {where_clause}
        GROUP BY mf.album_id
    """, params)
    
    album_play_counts = cursor.fetchall()
    
    # Update each album's play count in the annotation table
    for album_id, total_plays in album_play_counts:
        # Check if album annotation exists
        cursor.execute("""
            SELECT 1 FROM annotation
            WHERE user_id=? AND item_id=? AND item_type='album'
        """, (user_id, album_id))
        
        exists = cursor.fetchone()
        
        if exists:
            cursor.execute("""
                UPDATE annotation
                SET play_count=?
                WHERE user_id=? AND item_id=? AND item_type='album'
            """, (total_plays, user_id, album_id))
        else:
            # Only insert if total_plays > 0
            if total_plays > 0:
                cursor.execute("""
                    INSERT INTO annotation(user_id, item_id, item_type, play_count)
                    VALUES (?, ?, 'album', ?)
                """, (user_id, album_id, total_plays))
    
    conn.commit()
    return len(album_play_counts)