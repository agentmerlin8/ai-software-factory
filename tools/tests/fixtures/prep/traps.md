# Dispatch traps (curated)

Preamble prose that must NOT be inlined — only the bullet body is.

- Seed the cache fixture with an empty dict, never None (None means
  "not warmed" and silently skips the fast path).
- Order-preserving round trips need a sorted snapshot before compare.
