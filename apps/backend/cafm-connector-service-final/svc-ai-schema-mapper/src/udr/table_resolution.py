"""Feature 7 — Table-resolution report (B7.1 → B12.1), PURE.

Turns the data a UDR run already has (the per-table rows + metadata + the source→canonical
table routing the upstream graph nodes resolved) into the structured, step-by-step
"Migration Analysis" the UI renders instead of a bare "File Ingestion → Tables → Mapping"
summary card. Each section mirrors one expandable step:

  B7.1  Unique table identification  — per-table metadata cards + the highest pairwise
        name/metadata score and the consolidation verdict.
  B8.1  Primary-key detection        — kind · PK · uniqueness · null-rate · tie-break.
  B9.1  Deterministic table mapping  — exact / Levenshtein ≤ 2 name match (≥0.95 auto).
  B10.1 RAG / alias mapping          — FM-ontology / learned-registry alias hit (≥0.95 auto).
  B11.1 Semantic table mapping       — LLM matcher on name + metadata; ≥0.70 suggested.
  B12.1 Final per-source-table decisions.

PURE (stdlib only) so it is unit-testable and reusable by both the migration and Fiix
paths. The RAG/alias step takes an *injected* resolver (the node adapter passes
``matchers.fm_ontology.fm_table_lookup``) so this module never imports the matcher layer;
with no resolver, non-deterministic tables simply fall through to semantic — exactly the
gate cascade the spec describes.
"""

from __future__ import annotations

import re
from typing import Callable

from .primitives import primary_key_report
from .unique_tables import metadata_similarity, table_name_similarity

# Gate thresholds (mirror unique_tables + the B9/B10/B11 spec).
DETERMINISTIC_AUTO = 0.95
RAG_AUTO = 0.95
SEMANTIC_SUGGEST = 0.70
SEMANTIC_DEFAULT_CONF = 0.85   # used when the upstream node recorded no table-match confidence
_LEVENSHTEIN_MAX = 2
_SAMPLE_CARD_COLS = 4          # how many columns to preview on each metadata card
_MAX_CARDS = 40                # never explode the payload on a wide workbook


def _norm(s) -> str:
    """lowercase, strip non-alphanumerics — for exact/plural-tolerant name compare."""
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def _singular(s: str) -> str:
    return s[:-1] if s.endswith("s") and len(s) > 3 else s


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


def _pct(x) -> str:
    return f"{round(float(x) * 100)}%" if isinstance(x, (int, float)) else "—"


def _two(x) -> str:
    return f"{float(x):.2f}" if isinstance(x, (int, float)) else "—"


# ── B9/B10/B11 — classify HOW each source table was resolved to its destination ──
def classify_table_method(
    source: str,
    dest: str | None,
    *,
    alias_resolver: Callable[[str], tuple] | None = None,
) -> dict:
    """Decide the resolution method for one source→dest table pair, mirroring the
    deterministic → RAG/alias → semantic cascade.

    Returns ``{method, confidence, note, alias_hit, alias_source}`` where ``method`` is
    one of ``exact | levenshtein | rag_alias | semantic | unresolved``.
    """
    if not dest:
        return {"method": "unresolved", "confidence": None, "note": "no destination resolved",
                "alias_hit": None, "alias_source": None}

    ns, nd = _norm(source), _norm(dest)
    # B9 — deterministic. Exact (case/space/plural tolerant) then Levenshtein ≤ 2.
    if ns == nd or _singular(ns) == _singular(nd):
        note = "plural normalise" if ns != nd else "exact name match"
        return {"method": "exact", "confidence": 1.0, "note": note,
                "alias_hit": None, "alias_source": None}
    dist = _levenshtein(ns, nd)
    if dist <= _LEVENSHTEIN_MAX:
        return {"method": "levenshtein", "confidence": round(1.0 - dist * 0.03, 3),
                "note": f"Levenshtein {dist} ≤ {_LEVENSHTEIN_MAX}", "alias_hit": None,
                "alias_source": None}

    # B10 — RAG / alias (FM ontology + learned registry), via the injected resolver.
    if alias_resolver is not None:
        try:
            hit, conf = alias_resolver(source)
        except Exception:  # pragma: no cover — a resolver hiccup must never break the report
            hit, conf = None, 0.0
        if hit:
            return {"method": "rag_alias", "confidence": float(conf or RAG_AUTO),
                    "note": "FM ontology alias", "alias_hit": str(hit),
                    "alias_source": "FM_TABLE_SYNONYMS"}

    # B11 — semantic (handled by the caller, which knows the upstream confidence).
    return {"method": "semantic", "confidence": None, "note": "name + metadata semantic match",
            "alias_hit": None, "alias_source": None}


def _metadata_card(meta: dict) -> dict:
    pk = meta.get("primary_key") or []
    samples = meta.get("samples_by_column") or {}
    cols = list(meta.get("column_names") or [])
    preview = []
    for c in cols:  # full column list per table (every column, with up to 3 sample values)
        vals = [str(v) for v in (samples.get(c) or [])][:3]
        preview.append({"column": c, "values": vals})
    return {
        "table": meta.get("table"),
        "primary_key": pk,
        "primary_key_kind": meta.get("primary_key_kind"),
        "column_count": int(meta.get("column_count", len(cols)) or 0),
        "row_count": int(meta.get("row_count", 0) or 0),
        "samples": preview,
    }


def _pairwise_verdict(metas: list[dict], unique_report: dict) -> dict:
    """The B7.1 headline: the single highest-scoring table pair + the consolidation outcome."""
    auto = list((unique_report or {}).get("auto_consolidated") or [])
    cands = list((unique_report or {}).get("candidates") or [])
    best = None
    for i in range(len(metas)):
        for j in range(i + 1, len(metas)):
            a, b = metas[i], metas[j]
            meta_sim = metadata_similarity(a, b)
            name_sim = table_name_similarity(a.get("table") or "", b.get("table") or "")
            if best is None or meta_sim > best["metadata_similarity"]:
                best = {
                    "table_a": a.get("table"),
                    "table_b": b.get("table"),
                    "metadata_similarity": meta_sim,
                    "name_similarity": name_sim,
                }
    if auto:
        verdict = f"{len(auto)} group(s) auto-consolidated (same entity, different name)."
    elif cands:
        verdict = f"{len(cands)} consolidation candidate(s) — confirmation required."
    elif best is not None:
        verdict = (
            f"Highest pair: {best['table_a']} ↔ {best['table_b']} — meta {_two(best['metadata_similarity'])} · "
            f"name {_two(best['name_similarity'])}; both below the 0.80 / 0.95 gates → all kept distinct."
        )
    else:
        verdict = "Single table — no pairwise comparison."
    return {
        "highest_pair": best,
        "auto_consolidated": auto,
        "candidates": cands,
        "verdict": verdict,
    }


def _group_label(members: list[str]) -> str:
    """A representative name for a duplicate group. Handles the common cases:
      * ``work_order_1`` + ``work_order_2``  → ``work_order``  (trailing index stripped)
      * ``workorders``   + ``work_orders``   → ``work_orders`` (separator-insensitive match →
        prefer the richer member name)
    Falls back to the longest-common-prefix, then the shortest member name."""
    members = [m for m in members if m]
    if not members:
        return ""
    if len(members) == 1:
        return members[0]
    # Strip a trailing index / separator run (work_order_1 → work_order).
    stripped = [m.rstrip("_-. 0123456789") for m in members]
    if len(set(stripped)) == 1 and len(stripped[0]) >= 3:
        return stripped[0]
    # Separator- and plural-insensitive: if the alphanumeric-only (optionally de-pluralised)
    # forms all match, pick the richest original name (work_order vs workorders → work_order).
    alnum = [re.sub(r"[^a-z0-9]+", "", s.lower()) for s in stripped]
    if len(set(alnum)) == 1 and alnum[0]:
        return max(stripped, key=len)
    alnum_sing = [(a[:-1] if a.endswith("s") and len(a) > 3 else a) for a in alnum]
    if len(set(alnum_sing)) == 1 and alnum_sing[0]:
        return max(stripped, key=len)
    prefix = stripped[0]
    for s in stripped[1:]:
        i = 0
        while i < len(prefix) and i < len(s) and prefix[i] == s[i]:
            i += 1
        prefix = prefix[:i]
    prefix = prefix.rstrip("_-. 0123456789")
    return prefix if len(prefix) >= 3 else min(stripped, key=len)


def _uf_find(parent: dict, x):
    """Union-find root with path compression."""
    root = x
    while parent[root] != root:
        root = parent[root]
    while parent[x] != root:
        parent[x], x = root, parent[x]
    return root


# Two tables are DUPLICATE candidates when the Jaccard overlap of their (normalised) column
# names is at least this — i.e. they share most columns, so they likely describe the same entity.
DUPLICATE_COL_SIM_MIN = 0.6

# Generic PK names that are NOT a duplicate signal on their own — many unrelated tables use a
# bare "id". An entity-specific shared PK (wo_id, asset_id) IS a strong same-entity signal.
_GENERIC_PK_NORM = {"id", "uid", "pk", "key", "code", "no", "num", "rowid", "udrid"}


def detect_duplicate_tables(
    table_metas: list[dict], *, col_sim_min: float = DUPLICATE_COL_SIM_MIN
) -> dict:
    """Pre-B7.1 step — flag tables that look like DUPLICATES of each other because they share
    similar columns (a column appears in more than one table). Two tables are duplicate
    candidates when the Jaccard overlap of their normalised column names ≥ ``col_sim_min``.
    Overlapping tables are grouped (union-find); each group of ≥ 2 tables is a duplicate set
    with a ``count`` (e.g. 2 → "duplicate table count as 2").

    Returns ``{groups: [{tables, count, shared_columns, similarity}], duplicate_count, checked}``.
    """
    metas = [m for m in (table_metas or []) if isinstance(m, dict)]
    names = [m.get("table") for m in metas]
    colsets: dict = {}
    orig: dict = {}  # table → {normalised_col: original_col} for displaying shared columns
    pksets: dict = {}  # table → {normalised PK column names}
    for m in metas:
        t = m.get("table")
        cs: set = set()
        om: dict = {}
        for c in (m.get("column_names") or []):
            nc = _norm(c)
            if nc:
                cs.add(nc)
                om.setdefault(nc, str(c))
        colsets[t] = cs
        orig[t] = om
        pksets[t] = {_norm(c) for c in (m.get("primary_key") or []) if _norm(c)}

    parent = {n: n for n in names}
    pair_sim: dict = {}  # frozenset({a,b}) → similarity
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            sa, sb = colsets.get(a, set()), colsets.get(b, set())
            if not sa or not sb:
                continue
            inter = sa & sb
            union = sa | sb
            sim = len(inter) / len(union) if union else 0.0
            # Duplicate if columns overlap enough OR the two tables share the SAME
            # entity-specific primary key (e.g. both keyed on wo_id → same work-order entity).
            pka, pkb = pksets.get(a, set()), pksets.get(b, set())
            same_pk = bool(pka) and pka == pkb and not (pka <= _GENERIC_PK_NORM)
            if (inter and sim >= col_sim_min) or same_pk:
                ra, rb = _uf_find(parent, a), _uf_find(parent, b)
                if ra != rb:
                    parent[ra] = rb
                # For a PK-only match the column overlap may be low; keep it as the signal but
                # never below what a shared PK implies.
                pair_sim[frozenset((a, b))] = round(max(sim, 1.0 if same_pk else 0.0), 4)

    groups_map: dict = {}
    for n in names:
        groups_map.setdefault(_uf_find(parent, n), []).append(n)

    out_groups: list[dict] = []
    for members in groups_map.values():
        if len(members) < 2:
            continue
        members = sorted(members)
        # Columns shared across EVERY member (the duplicate evidence), shown by original name.
        common: set | None = None
        for mname in members:
            cs = colsets.get(mname, set())
            common = cs if common is None else (common & cs)
        common = common or set()
        first_orig = orig.get(members[0], {})
        shared_cols = sorted(first_orig.get(c, c) for c in common)
        sims = [s for pair, s in pair_sim.items() if pair <= set(members)]
        out_groups.append({
            "tables": members,
            "count": len(members),
            "label": _group_label(members),
            "shared_columns": shared_cols,
            "similarity": max(sims) if sims else 0.0,
        })

    out_groups.sort(key=lambda g: (-g["count"], -g["similarity"]))
    return {
        "groups": out_groups,
        "duplicate_count": sum(g["count"] for g in out_groups),
        "checked": len(names),
    }


def _apply_pk_override(cards: list[dict], pk_rows: list[dict], pk_override: dict) -> None:
    """Overlay the user-approved PK (from the confirmation gate) onto the B7.1 cards +
    B8.1 pk_detection rows, in place. ``pk_override`` = ``{table: [pk_col, ...]}`` where an
    empty list means the user approved a surrogate key. Tables absent from the override keep
    the auto-detected PK."""
    if not pk_override:
        return
    for card in cards:
        t = card.get("table")
        if t in pk_override:
            cols = list(pk_override[t] or [])
            card["primary_key"] = cols or ["_udr_id"]
            card["primary_key_kind"] = "surrogate" if not cols else (
                "composite" if len(cols) > 1 else "natural"
            )
            card["primary_key_confirmed"] = True
    for row in pk_rows:
        t = row.get("table")
        if t in pk_override:
            cols = list(pk_override[t] or [])
            row["primary_key"] = cols or ["_udr_id"]
            row["kind"] = "surrogate" if not cols else ("composite" if len(cols) > 1 else "natural")
            row["confirmed"] = True


def build_table_resolution(
    tables: dict[str, list[dict]],
    *,
    table_metas: list[dict] | None = None,
    unique_report: dict | None = None,
    dest_table_by_source: dict[str, str] | None = None,
    confidence_by_source: dict[str, float] | None = None,
    alias_resolver: Callable[[str], tuple] | None = None,
    pk_override_by_table: dict[str, list[str]] | None = None,
    duplicate_column_report: dict | None = None,
) -> dict:
    """Build the structured B7.1→B12.1 table-resolution report from a run's data.

    ``tables`` = ``{source_table: list[dict]}`` (the PRE-consolidation source rows, so the
    cards/PK match what the user uploaded). ``table_metas`` are the matching
    ``build_table_metadata`` dicts (rebuilt here if not supplied). ``dest_table_by_source``
    is the canonical destination each source table was routed to; ``confidence_by_source``
    the upstream table-match confidence (for the semantic rows). ``alias_resolver`` is an
    optional ``name -> (alias_entity, confidence)`` callable (FM ontology).
    """
    tables = tables or {}
    dest_by_src = dest_table_by_source or {}
    conf_by_src = confidence_by_source or {}

    if table_metas is None:
        from .primitives import build_table_metadata

        def _cols(rows):
            seen, out = set(), []
            for r in rows or []:
                for k in r:
                    if k not in seen:
                        seen.add(k); out.append(k)
            return out

        table_metas = [build_table_metadata(n, list(r or []), _cols(r)) for n, r in tables.items()]
    meta_by_name = {m.get("table"): m for m in table_metas}

    # Pre-B7.1 — duplicate-table detection (tables that share similar columns). Runs BEFORE
    # unique-table identification so the user sees which sheets look like duplicates first.
    duplicate_tables = detect_duplicate_tables(table_metas)

    # B7.1 — metadata cards + pairwise verdict.
    cards = [_metadata_card(m) for m in table_metas[:_MAX_CARDS]]
    pairwise = _pairwise_verdict(table_metas, unique_report or {})

    # B8.1 — primary-key detection (rich diagnostics from the source rows).
    pk_rows: list[dict] = []
    for name, rows in tables.items():
        meta = meta_by_name.get(name) or {}
        cols = list(meta.get("column_names") or [])
        rep = primary_key_report(list(rows or []), cols)
        pk_rows.append({
            "table": name,
            "kind": rep["kind"],
            "primary_key": rep["columns"],
            "uniqueness": rep["uniqueness"],
            "null_rate": rep["null_rate"],
            "tie_break": rep["tie_break"],
            "method": rep["method"],
            "confidence": rep["confidence"],
        })

    # B9 / B10 / B11 / B12 — classify every source→dest table pair by method.
    deterministic: list[dict] = []
    rag_alias: list[dict] = []
    semantic: list[dict] = []
    final: list[dict] = []
    for name in tables:
        dest = dest_by_src.get(name)
        cls = classify_table_method(name, dest, alias_resolver=alias_resolver)
        method = cls["method"]
        # Resolve the effective confidence: deterministic/RAG carry their own; semantic uses
        # the upstream table-match confidence (fallback to the suggested-band default).
        if method in ("exact", "levenshtein"):
            conf = cls["confidence"]
        elif method == "rag_alias":
            conf = cls["confidence"]
        elif method == "semantic":
            conf = conf_by_src.get(name)
            if not isinstance(conf, (int, float)):
                conf = SEMANTIC_DEFAULT_CONF
        else:
            conf = None

        # B9 row (deterministic table shows every source; non-deterministic = "→ next stage").
        if method in ("exact", "levenshtein"):
            deterministic.append({
                "source": name, "method": method, "destination": dest,
                "confidence": conf, "note": cls["note"],
            })
        else:
            deterministic.append({
                "source": name, "method": "none", "destination": None,
                "confidence": None,
                "note": "no canonical name match → next stage" if dest else "no destination",
            })

        # B10 row — only the tables that fell through deterministic.
        if method == "rag_alias":
            rag_alias.append({
                "source": name, "alias_hit": cls["alias_hit"], "destination": dest,
                "confidence": conf, "alias_source": cls["alias_source"],
            })
        elif method == "semantic":
            rag_alias.append({
                "source": name, "alias_hit": None, "destination": None,
                "confidence": None, "alias_source": "no alias → next stage",
            })

        # B11 row — only the tables that fell through to semantic.
        if method == "semantic":
            meta = meta_by_name.get(name) or {}
            signal = ", ".join(list(meta.get("column_names") or [])[:6])
            semantic.append({
                "source": name, "signal": signal, "destination": dest,
                "confidence": conf, "method": "semantic LLM",
                "band": "suggested" if (conf or 0) >= SEMANTIC_SUGGEST else "review",
            })

        # B12 — final decision row (every source table).
        final.append({
            "source": name,
            "destination": dest,
            "method": {"exact": "exact", "levenshtein": "Levenshtein", "rag_alias": "RAG/alias",
                       "semantic": "semantic LLM", "unresolved": "unresolved"}.get(method, method),
            "confidence": conf,
        })

    # Overlay the user-approved PK from the confirmation gate (if any) so the B7.1 cards +
    # B8.1 detection reflect what the human signed off, not just the auto-detected default.
    _apply_pk_override(cards, pk_rows, pk_override_by_table or {})

    # Node 1 merged these columns before any of this ran (works.tagnum == works.id -> tagnum).
    # Surfaced here so the UI can show the merge BEFORE the B8.1 primary-key step — the PK is
    # chosen from the merged column list, so the user needs to see the merge to read B8.1.
    merged_columns = [
        {
            "table": _tname,
            "kept": _e.get("kept"),
            "dropped": _e.get("dropped") or [],
            "members": _e.get("members") or [],
            "match_pct": _e.get("match_pct", 100),
            "row_count": _e.get("row_count", 0),
        }
        for _tname, _entries in ((duplicate_column_report or {}).get("tables") or {}).items()
        for _e in _entries
    ]

    return {
        # Pre-B7.1 — duplicate tables (share similar columns), shown before unique-table id.
        "duplicate_tables": duplicate_tables,
        # Pre-B8.1 — identical columns merged to one name in Node 1.
        "merged_columns": merged_columns,
        "metadata_cards": cards,
        "pairwise": pairwise,
        "pk_detection": pk_rows,
        "deterministic": deterministic,
        "rag_alias": rag_alias,
        "semantic": semantic,
        "final_decisions": final,
        "counts": {
            "tables": len(tables),
            "deterministic": sum(1 for r in deterministic if r["method"] != "none"),
            "rag_alias": sum(1 for r in rag_alias if r["alias_hit"]),
            "semantic": len(semantic),
        },
    }
