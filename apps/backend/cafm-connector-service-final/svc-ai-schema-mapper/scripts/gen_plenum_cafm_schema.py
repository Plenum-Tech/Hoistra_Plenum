"""Generator: plenum_cafm.csv -> src/matchers/plenum_cafm_schema.py.

Encodes the authoritative plenum_cafm destination schema (every base table + its real
columns) and derives SAFE, schema-grounded aliases:

  TABLES                  : {table: [columns]}                      (authoritative schema)
  TABLE_ALIASES           : {alias_norm: table}                     (table-name synonyms)
  COLUMN_ALIASES_BY_TABLE : {table: {alias_norm: real_column}}      (table-scoped column synonyms)

Column aliases are conservative: entity-prefix add/strip (assets.asset_name <-> name) +
a small abbreviation vocabulary. Any alias that collides with a real column or another
alias in the same table is dropped, so a generated alias never causes an ambiguous map.

Run:  python scripts/gen_plenum_cafm_schema.py  --csv <path/to/plenum_cafm.csv>
"""
import argparse
import csv
import os
import re
from collections import OrderedDict, defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(_HERE, "..", "src", "matchers", "plenum_cafm_schema.py")

# Internal / non-domain tables — no value aliasing source CMMS data to them.
SKIP_TABLES = {
    "alembic_version", "canonical_registry", "agent_audit_log", "orchestration_audit_log",
    "claude_api_usage", "claude_budget_config", "ingestion_audit_log", "ingestion_documents",
    "document_chunks", "document_generation_log", "corrections_log", "review_queue",
    "prompt_ab_tests", "prompt_templates", "migration_field_mappings", "migration_hierarchy",
    "migration_jobs", "schema_mapping_field_mappings", "schema_mapping_jobs",
    "fiix_ingestion_jobs", "fiix_schema_cache", "import_errors", "import_jobs", "field_maps",
    "connectors", "mapping_templates", "udr_entity_relationships", "udr_run_graph",
    "udr_run_versions", "activity_actions", "activity_log_entries", "audit_logs",
    "query_audit_log", "data", "meter",
}

# Bidirectional abbreviation vocabulary (token-level).
ABBREV = {
    "desc": "description", "qty": "quantity", "amt": "amount", "num": "number",
    "no": "number", "mfr": "manufacturer", "mfg": "manufacturer", "dept": "department",
    "addr": "address", "org": "organization", "ref": "reference", "loc": "location",
    "cat": "category", "tel": "telephone", "ph": "phone", "config": "configuration",
    "max": "maximum", "min": "minimum", "pct": "percent", "dob": "date_of_birth",
    "info": "information", "qty_on_hand": "quantity_on_hand",
}
# Columns too generic to alias by prefix-strip (would collide across concepts).
GENERIC_COLS = {"id", "name", "code", "status", "type", "description", "notes", "category"}

IRREGULAR = {"countries": "country", "currencies": "currency", "priorities": "priority",
             "companies": "company", "warranties": "warranty", "facilities": "facility",
             "histories": "history", "categories": "category", "businesses": "business"}


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")


def singular(table: str) -> str:
    if table in IRREGULAR:
        return IRREGULAR[table]
    if table.endswith("ies"):
        return table[:-3] + "y"
    if table.endswith("ses") or table.endswith("xes") or table.endswith("ches") or table.endswith("shes"):
        return table[:-2]
    if table.endswith("s") and not table.endswith("ss"):
        return table[:-1]
    return table


def abbr_variants(token_str: str):
    """Generate abbreviation expand/contract variants of an underscore token string."""
    toks = token_str.split("_")
    out = set()
    # expand abbreviations
    exp = [ABBREV.get(t, t) for t in toks]
    if exp != toks:
        out.add("_".join(exp))
    # contract (expansion -> abbrev) using reverse map for whole-word matches
    rev = {v: k for k, v in ABBREV.items() if "_" not in v}
    con = [rev.get(t, t) for t in toks]
    if con != toks:
        out.add("_".join(con))
    return {o for o in out if o and o != token_str}


def build():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=r"C:\Users\balap\Downloads\Dabur Vatika\plenum_cafm.csv")
    args = ap.parse_args()

    tables = OrderedDict()
    colmeta = OrderedDict()  # table -> [(column, nullable, default)]
    with open(args.csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tables.setdefault(row["table"], []).append(row["column"])
            colmeta.setdefault(row["table"], []).append(
                (row["column"], (row.get("nullable") or "").upper(), row.get("default_value") or "")
            )

    # ── PRIMARY_KEYS ── derive each table's PK column from the schema:
    #   1) an 'id' column that is NOT NULL, else
    #   2) a '<entity>_id' column that is NOT NULL, else
    #   3) the first NOT NULL column with a gen_random_uuid()/nextval default.
    primary_keys = {}
    for t, cols in colmeta.items():
        if t in SKIP_TABLES:
            continue
        notnull = [(c, d) for (c, n, d) in cols if n == "NO"]
        gen = [c for (c, d) in notnull if "gen_random_uuid" in d or "nextval" in d]
        pk = None
        if any(c == "id" for c, _ in notnull):
            pk = "id"
        else:
            ent_ids = [c for c, _ in notnull if c.endswith("_id")]
            if ent_ids:
                pk = next((c for c in ent_ids if c in gen), ent_ids[0])
            elif gen:
                pk = gen[0]
        if pk:
            primary_keys[t] = pk

    # ── TABLE_ALIASES ────────────────────────────────────────────────────────────
    table_aliases = {}
    alias_owner = defaultdict(set)
    for t in tables:
        if t in SKIP_TABLES:
            continue
        cands = {t, _norm(t), singular(t), _norm(singular(t)), t.replace("_", "")}
        cands |= abbr_variants(t)
        for a in cands:
            an = _norm(a)
            if an and an != t:
                alias_owner[an].add(t)
    for a, owners in alias_owner.items():
        if len(owners) == 1:  # drop cross-table collisions
            table_aliases[a] = next(iter(owners))

    # ── COLUMN_ALIASES_BY_TABLE ──────────────────────────────────────────────────
    col_aliases = {}
    for t, cols in tables.items():
        if t in SKIP_TABLES:
            continue
        sing = _norm(singular(t))
        real = {_norm(c) for c in cols}
        proposed = defaultdict(set)  # alias -> {real_col}
        for c in cols:
            cn = _norm(c)
            variants = set()
            # entity-prefix strip:  asset_name -> name ;  work_order_code -> code
            if sing and cn.startswith(sing + "_"):
                stripped = cn[len(sing) + 1:]
                if stripped and stripped not in GENERIC_COLS:
                    variants.add(stripped)
            # entity-prefix add:  (real 'name') 'asset_name'  — only if not generic
            if sing and "_" not in cn and cn not in GENERIC_COLS:
                variants.add(f"{sing}_{cn}")
            # abbreviation variants
            variants |= abbr_variants(cn)
            for v in variants:
                if v and v != cn and v not in real:
                    proposed[v].add(cn)
        # keep only aliases that map to exactly ONE real column in this table
        clean = {a: next(iter(cs)) for a, cs in proposed.items() if len(cs) == 1}
        if clean:
            col_aliases[t] = dict(sorted(clean.items()))

    with open(OUT, "w", encoding="utf-8") as f:
        f.write('"""AUTO-GENERATED from plenum_cafm.csv — do not edit by hand.\n\n')
        f.write("Authoritative plenum_cafm destination schema + schema-grounded aliases.\n")
        f.write("Regenerate with scripts/gen_plenum_cafm_schema.py.\n\"\"\"\n\n")
        f.write(f"# {len(tables)} base tables, {sum(len(c) for c in tables.values())} columns\n")
        f.write("TABLES = {\n")
        for t, cols in tables.items():
            f.write(f"    {t!r}: {cols!r},\n")
        f.write("}\n\n")
        f.write("# table-name synonym (normalized) -> real plenum_cafm table\n")
        f.write("TABLE_ALIASES = {\n")
        for a in sorted(table_aliases):
            f.write(f"    {a!r}: {table_aliases[a]!r},\n")
        f.write("}\n\n")
        f.write("# table -> {column alias (normalized) -> real column}\n")
        f.write("COLUMN_ALIASES_BY_TABLE = {\n")
        for t in sorted(col_aliases):
            f.write(f"    {t!r}: {col_aliases[t]!r},\n")
        f.write("}\n\n")
        f.write("# table -> primary-key column (derived from null/default schema metadata)\n")
        f.write("PRIMARY_KEYS = {\n")
        for t in sorted(primary_keys):
            f.write(f"    {t!r}: {primary_keys[t]!r},\n")
        f.write("}\n")

    print(f"tables: {len(tables)}  table_aliases: {len(table_aliases)}  "
          f"primary_keys: {len(primary_keys)}  "
          f"tables_with_col_aliases: {len(col_aliases)}  "
          f"col_aliases: {sum(len(v) for v in col_aliases.values())}")
    print("wrote", os.path.normpath(OUT))


if __name__ == "__main__":
    build()
