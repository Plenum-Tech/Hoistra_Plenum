"""Levenshtein edit-distance helpers for the deterministic mapper's 7.4 AC1 tier
('exact OR Levenshtein ≤ 2'). Standalone + dependency-free so it's unit-testable.
"""
from __future__ import annotations


def levenshtein(a: str, b: str) -> int:
    """Character edit distance (insert / delete / substitute). Catches typos and minor
    spelling variants (``assetnme`` → ``asset_name``) without a fuzzy ratio."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > 2:        # callers only care about distances ≤ 2
        return abs(la - lb)
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[lb]


def closest_canonical_within(norm_source: str, canonical_fields, max_dist: int = 2):
    """The UNIQUE canonical field within ``max_dist`` edits of ``norm_source`` → ``(field, d)``
    or ``None``. A tie (two canonical names equidistant) returns ``None`` so an ambiguous
    near-match is sent to semantic rather than guessed. Names shorter than 4 chars are
    skipped — a 2-edit on a 3-char name is too loose to be 'deterministic'."""
    if len(norm_source) < 4:
        return None
    best = None
    best_d = max_dist + 1
    tie = False
    for cf in canonical_fields:
        d = levenshtein(norm_source, cf)
        if d < best_d:
            best_d, best, tie = d, cf, False
        elif d == best_d:
            tie = True
    if best is not None and best_d <= max_dist and not tie:
        return best, best_d
    return None
