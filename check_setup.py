"""
Quick diagnostic tool for NaviSync.
Run this to check if your configuration is set up correctly.
"""

import os
import sys
from pathlib import Path

def check_env_file():
    """Check if .env file exists and has required variables."""
    if not os.path.exists('.env'):
        print("❌ No .env file found!")
        print("   → Copy env.example to .env and fill in your details.")
        return False
    
    print("✅ .env file exists")
    
    # Try to load it
    try:
        from src.config import NAVIDROME_DB_PATH, LASTFM_API_KEY, LASTFM_USER, PLAYCOUNT_CONFLICT_RESOLUTION
        
        issues = []
        
        if not NAVIDROME_DB_PATH:
            issues.append("NAVIDROME_DB_PATH is not set")
        elif not os.path.exists(NAVIDROME_DB_PATH):
            issues.append(f"NAVIDROME_DB_PATH points to non-existent file: {NAVIDROME_DB_PATH}")
        else:
            print(f"✅ Navidrome database found: {NAVIDROME_DB_PATH}")
        
        if not LASTFM_API_KEY:
            issues.append("LASTFM_API_KEY is not set")
        else:
            print(f"✅ Last.fm API key configured")
        
        if not LASTFM_USER:
            issues.append("LASTFM_USER is not set")
        else:
            print(f"✅ Last.fm user: {LASTFM_USER}")
        
        # Show conflict resolution mode
        print(f"✅ Conflict resolution: {PLAYCOUNT_CONFLICT_RESOLUTION}")
        
        if issues:
            print("\n⚠️  Issues found in .env:")
            for issue in issues:
                print(f"   - {issue}")
            return False
        
        return True
    except Exception as e:
        print(f"❌ Error loading configuration: {e}")
        return False

def check_dependencies():
    """Check if required packages are installed."""
    print("\n📦 Checking dependencies...")
    
    try:
        import requests
        print("✅ requests is installed")
    except ImportError:
        print("❌ requests is not installed")
        print("   → Run: pip install -r requirements.txt")
        return False
    
    try:
        import dotenv
        print("✅ python-dotenv is installed")
    except ImportError:
        print("❌ python-dotenv is not installed")
        print("   → Run: pip install -r requirements.txt")
        return False
    
    return True

def check_navidrome_backup():
    """Remind user about backing up Navidrome database."""
    print("\n⚠️  IMPORTANT REMINDER:")
    print("=" * 60)
    print("Before running NaviSync, make sure:")
    print("1. Your Navidrome database is backed up")
    print("2. Navidrome is NOT running")
    print("=" * 60)

def main():
    print("NaviSync Diagnostic Check")
    print("=" * 60)
    print()
    
    # Check Python version
    if sys.version_info < (3, 7):
        print(f"❌ Python {sys.version_info.major}.{sys.version_info.minor} detected")
        print("   → NaviSync requires Python 3.7 or higher")
        return
    else:
        print(f"✅ Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    
    # Check dependencies
    deps_ok = check_dependencies()
    
    print("\n🔧 Checking configuration...")
    config_ok = check_env_file()
    
    print()
    
    if deps_ok and config_ok:
        print("=" * 60)
        print("✅ All checks passed! You're ready to run NaviSync.")
        print("=" * 60)
        check_navidrome_backup()
        print("\nRun: python main.py")
    else:
        print("=" * 60)
        print("❌ Some checks failed. Please fix the issues above.")
        print("=" * 60)
    
    print()

if __name__ == "__main__":
    main()
