# Bibliographic Coupling

## What it is
Two papers are "coupled" when they cite the same references. Unlike direct citations (A cites B), coupling means A and B cite the same C — they're working on related problems.

## Algorithm
1. Fetch all raw citation entries per paper
2. Fingerprint each citation: lowercase, strip punctuation, first 80 chars
3. Build a reverse index: fingerprint -> set of papers that cite it
4. For each shared fingerprint, increment pair strength
5. Filter: only pairs with strength >= 2 (at least 2 shared references)
6. Cap at top-5 coupled partners per paper (sorted by strength)

## Why strength >= 2?
One shared reference is often noise (widely-cited foundational papers). Two shared references suggests genuine topical overlap.

## Where the code lives
- `vault/coupler.py`
