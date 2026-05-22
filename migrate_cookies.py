"""
migrate_cookies.py — Seamlessly migrates plaintext cookie files and .env variables to secure encrypted (.enc) format.
Zero-wipes and purges original plaintext files after successful encryption.
"""

import os
import sys
import re
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cuhi.cookie_migration")

# Ensure .env is loaded to resolve COOKIE_ENCRYPTION_KEY if run standalone
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    try:
        content = env_file.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                if v.startswith('"') and v.endswith('"'):
                    v = v[1:-1]
                elif v.startswith("'") and v.endswith("'"):
                    v = v[1:-1]
                os.environ[k] = v
    except Exception as e:
        logger.warning("Failed to parse .env file: %s", e)

# Add parent directory to path to resolve imports if run from subfolder
sys.path.append(str(Path(__file__).parent.absolute()))

# Auto-generate and set COOKIE_ENCRYPTION_KEY if not present
if not os.environ.get("COOKIE_ENCRYPTION_KEY"):
    logger.info("COOKIE_ENCRYPTION_KEY not set. Auto-generating a secure key...")
    try:
        from cryptography.fernet import Fernet
        generated_key = Fernet.generate_key().decode("utf-8")
        os.environ["COOKIE_ENCRYPTION_KEY"] = generated_key
        
        # Append to .env file if it exists
        if env_file.exists():
            env_content = env_file.read_text(encoding="utf-8")
            if "COOKIE_ENCRYPTION_KEY" not in env_content:
                env_file.write_text(env_content.rstrip() + f'\n\nCOOKIE_ENCRYPTION_KEY="{generated_key}"\n', encoding="utf-8")
                logger.info("Successfully appended COOKIE_ENCRYPTION_KEY to .env")
    except Exception as e:
        logger.critical("Failed to generate and save COOKIE_ENCRYPTION_KEY: %s", e)
        sys.exit(1)

try:
    from crypto_utils import get_crypto
except ImportError as e:
    logger.critical("Failed to import crypto_utils: %s. Make sure you run this in the cuhibot root.", e)
    sys.exit(1)

COOKIE_FILE_KEYS = {
    "instagram.com_cookies.txt",
    "tiktok.com_cookies.txt",
    "facebook.com_cookies.txt",
    "x.com_cookies.txt"
}

def migrate_env_cookies(crypto):
    """Parse raw cookies from .env, encrypt and save them, then strip them from .env."""
    if not env_file.exists():
        return
        
    logger.info("Scanning for raw cookies in .env...")
    content = env_file.read_text(encoding="utf-8")
    lines = content.splitlines()
    new_lines = []
    
    env_map = {
        "COOKIE_INSTAGRAM": "instagram.com_cookies.enc",
        "COOKIE_TIKTOK": "tiktok.com_cookies.enc",
        "COOKIE_FACEBOOK": "facebook.com_cookies.enc",
        "COOKIE_X": "x.com_cookies.enc",
    }
    
    cookies_root = Path(os.environ.get("COOKIES_ROOT", "./cookies"))
    global_dir = cookies_root / "_global"
    global_dir.mkdir(parents=True, exist_ok=True)
    
    import base64
    in_cookie = None
    cookie_lines = []
    
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        
        if in_cookie:
            if stripped.endswith('"') or stripped.endswith("'"):
                val = line.rstrip()[:-1]
                cookie_lines.append(val)
                cookie_data = "\n".join(cookie_lines)
                
                dest_path = global_dir / env_map[in_cookie]
                try:
                    try:
                        decoded = base64.b64decode(cookie_data, validate=True).decode("utf-8")
                        content_to_save = decoded if "\t" in decoded or decoded.lstrip().startswith("#") else cookie_data
                    except Exception:
                        content_to_save = cookie_data
                    
                    crypto.save_encrypted_cookie(dest_path, content_to_save)
                    logger.info("Successfully encrypted and saved env cookie: %s to %s", in_cookie, dest_path)
                except Exception as e:
                    logger.error("Failed to encrypt env cookie %s: %s", in_cookie, e)
                
                in_cookie = None
                cookie_lines = []
            else:
                cookie_lines.append(line)
            i += 1
            continue
            
        is_cookie_start = False
        for env_key in env_map:
            if stripped.startswith(f"{env_key}="):
                is_cookie_start = True
                val_start = line.split("=", 1)[1].strip()
                if (val_start.startswith('"') and val_start.endswith('"')) or (val_start.startswith("'") and val_start.endswith("'")):
                    cookie_data = val_start[1:-1]
                    dest_path = global_dir / env_map[env_key]
                    try:
                        try:
                            decoded = base64.b64decode(cookie_data, validate=True).decode("utf-8")
                            content_to_save = decoded if "\t" in decoded or decoded.lstrip().startswith("#") else cookie_data
                        except Exception:
                            content_to_save = cookie_data
                        
                        crypto.save_encrypted_cookie(dest_path, content_to_save)
                        logger.info("Successfully encrypted and saved env cookie: %s to %s", env_key, dest_path)
                    except Exception as e:
                        logger.error("Failed to encrypt env cookie %s: %s", env_key, e)
                elif val_start.startswith('"') or val_start.startswith("'"):
                    in_cookie = env_key
                    cookie_lines = [val_start[1:]]
                else:
                    cookie_data = val_start
                    dest_path = global_dir / env_map[env_key]
                    try:
                        try:
                            decoded = base64.b64decode(cookie_data, validate=True).decode("utf-8")
                            content_to_save = decoded if "\t" in decoded or decoded.lstrip().startswith("#") else cookie_data
                        except Exception:
                            content_to_save = cookie_data
                        
                        crypto.save_encrypted_cookie(dest_path, content_to_save)
                        logger.info("Successfully encrypted and saved env cookie: %s to %s", env_key, dest_path)
                    except Exception as e:
                        logger.error("Failed to encrypt env cookie %s: %s", env_key, e)
                break
                
        if not is_cookie_start:
            new_lines.append(line)
        i += 1
        
    cleaned_content = "\n".join(new_lines) + "\n"
    cleaned_content = re.sub(r'\n\n+', '\n\n', cleaned_content)
    env_file.write_text(cleaned_content, encoding="utf-8")
    logger.info("Successfully cleaned and stripped plaintext cookies from .env!")

def migrate():
    """Scan and encrypt plaintext cookies."""
    cookies_root = Path(os.environ.get("COOKIES_ROOT", "./cookies"))
    
    try:
        crypto = get_crypto()
    except Exception as e:
        logger.critical("Failed to initialize cryptographic cipher: %s", e)
        sys.exit(1)

    # First migrate and wipe raw cookies in .env
    migrate_env_cookies(crypto)

    if not cookies_root.exists():
        logger.info("Cookies root directory does not exist at %s. Nothing to migrate.", cookies_root)
        return

    logger.info("Scanning for plaintext cookies in %s...", cookies_root)
    
    migrated_count = 0
    removed_count = 0

    # Walk through all directories under cookies root using os.walk (optimized and fast)
    for root, _, filenames in os.walk(cookies_root):
        for filename in filenames:
            if filename in COOKIE_FILE_KEYS:
                path = Path(root) / filename
                enc_path = path.with_suffix(".enc")
                
                logger.info("Found plaintext cookie: %s", path)
                
                if enc_path.exists():
                    logger.info("Encrypted version already exists: %s. Plaintext file will be securely purged.", enc_path)
                else:
                    try:
                        cookie_data = path.read_text(encoding="utf-8")
                        if cookie_data.strip():
                            crypto.save_encrypted_cookie(enc_path, cookie_data)
                            logger.info("Successfully encrypted and saved: %s", enc_path)
                            migrated_count += 1
                        else:
                            logger.warning("Skipping empty cookie file: %s", path)
                    except Exception as e:
                        logger.error("Failed to encrypt cookie file %s: %s", path, e)
                        continue

                # Securely zero-wipe and unlink plaintext file
                try:
                    size = path.stat().st_size
                    if size > 0:
                        path.write_bytes(b"\x00" * size)
                    path.unlink(missing_ok=True)
                    logger.info("Securely zero-wiped and deleted plaintext file: %s", path)
                    removed_count += 1
                except Exception as e:
                    logger.error("Failed to delete plaintext file %s: %s", path, e)

    logger.info("Migration finished. Migrated: %d files, Purged: %d plaintext files.", migrated_count, removed_count)

if __name__ == "__main__":
    migrate()
