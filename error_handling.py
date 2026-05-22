"""
error_handling.py — Centralized error handling utilities.
"""
import logging
import asyncio
import functools
from typing import Callable, Any

logger = logging.getLogger(__name__)

def log_exceptions(operation_name: str):
    """Decorator to log exceptions with context."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.exception(
                    "%s failed in %s: %s",
                    operation_name,
                    func.__name__,
                    e,
                    extra={
                        "operation": operation_name,
                        "function": func.__name__,
                        "args": str(args)[:100],  # Truncate for safety
                    }
                )
                raise
        
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.exception(
                    "%s failed in %s: %s",
                    operation_name,
                    func.__name__,
                    e,
                    extra={
                        "operation": operation_name,
                        "function": func.__name__,
                        "args": str(args)[:100],
                    }
                )
                raise
        
        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper
    return decorator

class ErrorContext:
    """Context manager for safe error handling."""
    
    def __init__(self, operation: str, reraise: bool = False):
        self.operation = operation
        self.reraise = reraise
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            logger.exception(
                "%s failed: %s",
                self.operation,
                exc_val,
                extra={"operation": self.operation}
            )
            return not self.reraise  # Suppress if reraise=False
        return True
