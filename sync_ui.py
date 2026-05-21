#!/usr/bin/env python3
"""
Developer utility to synchronize the source-of-truth app.html
to index.html and mobile_app/www/index.html to prevent drift.
Also ensures logo.jpg is in sync.
"""

import shutil
from pathlib import Path

def main():
    root = Path(__file__).parent.resolve()
    app_html = root / "app.html"
    index_html = root / "index.html"
    mobile_html = root / "mobile_app" / "www" / "index.html"
    
    if not app_html.exists():
        print(f"Error: Source file {app_html} does not exist.")
        return

    print("Synchronizing HTML mirrors...")
    
    # Read the source of truth
    content = app_html.read_text(encoding="utf-8")
    
    # Write to target mirrors
    index_html.write_text(content, encoding="utf-8")
    print(f"-> Synchronized {index_html}")
    
    mobile_html.parent.mkdir(parents=True, exist_ok=True)
    mobile_html.write_text(content, encoding="utf-8")
    print(f"-> Synchronized {mobile_html}")
    
    # Sync logo.jpg
    logo_src = root / "logo.jpg"
    logo_dest = root / "mobile_app" / "www" / "logo.jpg"
    if logo_src.exists():
        shutil.copy2(logo_src, logo_dest)
        print(f"-> Synchronized logo.jpg to {logo_dest}")

    print("UI synchronization complete. All mirrors are 100% identical.")

if __name__ == "__main__":
    main()
