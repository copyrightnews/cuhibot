"""
File utility functions with security enhancements.
Provides safe file operations with path validation and integrity checks.
"""
import hashlib
import logging
import os
import shlex
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Allowed file extensions for media
PHOTO_EXT = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"})
VIDEO_EXT = frozenset({".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v"})
ALL_MEDIA_EXT = PHOTO_EXT | VIDEO_EXT


def validate_file_path(file_path: str, base_dir: Path) -> Optional[Path]:
    """
    Validate and resolve a file path to prevent path traversal attacks.
    
    Args:
        file_path: User-provided file path
        base_dir: Base directory that file must be within
        
    Returns:
        Resolved Path object if valid, None if invalid
    """
    if not file_path or not file_path.strip():
        logger.warning("Empty file path provided")
        return None
    
    # Reject dangerous patterns
    dangerous_patterns = ['..', '~', '\x00', '\\\\', '//']
    if any(pattern in file_path for pattern in dangerous_patterns):
        logger.warning("Dangerous pattern detected in file path: %s", file_path)
        return None
    
    # Reject absolute paths
    if os.path.isabs(file_path):
        logger.warning("Absolute path rejected: %s", file_path)
        return None
    
    try:
        # Resolve paths with strict=True to reject non-existent paths
        target = (base_dir / file_path).resolve(strict=True)
        base_resolved = base_dir.resolve(strict=True)
        
        # Ensure target is within base directory
        target.relative_to(base_resolved)
        
        # Check for symlinks
        if target.is_symlink():
            logger.warning("Symlink rejected: %s", file_path)
            return None
        
        # Verify it's a regular file
        if not target.is_file():
            logger.warning("Not a regular file: %s", file_path)
            return None
        
        return target
        
    except (ValueError, FileNotFoundError, OSError) as e:
        logger.warning("Path validation failed for %s: %s", file_path, e)
        return None


def calculate_file_hash(file_path: Path, algorithm: str = "sha256") -> Optional[str]:
    """
    Calculate cryptographic hash of a file for integrity verification.
    
    Args:
        file_path: Path to file
        algorithm: Hash algorithm (sha256, md5, etc.)
        
    Returns:
        Hex digest of file hash, or None on error
    """
    try:
        hasher = hashlib.new(algorithm)
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception as e:
        logger.error("Failed to calculate hash for %s: %s", file_path, e)
        return None


def verify_file_type(file_path: Path) -> bool:
    """
    Verify file type by checking magic bytes (file signature).
    
    Args:
        file_path: Path to file
        
    Returns:
        True if file type matches extension, False otherwise
    """
    try:
        with open(file_path, 'rb') as f:
            header = f.read(12)
        
        ext = file_path.suffix.lower()
        
        # JPEG
        if ext in {'.jpg', '.jpeg'}:
            return header[:3] == b'\xff\xd8\xff'
        
        # PNG
        if ext == '.png':
            return header[:8] == b'\x89PNG\r\n\x1a\n'
        
        # GIF
        if ext == '.gif':
            return header[:6] in (b'GIF87a', b'GIF89a')
        
        # WebP
        if ext == '.webp':
            return header[:4] == b'RIFF' and header[8:12] == b'WEBP'
        
        # MP4
        if ext == '.mp4':
            return b'ftyp' in header[:12]
        
        # WebM
        if ext == '.webm':
            return header[:4] == b'\x1a\x45\xdf\xa3'
        
        # For other types, just check if file is readable
        return True
        
    except Exception as e:
        logger.error("Failed to verify file type for %s: %s", file_path, e)
        return False


def sanitize_command_arg(arg: str) -> str:
    """
    Sanitize command-line argument to prevent injection attacks.
    
    Args:
        arg: Command argument to sanitize
        
    Returns:
        Safely quoted argument
    """
    return shlex.quote(arg)


def get_safe_filename(filename: str) -> str:
    """
    Generate a safe filename by removing dangerous characters.
    
    Args:
        filename: Original filename
        
    Returns:
        Sanitized filename
    """
    # Remove path separators and dangerous characters
    dangerous_chars = ['/', '\\', '\x00', '..', '~', '|', ';', '&', '$', '`']
    safe_name = filename
    for char in dangerous_chars:
        safe_name = safe_name.replace(char, '_')
    
    # Limit length
    if len(safe_name) > 255:
        name, ext = os.path.splitext(safe_name)
        safe_name = name[:255-len(ext)] + ext
    
    return safe_name


def check_disk_space(path: Path, required_mb: float = 100) -> bool:
    """
    Check if sufficient disk space is available.
    
    Args:
        path: Path to check
        required_mb: Required space in megabytes
        
    Returns:
        True if sufficient space available, False otherwise
    """
    try:
        import shutil
        stat = shutil.disk_usage(path)
        available_mb = stat.free / (1024 * 1024)
        return available_mb >= required_mb
    except Exception as e:
        logger.error("Failed to check disk space: %s", e)
        return False
