import sqlite3
from datetime import datetime, timezone

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
    """Get all tracks from Navidrome database."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, artist, album FROM media_file")
        tracks = [{'id': row[0], 'title': row[1], 'artist': row[2], 'album': row[3]} for row in cursor.fetchall()]
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