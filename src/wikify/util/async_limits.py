"""Shared async-call decorators: rate limiter + concurrency cap.

Used by every outbound-HTTP caller in wikify (OpenAlex, CrossRef, doi.org
content negotiation). Kept here so the pattern is defined once.
"""

from __future__ import annotations

from asyncio import Semaphore
from functools import wraps

from aiolimiter import AsyncLimiter


def with_limiter(limiter: AsyncLimiter):
    """Decorate an async function with an AsyncLimiter context manager."""
    def inner(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            async with limiter:
                return await func(*args, **kwargs)
        return wrapper
    return inner


def with_semaphore(semaphore: Semaphore):
    """Decorate an async function with a Semaphore context manager."""
    def inner(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            async with semaphore:
                return await func(*args, **kwargs)
        return wrapper
    return inner
