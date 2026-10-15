"""A small in-memory cache with expiry. Lives only as long as the server process."""

import time
from typing import Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    def __init__(self, ttl_seconds: float, max_entries: int = 100):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._items: dict[tuple, tuple[float, T]] = {}

    def get(self, key: tuple) -> T | None:
        hit = self._items.get(key)
        if hit is None or hit[0] < time.monotonic():
            self._items.pop(key, None)
            return None
        return hit[1]

    def set(self, key: tuple, value: T) -> None:
        if len(self._items) >= self.max_entries:
            # Drop the entry closest to expiry
            self._items.pop(min(self._items, key=lambda k: self._items[k][0]))
        self._items[key] = (time.monotonic() + self.ttl, value)
