"""Sampling for open coding, the Map, and the Progress metrics (Trace Lab, Phase 4).

A batch draws answers (turns) from a company's pool with one strategy, records every pick with a
one-line reason, its seed and its parameters, and never draws a turn that is already in a batch of
that company. Only uniform batches count toward rates. The pickers, the feature matrix, k-means, the
two-axis projection and the progress metrics in the first half of this module are pure functions of their
inputs: no database, no network, and nothing outside the standard library (no numpy; G0 addendum R11), so
every result is reproducible from the seed.

The second half (from "database: pool, batches, queue ...") reads and writes through the two databases,
never one SQL statement across them (Golden Rule G0). The chat's turns, spans and accounts are READ through
db.main_engine() (read-only, company scoped); batches, items and every verdict are in the lab database and go
through db.lab_engine(). Where a question needs both, ids are read from one side and handed to the other as
`= ANY(CAST(:ids AS text[]))`, and the rows are merged in Python. A batch and its items are written in one lab
transaction, and every sampled answer is first copied into the evidence tables (evidence.snapshot), so it
outlives the chat's 90-day span purge.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import re
import statistics
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.agents import trace

from . import annotations, conversations, db, evidence, normalize, provenance
from .review_errors import Conflict, Invalid, NotFound
from .stats import cohen_kappa, wilson_interval

STRATEGIES = ("uniform", "diversity", "dimension", "outlier", "enrich")   # drawable (the route's BatchBody)
#: Strategies whose items the caller names instead of drawing them (monitoring's flagged answers).
RECORDED_STRATEGIES = ("monitor_flagged",)
RECORDED_MAX = 200
PLAN_STEPS = ("initial_uniform", "cluster_representatives", "dimension", "depth_search", "final_uniform", "extra")
HW4_PLAN = (("initial_uniform", "uniform", 15), ("cluster_representatives", "diversity", 15),
            ("dimension", "dimension", 30), ("depth_search", "enrich", 25), ("final_uniform", "uniform", 15))
PLAN_TARGET = 100
DIMENSIONS = ("channel", "route_target", "asker_role", "page")
FEATURES = ("turn_position", "llm_calls", "tool_calls", "distinct_tools", "db_queries", "tokens",
            "latency_ms", "answer_chars")
LOG1P_FEATURES = frozenset(FEATURES) - {"distinct_tools"}
FEATURE_LABELS = {"turn_position": "Position in the conversation", "llm_calls": "Model calls",
                  "tool_calls": "Tool calls", "distinct_tools": "Different tools", "db_queries": "Database reads",
                  "tokens": "Tokens", "latency_ms": "Time to answer", "answer_chars": "Answer length"}
FEATURE_VERSION = "f1"
DEFAULT_SEED = 7
KMEANS_ITERS = 25
POOL_MAX = 20000
ENRICH_POOL_MAX = 5000
SEEDS_MAX = 500
MAP_MAX = 3000
MAP_CLUSTERS = 8
DIMENSION_TOP = 10
MISSING_LISTED = 25
TIMEOUT = "SET LOCAL statement_timeout = '10s'"
_TOKEN = re.compile(r"[a-z0-9']+")
_JACOBI_SWEEPS = 60
_EPS = 1e-12
#: Why a sampled answer was copied into the evidence tables (evidence_turns.reason, at most 40 characters).
SNAPSHOT_REASON = "sampled"
BATCH_MAX = 200
USERS = "plenum_cafm.users"
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Candidate:
    turn_id: str
    session_id: str | None
    started_at: datetime
    features: dict[str, float | None]
    dims: dict[str, str | None]
    text: str | None = None            # question + "\n" + answer; loaded only for enrich
    question: str | None = None        # redacted, for enrich de-duplication and the Map's hover
    organization_id: str | None = None
    batch_id: str | None = None        # the batch that drew it, when the pool includes sampled turns

    @property
    def conversation_key(self) -> str:
        return normalize.group_key(self.session_id, self.turn_id)


@dataclass(frozen=True)
class Pick:
    turn_id: str
    reason: str
    cluster: int | None = None
    dimension_value: str | None = None
    score: float | None = None


# ── features, standardizing, k-means and the projection (pure Python) ───────────────────

def route_target(route: str | None) -> str:
    """'single:compliance' -> 'compliance'; 'multi:a,b' -> 'multi'; NULL -> 'unrouted'."""
    if not route:
        return "unrouted"
    mode, _, targets = route.partition(":")
    if mode == "multi":
        return "multi"
    return targets or mode


def _filled_columns(pool: Sequence[Candidate]) -> list[list[float]]:
    """One list per feature, in pool order: a missing value takes the column's median."""
    cols: list[list[float]] = []
    for name in FEATURES:
        raw = [c.features.get(name) for c in pool]
        present = [float(v) for v in raw if v is not None and math.isfinite(float(v))]
        median = statistics.median(present) if present else 0.0
        cols.append([float(v) if v is not None and math.isfinite(float(v)) else median for v in raw])
    return cols


def feature_matrix(pool: Sequence[Candidate]) -> list[list[float]]:
    """Rows in pool order, columns in FEATURES order: NULL -> column median, then log1p where listed."""
    if not pool:
        return []
    cols = _filled_columns(pool)
    cols = [[math.log1p(max(0.0, v)) for v in col] if name in LOG1P_FEATURES else col
            for name, col in zip(FEATURES, cols)]
    return [list(row) for row in zip(*cols)]


def raw_matrix(pool: Sequence[Candidate]) -> list[list[float]]:
    """As feature_matrix, without the log1p: the values a person reads."""
    if not pool:
        return []
    return [list(row) for row in zip(*_filled_columns(pool))]


def standardize(rows: list[list[float]]) -> list[list[float]]:
    """z-score every column with the population deviation; a zero deviation becomes 1 (a flat column stays 0)."""
    if not rows:
        return []
    n = len(rows)
    out_cols = []
    for col in zip(*rows):
        mean = math.fsum(col) / n
        sd = math.sqrt(math.fsum((v - mean) ** 2 for v in col) / n)
        if sd == 0 or not math.isfinite(sd):
            sd = 1.0
        out_cols.append([(v - mean) / sd for v in col])
    return [list(row) for row in zip(*out_cols)]


def kmeans(vectors: list[list[float]], k: int, *, seed: int = DEFAULT_SEED,
           iters: int = KMEANS_ITERS) -> tuple[list[int], list[list[float]]]:
    """Lloyd's k-means. Initial centroids: random.Random(seed).sample(range(n), k); an empty cluster keeps
    its centroid; ties go to the lowest cluster index; it stops early once no label changes.
    Labels are 0-based. Returns (labels, centroids)."""
    n = len(vectors)
    if n == 0 or k < 1:
        return [], []
    k = min(k, n)
    centroids = [list(vectors[i]) for i in random.Random(seed).sample(range(n), k)]
    labels: list[int] | None = None
    dist = math.dist
    for _ in range(iters):
        new = []
        for v in vectors:
            best, best_d = 0, dist(v, centroids[0])
            for j in range(1, k):
                d = dist(v, centroids[j])
                if d < best_d:
                    best, best_d = j, d
            new.append(best)
        if labels is not None and new == labels:
            break
        labels = new
        for j in range(k):
            members = [vectors[i] for i in range(n) if labels[i] == j]
            if members:
                centroids[j] = [math.fsum(col) / len(members) for col in zip(*members)]
    return list(labels or []), centroids


def _jacobi_eigen(matrix: list[list[float]]) -> tuple[list[float], list[list[float]]]:
    """Eigenvalues and eigenvectors (as columns of the second result) of a small symmetric matrix by the
    cyclic Jacobi method. Deterministic: no random start, a fixed sweep order."""
    d = len(matrix)
    a = [row[:] for row in matrix]
    v = [[1.0 if i == j else 0.0 for j in range(d)] for i in range(d)]
    for _ in range(_JACOBI_SWEEPS):
        off = math.fsum(a[i][j] ** 2 for i in range(d) for j in range(i + 1, d))
        scale = math.fsum(a[i][i] ** 2 for i in range(d)) + off
        if off <= _EPS * _EPS * max(scale, _EPS):
            break
        for p in range(d - 1):
            for q in range(p + 1, d):
                if abs(a[p][q]) < 1e-300:
                    continue
                theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
                t = (1.0 if theta >= 0 else -1.0) / (abs(theta) + math.sqrt(theta * theta + 1.0))
                c = 1.0 / math.sqrt(t * t + 1.0)
                s = t * c
                for r in range(d):
                    arp, arq = a[r][p], a[r][q]
                    a[r][p], a[r][q] = c * arp - s * arq, s * arp + c * arq
                for r in range(d):
                    apr, aqr = a[p][r], a[q][r]
                    a[p][r], a[q][r] = c * apr - s * aqr, s * apr + c * aqr
                for r in range(d):
                    vrp, vrq = v[r][p], v[r][q]
                    v[r][p], v[r][q] = c * vrp - s * vrq, s * vrp + c * vrq
    return [a[i][i] for i in range(d)], v


def pca2(vectors: list[list[float]]) -> tuple[list[tuple[float, float]], tuple[float, float]]:
    """Project to the first two principal components (eigenvectors of the covariance matrix, found by the
    Jacobi method). Each component is flipped so its largest-magnitude loading is positive, so the plot is the
    same on every run and for any row order. Returns (points, (share1, share2)) with the shares of the
    total variance; a missing or flat component scores 0."""
    n = len(vectors)
    if n == 0:
        return [], (0.0, 0.0)
    d = len(vectors[0])
    means = [math.fsum(col) / n for col in zip(*vectors)] if d else []
    x = [[v - m for v, m in zip(row, means)] for row in vectors]
    cov = [[0.0] * d for _ in range(d)]
    for i in range(d):
        ci = [row[i] for row in x]
        for j in range(i, d):
            cov[i][j] = cov[j][i] = math.fsum(a * row[j] for a, row in zip(ci, x)) / n
    values, vecs = _jacobi_eigen(cov) if d else ([], [])
    total = math.fsum(max(0.0, cov[i][i]) for i in range(d))
    order = sorted(range(d), key=lambda i: (-values[i], i))
    coords: list[list[float]] = []
    shares: list[float] = []
    for rank in range(2):
        if rank < d and total > 0 and values[order[rank]] > _EPS * total:
            col = order[rank]
            loading = [vecs[r][col] for r in range(d)]
            pivot = max(range(d), key=lambda r: (round(abs(loading[r]), 9), -r))
            if loading[pivot] < 0:
                loading = [-w for w in loading]
            coords.append([math.fsum(a * b for a, b in zip(row, loading)) for row in x])
            shares.append(round(values[col] / total, 4))
        else:
            coords.append([0.0] * n)
            shares.append(0.0)
    return ([(round(a, 4) + 0.0, round(b, 4) + 0.0) for a, b in zip(coords[0], coords[1])],
            (shares[0], shares[1]))


# ── pickers (pure) ─────────────────────────────────────────────────────────────────────

def _by_id(pool: Sequence[Candidate]) -> list[Candidate]:
    return sorted(pool, key=lambda c: c.turn_id)


def _fill(order: Sequence[Candidate], n: int, used: set[str], picks: list[Pick], reason: str) -> None:
    """Append up to n picks from `order`, one per conversation."""
    want = len(picks) + n
    taken = {p.turn_id for p in picks}
    for c in order:
        if len(picks) >= want:
            break
        if c.conversation_key in used or c.turn_id in taken:
            continue
        used.add(c.conversation_key)
        taken.add(c.turn_id)
        picks.append(Pick(c.turn_id, reason))


def pick_uniform(pool: list[Candidate], k: int, seed: int) -> list[Pick]:
    """random.Random(seed).sample over the pool sorted by turn_id, one per conversation."""
    ordered = _by_id(pool)
    order = random.Random(seed).sample(ordered, len(ordered))
    picks: list[Pick] = []
    _fill(order, k, set(), picks, "uniform pick")
    return picks


def pick_diversity(pool: list[Candidate], k: int, seed: int, *, random_share: float = 1 / 3) -> list[Pick]:
    """Cluster representatives (closest to the centroid first, round-robin across clusters), then random picks."""
    if not pool or k < 1:
        return []
    n_rand = math.floor(k * random_share)
    n_rep = max(1, k - n_rand)
    ordered = _by_id(pool)
    n_clusters = min(max(1, n_rep // 2), len(ordered))
    x = standardize(feature_matrix(ordered))
    labels, centroids = kmeans(x, n_clusters, seed=seed)
    dist = [math.dist(x[i], centroids[labels[i]]) for i in range(len(ordered))]
    queues = {j: sorted((i for i in range(len(ordered)) if labels[i] == j), key=lambda i: (dist[i], ordered[i].turn_id))
              for j in range(len(centroids))}
    picks: list[Pick] = []
    used: set[str] = set()
    moved = True
    while len(picks) < n_rep and moved:
        moved = False
        for j in range(len(centroids)):
            if len(picks) >= n_rep:
                break
            queue = queues[j]
            while queue:
                i = queue.pop(0)
                c = ordered[i]
                if c.conversation_key in used:
                    continue
                used.add(c.conversation_key)
                picks.append(Pick(c.turn_id, f"cluster {j + 1} representative (distance {dist[i]:.2f})",
                                  cluster=j + 1, score=round(dist[i], 4)))
                moved = True
                break
    taken = {p.turn_id for p in picks}
    rest = [c for c in ordered if c.turn_id not in taken]
    _fill(random.Random(seed).sample(rest, len(rest)), n_rand, used, picks, "random pick")
    return picks


def dimension_value(c: Candidate, dimension: str) -> str:
    v = c.dims.get(dimension)
    return "(none)" if v is None or str(v).strip() == "" else str(v)


def dimension_groups(pool: Sequence[Candidate], dimension: str) -> list[tuple[str, list[Candidate]]]:
    """Values by pool count (largest first, then by name); the top 10 kept, the rest as '(other)'."""
    if dimension not in DIMENSIONS:
        raise Invalid("dimension_not_allowed")
    groups: dict[str, list[Candidate]] = {}
    for c in pool:
        groups.setdefault(dimension_value(c, dimension), []).append(c)
    ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    kept = ranked[:DIMENSION_TOP]
    other = [c for _, cs in ranked[DIMENSION_TOP:] for c in cs]
    if other:
        kept.append(("(other)", other))
    return sorted(kept, key=lambda kv: (-len(kv[1]), kv[0]))


def dimension_quotas(sizes: Sequence[int], k: int) -> list[int]:
    """k // m per value, the remainder to the largest values; a value with too few turns hands its
    shortfall on to the next value that has room (wrapping round to the first)."""
    m = len(sizes)
    if m == 0 or k < 1:
        return [0] * m
    quotas = [k // m + (1 if i < k % m else 0) for i in range(m)]
    short = 0
    for i, size in enumerate(sizes):
        if quotas[i] > size:
            short += quotas[i] - size
            quotas[i] = size
    i = 0
    while short and any(q < s for q, s in zip(quotas, sizes)):
        if quotas[i % m] < sizes[i % m]:
            quotas[i % m] += 1
            short -= 1
        i += 1
    return quotas


def pick_dimension(pool: list[Candidate], k: int, seed: int, *, dimension: str) -> list[Pick]:
    """A quota per value (dimension_quotas), drawn uniformly within the value, one turn per conversation."""
    groups = dimension_groups(pool, dimension)
    if not groups or k < 1:
        return []
    quotas = dimension_quotas([len(members) for _, members in groups], k)
    rng = random.Random(seed)
    picks: list[Pick] = []
    used: set[str] = set()
    carry = 0
    for (value, members), quota in zip(groups, quotas):
        want = quota + carry
        got = 0
        ordered = _by_id(members)
        for c in rng.sample(ordered, len(ordered)):
            if got >= want:
                break
            if c.conversation_key in used:
                continue
            used.add(c.conversation_key)
            picks.append(Pick(c.turn_id, f"dimension {dimension} = {value} (quota {quota} of {k})",
                              dimension_value=value))
            got += 1
        carry = want - got
    return picks


def pick_outliers(pool: list[Candidate], k: int) -> list[Pick]:
    """1.5 x IQR fences on tokens (q1 = s[n//4], q3 = s[3n//4]); both tails, furthest past its fence first."""
    if not pool or k < 1:
        return []
    tokens = sorted(float(c.features.get("tokens") or 0) for c in pool)
    n = len(tokens)
    q1, q3 = tokens[n // 4], tokens[(3 * n) // 4]
    low, high = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
    flagged = []
    for c in pool:
        t = float(c.features.get("tokens") or 0)
        if t > high:
            flagged.append((t - high, c, t, high))
        elif t < low:
            flagged.append((low - t, c, t, low))
    flagged.sort(key=lambda r: (-r[0], r[1].turn_id))
    picks: list[Pick] = []
    used: set[str] = set()
    for distance, c, t, fence in flagged:
        if len(picks) >= k:
            break
        if c.conversation_key in used:
            continue
        used.add(c.conversation_key)
        picks.append(Pick(c.turn_id, f"IQR outlier on tokens: {t:,.0f} (fence {fence:,.0f})",
                          score=round(distance, 4)))
    return picks


def _bow(text_: str | None) -> Counter:
    return Counter(_TOKEN.findall((text_ or "").lower()))


def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(t, 0) for t, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def _norm_question(c: Candidate) -> str:
    q = c.question if c.question is not None else (c.text or "").split("\n", 1)[0]
    return " ".join(q.lower().split())


def pick_enrich(pool: list[Candidate], k: int, seed: int, *, seeds: list[Candidate],
                random_share: float = 0.2, seed_label: str = "a wrong answer") -> list[Pick]:
    """Bag-of-words cosine to the seeds (best match), top first, no repeated question; then random picks. `seed_label`
    is what the seeds are, in each pick's reason. With no seeds it falls back to diversity with the same random share."""
    if not seeds:
        return pick_diversity(pool, k, seed, random_share=random_share)
    seed_bows = [_bow(s.text) for s in seeds]
    seed_ids = {s.turn_id for s in seeds}
    scored = []
    for c in _by_id(pool):
        if c.turn_id in seed_ids:
            continue
        bow = _bow(c.text)
        scored.append((max(_cosine(bow, sb) for sb in seed_bows), c))
    scored.sort(key=lambda r: (-r[0], r[1].turn_id))
    n_rand = math.floor(k * random_share)
    n_top = k - n_rand
    picks: list[Pick] = []
    used: set[str] = set()
    seen: set[str] = set()
    for score, c in scored:
        if len(picks) >= n_top:
            break
        q = _norm_question(c)
        if c.conversation_key in used or (q and q in seen):
            continue
        used.add(c.conversation_key)
        seen.add(q)
        picks.append(Pick(c.turn_id, f"similar to {seed_label} ({score:.2f})", score=round(score, 4)))
    taken = {p.turn_id for p in picks}
    rest = [c for c in _by_id(pool) if c.turn_id not in taken and c.turn_id not in seed_ids]
    _fill(random.Random(seed).sample(rest, len(rest)), n_rand, used, picks, "random pick")
    return picks


def pick_ordered(pool: list[Candidate], k: int, ordered: Sequence[tuple[str, str]]) -> list[Pick]:
    """Answers a depth scan's people decided, in the order they were decided, each with its reason (spec §3.3). Only an
    answer still in the pool can be drawn (the window, and never one already in a batch), once, and one per conversation."""
    in_pool = {c.turn_id: c for c in pool}
    picks: list[Pick] = []
    used: set[str] = set()
    conversations_used: set[str] = set()
    for turn_id, why in ordered:
        c = in_pool.get(turn_id)
        if c is None or turn_id in used or c.conversation_key in conversations_used:
            continue
        used.add(turn_id)
        conversations_used.add(c.conversation_key)
        picks.append(Pick(turn_id, why))
        if len(picks) >= k:
            break
    return picks


# ── requests (pure) ────────────────────────────────────────────────────────────────────

def retention_days() -> int:
    try:
        return int(os.environ.get("TRACE_RETENTION_DAYS") or trace.DEFAULT_RETENTION_DAYS)
    except ValueError:
        return trace.DEFAULT_RETENTION_DAYS


def check_window(since: datetime, until: datetime, *, now: datetime | None = None) -> None:
    """since must lie inside span retention (every sampled turn still has its spans); until > since."""
    now = now or datetime.now(timezone.utc)
    if not isinstance(since, datetime) or not isinstance(until, datetime):
        raise Invalid("bad_window", "Send the period as a start and an end.")
    if since.tzinfo is None or until.tzinfo is None:
        raise Invalid("bad_window", "Send the period with a time zone.")
    if until <= since:
        raise Invalid("bad_window")
    days = retention_days()
    if since < now - timedelta(days=days):
        raise Invalid("window_outside_retention",
                      f"Pick a period inside the last {days} days; older steps are no longer kept.")


def _check_recorded(req: Mapping[str, Any], *, now: datetime | None = None) -> None:
    """A recorded batch (monitor_flagged) names 1-200 answers, each with an id, inside span retention."""
    items = req.get("items")
    if not isinstance(items, (list, tuple)) or not 1 <= len(items) <= RECORDED_MAX or not all(
            isinstance(it, Mapping) and isinstance(it.get("turn_id"), str) and it["turn_id"].strip() for it in items):
        raise Invalid("bad_items", f"Name between 1 and {RECORDED_MAX} answers, each by its id.")
    check_window(req.get("since"), req.get("until"), now=now)


SEED_SOURCES = ("bow", "depth_scan")
SEED_TEXT = {
    "source": "Choose where the similar answers should come from.",
    "enrich_only": "A failure mode or a depth scan can only be chosen for the batch that looks for answers like the ones "
                   "already found.",
    "both": "Choose a failure mode or a depth scan, not both.",
    "scan": "Choose the depth scan to take the answers from.",
    "scan_only": "A depth scan can only be chosen when the answers come from a depth scan.",
    "mode": "Choose a failure mode from the list.",
    "live": "Choose a failure mode that is still in use.",
}


def _bad_seed(kind: str) -> Invalid:
    return Invalid("bad_seed_source", SEED_TEXT[kind])


def check_seed_source(req: Mapping[str, Any]) -> None:
    """The options that seed an enrich batch from a failure mode or from a depth scan (spec §3.3): only the enrich batch
    takes them, and each must name something that can exist. Whether it does is read later (enrich_inputs)."""
    mode_id, run_id, source = req.get("mode_id"), req.get("proposal_run_id"), req.get("source") or "bow"
    if source not in SEED_SOURCES:
        raise _bad_seed("source")
    if req.get("strategy") != "enrich" and (mode_id or run_id or source != "bow"):
        raise _bad_seed("enrich_only")
    if source == "depth_scan":
        if mode_id:
            raise _bad_seed("both")
        if _uuid_or_none(run_id) is None:
            raise _bad_seed("scan")
    elif run_id:
        raise _bad_seed("scan_only")
    if mode_id and _uuid_or_none(mode_id) is None:
        raise _bad_seed("mode")


def check_request(req: Mapping[str, Any], *, now: datetime | None = None) -> None:
    """Refuse what a batch cannot draw: outcome dimensions, an outside window, golden runs as a channel and
    the options that need failure modes. A recorded strategy (monitor_flagged) is checked by _check_recorded:
    it draws nothing."""
    if req.get("strategy") in RECORDED_STRATEGIES:
        _check_recorded(req, now=now)
        return
    if req.get("strategy") not in STRATEGIES or req.get("plan_step") not in PLAN_STEPS:
        raise Invalid("bad_strategy", "Unknown batch kind.")
    dim = req.get("dimension")
    if req["strategy"] == "dimension" and dim not in DIMENSIONS:
        raise Invalid("dimension_not_allowed")
    if dim is not None and dim not in DIMENSIONS:
        raise Invalid("dimension_not_allowed")
    check_seed_source(req)
    channel = req.get("channel") or "chat"
    if channel not in provenance.CHANNELS or channel == "golden":
        raise Invalid("bad_channel", "Pick a channel other than golden runs.")
    check_window(req.get("since"), req.get("until"), now=now)


def random_share_for(req: Mapping[str, Any]) -> float:
    if req.get("source") == "depth_scan":                      # a depth scan's own decided answers: nothing is added at random
        return 0.0
    share = req.get("random_share")
    if share is not None:
        return float(share)
    if req["strategy"] == "enrich":
        return 0.2
    return 0.0 if req["plan_step"] == "cluster_representatives" else 1 / 3


def draw(pool: list[Candidate], req: Mapping[str, Any], *, seeds: Sequence[Candidate] = (),
         seed_label: str = "a wrong answer", ordered: Sequence[tuple[str, str]] | None = None) -> list[Pick]:
    s, k, seed = req["strategy"], int(req["k"]), int(req.get("seed", DEFAULT_SEED))
    if s == "enrich" and ordered is not None:
        return pick_ordered(pool, k, ordered)
    if s == "uniform":
        return pick_uniform(pool, k, seed)
    if s == "diversity":
        return pick_diversity(pool, k, seed, random_share=random_share_for(req))
    if s == "dimension":
        return pick_dimension(pool, k, seed, dimension=req["dimension"])
    if s == "outlier":
        return pick_outliers(pool, k)
    return pick_enrich(pool, k, seed, seeds=list(seeds), random_share=random_share_for(req), seed_label=seed_label)


# ── progress metrics (pure) ────────────────────────────────────────────────────────────

def resolve(verdicts: Sequence[str]) -> str | None:
    """An item's common verdict: 'right', 'wrong', 'disputed' when its reviewers disagree, None when unreviewed."""
    kinds = set(verdicts)
    if not kinds:
        return None
    return kinds.pop() if len(kinds) == 1 else "disputed"


def fair_sample(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Wrong rate over the items of uniform, non-withdrawn batches only; each item resolves to its
    reviewers' common verdict and disputed items are left out. items: {strategy, withdrawn, verdicts}."""
    fair = [i for i in items if i["strategy"] == "uniform" and not i.get("withdrawn")]
    counts = Counter(resolve(i["verdicts"]) for i in fair)
    wrong, right = counts.get("wrong", 0), counts.get("right", 0)
    n = wrong + right
    low, high = wilson_interval(wrong, n)
    return {"label": "From fair (uniform) batches only", "items": len(fair), "wrong": wrong, "right": right,
            "disputed": counts.get("disputed", 0), "unreviewed": counts.get(None, 0),
            "rate": round(wrong / n, 4) if n else None, "ci_low": low, "ci_high": high}


def plan_view(batches: Sequence[Mapping[str, Any]], distinct_sampled: int) -> dict[str, Any]:
    """The plan of five steps: per step, its target, what was drawn and reviewed, and the next step."""
    live = [b for b in batches if not b.get("withdrawn_at")]
    steps = []
    for step, strategy, target in HW4_PLAN:
        mine = [b for b in live if b["plan_step"] == step]
        steps.append({"step": step, "strategy": strategy, "target": target,
                      "selected": sum(int(b["selected"]) for b in mine),
                      "reviewed": sum(int(b.get("reviewed") or 0) for b in mine),
                      "batch_ids": [str(b["id"]) for b in mine]})
    nxt = next((s["step"] for s in steps if s["selected"] < s["target"]), "extra")
    return {"steps": steps, "next_step": nxt, "distinct_sampled": int(distinct_sampled), "target_total": PLAN_TARGET}


def _ts(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


def open_coding(heads: Sequence[Mapping[str, Any]], active_items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Reviewed, wrong, right, missing verdicts (first 25 listed) and per-reviewer counts.

    heads = live non-tombstone verdict heads {turn_id, reviewer_id, reviewer_email, verdict, first_at};
    active_items = items of non-withdrawn batches {turn_id, reviews_required}."""
    by_turn: dict[str, list[str]] = {}
    for h in heads:
        by_turn.setdefault(h["turn_id"], []).append(h["verdict"])
    resolved = Counter(resolve(v) for v in by_turn.values())
    reviewers: dict[str, dict[str, Any]] = {}
    for h in heads:
        r = reviewers.setdefault(str(h["reviewer_id"]), {"email": h.get("reviewer_email"), "reviewed": 0,
                                                         "first_reviewed_at": h["first_at"]})
        r["reviewed"] += 1
        if h["first_at"] < r["first_reviewed_at"]:
            r["first_reviewed_at"] = h["first_at"]
    distinct_reviewers: dict[str, set[str]] = {}
    for h in heads:
        distinct_reviewers.setdefault(h["turn_id"], set()).add(str(h["reviewer_id"]))
    missing = [i["turn_id"] for i in active_items
               if len(distinct_reviewers.get(i["turn_id"], ())) < int(i["reviews_required"])]
    return {"reviewed": len(by_turn), "sampled": len(active_items), "wrong": resolved.get("wrong", 0),
            "right": resolved.get("right", 0), "disputed": resolved.get("disputed", 0),
            "missing": len(missing), "missing_turns": missing[:MISSING_LISTED],
            "missing_more": max(0, len(missing) - MISSING_LISTED),
            "reviewers": sorted(({"email": r["email"], "reviewed": r["reviewed"],
                                  "first_reviewed_at": _ts(r["first_reviewed_at"])} for r in reviewers.values()),
                                key=lambda r: (-r["reviewed"], r["email"] or ""))}


def coverage(pool: Sequence[Candidate], reviewed: set[str]) -> dict[str, Any]:
    """Per dimension value: pool and reviewed counts and shares; under-covered when the pool share is
    >= 0.05 and the reviewed share is below half of it."""
    total = len(pool)
    reviewed_total = sum(1 for c in pool if c.turn_id in reviewed)
    out: dict[str, Any] = {"pool": total, "reviewed": reviewed_total, "dimensions": {}}
    for dim in DIMENSIONS:
        rows = []
        for value, members in (dimension_groups(pool, dim) if pool else []):
            n_rev = sum(1 for c in members if c.turn_id in reviewed)
            pool_share = len(members) / total if total else None
            rev_share = n_rev / reviewed_total if reviewed_total else None
            rows.append({"value": value, "pool": len(members), "reviewed": n_rev,
                         "pool_share": round(pool_share, 4) if pool_share is not None else None,
                         "reviewed_share": round(rev_share, 4) if rev_share is not None else None,
                         "under_covered": bool(pool_share is not None and rev_share is not None
                                               and pool_share >= 0.05 and rev_share < pool_share / 2)})
        out["dimensions"][dim] = rows
    return out


def agreement(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """First-version blind verdicts {turn_id, reviewer_id, verdict}: the reviewer pair sharing the most turns,
    their percent agreement, and Cohen's kappa once they share >= 20 turns. None without a shared turn."""
    by_turn: dict[str, dict[str, str]] = {}
    for r in rows:
        by_turn.setdefault(r["turn_id"], {})[str(r["reviewer_id"])] = r["verdict"]
    pairs: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for tid in sorted(by_turn):
        votes = by_turn[tid]
        ids = sorted(votes)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pairs.setdefault((ids[i], ids[j]), []).append((votes[ids[i]], votes[ids[j]]))
    if not pairs:
        return None
    (a, b), shared = sorted(pairs.items(), key=lambda kv: (-len(kv[1]), kv[0]))[0]
    agree = sum(1 for x, y in shared if x == y)
    enough = len(shared) >= 20
    return {"reviewers": [a, b], "shared": len(shared), "percent": round(agree / len(shared), 4),
            "kappa": cohen_kappa([x for x, _ in shared], [y for _, y in shared]) if enough else None,
            "note": None if enough else "fewer than 20 shared answers"}


def discovery(heads: Sequence[Mapping[str, Any]], block: int) -> dict[str, Any]:
    """Reviews in order of their first version, in blocks of `block`. Failure modes do not exist yet at this
    point of the build, so each point carries the wrong verdicts found in its block and new_modes = None
    (the taxonomy phase fills new_modes)."""
    block = max(1, int(block))
    ordered = sorted(heads, key=lambda h: (h["first_at"], h["turn_id"], str(h["reviewer_id"])))
    points = []
    for start in range(0, len(ordered), block):
        chunk = ordered[start:start + block]
        points.append({"block": start // block + 1, "reviews_through": start + len(chunk),
                       "wrong": sum(1 for h in chunk if h["verdict"] == "wrong"),
                       "partial": len(chunk) < block, "new_modes": None, "cumulative_modes": None})
    by_batch: dict[str, dict[str, Any]] = {}
    for h in ordered:
        if h.get("batch_id"):
            b = by_batch.setdefault(str(h["batch_id"]), {"batch_id": str(h["batch_id"]), "reviews": 0, "wrong": 0,
                                                        "new_modes": None})
            b["reviews"] += 1
            b["wrong"] += 1 if h["verdict"] == "wrong" else 0
    return {"block": block, "reviews": len(ordered), "points": points, "by_batch": list(by_batch.values()),
            "modes_available": False}


def saturation(points: Sequence[Mapping[str, Any]], block: int, *,
               final_uniform: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The saturation hint. Needs new_modes on every point (the taxonomy phase); until then it says it is not
    available, never 'saturated'."""
    off = {"available": False, "hint": False, "kind": None, "reviews_without_new": None, "final_new_modes": None}
    if not points or any(p.get("new_modes") is None for p in points):
        return off
    if final_uniform and final_uniform.get("complete") and int(final_uniform.get("new_modes") or 0) >= 2:
        return {"available": True, "hint": True, "kind": "final_batch", "reviews_without_new": None,
                "final_new_modes": int(final_uniform["new_modes"])}
    full = [p for p in points if not p.get("partial")]
    reviews = points[-1]["reviews_through"]
    if reviews >= 3 * block and len(full) >= 2 and full[-1]["new_modes"] == 0 and full[-2]["new_modes"] == 0:
        return {"available": True, "hint": True, "kind": "quiet", "reviews_without_new": 2 * block,
                "final_new_modes": None}
    return {**off, "available": True}


def map_payload(pool: Sequence[Candidate], *, reviewed: set[str], mine: set[str]) -> dict[str, Any]:
    """Map view data: a two-axis projection of the standardized features and k-means clusters
    (k = min(8, n), seed 7). Fewer than 3 answers -> too_few, no points."""
    base = {"ok": True, "feature_names": [{"key": f, "label": FEATURE_LABELS[f]} for f in FEATURES],
            "feature_version": FEATURE_VERSION, "pool_size": len(pool)}
    if len(pool) < 3:
        return {**base, "too_few": True, "explained_variance": None, "nodes": [], "clusters": []}
    x = standardize(feature_matrix(pool))
    points, shares = pca2(x)
    labels, _ = kmeans(x, min(MAP_CLUSTERS, len(pool)), seed=DEFAULT_SEED)
    raw = raw_matrix(pool)
    nodes = []
    for c, (px, py), lab in zip(pool, points, labels):
        nodes.append({"turn_id": c.turn_id, "conversation_key": c.conversation_key, "x": px, "y": py,
                      "cluster": lab + 1, "batch_id": c.batch_id, "reviewed": c.turn_id in reviewed,
                      "reviewed_by_me": c.turn_id in mine, "channel": c.dims.get("channel"),
                      "route_target": c.dims.get("route_target"), "question": (c.question or "")[:160],
                      "started_at": _ts(c.started_at)})
    clusters = []
    for j in sorted(set(labels)):
        idx = [i for i, lab in enumerate(labels) if lab == j]
        clusters.append({"id": j + 1, "size": len(idx), "reviewed": sum(1 for i in idx if pool[i].turn_id in reviewed),
                         "centroid": {f: round(sum(raw[i][fi] for i in idx) / len(idx), 2)
                                      for fi, f in enumerate(FEATURES)}})
    return {**base, "too_few": False, "explained_variance": list(shares), "nodes": nodes, "clusters": clusters}


# ── database: pool, batches, queue, manifest, map, progress ───────────────────────────────

TURNS = conversations.TURNS
SPANS = conversations.SPANS


@asynccontextmanager
async def _read_main() -> AsyncIterator[Any]:
    """A read-only, 10-second-bounded transaction on the read-only main engine (the chat's tables)."""
    async with db.main_engine().begin() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        await conn.execute(text(TIMEOUT))
        yield conn


@asynccontextmanager
async def _read_lab() -> AsyncIterator[Any]:
    """A read-only, 10-second-bounded transaction on Trace Lab's own database."""
    async with db.lab_engine().begin() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        await conn.execute(text(TIMEOUT))
        yield conn


def _uuid_or_none(value: str | None) -> str | None:
    try:
        return str(uuid.UUID(str(value))) if value else None
    except ValueError:
        return None


def _need_company(org: str | None) -> str:
    if not org:
        raise Invalid("name_a_company", "Choose a company to view as first.")
    return org


def check_size(req: Mapping[str, Any]) -> None:
    """The batch size and the reviews each answer needs, which the table's CHECKs also hold: 1-200 and 1-3."""
    k, rr = req.get("k"), req.get("reviews_required", 1)
    if req.get("strategy") not in RECORDED_STRATEGIES and (
            isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= BATCH_MAX):
        raise Invalid("bad_size", f"Choose between 1 and {BATCH_MAX} answers.")
    if isinstance(rr, bool) or not isinstance(rr, int) or not 1 <= rr <= 3:
        raise Invalid("bad_size", "Ask for between 1 and 3 reviews of each answer.")


def _view_as_clause(accounts: Sequence[tuple[str, str]]) -> tuple[str, dict[str, Any]]:
    """Leave out a superadmin's answers given while acting for a company that is not their own (R20)."""
    if not accounts:
        return "TRUE", {}
    return ("NOT EXISTS (SELECT 1 FROM unnest(CAST(:tl_va_ids AS uuid[]), CAST(:tl_va_orgs AS uuid[])) AS va(uid, oid) "
            "WHERE va.uid = tt.user_id AND va.oid IS DISTINCT FROM tt.organization_id)",
            {"tl_va_ids": [a[0] for a in accounts], "tl_va_orgs": [a[1] for a in accounts]})


async def _accounts(conn: Any, sql: str, params: Mapping[str, Any]) -> list[Any]:
    """Rows of the sign-in service's account table, which Trace Lab does not own: a hint, so a database without
    it (or without a column) reads as no rows, inside a savepoint that keeps the read transaction usable."""
    try:
        async with conn.begin_nested():
            return list((await conn.execute(text(sql), dict(params))).all())
    except Exception as exc:  # noqa: BLE001 - optional evidence
        log.warning("trace_lab.sampling.accounts_unavailable: %s", str(exc)[:200])
        return []


async def _superadmins(conn: Any) -> list[tuple[str, str]]:
    rows = await _accounts(conn, f"SELECT id::text, organization_id::text FROM {USERS} WHERE platform_role = 'superadmin'", {})
    return [(r[0], r[1]) for r in rows]


async def _asker_roles(conn: Any, user_ids: Sequence[str]) -> dict[str, str]:
    ids = sorted({u for u in user_ids if u})
    out: dict[str, str] = {}
    for i in range(0, len(ids), 5000):
        for r in await _accounts(conn, f"SELECT id::text, platform_role FROM {USERS} WHERE id = ANY(CAST(:tl_ids AS uuid[]))",
                                 {"tl_ids": ids[i:i + 5000]}):
            out[r[0]] = r[1]
    return out


def _window_cte(principal: Any, org: str, *, since: datetime, until: datetime, channel: str,
                accounts: Sequence[tuple[str, str]], include_deleted: bool, with_text: bool) -> tuple[str, dict[str, Any]]:
    """The window's eligible turns (spec 3.1) from the chat's tables: one company's (conversations.scope_sql), never
    a NULL company (R27), never a golden run, the requested inferred channel, no superadmin view-as unless the
    caller dropped `accounts` (R20), no deleted chat unless asked (R21). turn_position counts the whole eligible
    window; the sampled turns, known only to the lab database, are taken out afterwards."""
    where, p = conversations.scope_sql(principal, org)
    va, vp = _view_as_clause(accounts)
    text_cols = ", tt.question, tt.answer" if with_text else ", left(tt.question, 300) AS question, NULL::text AS answer"
    ch = provenance.INFERRED_CHANNEL_SQL
    sql = f"""
        WITH w AS (
          SELECT tt.turn_id, tt.session_id, tt.organization_id::text AS organization_id, tt.user_id::text AS user_id,
                 tt.started_at, tt.llm_calls, tt.tool_calls,
                 CASE WHEN jsonb_typeof(tt.tools) = 'array' THEN jsonb_array_length(tt.tools) ELSE 0 END AS distinct_tools,
                 coalesce(tt.input_tokens, 0) + coalesce(tt.output_tokens, 0) AS tokens,
                 tt.latency_ms, length(coalesce(tt.answer, '')) AS answer_chars, tt.route, {ch} AS channel,
                 row_number() OVER (PARTITION BY tt.session_id ORDER BY tt.started_at, tt.turn_id) AS turn_position
                 {text_cols}
            FROM {TURNS} tt
            {conversations.THREAD_JOIN}
           WHERE {where} AND tt.organization_id IS NOT NULL
             AND tt.started_at >= :tl_since AND tt.started_at < :tl_until
             AND {ch} = :tl_channel AND {ch} <> 'golden'
             AND {va}
             AND (CAST(:tl_deleted AS boolean) OR th.deleted_at IS NULL)
        )"""
    return sql, {**p, **vp, "tl_since": since, "tl_until": until, "tl_channel": channel,
                 "tl_deleted": bool(include_deleted)}


async def _db_counts(conn: Any, turn_ids: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in range(0, len(turn_ids), 5000):
        chunk = turn_ids[i:i + 5000]
        for r in (await conn.execute(text(
                f"SELECT turn_id, count(*) FILTER (WHERE kind = 'db') AS n FROM {SPANS} "
                "WHERE turn_id = ANY(CAST(:tl_ids AS text[])) GROUP BY turn_id"), {"tl_ids": chunk})).all():
            out[r[0]] = int(r[1])
    return out


def _candidate(r: Mapping[str, Any], db_counts: Mapping[str, int], roles: Mapping[str, str],
               batches: Mapping[str, str], with_text: bool) -> Candidate:
    sid = r["session_id"]
    single = not sid or not str(sid).strip() or normalize.session_kind(sid, thread_exists=False) == "synthetic"
    question = normalize.redact_text(r["question"] or "")
    return Candidate(
        turn_id=r["turn_id"], session_id=sid, started_at=r["started_at"],
        features={"turn_position": 1 if single else int(r["turn_position"]), "llm_calls": r["llm_calls"],
                  "tool_calls": r["tool_calls"], "distinct_tools": r["distinct_tools"],
                  "db_queries": db_counts.get(r["turn_id"], 0), "tokens": r["tokens"], "latency_ms": r["latency_ms"],
                  "answer_chars": r["answer_chars"]},
        dims={"channel": r["channel"], "route_target": route_target(r["route"]),
              "asker_role": roles.get(r["user_id"]) if r["user_id"] else None, "page": None},
        text=(question + "\n" + normalize.redact_text(r["answer"] or "")) if with_text else None,
        question=question, organization_id=r["organization_id"], batch_id=batches.get(r["turn_id"]))


async def _sampled(org: str) -> dict[str, str]:
    """{turn_id: batch_id} of every answer already in one of the company's batches (lab database)."""
    cw, cp = annotations.company_clause(org, "si")
    async with _read_lab() as conn:
        rows = (await conn.execute(text(
            f"SELECT si.turn_id, si.batch_id::text FROM tracelab.sample_items si WHERE {cw}"), cp)).all()
    return {r[0]: r[1] for r in rows}


async def _load_pool(principal: Any, org: str, *, since: datetime, until: datetime, channel: str,
                     exclude_sampled: bool, with_text: bool, limit: int, include_view_as: bool = False,
                     include_deleted: bool = False) -> tuple[list[Candidate], bool, int]:
    """(candidates newest first, truncated, sampled turns left out of the window). Two reads, one per database:
    the sampled turn ids from the lab, then the window from the chat with those ids handed over."""
    org = _need_company(org)
    sampled = await _sampled(org)
    ids = sorted(sampled)
    async with _read_main() as conn:
        accounts = [] if include_view_as else await _superadmins(conn)
        cte, p = _window_cte(principal, org, since=since, until=until, channel=channel, accounts=accounts,
                             include_deleted=include_deleted, with_text=with_text)
        p = {**p, "tl_sampled": ids}
        left_out = "WHERE NOT (turn_id = ANY(CAST(:tl_sampled AS text[])))" if exclude_sampled else ""
        rows = (await conn.execute(text(
            cte + f" SELECT * FROM w {left_out} ORDER BY started_at DESC, turn_id DESC LIMIT :tl_lim"),
            {**p, "tl_lim": limit + 1})).mappings().all()
        excluded = 0
        if exclude_sampled and ids:
            excluded = int((await conn.execute(text(
                cte + " SELECT count(*) FROM w WHERE turn_id = ANY(CAST(:tl_sampled AS text[]))"), p)).scalar_one())
        truncated = len(rows) > limit
        rows = [dict(r) for r in rows[:limit]]
        counts = await _db_counts(conn, [r["turn_id"] for r in rows])
        roles = await _asker_roles(conn, [r["user_id"] for r in rows])
    return [_candidate(r, counts, roles, sampled, with_text) for r in rows], truncated, excluded


async def pool(principal: Any, org: str, *, since: datetime, until: datetime, channel: str = "chat",
               exclude_sampled: bool = True, with_text: bool = False, limit: int = POOL_MAX,
               include_view_as: bool = False, include_deleted: bool = False) -> tuple[list[Candidate], bool]:
    cands, truncated, _ = await _load_pool(principal, org, since=since, until=until, channel=channel,
                                           exclude_sampled=exclude_sampled, with_text=with_text, limit=limit,
                                           include_view_as=include_view_as, include_deleted=include_deleted)
    return cands, truncated


async def _wrong_seeds(org: str) -> list[Candidate]:
    """The enrich seeds: the company's answers with a live wrong verdict. The verdicts are read from the lab
    database, the answers from the chat's tables (a golden answer lives only in the lab and is not a seed)."""
    cw, cp = annotations.company_clause(org, "a")
    async with _read_lab() as conn:
        ids = [r[0] for r in (await conn.execute(text(
            f"SELECT DISTINCT a.turn_id FROM tracelab.annotations a WHERE a.kind = 'verdict' "
            f"AND a.superseded_by IS NULL AND NOT a.tombstone AND a.verdict = 'wrong' AND {cw} "
            f"ORDER BY a.turn_id LIMIT :tl_lim"), {**cp, "tl_lim": SEEDS_MAX})).all()]
    return await _seed_candidates(org, ids)


async def _seed_candidates(org: str, ids: list[str]) -> list[Candidate]:
    """The company's own answers among `ids` as enrich seeds (question and answer, redacted), from the chat's tables."""
    if not ids:
        return []
    async with _read_main() as conn:
        rows = (await conn.execute(text(
            f"SELECT tt.turn_id, tt.session_id, tt.started_at, tt.question, tt.answer FROM {TURNS} tt "
            f"WHERE tt.turn_id = ANY(CAST(:tl_ids AS text[])) AND tt.organization_id = CAST(:tl_org AS uuid) "
            f"ORDER BY tt.turn_id"), {"tl_ids": ids, "tl_org": org})).mappings().all()
    return [Candidate(turn_id=r["turn_id"], session_id=r["session_id"], started_at=r["started_at"], features={},
                      dims={}, text=normalize.redact_text(r["question"] or "") + "\n" + normalize.redact_text(r["answer"] or ""),
                      question=normalize.redact_text(r["question"] or "")) for r in rows]


def _pick_out(p: Pick) -> dict[str, Any]:
    return {"turn_id": p.turn_id, "reason": p.reason, "cluster": p.cluster, "dimension_value": p.dimension_value,
            "score": p.score}


async def _drawn(principal: Any, org: str, req: Mapping[str, Any]) -> tuple[list[Candidate], list[Pick], bool, int]:
    limit = ENRICH_POOL_MAX if req["strategy"] == "enrich" else POOL_MAX
    cands, truncated, excluded = await _load_pool(
        principal, org, since=req["since"], until=req["until"], channel=req.get("channel") or "chat",
        exclude_sampled=True, with_text=req["strategy"] == "enrich", limit=limit,
        include_view_as=bool(req.get("include_view_as")), include_deleted=bool(req.get("include_deleted")))
    seeds: list[Candidate] = []
    seed_label, ordered = "a wrong answer", None
    if req["strategy"] == "enrich":
        async with _read_lab() as conn:
            plan = await enrich_inputs(conn, org, mode_id=req.get("mode_id"), source=req.get("source") or "bow",
                                       proposal_run_id=req.get("proposal_run_id"))
        if "ordered" in plan:          # a depth scan's decided suggestions, in decision order: nothing is scored
            ordered = plan["ordered"]
        elif "seed_turn_ids" in plan:  # the answers that show a failure mode
            seeds, seed_label = await _seed_candidates(org, plan["seed_turn_ids"]), "an answer with this failure mode"
        else:
            seeds = await _wrong_seeds(org)
    picks = await asyncio.to_thread(draw, cands, req, seeds=seeds, seed_label=seed_label, ordered=ordered)
    return cands, picks, truncated, excluded


async def preview(principal: Any, org: str, req: dict) -> dict:
    """What a batch would draw. Writes nothing (and copies no evidence). Only a drawn batch has a preview: a
    monitor_flagged batch records answers that are named, so there is nothing to preview."""
    check_request(req)
    if req["strategy"] in RECORDED_STRATEGIES:
        raise Invalid("bad_strategy", "A flagged batch is recorded, not drawn, so it has no preview.")
    check_size(req)
    cands, picks, truncated, excluded = await _drawn(principal, org, req)
    return {"ok": True, "pool_size": len(cands), "excluded_sampled": excluded, "truncated": truncated,
            "picks": [_pick_out(p) for p in picks]}


_BATCH_INSERT = text("""
    INSERT INTO tracelab.sample_batches
        (id, organization_id, plan_step, strategy, params, seed, window_since, window_until, channel, requested,
         selected, pool_size, excluded_sampled, counts_for_prevalence, reviews_required, feature_version,
         created_by, created_by_email)
    VALUES (CAST(:id AS uuid), CAST(:org AS uuid), :plan_step, :strategy, CAST(:params AS jsonb), :seed, :since,
            :until, :channel, :requested, :selected, :pool_size, :excluded, :cfp, :rr, :fv, CAST(:me AS uuid), :email)
    RETURNING *""")
_ITEM_INSERT = text("""
    INSERT INTO tracelab.sample_items
        (id, batch_id, organization_id, turn_id, session_id, position, reason, cluster, dimension_value, score, features)
    VALUES (CAST(:id AS uuid), CAST(:batch AS uuid), CAST(:org AS uuid), :tid, :sid, :pos, :reason, :cluster, :dim,
            :score, CAST(:features AS jsonb))""")


def _took_a_turn_first(exc: IntegrityError) -> bool:
    return "ux_tl_items_turn" in str(exc) or annotations._constraint(exc) == "ux_tl_items_turn"


async def _write_batch(batch_row: dict[str, Any], item_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The batch and its items in ONE lab transaction. Another batch having taken one of the turns first is
    409 pool_changed, and nothing of this batch is kept."""
    try:
        async with db.lab_engine().begin() as conn:
            await conn.execute(text(TIMEOUT))
            batch = dict((await conn.execute(_BATCH_INSERT, batch_row)).mappings().one())
            await conn.execute(_ITEM_INSERT, item_rows)
    except IntegrityError as exc:
        if _took_a_turn_first(exc):
            raise Conflict("pool_changed") from exc
        raise
    return batch


def _batch_out(row: dict[str, Any]) -> dict[str, Any]:
    r = trace._iso(dict(row))
    for k in ("n_items", "n_reviewed", "n_mine", "n_missing", "n_wrong", "n_right", "n_disputed"):
        r[k[2:]] = int(r.pop(k, 0) or 0)
    if isinstance(r.get("params"), str):
        r["params"] = json.loads(r["params"])
    return r


async def create_batch(principal: Any, org: str, req: dict) -> dict:
    """Draw and record a batch and its items. Invalid (incl. pool_empty), or Conflict(pool_changed) when another
    batch took one of these turns first. Every drawn answer is copied into the evidence tables before anything is
    written (a failure there stops the batch). A monitor_flagged request (monitoring) records the answers it names
    instead of drawing them (_record_flagged)."""
    check_request(req)
    check_size(req)
    if req["strategy"] in RECORDED_STRATEGIES:
        return await _record_flagged(principal, org, req)
    cands, picks, truncated, excluded = await _drawn(principal, org, req)
    if not picks:
        raise Invalid("pool_empty")
    by_id = {c.turn_id: c for c in cands}
    await evidence.snapshot([p.turn_id for p in picks], organization_id=org, reason=SNAPSHOT_REASON)
    batch_id = str(uuid.uuid4())
    params = {"k": int(req["k"]), "dimension": req.get("dimension"), "source": req.get("source") or "bow",
              "mode_id": req.get("mode_id") or None, "proposal_run_id": req.get("proposal_run_id") or None,
              "random_share": random_share_for(req) if req["strategy"] in ("diversity", "enrich") else None,
              "include_view_as": bool(req.get("include_view_as")),
              "include_deleted": bool(req.get("include_deleted")), "pool_truncated": truncated}
    batch = await _write_batch(
        {"id": batch_id, "org": org, "plan_step": req["plan_step"], "strategy": req["strategy"],
         "params": json.dumps(params), "seed": int(req.get("seed", DEFAULT_SEED)), "since": req["since"],
         "until": req["until"], "channel": req.get("channel") or "chat", "requested": int(req["k"]),
         "selected": len(picks), "pool_size": len(cands), "excluded": excluded,
         "cfp": req["strategy"] == "uniform", "rr": int(req.get("reviews_required") or 1),
         "fv": FEATURE_VERSION, "me": str(principal.user_id), "email": principal.email or None},
        [{"id": str(uuid.uuid4()), "batch": batch_id, "org": org, "tid": p.turn_id,
          "sid": by_id[p.turn_id].session_id, "pos": i + 1, "reason": p.reason[:300], "cluster": p.cluster,
          "dim": p.dimension_value, "score": p.score, "features": json.dumps(by_id[p.turn_id].features)}
         for i, p in enumerate(picks)])
    out = _batch_out(batch)
    out["items"] = len(picks)
    return {"ok": True, "batch": out, "items": [
        {**_pick_out(p), "position": i + 1, "conversation_key": by_id[p.turn_id].conversation_key}
        for i, p in enumerate(picks)]}


async def _record_flagged(principal: Any, org: str, req: Mapping[str, Any]) -> dict:
    """Record the answers a monitor batch flagged as one review batch; nothing is drawn. Keeps the named turns
    that are this company's (never another company's or a NULL company's, R27; never a golden run) and not
    already in one of its batches (excluded_sampled); none left -> Invalid(pool_empty); a concurrent batch took
    one -> Conflict(pool_changed). The company's own turns are read from the chat, the sampled ones from the lab."""
    named: dict[str, Mapping[str, Any]] = {}
    for it in req["items"]:
        named.setdefault(it["turn_id"].strip(), it)
    ids = list(named)
    async with _read_main() as conn:
        found = {r["turn_id"]: dict(r) for r in (await conn.execute(text(
            f"SELECT tt.turn_id, tt.session_id, {provenance.INFERRED_CHANNEL_SQL} AS channel FROM {TURNS} tt "
            f"WHERE tt.turn_id = ANY(CAST(:tl_ids AS text[])) AND tt.organization_id = CAST(:tl_org AS uuid)"),
            {"tl_ids": ids, "tl_org": org})).mappings().all()}
    taken = set(await _sampled(org)) & set(ids)
    eligible = [t for t in ids if t in found and found[t]["channel"] != "golden"]
    kept = [t for t in eligible if t not in taken]
    if not kept:
        raise Invalid("pool_empty")
    picks = [Pick(t, str(named[t].get("reason") or "flagged by monitoring")[:300]) for t in kept]
    await evidence.snapshot(kept, organization_id=org, reason=SNAPSHOT_REASON)
    batch_id = str(uuid.uuid4())
    batch = await _write_batch(
        {"id": batch_id, "org": org, "plan_step": "extra", "strategy": req["strategy"],
         "params": json.dumps(req.get("params") or {}, default=str),
         "seed": DEFAULT_SEED if req.get("seed") is None else int(req["seed"]),
         "since": req["since"], "until": req["until"], "channel": req.get("channel") or "chat",
         "requested": len(req["items"]), "selected": len(picks), "pool_size": len(eligible),
         "excluded": len(eligible) - len(picks), "cfp": False, "rr": 1, "fv": None,
         "me": str(principal.user_id), "email": principal.email or None},
        [{"id": str(uuid.uuid4()), "batch": batch_id, "org": org, "tid": p.turn_id,
          "sid": found[p.turn_id]["session_id"], "pos": i + 1, "reason": p.reason, "cluster": None,
          "dim": None, "score": None, "features": None} for i, p in enumerate(picks)])
    out = _batch_out(batch)
    out["items"] = len(picks)
    return {"ok": True, "batch": out, "items": [
        {**_pick_out(p), "position": i + 1,
         "conversation_key": normalize.group_key(found[p.turn_id]["session_id"], p.turn_id)}
        for i, p in enumerate(picks)]}


async def _batch_rows(conn: Any, principal: Any, org: str | None, *, include_withdrawn: bool) -> list[dict[str, Any]]:
    """Batches with the per-batch counts of spec section 4. `conn` is a LAB database connection."""
    bw, bp = annotations.company_clause(org, "b")
    aw, ap = annotations.company_clause(org, "a")
    rows = (await conn.execute(text(f"""
        WITH v AS (
          SELECT a.turn_id, a.organization_id, count(DISTINCT a.reviewer_id) AS reviewers,
                 count(*) FILTER (WHERE a.verdict = 'wrong') AS wrong_n,
                 count(*) FILTER (WHERE a.verdict = 'right') AS right_n,
                 bool_or(a.reviewer_id = CAST(:tl_me AS uuid)) AS mine
            FROM tracelab.annotations a
           WHERE a.kind = 'verdict' AND a.superseded_by IS NULL AND NOT a.tombstone AND {aw}
           GROUP BY a.turn_id, a.organization_id)
        SELECT b.*, count(si.id) AS n_items,
               count(si.id) FILTER (WHERE coalesce(v.reviewers, 0) >= 1) AS n_reviewed,
               count(si.id) FILTER (WHERE coalesce(v.mine, false)) AS n_mine,
               count(si.id) FILTER (WHERE coalesce(v.reviewers, 0) < b.reviews_required) AS n_missing,
               count(si.id) FILTER (WHERE v.wrong_n > 0 AND v.right_n = 0) AS n_wrong,
               count(si.id) FILTER (WHERE v.right_n > 0 AND v.wrong_n = 0) AS n_right,
               count(si.id) FILTER (WHERE v.right_n > 0 AND v.wrong_n > 0) AS n_disputed
          FROM tracelab.sample_batches b
          LEFT JOIN tracelab.sample_items si ON si.batch_id = b.id
          LEFT JOIN v ON v.turn_id = si.turn_id AND v.organization_id = si.organization_id
         WHERE {bw} AND (CAST(:tl_incw AS boolean) OR b.withdrawn_at IS NULL)
         GROUP BY b.id ORDER BY b.created_at DESC"""),
        {**bp, **ap, "tl_me": str(principal.user_id), "tl_incw": include_withdrawn})).mappings().all()
    return [_batch_out(dict(r)) for r in rows]


async def list_batches(principal: Any, org: str | None, *, include_withdrawn: bool = False) -> list[dict]:
    async with _read_lab() as conn:
        return await _batch_rows(conn, principal, org, include_withdrawn=include_withdrawn)


async def batch_detail(principal: Any, org: str | None, batch_id: str) -> dict | None:
    bid = _uuid_or_none(batch_id)
    if bid is None:
        return None
    async with _read_lab() as conn:
        batch = next((b for b in await _batch_rows(conn, principal, org, include_withdrawn=True) if b["id"] == bid), None)
        if batch is None:
            return None
        items = (await conn.execute(text("""
            SELECT si.turn_id, si.session_id, si.position, si.reason, si.cluster, si.dimension_value, si.score,
                   count(DISTINCT a.reviewer_id) AS reviewers,
                   coalesce(bool_or(a.reviewer_id = CAST(:tl_me AS uuid)), false) AS mine,
                   count(DISTINCT a.reviewer_id) FILTER (WHERE a.reviewer_id <> CAST(:tl_me AS uuid)) AS others_count
              FROM tracelab.sample_items si
              LEFT JOIN tracelab.annotations a
                     ON a.turn_id = si.turn_id AND a.organization_id = si.organization_id AND a.kind = 'verdict'
                    AND a.superseded_by IS NULL AND NOT a.tombstone
             WHERE si.batch_id = CAST(:tl_bid AS uuid)
             GROUP BY si.id ORDER BY si.position"""), {"tl_me": str(principal.user_id), "tl_bid": bid})).mappings().all()
    return {"batch": batch, "items": [
        {"turn_id": i["turn_id"], "conversation_key": normalize.group_key(i["session_id"], i["turn_id"]),
         "position": i["position"], "reason": i["reason"], "cluster": i["cluster"],
         "dimension_value": i["dimension_value"], "score": i["score"], "reviewed": int(i["reviewers"]) > 0,
         "reviewed_by_me": bool(i["mine"]), "others_count": int(i["others_count"])} for i in items]}


async def withdraw(principal: Any, org: str, batch_id: str, *, reason: str) -> dict:
    """Set the three withdrawn_* columns once (the only update sample_batches ever gets)."""
    bid = _uuid_or_none(batch_id)
    if bid is None:
        raise NotFound("batch_not_found")
    async with db.lab_engine().begin() as conn:
        await conn.execute(text(TIMEOUT))
        row = (await conn.execute(text(
            "UPDATE tracelab.sample_batches SET withdrawn_at = now(), withdrawn_by = CAST(:tl_me AS uuid), "
            "withdrawn_reason = :tl_reason WHERE id = CAST(:tl_bid AS uuid) AND organization_id = CAST(:tl_org AS uuid) "
            "AND withdrawn_at IS NULL RETURNING *"),
            {"tl_me": str(principal.user_id), "tl_reason": reason[:500], "tl_bid": bid, "tl_org": org})).mappings().first()
        if row is None:
            exists = (await conn.execute(text(
                "SELECT EXISTS (SELECT 1 FROM tracelab.sample_batches WHERE id = CAST(:tl_bid AS uuid) "
                "AND organization_id = CAST(:tl_org AS uuid))"), {"tl_bid": bid, "tl_org": org})).scalar_one()
            raise Conflict("already_withdrawn") if exists else NotFound("batch_not_found")
    return _batch_out(dict(row))


async def queue(principal: Any, org: str, *, batch_id: str | None = None, limit: int = 20) -> dict:
    """Items I have not judged that still need reviews, in a stable per-reviewer order."""
    if batch_id and _uuid_or_none(batch_id) is None:
        raise NotFound("batch_not_found")
    me = str(principal.user_id)
    live = ("SELECT {sel} FROM tracelab.annotations a WHERE a.kind = 'verdict' AND a.turn_id = si.turn_id "
            "AND a.organization_id = si.organization_id AND a.superseded_by IS NULL AND NOT a.tombstone")
    async with _read_lab() as conn:
        rows = (await conn.execute(text(f"""
            SELECT si.turn_id, si.session_id, si.batch_id::text AS batch_id, b.plan_step, si.reason,
                   ({live.format(sel='count(DISTINCT a.reviewer_id)')} AND a.reviewer_id <> CAST(:tl_me AS uuid)) AS others_count,
                   count(*) OVER () AS remaining
              FROM tracelab.sample_items si
              JOIN tracelab.sample_batches b ON b.id = si.batch_id
             WHERE si.organization_id = CAST(:tl_org AS uuid) AND b.withdrawn_at IS NULL
               AND (CAST(:tl_bid AS uuid) IS NULL OR si.batch_id = CAST(:tl_bid AS uuid))
               AND NOT EXISTS ({live.format(sel='1')} AND a.reviewer_id = CAST(:tl_me AS uuid))
               AND ({live.format(sel='count(DISTINCT a.reviewer_id)')}) < b.reviews_required
             ORDER BY md5(si.turn_id || CAST(:tl_me AS text)), si.turn_id
             LIMIT :tl_lim"""), {"tl_org": org, "tl_bid": _uuid_or_none(batch_id), "tl_me": me,
                                  "tl_lim": max(1, min(int(limit), 50))})).mappings().all()
    return {"ok": True, "remaining": int(rows[0]["remaining"]) if rows else 0, "items": [
        {"turn_id": r["turn_id"], "conversation_key": normalize.group_key(r["session_id"], r["turn_id"]),
         "batch_id": r["batch_id"], "plan_step": r["plan_step"], "reason": r["reason"],
         "others_count": int(r["others_count"] or 0)} for r in rows]}


async def _distinct_sampled(conn: Any, org: str) -> int:
    return int((await conn.execute(text(
        "SELECT count(*) FROM tracelab.sample_items si JOIN tracelab.sample_batches b ON b.id = si.batch_id "
        "WHERE si.organization_id = CAST(:tl_org AS uuid) AND (b.withdrawn_at IS NULL OR EXISTS (SELECT 1 FROM "
        "tracelab.annotations a WHERE a.kind = 'verdict' AND a.turn_id = si.turn_id AND "
        "a.organization_id = si.organization_id AND a.superseded_by IS NULL AND NOT a.tombstone))"),
        {"tl_org": org})).scalar_one())


async def plan_status(principal: Any, org: str, *, since: datetime | None = None, until: datetime | None = None) -> dict:
    async with _read_lab() as conn:
        batches = await _batch_rows(conn, principal, org, include_withdrawn=False)
        plan = plan_view(batches, await _distinct_sampled(conn, org))
    if since and until:
        check_window(since, until)
        cands, _, _ = await _load_pool(principal, org, since=since, until=until, channel="chat",
                                       exclude_sampled=True, with_text=False, limit=POOL_MAX)
        plan["pool_size"] = len(cands)
    return {"ok": True, **plan}


async def manifest(principal: Any, org: str | None) -> dict:
    """The record of every batch drawn: seed, parameters, window and each item's reason (spec 3.5)."""
    bw, bp = annotations.company_clause(org, "b")
    async with _read_lab() as conn:
        batches = await _batch_rows(conn, principal, org, include_withdrawn=True)
        items = (await conn.execute(text(
            f"SELECT si.batch_id::text AS batch_id, si.turn_id, si.position, si.reason, si.cluster, si.dimension_value, "
            f"si.score FROM tracelab.sample_items si JOIN tracelab.sample_batches b ON b.id = si.batch_id "
            f"WHERE {bw} ORDER BY si.batch_id, si.position"), bp)).mappings().all()
        distinct = await _distinct_sampled(conn, org) if org else sum(int(b["items"]) for b in batches)
    by_batch: dict[str, list[dict[str, Any]]] = {}
    for i in items:
        by_batch.setdefault(i["batch_id"], []).append({k: i[k] for k in ("turn_id", "position", "reason", "cluster",
                                                                        "dimension_value", "score")})
    return {"schema_version": 1, "organization_id": org, "generated_at": datetime.now(timezone.utc).isoformat(),
            "plan": plan_view([b for b in batches if not b.get("withdrawn_at")], distinct) if org else None,
            "batches": [{"id": b["id"], "organization_id": b["organization_id"], "plan_step": b["plan_step"],
                         "strategy": b["strategy"], "seed": b["seed"], "params": b["params"],
                         "window": {"since": b["window_since"], "until": b["window_until"]}, "channel": b["channel"],
                         "requested": b["requested"], "selected": b["selected"], "pool_size": b["pool_size"],
                         "excluded_sampled": b["excluded_sampled"], "counts_for_prevalence": b["counts_for_prevalence"],
                         "reviews_required": b["reviews_required"], "created_by_email": b["created_by_email"],
                         "created_at": b["created_at"], "withdrawn_at": b["withdrawn_at"],
                         "items": by_batch.get(b["id"], [])} for b in batches]}


async def _mine(conn: Any, org: str | None, me: str) -> set[str]:
    cw, cp = annotations.company_clause(org, "a")
    return {r[0] for r in (await conn.execute(text(
        f"SELECT a.turn_id FROM tracelab.annotations a WHERE a.kind = 'verdict' AND a.superseded_by IS NULL "
        f"AND NOT a.tombstone AND a.reviewer_id = CAST(:tl_me AS uuid) AND {cw}"), {**cp, "tl_me": me})).all()}


async def map_view(principal: Any, org: str, *, since: datetime, until: datetime, channel: str = "chat",
                   limit: int = MAP_MAX) -> dict:
    """The Map of one company's answers in a window: sampled answers marked with their batch, reviewed ones flagged."""
    _need_company(org)
    check_window(since, until)
    cands, truncated, _ = await _load_pool(principal, org, since=since, until=until, channel=channel,
                                           exclude_sampled=False, with_text=False, limit=max(1, min(limit, MAP_MAX)))
    async with _read_lab() as conn:
        reviewed = await annotations.reviewed_turns(conn, org)
        mine = await _mine(conn, org, str(principal.user_id))
    payload = await asyncio.to_thread(map_payload, cands, reviewed=reviewed, mine=mine)
    return {**payload, "truncated": truncated}


#: Golden answers are not live traffic. They live in the lab database, so "not golden" needs no join to the chat.
_NOT_GOLDEN = "NOT EXISTS (SELECT 1 FROM tracelab.golden_results gr WHERE gr.turn_id = a.turn_id)"


async def progress(principal: Any, org: str | None, *, since: datetime | None, until: datetime | None,
                   block: int = 10, filter_org: str | None = None) -> dict:
    """Section 4's metrics. A company's reviewers see their company; the platform owner (org None) sees totals
    and a per-company breakdown, or one company with filter_org. Golden answers never count."""
    if org is None and filter_org:
        org = _uuid_or_none(filter_org)
        if org is None:
            raise Invalid("bad_company", "That company id is not valid.")
    now = datetime.now(timezone.utc)
    until = until or now + timedelta(days=1)
    since = since or max(now - timedelta(days=30), now - timedelta(days=retention_days()) + timedelta(minutes=1))
    check_window(since, until)
    cw, cp = annotations.company_clause(org, "a")
    iw, ip = annotations.company_clause(org, "si")
    async with _read_lab() as conn:
        heads = [dict(r) for r in (await conn.execute(text(f"""
            SELECT a.turn_id, a.organization_id::text AS organization_id, a.reviewer_id::text AS reviewer_id,
                   a.reviewer_email, a.verdict, f.created_at AS first_at, si.batch_id::text AS batch_id
              FROM tracelab.annotations a
              JOIN tracelab.annotations f ON f.id = a.lineage_id
              LEFT JOIN tracelab.sample_items si ON si.organization_id = a.organization_id AND si.turn_id = a.turn_id
             WHERE a.kind = 'verdict' AND a.superseded_by IS NULL AND NOT a.tombstone AND {cw} AND {_NOT_GOLDEN}
             ORDER BY f.created_at, a.turn_id"""), cp)).mappings().all()]
        items = [dict(r) for r in (await conn.execute(text(f"""
            SELECT si.turn_id, si.organization_id::text AS organization_id, b.strategy, b.reviews_required,
                   b.withdrawn_at IS NOT NULL AS withdrawn
              FROM tracelab.sample_items si JOIN tracelab.sample_batches b ON b.id = si.batch_id
             WHERE {iw}"""), ip)).mappings().all()]
        firsts = [dict(r) for r in (await conn.execute(text(f"""
            SELECT a.turn_id, a.reviewer_id::text AS reviewer_id, a.verdict FROM tracelab.annotations a
             WHERE a.kind = 'verdict' AND a.version = 1 AND a.blind AND NOT a.tombstone AND {cw} AND {_NOT_GOLDEN}"""),
            cp)).mappings().all()]
        batches = await _batch_rows(conn, principal, org, include_withdrawn=True) if org else []
        distinct = await _distinct_sampled(conn, org) if org else None
    cands: list[Candidate] = []
    if org:
        cands, _, _ = await _load_pool(principal, org, since=since, until=until, channel="chat",
                                       exclude_sampled=False, with_text=False, limit=MAP_MAX)
    verdicts: dict[str, list[str]] = {}
    for h in heads:
        verdicts.setdefault(h["turn_id"], []).append(h["verdict"])
    reviewed_ids = set(verdicts)
    active = [i for i in items if not i["withdrawn"] or i["turn_id"] in reviewed_ids]
    disc = discovery(heads, block)
    out: dict[str, Any] = {
        "ok": True, "scope": "company" if org else "platform", "organization_id": org, "block": block,
        "window": {"since": since.isoformat(), "until": until.isoformat()},
        "plan": plan_view([b for b in batches if not b.get("withdrawn_at")], distinct) if org else None,
        "batches": batches,
        "open_batches": sum(1 for b in batches if not b.get("withdrawn_at") and int(b["missing"]) > 0) if org else None,
        "open_coding": open_coding(heads, active),
        "fair_sample": fair_sample([{"strategy": i["strategy"], "withdrawn": i["withdrawn"],
                                     "verdicts": verdicts.get(i["turn_id"], [])} for i in items]),
        "coverage": None, "agreement": agreement(firsts), "discovery": disc,
        "saturation": saturation(disc["points"], block), "by_company": None}
    if org:
        cov = coverage(cands, reviewed_ids)
        if len(cands) >= 3:
            m = await asyncio.to_thread(map_payload, cands, reviewed=reviewed_ids, mine=set())
            cov["clusters_unreviewed"] = [{"id": c["id"], "size": c["size"]} for c in m["clusters"] if not c["reviewed"]]
        else:
            cov["clusters_unreviewed"] = None
        out["coverage"] = cov
    else:
        by: dict[str, dict[str, Any]] = {}
        for i in items:
            by.setdefault(i["organization_id"], {"organization_id": i["organization_id"], "sampled": 0, "reviewed": 0})
            by[i["organization_id"]]["sampled"] += 1
        for tid in reviewed_ids:
            o = next(h["organization_id"] for h in heads if h["turn_id"] == tid)
            by.setdefault(o, {"organization_id": o, "sampled": 0, "reviewed": 0})["reviewed"] += 1
        out["by_company"] = sorted(by.values(), key=lambda r: -r["reviewed"])
    return out


# ── Phase 5: the Progress rows ride on GET /progress, and enrich gains its taxonomy sources ──────────────

_progress_m2 = progress


async def progress(principal: Any, org: str | None, *, since: datetime | None = None, until: datetime | None = None,
                   block: int = 10, filter_org: str | None = None) -> dict:
    """Open coding's progress (above) plus the failure-mode rows (taxonomy.progress_extension). The route keeps calling
    sampling.progress, so GET /progress answers both. The extension's `discovery` and `saturation` are the failure-mode
    curve and hint, and replace open coding's interim ones of the same names (wrong verdicts per block). The extension
    reads the company the open coding rows read (the platform owner's filter_org included)."""
    from . import taxonomy

    out = await _progress_m2(principal, org, since=since, until=until, block=block, filter_org=filter_org)
    out.update(await taxonomy.progress_extension(principal, out.get("organization_id", org), block=block))
    return out


async def enrich_inputs(conn: Any, org: str | None, *, mode_id: str | None = None, source: str = "bow",
                        proposal_run_id: str | None = None) -> dict:
    """The taxonomy sources of the enrich strategy (spec §3.3), read on `conn`, a LAB connection. With a mode, the
    seeds are the company's answers that resolve present for it ({"seed_turn_ids": [...]}; a mode that is gone or not
    in use is refused). With source 'depth_scan', the batch is the answers behind that scan's decided suggestions,
    accepted and dismissed, in the order they were decided, each with its reason ({"ordered": [(turn_id, reason)]}).
    Neither: {} (open coding's own path, seeded by the company's wrong verdicts)."""
    from . import labels, proposals, taxonomy

    if source == "depth_scan":
        if _uuid_or_none(proposal_run_id) is None:
            return {"ordered": []}
        run_id = str(uuid.UUID(str(proposal_run_id)))
        name = (await conn.execute(text(
            "SELECT m.name FROM tracelab.proposal_runs r JOIN tracelab.modes m ON m.id = r.mode_id "
            "WHERE r.id = CAST(:r AS uuid)"), {"r": run_id})).scalar() or "this mode"
        return {"ordered": [(t, f"depth-scan suggestion for {name}, {how}")
                            for t, how in await proposals.depth_batch_turns(conn, run_id, org)]}
    if mode_id and _uuid_or_none(mode_id) is not None:
        mode_id = str(uuid.UUID(str(mode_id)))
        mode = (await taxonomy.load_graph(conn)).modes.get(mode_id)
        if mode is None or mode["status"] not in taxonomy.LIVE_STATUSES:
            raise _bad_seed("live")
        return {"seed_turn_ids": await labels.present_turn_ids(conn, mode_id, org)}
    return {}
