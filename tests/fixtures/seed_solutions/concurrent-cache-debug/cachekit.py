"""cachekit: a tiny tokenizer and a thread-safe memoizing LRU cache."""

import threading
from collections import OrderedDict


def tokenize(text):
    """Split `text` into tokens and return a list of (kind, value, offset) tuples.

    Kinds:
      "num"   integer or decimal: digits, optionally followed by "." and at least one digit ("12", "3.25").
              "7." is the number "7" followed by the punctuation ".".
      "ident" [A-Za-z_][A-Za-z0-9_]*
      "str"   a double-quoted string; backslash escapes the next character. The value keeps the quotes and
              escapes exactly as written, e.g. '"a\\"b"'.
      "punct" any other single non-whitespace character.
    Whitespace separates tokens and is dropped. `offset` is the index of the token's first character.
    Raises ValueError if a string is not terminated.
    """
    tokens = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch.isdigit():
            j = i
            while j < n and text[j].isdigit():  # FIX: was `j < n - 1`, dropping a final digit
                j += 1
            if j < n - 1 and text[j] == "." and text[j + 1].isdigit():
                j += 1
                while j < n and text[j].isdigit():
                    j += 1
            tokens.append(("num", text[i:j], i))
            i = j
        elif ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            tokens.append(("ident", text[i:j], i))
            i = j
        elif ch == '"':
            # FIX: scan character by character so that an escaped backslash before the closing quote
            # ("a\\") is handled; the old code only looked at the previous character.
            j = i + 1
            while True:
                if j >= n:
                    raise ValueError(f"unterminated string at {i}")
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    break
                j += 1
            tokens.append(("str", text[i : j + 1], i))
            i = j + 1
        else:
            tokens.append(("punct", ch, i))
            i += 1
    return tokens


class _Flight:
    """An in-flight computation that other callers can wait for."""

    __slots__ = ("done", "error", "value")

    def __init__(self):
        self.done = threading.Event()
        self.value = None
        self.error = None


class MemoCache:
    """Thread-safe memoizing cache with LRU eviction.

    get_or_compute(key, fn):
      * returns the cached value for `key` if present (a hit; it becomes the most recently used entry);
      * otherwise calls fn() once, stores and returns its result (a miss).
      Guarantees under concurrency:
      * fn is never running twice at the same time for the same key: callers that ask for a key whose value is
        being computed wait for that computation and receive its result (or its exception). Such waiting callers
        count as hits.
      * computations for different keys run in parallel (no lock is held while fn runs), and fn itself may call
        get_or_compute for other keys.
      * if fn raises, the exception propagates to the caller and to every waiting caller, nothing is cached, and
        a later call computes again.
    put(key, value): insert or overwrite; the entry becomes the most recently used.
    At most `capacity` entries are kept; inserting beyond that evicts the least recently used entry.
    stats() returns {"hits": int, "misses": int, "evictions": int, "size": int}.
    """

    def __init__(self, capacity):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._data = OrderedDict()
        self._inflight = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def _store(self, key, value):
        self._data[key] = value
        self._data.move_to_end(key)  # FIX: overwriting an existing key must refresh its recency
        if len(self._data) > self._capacity:
            self._data.popitem(last=False)
            self._evictions += 1

    def get_or_compute(self, key, fn):
        # FIX: the old version let concurrent callers compute the same key several times. Track in-flight
        # computations so that later callers wait for the first one (without holding the lock while fn runs).
        with self._lock:
            if key in self._data:
                self._hits += 1
                self._data.move_to_end(key)
                return self._data[key]
            flight = self._inflight.get(key)
            if flight is None:
                flight = self._inflight[key] = _Flight()
                self._misses += 1
                owner = True
            else:
                self._hits += 1
                owner = False
        if not owner:
            flight.done.wait()
            if flight.error is not None:
                raise flight.error
            return flight.value
        try:
            value = fn()
        except BaseException as exc:
            with self._lock:
                del self._inflight[key]
            flight.error = exc
            flight.done.set()
            raise
        with self._lock:
            del self._inflight[key]
            self._store(key, value)
        flight.value = value
        flight.done.set()
        return value

    def put(self, key, value):
        with self._lock:
            self._store(key, value)

    def get(self, key, default=None):
        """Return the cached value (counts as a use, not as a hit) or `default`."""
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
            return default

    def __len__(self):
        with self._lock:
            return len(self._data)

    def stats(self):
        with self._lock:
            return {"hits": self._hits, "misses": self._misses, "evictions": self._evictions, "size": len(self._data)}
