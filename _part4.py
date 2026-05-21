import os
import shutil
from pathlib import Path

# Import the sync function from part3
from _part3 import sync_html_files

if __name__ == "__main__":
    try:
        sync_html_files()
        print("HTML files synchronized successfully.")
    except Exception as e:
        print(f"Error synchronizing HTML files: {e}")
