# STORY-101 — Structured parser cache warm-up

## Why

The parser rebuilds its lookup tables on every start. A warm cache layer
removes the rebuild cost. This rebuild is NON-DATA-PRESERVING by design:
a stale cache is discarded, never migrated.

## Acceptance criteria

- AC-1: malformed input is rejected with a refusal message and nothing is
  written. Test hooks: `test_rejects_malformed_input`
- AC-2: a round trip preserves order. Test hooks: `test_round_trip_missing`

## Design notes

Extend `Parser` (src/app/parser.py) with a cache seam `CacheWarming`
backed by `src/app/cache.py`. The loader surface `Loader.LoadEntries`
keeps its one production call site; `cache_ttl` is read from
src/app/config.py. `missing_vocab_token` is a candidate that must not
exist at base. Paths: src/app/parser.py exists; src/app/cache.py is
created by this story.
