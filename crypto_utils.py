"""
crypto_utils.py — Cryptographic utilities for secure cookie rest encryption.
"""

import os
import logging
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from typing import Optional

logger = logging.getLogger(__name__)

class CookieEncryption:
    """Handles encryption/decryption of cookie files."""
    
    def __init__(self):
        key = os.environ.get("COOKIE_ENCRYPTION_KEY")
        if not key:
            raise ValueError("COOKIE_ENCRYPTION_KEY not set in environment")
        
        try:
            self.cipher = Fernet(key.encode("utf-8"))
        except Exception as e:
            raise ValueError(f"Invalid encryption key format: {e}")
    
    def encrypt_cookie(self, cookie_data: str) -> bytes:
        """Encrypt cookie data."""
        try:
            return self.cipher.encrypt(cookie_data.encode("utf-8"))
        except Exception as e:
            logger.error("Cookie encryption failed: %s", e)
            raise
    
    def decrypt_cookie(self, encrypted_data: bytes) -> str:
        """Decrypt cookie data."""
        try:
            return self.cipher.decrypt(encrypted_data).decode("utf-8")
        except InvalidToken:
            logger.error("Invalid encryption token - cookie may be corrupted or key changed")
            raise
        except Exception as e:
            logger.error("Cookie decryption failed: %s", e)
            raise
    
    def save_encrypted_cookie(self, path: Path, cookie_data: str) -> None:
        """Save encrypted cookie to file."""
        encrypted = self.encrypt_cookie(cookie_data)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encrypted)
        logger.info("Encrypted cookie saved to %s", path)
    
    def load_encrypted_cookie(self, path: Path) -> Optional[str]:
        """Load and decrypt cookie from file."""
        if not path.exists():
            return None
        
        try:
            encrypted = path.read_bytes()
            return self.decrypt_cookie(encrypted)
        except Exception as e:
            logger.error("Failed to load cookie from %s: %s", path, e)
            return None

# Global instance
_crypto = None

def get_crypto() -> CookieEncryption:
    """Get or create global crypto instance."""
    global _crypto
    if _crypto is None:
        _crypto = CookieEncryption()
    return _crypto
