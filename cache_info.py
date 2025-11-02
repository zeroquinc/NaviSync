"""
Cache information and management utility for NaviSync.
Run this script to view cache statistics or perform cache maintenance.
"""

import sys
from src.config import CACHE_DB_PATH
from src.cache import ScrobbleCache

def print_cache_info():
    """Display detailed cache information."""
    cache = ScrobbleCache(CACHE_DB_PATH)
    stats = cache.get_cache_stats()
    
    print("=" * 60)
    print("NaviSync Cache Statistics")
    print("=" * 60)
    
    if stats['total_scrobbles'] == 0:
        print("\n❌ Cache is empty. Run main.py to populate the cache.\n")
        return
    
    print(f"\n📊 Scrobbles:")
    print(f"  Total cached: {stats['total_scrobbles']:,}")
    print(f"  Synced to Navidrome: {stats['synced_scrobbles']:,}")
    print(f"  Unsynced: {stats['unsynced_scrobbles']:,}")
    
    print(f"\n❤️  Loved tracks: {stats['loved_tracks']:,}")
    
    # Show fuzzy match mappings
    fuzzy_matches = cache.get_all_fuzzy_matches()
    if fuzzy_matches:
        print(f"\n🔍 Fuzzy match mappings: {len(fuzzy_matches):,}")
        print("  (These are remembered track matches between Last.fm and Navidrome)")
    
    print(f"\n📅 Date range:")
    print(f"  Oldest scrobble: {stats['oldest_scrobble']}")
    print(f"  Newest scrobble: {stats['newest_scrobble']}")
    
    last_sync = cache.get_metadata('last_sync_time')
    if last_sync:
        print(f"\n🔄 Last sync: {last_sync}")
    
    print(f"\n💾 Database location: {CACHE_DB_PATH}")
    print("=" * 60)
    print()

def reset_sync_status():
    """Reset all tracks to unsynced status."""
    print("\n⚠️  WARNING: This will mark all cached scrobbles as unsynced.")
    print("This is useful if you want to force a full re-sync with Navidrome.")
    confirm = input("\nAre you sure you want to continue? [y/N]: ").strip().lower()
    
    if confirm == 'y':
        cache = ScrobbleCache(CACHE_DB_PATH)
        cache.reset_sync_status()
        print("✅ All scrobbles marked as unsynced. Run main.py to re-sync.\n")
    else:
        print("❌ Cancelled.\n")

def show_fuzzy_matches():
    """Display all saved fuzzy match mappings."""
    cache = ScrobbleCache(CACHE_DB_PATH)
    mappings = cache.get_all_fuzzy_matches()
    
    if not mappings:
        print("\n📭 No fuzzy match mappings saved yet.\n")
        return
    
    print(f"\n🔍 Fuzzy Match Mappings ({len(mappings)} total)")
    print("=" * 60)
    print("\nThese are tracks that were manually matched and will be")
    print("automatically matched in future runs:\n")
    
    for i, m in enumerate(mappings, 1):
        print(f"{i}. Navidrome: {m['navidrome_artist']} - {m['navidrome_track']}")
        print(f"   → Last.fm: {m['lastfm_artist']} - {m['lastfm_track']}\n")
    
    print("=" * 60)
    print()

def show_menu():
    """Display interactive menu."""
    print("\nNaviSync Cache Management")
    print("=" * 60)
    print("1. View cache statistics")
    print("2. View fuzzy match mappings")
    print("3. Reset sync status (force full re-sync)")
    print("4. Exit")
    print("=" * 60)
    
    choice = input("\nSelect an option [1-4]: ").strip()
    
    if choice == '1':
        print_cache_info()
    elif choice == '2':
        show_fuzzy_matches()
    elif choice == '3':
        reset_sync_status()
    elif choice == '4':
        print("Goodbye!\n")
        sys.exit(0)
    else:
        print("❌ Invalid option. Please try again.\n")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] in ['--info', '-i']:
            print_cache_info()
        elif sys.argv[1] in ['--reset', '-r']:
            reset_sync_status()
        elif sys.argv[1] in ['--fuzzy', '-f']:
            show_fuzzy_matches()
        else:
            print("Usage:")
            print("  python cache_info.py           - Interactive menu")
            print("  python cache_info.py --info    - Show cache statistics")
            print("  python cache_info.py --fuzzy   - Show fuzzy match mappings")
            print("  python cache_info.py --reset   - Reset sync status")
    else:
        # Interactive mode
        try:
            while True:
                show_menu()
        except KeyboardInterrupt:
            print("\n\nGoodbye!\n")
            sys.exit(0)
