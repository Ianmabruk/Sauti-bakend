"""In-memory caches with predictable eviction.

Two policies are provided because the two workloads are different:

- :class:`TTLCache` for **web search** results. Entries expire on a wall-clock
  TTL, because a stale price is worse than a re-fetch.
- :class:`LRUCache` for **model replies**, bounded by entry count. Eviction is
  strictly least-recently-used.

Both are safe under ``asyncio`` concurrency: every operation is synchronous and
runs to completion without an ``await`` inside the critical section, so there
is no interleaving to guard against. That also means no lock overhead on the
hot path.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Generic, Iterator, Optional, TypeVar

from .logsetup import get_logger

logger = get_logger(__name__)

K = TypeVar("K")
V = TypeVar("V")


@dataclass
class _Entry(Generic[V]):
    """One cached value plus its expiry stamp."""

    value: V
    expires_at: float


class TTLCache(Generic[K, V]):
    """A size- and time-bounded cache.

    Args:
        max_size: Hard cap on live entries. The least recently inserted entry
            is dropped when full.
        ttl: Default lifetime in seconds for entries added without one.
    """

    def __init__(self, max_size: int = 256, ttl: float = 600.0) -> None:
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        if ttl <= 0:
            raise ValueError("ttl must be > 0")
        self._max_size = max_size
        self._ttl = ttl
        self._data: "OrderedDict[K, _Entry[V]]" = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def __len__(self) -> int:
        """Number of live, unexpired entries."""
        self._purge()
        return len(self._data)

    @property
    def stats(self) -> dict[str, int | float]:
        """Hit/miss counters, useful in the log line emitted at shutdown."""
        total = self._hits + self._misses
        return {
            "size": len(self),
            "hits": self._hits,
            "misses": self._misses,
            "evictions": self._evictions,
            "hit_rate": round(self._hits / total, 3) if total else 0.0,
        }

    def get(self, key: K) -> Optional[V]:
        """Return a live value, or None when absent or expired."""
        entry = self._data.get(key)
        if entry is None:
            self._misses += 1
            return None
        if entry.expires_at <= time.monotonic():
            del self._data[key]
            self._misses += 1
            return None
        self._data.move_to_end(key)
        self._hits += 1
        return entry.value

    def set(self, key: K, value: V, ttl: Optional[float] = None) -> None:
        """Store a value, evicting the oldest entry if the cache is full."""
        lifetime = self._ttl if ttl is None else ttl
        if lifetime <= 0:
            return
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = _Entry(value, time.monotonic() + lifetime)
        while len(self._data) > self._max_size:
            self._data.popitem(last=False)
            self._evictions += 1

    def invalidate(self, key: K) -> bool:
        """Drop one key. Returns True when something was removed."""
        return self._data.pop(key, None) is not None

    def clear(self) -> None:
        """Remove every entry and reset counters."""
        self._data.clear()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def _purge(self) -> None:
        now = time.monotonic()
        dead = [key for key, entry in self._data.items() if entry.expires_at <= now]
        for key in dead:
            del self._data[key]

    def __contains__(self, key: object) -> bool:
        return self.get(key) is not None  # type: ignore[arg-type]

    def __iter__(self) -> Iterator[K]:
        self._purge()
        return iter(list(self._data.keys()))


class LRUCache(Generic[K, V]):
    """A count-bounded least-recently-used cache.

    Args:
        max_size: Maximum live entries. 100 is a good default for replies.
        ttl: Optional lifetime; 0 or None disables time-based expiry, leaving
            pure LRU behaviour.
    """

    def __init__(self, max_size: int = 100, ttl: Optional[float] = None) -> None:
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        self._max_size = max_size
        self._ttl = ttl if ttl and ttl > 0 else None
        self._data: "OrderedDict[K, _Entry[V]]" = OrderedDict()
        self._hits = 0
        self._misses = 0

    def __len__(self) -> int:
        return len(self._data)

    @property
    def stats(self) -> dict[str, int | float]:
        """Hit/miss counters."""
        total = self._hits + self._misses
        return {
            "size": len(self._data),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 3) if total else 0.0,
        }

    def get(self, key: K) -> Optional[V]:
        """Return a live value, refreshing its recency."""
        entry = self._data.get(key)
        if entry is None:
            self._misses += 1
            return None
        if self._ttl and entry.expires_at <= time.monotonic():
            del self._data[key]
            self._misses += 1
            return None
        self._data.move_to_end(key)
        self._hits += 1
        return entry.value

    def set(self, key: K, value: V) -> None:
        """Store a value, evicting the least recently used entry when full."""
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = _Entry(
            value, time.monotonic() + (self._ttl or float("inf"))
        )
        while len(self._data) > self._max_size:
            self._data.popitem(last=False)

    def invalidate(self, key: K) -> bool:
        """Drop one key."""
        return self._data.pop(key, None) is not None

    def clear(self) -> None:
        """Remove every entry."""
        self._data.clear()
        self._hits = 0
        self._misses = 0
