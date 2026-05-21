import os
import re
import time
from pathlib import Path

log_file = Path("tunnel.log")
env_file = Path(".env")

print("Waiting for Cloudflare Tunnel to generate URL...")
url = None
for _ in range(30): # try for 30 seconds
    if log_file.exists():
        try:
            content = log_file.read_text(errors="ignore")
            # Look for trycloudflare.com URL pattern
            match = re.search(r"https://([a-zA-Z0-9\-]+\.trycloudflare\.com)", content)
            if match:
                url = match.group(1)
                break
        except Exception:
            pass
    time.sleep(1)

if url:
    print(f"\n==================================================")
    print(f" PUBLIC HTTPS URL: https://{url}")
    print(f"==================================================\n")
    if env_file.exists():
        try:
            content = env_file.read_text(encoding="utf-8", errors="ignore")
            # Replace RAILWAY_PUBLIC_DOMAIN="..." and PUBLIC_DOMAIN="..." with the new URL
            if "RAILWAY_PUBLIC_DOMAIN=" in content:
                content = re.sub(r'RAILWAY_PUBLIC_DOMAIN="[^"]*"', f'RAILWAY_PUBLIC_DOMAIN="https://{url}"', content)
            else:
                content += f'\nRAILWAY_PUBLIC_DOMAIN="https://{url}"\n'
                
            if "PUBLIC_DOMAIN=" in content:
                content = re.sub(r'PUBLIC_DOMAIN="[^"]*"', f'PUBLIC_DOMAIN="https://{url}"', content)
            else:
                content += f'\nPUBLIC_DOMAIN="https://{url}"\n'
                
            env_file.write_text(content, encoding="utf-8")
            print("Auto-updated .env with the new Tunnel URL!")
        except Exception as e:
            print(f"Failed to update .env automatically: {e}")
else:
    print("Warning: Could not detect Cloudflare Tunnel URL automatically within 30s.")
    print("Make sure cloudflared.exe runs correctly and check tunnel.log.")
