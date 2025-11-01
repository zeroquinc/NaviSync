"""
Quick diagnostic tool for NaviSync.
Run this to check if your configuration is set up correctly.
"""

import os
import sys

def check_env_file():
    """Check if .env file exists and has required variables."""
    if not os.path.exists('.env'):
        print("❌ No .env file found!")
        print("   → Copy env.example to .env and fill in your details.")
        return False
    
    print("✅ .env file exists")
    
    # Try to load it
    try:
        from src.config import NAVIDROME_URL, LASTFM_API_KEY, LASTFM_USER
        
        issues = []
        
        # Check Navidrome API credentials
        if not NAVIDROME_URL:
            issues.append("NAVIDROME_URL is not set")
        else:
            print(f"✅ Navidrome URL: {NAVIDROME_URL}")
        
        # Check Last.fm credentials
        if not LASTFM_API_KEY:
            issues.append("LASTFM_API_KEY is not set")
        else:
            print(f"✅ Last.fm API key configured")
        
        if not LASTFM_USER:
            issues.append("LASTFM_USER is not set")
        else:
            print(f"✅ Last.fm user: {LASTFM_USER}")
        
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
    
    missing = []
    
    try:
        import requests
        print("✅ requests is installed")
    except ImportError:
        print("❌ requests is not installed")
        missing.append("requests")
    
    try:
        import dotenv
        print("✅ python-dotenv is installed") 
    except ImportError:
        print("❌ python-dotenv is not installed")
        missing.append("python-dotenv")
    
    try:
        import tqdm
        print("✅ tqdm is installed")
    except ImportError:
        print("❌ tqdm is not installed")
        missing.append("tqdm")
    
    if missing:
        print(f"\n   → Run: pip install {' '.join(missing)}")
        print("   → Or: pip install -r requirements.txt")
        return False
    
    return True

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
        print("\nRun: python main.py")
    else:
        print("=" * 60)
        print("❌ Some checks failed. Please fix the issues above.")
        print("=" * 60)
    
    print()

if __name__ == "__main__":
    main()