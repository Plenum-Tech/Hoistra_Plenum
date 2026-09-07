"""Combined column-mapping score + decision — the SHARED contract used by both the
deterministic mapper (the actual DB-write path) and the B21.1 destination-column-mapping
display, so they can never disagree.

PURE (stdlib only). Destination sample values are FETCHED by the caller and INJECTED here
(this module never touches the DB or the matcher layer). The FM-ontology resolver is also
injected; with no resolver the ontology dimension is treated as "unknown" (never a hard fail).

Why this exists
---------------
The historic bug auto-resolved a mapping on a NAME match alone — e.g. ``vendors.id → vendors.id``
at 98% — even though the source column's values were asset codes (A-001…), merging two
unrelated business entities into one destination column. Auto-resolution must require
agreement on NAME *and* VALUE *and* ONTOLOGY (AND, not OR), with table context as a tie-breaker:

    final = name·0.30 + value·0.40 + ontology·0.20 + context·0.10

A pair only AUTO-RESOLVES when every KNOWN critical dimension agrees and final ≥ 0.95.
A dimension that genuinely cannot be evaluated (e.g. an empty destination table has no sample
values) is "unknown" and is excluded from the gate and the weighted average rather than
silently failing it — otherwise a fresh migration into empty tables could never auto-resolve.
"""

from __future__ import annotations

import re
from typing import Callable, Optional

# Dimension weights (sum to 1.0). Value is the heaviest — actual data agreement matters most.
W_NAME, W_VALUE, W_ONTOLOGY, W_CONTEXT = 0.30, 0.40, 0.20, 0.10

# Per-dimension pass thresholds for the hard AND-gate.
NAME_MIN = 0.80
VALUE_MIN = 0.50          # ≥50% of the smaller side's distinct values overlap
ONTOLOGY_MIN = 0.50

# Tokens that denote a real-world entity in a column name. When a qualified-PK
# containment match crosses entities (source 'asset_id' against dest 'id' in a
# vendors table → 'asset' isn't the vendors entity), the name dimension reads
# misleadingly high. Demoting it stops the value gate from auto-resolving on
# coincidental code overlap (A-### vendor ids that happen to be valid asset ids).
_ENTITY_PREFIXES = frozenset({
    "asset", "vendor", "site", "work", "workorder", "wo", "resource",
    "customer", "employee", "engineer", "technician", "supplier", "product",
    "contact", "location", "building", "department", "user", "team",
    "company", "client", "tenant", "lease", "contract", "invoice", "task",
})


def _singular_token(table: str) -> str:
    """Lowercase singular form of a destination table name — 'vendors'→'vendor',
    'workorders'→'workorder', 'sites'→'site'. Matches _singular() in column_intelligence
    but is intentionally local so this module stays standalone."""
    if not table:
        return ""
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", table).lower()
    if snake.endswith("s") and not snake.endswith("ss"):
        snake = snake[:-1]
    return snake


def _dest_entity_tokens(table: str) -> set:
    """All entity aliases a destination table can legitimately be referred to by, so the
    cross-entity guard doesn't flag a column whose canonical merely abbreviates its OWN table.

    For 'work_orders': the per-word tokens ('work', 'order(s)'), the collapsed forms
    ('workorders', 'workorder'), the singular ('work_order'), AND the initialism of a multi-word
    name ('wo'). So a 'wo_description' → work_orders.description mapping is NOT a conflict ('wo' is
    work_orders), while 'asset_id' → vendors.id still is ('asset' ∉ vendors' aliases).
    """
    if not table:
        return set()
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", str(table)).lower()
    words = [w for w in re.split(r"[^a-z0-9]+", snake) if w]
    if not words:
        return set()
    sing_words = list(words)
    if sing_words[-1].endswith("s") and not sing_words[-1].endswith("ss"):
        sing_words[-1] = sing_words[-1][:-1]
    toks: set = set(words) | set(sing_words)
    for w in list(toks):  # generic singularisation of every word token
        if w.endswith("s") and not w.endswith("ss"):
            toks.add(w[:-1])
    toks.add("".join(words))        # workorders
    toks.add("".join(sing_words))   # workorder
    if len(sing_words) > 1:
        toks.add("".join(w[0] for w in sing_words))  # initialism: work_order → 'wo'
    return {t for t in toks if t}


def entity_prefix_conflict(source_name: str, dest_name: str, dest_table: Optional[str]) -> Optional[str]:
    """Return the conflicting entity prefix if source's qualified-PK extra token
    names an entity different from the destination table — else None.

    Example: source='asset_id', dest='id', dest_table='vendors'.
      src tokens = {asset, id}, dst tokens = {id} → containment match.
      extra = {asset}. dest_table singular = 'vendor'. 'asset' ∈ _ENTITY_PREFIXES
      and 'asset' ≠ 'vendor' → returns 'asset' (conflict).
    """
    if not dest_table:
        return None
    src_t = set(_norm(source_name).split("_"))
    dst_t = set(_norm(dest_name).split("_"))
    if not src_t or not dst_t or src_t == dst_t:
        return None
    # Only fires when one side is a strict subset of the other (the qualified-PK case).
    if not (src_t.issubset(dst_t) or dst_t.issubset(src_t)):
        return None
    larger = src_t if len(src_t) > len(dst_t) else dst_t
    smaller = src_t if len(src_t) < len(dst_t) else dst_t
    extra = larger - smaller
    if not extra:
        return None
    # An entity token is a conflict only when it names an entity OTHER than the destination
    # table. Compare against ALL of the table's aliases (singular, collapsed, per-word, and the
    # multi-word initialism) so a column whose canonical abbreviates its own table — e.g.
    # 'wo_description' → work_orders.description ('wo' = work_orders) — is NOT flagged.
    dest_toks = _dest_entity_tokens(dest_table)
    for tok in extra:
        if tok and tok not in dest_toks and tok in _ENTITY_PREFIXES:
            return tok
    return None

# Final-score bands.
AUTO_MIN = 0.95
SUGGEST_MIN = 0.70

Decision = str  # "auto_resolved" | "suggested" | "new_column"


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s or "").lower()).strip("_")


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def name_similarity(a, b) -> float:
    """0..1 column-name similarity — exact/normalised match, qualified-PK alias
    (``vendor_id`` / ``asset_id`` ↔ ``id``), token overlap, or edit ratio.

    A qualified-PK alias scores HIGH on purpose: ``asset_id`` and ``vendor_id`` both read as
    a strong NAME match against ``id``. That is by design — the NAME dimension does not
    distinguish them; the VALUE dimension does (asset codes vs vendor ids never overlap), so a
    high name score here is safe because auto-resolve still requires the value gate to pass.
    """
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = set(na.split("_")), set(nb.split("_"))
    union = ta | tb
    jaccard = len(ta & tb) / len(union) if union else 0.0
    # Containment: the smaller token-set is fully inside the larger (e.g. 'id' ⊆ 'vendor_id').
    # Capped just below an exact match so a qualified alias is "high" but not "identical".
    smaller = min(len(ta), len(tb)) or 1
    containment = 0.9 * (len(ta & tb) / smaller)
    lev_ratio = 1.0 - _levenshtein(na, nb) / max(len(na), len(nb))
    return round(max(jaccard, containment, lev_ratio), 4)


def _distinct(values) -> set[str]:
    return {str(v).strip().lower() for v in (values or []) if str(v).strip()}


def value_overlap(source_values, dest_values) -> Optional[float]:
    """Two-way containment of distinct values — max(|∩|/|src|, |∩|/|dst|).

    Returns ``None`` when the destination side is unknown (no sample values — e.g. an empty
    target table), so the caller can treat the value dimension as un-evaluable rather than 0.
    """
    dd = _distinct(dest_values)
    if not dd:
        return None
    sd = _distinct(source_values)
    if not sd:
        return 0.0
    inter = sd & dd
    return round(max(len(inter) / len(sd), len(inter) / len(dd)), 4)


def _dominant_prefix(values) -> Optional[str]:
    """The most common ID namespace prefix among id-like values — the leading non-digit run
    before the first digit, uppercased ('A-001'→'A-', 'WO123'→'WO', 'V-1001'→'V-'). Returns
    None when the values aren't id-like (free text, pure numbers, dates) so prefix logic only
    applies where a namespace actually exists."""
    counts: dict[str, int] = {}
    seen = 0
    for v in values or []:
        s = str(v).strip()
        if not s:
            continue
        m = re.match(r"^([^\d]{1,8}?)\d", s)  # alpha/punct run immediately followed by a digit
        if m:
            counts[m.group(1).upper()] = counts.get(m.group(1).upper(), 0) + 1
        seen += 1
    if not counts or seen == 0:
        return None
    best = max(counts.items(), key=lambda kv: kv[1])
    # Require the prefix to dominate (≥60% of values) to be a real namespace signal.
    return best[0] if best[1] / seen >= 0.6 else None


def value_alignment(source_values, dest_values) -> tuple[Optional[float], Optional[bool]]:
    """(score, match) for the VALUE dimension against the destination's real data.

    Returns ``(None, None)`` when the destination is unknown (empty target table) so the gate
    can't be evaluated. Otherwise:
      • high distinct overlap → strong match (re-migration / shared entity);
      • else, when BOTH sides carry an id namespace prefix, equal prefix = match (handles brand
        new ids that don't overlap yet), differing prefix = confident MISMATCH (A- vs V-);
      • else (no overlap, no namespace signal) → ``(overlap, None)`` = inconclusive, never a
        hard fail — so free text / numbers / dates are not demoted on weak value evidence.
    """
    overlap = value_overlap(source_values, dest_values)
    if overlap is None:
        return None, None
    if overlap >= VALUE_MIN:
        return overlap, True
    sp, dp = _dominant_prefix(source_values), _dominant_prefix(dest_values)
    if sp and dp:
        same = sp == dp
        return (1.0 if same else 0.0), same
    # Inconclusive (low overlap, no comparable id namespace — free text, dates, UUID dests).
    # Return (None, None) so the value dimension is EXCLUDED from the weighted final entirely,
    # rather than a 0.0 that would drag an exact name match below the auto-resolve threshold.
    return None, None


def _ontology_agreement(src_name, dst_name, resolver) -> Optional[float]:
    if not resolver:
        return None
    try:
        cs, _ = resolver(src_name)
        cd, _ = resolver(dst_name)
    except Exception:  # pragma: no cover — a resolver hiccup must never break scoring
        return None
    if not cs or not cd:
        return None
    return 1.0 if _norm(cs) == _norm(cd) else 0.0


def score_column_mapping(
    *,
    source_name: str,
    source_values,
    dest_name: str,
    dest_values=None,
    same_table: bool = False,
    field_resolver: Optional[Callable[[str], tuple]] = None,
    dest_table: Optional[str] = None,
) -> dict:
    """Score a single (source column → destination column) candidate across four dimensions
    and return the decision. ``source_name`` should be the column's CANONICAL name when one
    exists (it reflects the column's real meaning better than a generic source header).

    When ``dest_table`` is supplied the entity-prefix check fires: a qualified-PK
    containment that crosses entities (e.g. ``asset_id`` → ``vendors.id``) collapses the
    name score so a coincidental value overlap can't push the pair to auto-resolve.
    """
    name = name_similarity(source_name, dest_name)
    # Entity-prefix conflict: collapse the name dimension when the qualified-PK
    # containment crosses entities. Stops vendors.id (with values that happen to
    # look like asset codes) from auto-resolving into a renamed 'asset_id' slot.
    conflicting_entity = entity_prefix_conflict(source_name, dest_name, dest_table)
    if conflicting_entity is not None:
        name = round(min(name, 0.30), 4)
    value, value_match = value_alignment(source_values, dest_values)  # namespace-aware; None=unknown
    ontology = _ontology_agreement(source_name, dest_name, field_resolver)  # None when unknown
    context = 1.0 if same_table else 0.0

    # Weighted final over the KNOWN dimensions only (unknown dims excluded, weights renormalised).
    known = [(name, W_NAME), (context, W_CONTEXT)]
    if value is not None:
        known.append((value, W_VALUE))
    if ontology is not None:
        known.append((ontology, W_ONTOLOGY))
    total_w = sum(w for _, w in known) or 1.0
    final = round(sum(v * w for v, w in known) / total_w, 4)

    name_match = name >= NAME_MIN
    ontology_match = None if ontology is None else ontology >= ONTOLOGY_MIN

    # New column: the values demonstrably DON'T overlap, or ontology says different entity,
    # OR the qualified-PK alias crosses real-world entities (asset_id ↔ vendors.id). The
    # entity-prefix conflict is a hard semantic gate: the two columns name different things,
    # so they must NOT share a destination slot — drop the match entirely so the row reads
    # 'new column' instead of suggesting an obviously-wrong target.
    if value_match is False or ontology_match is False or conflicting_entity is not None:
        decision: Decision = "new_column"
    # Auto-resolve: every KNOWN critical dimension agrees AND the blended score clears 0.95.
    elif name_match and value_match is not False and ontology_match is not False and final >= AUTO_MIN:
        decision = "auto_resolved"
    elif final >= SUGGEST_MIN:
        decision = "suggested"
    else:
        decision = "new_column"

    return {
        "source_name": source_name,
        "dest_name": dest_name,
        "name_score": name,
        "value_score": value,
        "ontology_score": ontology,
        "context_score": context,
        "final_score": final,
        "name_match": name_match,
        "value_match": value_match,
        "ontology_match": ontology_match,
        "decision": decision,
        "entity_conflict": conflicting_entity,
    }
