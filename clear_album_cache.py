#!/usr/bin/env python3
"""
Clear album-related cache entries to reset and test album matching from scratch.

This script clears:
- Duplicate track selections (user choices for duplicate albums)
- Loved track duplicate selections
- Album scrobble count associations

Run this before testing album_aware mode to start fresh.
"""

import sqlite3
import os
from src.config import CACHE_DB_PATH

def clear_album_cache():
    """Clear all album-related cache entries."""
    
    if not os.path.exists(CACHE_DB_PATH):
        print(f"⚠️  Cache database not found at {CACHE_DB_PATH}")
        return
    
    try:
        conn = sqlite3.connect(CACHE_DB_PATH)
        cursor = conn.cursor()
        
        # Get counts before clearing
        cursor.execute("SELECT COUNT(*) FROM duplicate_track_selections")
        dup_count = cursor.fetchone()[0] or 0
        
        cursor.execute("SELECT COUNT(*) FROM loved_duplicate_selections")
        loved_dup_count = cursor.fetchone()[0] or 0
        
        # Clear the tables
        print("🗑️  Clearing album cache...")
        
        cursor.execute("DELETE FROM duplicate_track_selections")
        print(f"   ✓ Cleared {dup_count} duplicate track selections")
        
        cursor.execute("DELETE FROM loved_duplicate_selections")
        print(f"   ✓ Cleared {loved_dup_count} loved track duplicate selections")
        
        conn.commit()
        conn.close()
        
        print(f"\n✅ Album cache cleared! Ready to test album matching from scratch.")
        print(f"   Run 'python main.py' to test the new prompts.\n")
        
    except sqlite3.Error as e:
        print(f"❌ Error clearing cache: {e}")
        return False
    
    return True

if __name__ == "__main__":
    clear_album_cache()
