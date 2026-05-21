import shutil
import os


def sync_html_files():
    """Synchronize app.html to index.html and mobile_app/www/index.html.
    This ensures the three HTML entry points stay identical.
    """
    base_dir = os.path.abspath(os.path.dirname(__file__))
    src = os.path.join(base_dir, "app.html")
    dest_main = os.path.join(base_dir, "index.html")
    dest_mobile = os.path.join(base_dir, "mobile_app", "www", "index.html")
    try:
        shutil.copy2(src, dest_main)
        shutil.copy2(src, dest_mobile)
        print("HTML files synchronized successfully.")
    except Exception as e:
        print(f"Error syncing HTML files: {e}")
