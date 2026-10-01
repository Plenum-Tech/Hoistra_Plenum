"""Ontology question answering - a record question answered from SQL the engine writes itself.

    question --LLM--> JSON plan in business terms (concepts, states, rules)   <- never SQL
             --> names/codes resolved to keys --> SQL compiled from the ontology --> scoped read
             --> linked records per focus record --> evidence for the agent to answer from

The port of cafm_assistant.py (30 Sep 2026) into the service. What it keeps is the whole idea:
the model never writes SQL. It picks concepts, states and relations by name, the plan is
validated against the ontology (and corrected up to three times), every name or code in it is
resolved to keys, and the SQL is compiled from the ontology's joins. The ontology is built from
the schema itself: concepts from the business tables, links from foreign keys, naming
conventions checked against the data and shared codes, states from the real status values,
and the BLOCKS rule (a lapsed or blocked certificate stops open work on the same asset, else
the same building).

Why it exists: "what is the status of Lift Asset-4471" came back "not found" from the agent
tools on 30 Sep 2026, while this engine found B-301-LIFT-4471 on its first plan. Resolution
here tries each concept's code columns exactly, then its name columns loosely
("Lift Asset-4471" also matches "lift_asset 4471"), then every other concept.

What changed on the way in:

* **Scope.** The script read the whole database. Every read made for a question here - the
  lookups and the compiled queries - runs against ``plenum_scoped``, the self-filtering views
  svc-udr's custom SELECT uses, with the caller's company and buildings set for that
  transaction only. A caller allocated to no building gets nothing, and so does a call with no
  caller at all. Only building the ontology (column names, status spellings, which columns
  join) reads the base tables, and only the values of status/type/category-like columns reach
  the model - the script's "any text column with twelve values or fewer" rule would have put
  one company's vendor and building names in another company's prompt.
* **Async and parameters.** SQLAlchemy text() with named parameters instead of psycopg; every
  identifier comes from the catalogue and is checked before it is quoted.
* **No second model call.** The script summarised with the model; here the calling agent is
  already a model, so this returns the evidence and the answer rules and the agent writes it.
* **Table descriptions** come from ontology_tables.json (the table-usage workbook's purposes).

The ontology is built once per process per schema fingerprint and cached in memory and in the
temp directory. ONTOLOGY_OVERRIDES may name a JSON file merged on top (same shape).
"""
from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import os
import re
import tempfile
from collections import defaultdict
from typing import Any

import structlog
from langchain_core.tools import tool
from sqlalchemy import text

log = structlog.get_logger(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================
BASE_SCHEMA = "plenum_cafm"
SCOPED_SCHEMA = "plenum_scoped"
HERE = os.path.dirname(os.path.abspath(__file__))
DESCRIPTIONS_FILE = os.path.join(HERE, "ontology_tables.json")

#: Tables the app uses day to day, per report page (hoistra_test_table_usage.xlsx, "In use").
AREAS = {
    "Buildings": ["sites", "buildings", "floors", "building_sections", "locations", "spaces", "portfolios",
                  "building_country_packs", "site_occupancy_logs"],
    "Assets": ["assets", "asset_categories", "asset_criticality", "asset_condition_rules", "asset_condition_runs",
               "asset_condition_scores", "asset_condition_verdicts", "asset_documents", "asset_failure_assessments",
               "asset_offline_log", "asset_reading_bands", "asset_readings", "asset_types", "asset_warranties",
               "equipment", "inspections", "inspection_read_runs", "maintenance_history"],
    "Maintenance": ["work_orders", "maintenance_plans", "ppm_visits", "approvals_queue_items", "spare_parts",
                    "technicians", "resource_skills", "work_order_assets", "work_order_parts"],
    "Compliance": ["compliance_certificates", "country_certificate_packs", "compliance_verification_sources",
                   "compliance_risk_snapshots", "regulation_packs", "regulatory_filings"],
    "Vendors": ["vendors", "vendor_contracts", "contract_sla_parameters", "vendor_wo_scores",
                "vendor_monthly_scorecards", "vendor_score_weight_config", "invoice_lines", "invoice_verifications",
                "cost_variance_alerts", "vendor_contacts", "contract_documents"],
    "Energy": ["energy_meters", "meter_readings", "energy_anomalies", "chiller_performance_readings",
               "chiller_design_specs", "eui_snapshots", "weather_degree_days", "building_energy_profiles",
               "energy_recommendations", "meters"],
}
EXCLUDE = ["users", "auth_*", "organizations", "user_buildings", "invitations", "audit_logs", "migration_*",
           "workbook_extras_runs", "canonical_registry", "report_card*", "reports", "saved_spaces",
           "platform_usage_events", "ops_email_log", "ops_audit_log", "activity_actions", "checkpoint*", "rag_*",
           "readme", "ingestion_*", "document_chunks", "rca_*", "purchase_order*", "receipt*", "wo_*", "udr_*",
           "prompt_*", "fiix_*", "schema_mapping_*", "mapping_templates", "claude_*", "alembic_version"]
SKIP_EMPTY_TABLES = True           # an empty table can only ever answer "none"

#: Places / equipment, MOST SPECIFIC FIRST. Records attached to the same one are related.
HUB_ORDER = ["assets", "equipment", "energy_meters", "spaces", "building_sections", "floors", "buildings",
             "locations", "sites", "portfolios"]

CONCEPT_NAMES = {
    "energy_meters": "Meter", "meters": "LegacyMeter", "meter_readings": "MeterReading",
    "energy_anomalies": "EnergyAnomaly", "ppm_visits": "PPMVisit", "maintenance_plans": "MaintenancePlan",
    "vendor_contracts": "VendorContract", "compliance_certificates": "ComplianceCertificate",
    "work_orders": "WorkOrder", "approvals_queue_items": "ApprovalItem", "vendor_wo_scores": "VendorWorkOrderScore",
}
SYNONYMS = {
    "sites": ["site", "property", "campus", "tower"],
    "buildings": ["building", "block", "tower", "podium"],
    "assets": ["asset", "equipment", "plant", "boiler", "chiller", "ahu", "lift", "pump"],
    "energy_meters": ["meter", "gas meter", "electricity meter", "sub-meter", "mpan", "mprn"],
    "meter_readings": ["reading", "readings", "consumption", "kwh", "demand", "load profile"],
    "energy_anomalies": ["anomaly", "insight", "excursion", "mismatch", "energy alert"],
    "work_orders": ["work order", "wo", "wos", "job", "ticket", "reactive work"],
    "ppm_visits": ["ppm", "last ppm", "ppm visit", "planned maintenance visit", "service visit"],
    "maintenance_plans": ["ppm plan", "ppm schedule", "maintenance plan", "preventive maintenance"],
    "compliance_certificates": ["certificate", "cert", "certs", "compliance", "statutory certificate"],
    "vendors": ["vendor", "contractor", "supplier", "subcontractor", "service provider"],
    "vendor_contracts": ["contract", "service contract", "sla contract"],
    "technicians": ["technician", "engineer", "operative"],
    "inspections": ["inspection", "load test", "condition check", "condition grade", "service report"],
}
BLOCKED_VALUES_RE = r"block|suspend|on[\s_-]?hold|\bhold\b|\bheld\b|fail|non[\s_-]?compl|reject|revok|invalid|withdrawn|quarantin"
BUILDER_VERSION = "5-svc1"          # bump when build logic changes -> cached ontology is rebuilt
ORG_COLUMN_RE = re.compile(r"^(organization|organisation|org|tenant|company|account|workspace)_id$", re.I)
STATE_COL_RE = re.compile(r"(status|stage)$|_state$", re.I)
STATE_LIKE_COL_RE = re.compile(r"(status|stage|result|outcome)$|_state$", re.I)
SCOPE_WORDS = {"asset": "assets", "equipment": "assets", "building": "buildings", "site": "sites",
               "floor": "floors", "space": "spaces", "vendor": "vendors", "contractor": "vendors",
               "supplier": "vendors"}
CLOSED_VALUES_RE = r"closed|complete|cancel|done|resolved|finished|rejected|void|archived|superseded"
LAPSED_VALUES_RE = r"laps|expired|overdue|out[\s_-]?of[\s_-]?date"
BLOCKED_VALUES_RE_EXTRA = r"unsatisf|not[\s_-]?compliant|condemn|immediately[\s_-]?dangerous|at[\s_-]?risk"
CANONICAL_CLOSED = ["Closed", "Completed", "Complete", "Cancelled", "Canceled", "Resolved", "Done"]
CERT_TABLE, WORK_ORDER_TABLE = "compliance_certificates", "work_orders"

STATEMENT_TIMEOUT_MS = 15_000
MAX_FOCUS_RECORDS = 200
MAX_MATCH_KEYS = 50
DEFAULT_INCLUDE_LIMIT = 20
MAX_INCLUDE_LIMIT = 200
MAX_PATH_HOPS = 5
MAX_PLAN_ATTEMPTS = 3
#: What the calling agent reads back. The script allowed 120K for a model call of its own; here
#: the evidence joins an agent's context, so it is kept to a size that leaves room to answer.
MAX_EVIDENCE_CHARS = 40_000
MAX_CELL_CHARS = 500

SAMPLE_ROWS = 2000
MAX_CATEGORICAL = 30
MAX_ATTRIBUTES = 30
CODE_RATIO = 0.6
TEXT_TYPES = {"text", "character varying", "character"}
DATE_TYPES = {"date", "timestamp without time zone", "timestamp with time zone"}
NUMERIC_TYPES = {"integer", "bigint", "smallint", "numeric", "double precision", "real"}
SKIP_TYPES = {"json", "jsonb", "bytea", "tsvector", "ARRAY", "USER-DEFINED", "xml"}
SKIP_NAME_RE = re.compile(r"password|passwd|secret|token|hash|salt|embedding|vector|api_key", re.I)
CATEGORICAL_RE = re.compile(r"status|state|stage|type|category|priority|severity|criticality|kind|class|"
                            r"frequency|trade|outcome|result|rating|band|grade|utility|scope|level", re.I)
LABEL_ORDER = [r"^name$", r"^title$", r"_name$", r"^code$", r"_code$", r"_number$", r"^number$", r"_no$",
               r"_tag$", r"^subject$", r"^description$", r"_title$", r"_type$", r"^type$"]
EXPIRY_RE = re.compile(r"expir|valid_to|valid_until|end_date|expires|renewal_date", re.I)
DUE_RE = re.compile(r"(^|_)due", re.I)
DONE_RE = re.compile(r"complet|done|closed_at|closed_on|performed|actual_end|finished", re.I)
SORT_RE = re.compile(r"created|raised|reported|detected|reading_at|read_at|recorded|logged|timestamp|_date$|_at$", re.I)

OPS = {"eq", "ne", "in", "not_in", "lt", "lte", "gt", "gte", "between", "within", "contains", "is_null"}
IDENTIFIER_RE = re.compile(r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)+")
RELATIVE_DATE_RE = re.compile(r"^today(?:\s*([+-])\s*(\d+)\s*([dwmy]))?$", re.IGNORECASE)
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class OntologyError(Exception):
    pass


class NotAllowed(Exception):
    """The caller may not read anything - no caller, or allocated to no building."""


# =============================================================================
# small helpers
# =============================================================================
def qi(name: str) -> str:
    """A quoted identifier. Every name here comes from the catalogue; this refuses anything else."""
    if not _IDENT_RE.match(str(name or "")):
        raise OntologyError("unsafe identifier: " + repr(name))
    return '"' + name + '"'


class Params:
    """Named bind parameters for one statement."""

    def __init__(self):
        self.d: dict[str, Any] = {}

    def add(self, value: Any, cast: str | None = None) -> str:
        name = "p" + str(len(self.d))
        self.d[name] = value
        return "CAST(:" + name + " AS " + cast + ")" if cast else ":" + name


def is_identifier(value) -> bool:
    s = str(value).strip()
    return (3 <= len(s) <= 80 and IDENTIFIER_RE.fullmatch(s) is not None
            and any(c.isdigit() for c in s) and any(c.isalpha() for c in s)
            and not re.match(r"^\d{4}-\d{2}-\d{2}", s))


def loose_pattern(text_: str) -> str:
    """'peak demand excursion' also matches 'peak_demand_excursion' (Postgres regex).

    Words are the letters and digits; whatever sits between them - spaces, underscores, a
    hyphen, an em dash - matches anything that is not a letter or digit. The script allowed only
    spaces, underscores and hyphens, so "Boiler 2 - central plant" typed with a hyphen missed the
    register's "Boiler 2 — central plant", which is written with an em dash."""
    words = re.findall(r"[^\W_]+", text_)
    return "[^[:alnum:]]*".join(re.escape(w) for w in words)


def strip_code_fences(t: str) -> str:
    t = (t or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    return re.sub(r"\s*```$", "", t).strip()


def singular(name):
    if name.endswith("ies"):
        return name[:-3] + "y"
    if name.endswith("sses"):
        return name[:-2]
    if name.endswith("s") and not name.endswith("ss"):
        return name[:-1]
    return name


def snake(camel_name):
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", camel_name).lower()


def matches_any(name, patterns):
    return any(name.startswith(p[:-1]) if p.endswith("*") else name == p for p in patterns)


def slug(v):
    return re.sub(r"[^a-z0-9]+", "_", str(v).lower()).strip("_") or "blank"


def col_base(col):
    return re.sub(r"_(id|uuid|code|no|number|ref|key|tag)$", "", col)


# =============================================================================
# SCHEMA DISCOVERY -> ONTOLOGY
# =============================================================================
async def _fetch(session, sql: str, params: dict | None = None) -> list[dict]:
    res = await session.execute(text(sql), params or {})
    return [dict(r) for r in res.mappings().all()]


async def load_catalog(session, schema: str) -> dict:
    cat: dict[str, dict] = {}
    for r in await _fetch(session, """
            SELECT c.table_name, c.column_name, c.data_type
            FROM information_schema.columns c
            JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
            WHERE c.table_schema = :s AND t.table_type = 'BASE TABLE'
            ORDER BY c.table_name, c.ordinal_position""", {"s": schema}):
        m = cat.setdefault(r["table_name"], {"columns": [], "types": {}, "pk": [], "fks": []})
        m["columns"].append(r["column_name"])
        m["types"][r["column_name"]] = r["data_type"]
    for r in await _fetch(session, """
            SELECT cl.relname AS t, a.attname AS c FROM pg_constraint con
            JOIN pg_class cl ON cl.oid = con.conrelid JOIN pg_namespace ns ON ns.oid = cl.relnamespace
            JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = ANY(con.conkey)
            WHERE con.contype = 'p' AND ns.nspname = :s""", {"s": schema}):
        if r["t"] in cat:
            cat[r["t"]]["pk"].append(r["c"])
    for r in await _fetch(session, """
            SELECT cl.relname AS t, a.attname AS c, rcl.relname AS rt, ra.attname AS rc
            FROM pg_constraint con
            JOIN pg_class cl ON cl.oid = con.conrelid JOIN pg_namespace ns ON ns.oid = cl.relnamespace
            JOIN pg_class rcl ON rcl.oid = con.confrelid JOIN pg_namespace rns ON rns.oid = rcl.relnamespace
            CROSS JOIN LATERAL unnest(con.conkey, con.confkey) AS k(col, rcol)
            JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.col
            JOIN pg_attribute ra ON ra.attrelid = con.confrelid AND ra.attnum = k.rcol
            WHERE con.contype = 'f' AND ns.nspname = :s AND rns.nspname = :s""", {"s": schema}):
        if r["t"] in cat:
            cat[r["t"]]["fks"].append((r["c"], r["rt"], r["rc"]))
    return cat


def schema_fingerprint(cat: dict) -> str:
    parts = [t + ":" + ",".join(c + "/" + m["types"][c] for c in m["columns"]) + "|" + str(sorted(m["fks"]))
             for t, m in sorted(cat.items())]
    return hashlib.sha256(("v" + BUILDER_VERSION + "\n" + "\n".join(parts)).encode()).hexdigest()[:16]


class Profiler:
    def __init__(self, session, schema):
        self.s, self.schema = session, schema
        self._unique: dict = {}

    async def q(self, sql: str, params: dict | None = None) -> list[dict]:
        try:
            async with self.s.begin_nested():
                return await _fetch(self.s, sql, params)
        except Exception as e:  # noqa: BLE001 - a profiling query that fails costs one column
            log.warning("ontology.profile_query_failed", error=str(e).splitlines()[0][:200])
            return []

    def _t(self, t):
        return qi(self.schema) + "." + qi(t)

    async def distinct_sample(self, t, c, limit=SAMPLE_ROWS):
        rows = await self.q(f"SELECT DISTINCT v FROM (SELECT {qi(c)}::text AS v FROM {self._t(t)} "
                            f"WHERE {qi(c)} IS NOT NULL LIMIT {int(limit)}) x")
        return [r["v"] for r in rows]

    async def value_counts(self, t, c):
        rows = await self.q(f"SELECT {qi(c)}::text AS v, count(*) AS n FROM {self._t(t)} WHERE {qi(c)} IS NOT NULL "
                            f"GROUP BY 1 ORDER BY n DESC LIMIT {MAX_CATEGORICAL + 1}")
        return [(r["v"], r["n"]) for r in rows]

    async def is_unique(self, t, c):
        if (t, c) not in self._unique:
            rows = await self.q(f"SELECT count({qi(c)}) = count(DISTINCT {qi(c)}) AS u FROM {self._t(t)}")
            self._unique[(t, c)] = bool(rows and rows[0]["u"])
        return self._unique[(t, c)]

    async def fill_ratio(self, t, c):
        rows = await self.q(f"SELECT count({qi(c)})::float / greatest(count(*), 1) AS r FROM {self._t(t)}")
        return round(rows[0]["r"], 3) if rows else 0.0

    async def containment(self, values, t, c):
        if not values:
            return 0.0
        rows = await self.q(f"SELECT count(DISTINCT {qi(c)}::text) AS n FROM {self._t(t)} "
                            f"WHERE {qi(c)}::text = ANY(CAST(:v AS text[]))", {"v": [str(v) for v in values]})
        return rows[0]["n"] / len(values) if rows else 0.0

    async def row_counts(self):
        rows = await self.q("SELECT c.relname AS t, c.reltuples::bigint AS est FROM pg_class c JOIN pg_namespace n "
                            "ON n.oid = c.relnamespace WHERE n.nspname = :s AND c.relkind = 'r'", {"s": self.schema})
        out = {}
        for r in rows:
            est = int(r["est"])
            if est <= 0:
                got = await self.q(f"SELECT count(*) AS n FROM {self._t(r['t'])}")
                est = got[0]["n"] if got else 0
            out[r["t"]] = est
        return out


def _table_descriptions() -> dict:
    try:
        with open(DESCRIPTIONS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


async def build_ontology(session, schema=BASE_SCHEMA):
    """Read the schema + data and return (ontology_dict, report_lines)."""
    cat = await load_catalog(session, schema)
    prof = Profiler(session, schema)
    rows = await prof.row_counts()
    usage = _table_descriptions()
    report = defaultdict(list)

    area_of = {}
    for area, tables in AREAS.items():
        for t in tables:
            if matches_any(t, EXCLUDE):
                report["excluded"].append(t)
            elif t not in cat:
                report["missing"].append(t)
            elif SKIP_EMPTY_TABLES and rows.get(t, 0) == 0:
                report["empty (skipped)"].append(t)
            elif len(cat[t]["pk"]) != 1:
                report["no single-column primary key (skipped)"].append(t)
            else:
                area_of[t] = area
    name_of = {}
    for t in area_of:
        n = CONCEPT_NAMES.get(t) or "".join(w.capitalize() for w in singular(t).split("_"))
        while n in name_of.values():
            n += "X"
        name_of[t] = n
    table_of = {n: t for t, n in name_of.items()}
    hubs = [t for t in HUB_ORDER if t in area_of]

    ref_names = defaultdict(set)
    last_word_count = defaultdict(int)
    for t in area_of:
        last_word_count[singular(t).split("_")[-1]] += 1
    for t in area_of:
        pk = cat[t]["pk"][0]
        words = {singular(t), snake(name_of[t])}
        lw = singular(t).split("_")[-1]
        if last_word_count[lw] == 1:
            words.add(lw)
        for w in words:
            ref_names[t] |= ({w + "_id", w + "_uuid"} if pk in ("id", "uuid") else {w + "_" + pk, pk})
            ref_names[t] |= {w + "_code", w + "_tag", w + "_number", w + "_no", w + "_ref"}

    def is_reference(t, c):
        if c in {fc for fc, _, _ in cat[t]["fks"]}:
            return True
        base = col_base(c)
        own = {singular(t), snake(name_of[t]), singular(t).split("_")[-1]}
        if base in own:
            return False
        return any(c in ref_names[o] or base in (singular(o), snake(name_of[o])) for o in area_of if o != t)

    # ---- profile columns ------------------------------------------------------------------
    prof_info = {}
    for t in sorted(area_of):
        m = cat[t]
        types = m["types"]
        pk = m["pk"][0]
        usable = [c for c in m["columns"] if types[c] not in SKIP_TYPES and not SKIP_NAME_RE.search(c)
                  and _IDENT_RE.match(c)]
        identifiers, categorical, code_like = [], {}, {}
        for c in usable:
            if types[c] not in TEXT_TYPES:
                continue
            vals = await prof.distinct_sample(t, c)
            if not vals:
                continue
            code_like[c] = sum(1 for v in vals if is_identifier(v)) / len(vals) >= CODE_RATIO
            if code_like[c]:
                if not is_reference(t, c) and (len(vals) > MAX_CATEGORICAL or await prof.is_unique(t, c)):
                    identifiers.append(c)
            # Only a status/type/category-like column's values reach the model. The script also
            # took any text column with twelve values or fewer, which on a shared database puts
            # one company's names (vendors, buildings) into another company's prompt.
            elif len(vals) <= MAX_CATEGORICAL and CATEGORICAL_RE.search(c):
                counts = await prof.value_counts(t, c)
                if 0 < len(counts) <= MAX_CATEGORICAL:
                    categorical[c] = counts
        label = pk
        for pat in LABEL_ORDER:
            hit = next((c for c in usable if re.search(pat, c, re.I) and types[c] in TEXT_TYPES
                        and not is_reference(t, c)), None)
            if hit:
                label = hit
                break
        prof_info[t] = {"usable": usable, "identifiers": identifiers, "categorical": categorical,
                        "code_like": code_like, "label": label}

    # ---- links -------------------------------------------------------------------------------
    links = {}

    async def owner_rank(t, c):
        if c in cat[t]["pk"]:
            return 3
        pi = prof_info[t]
        if is_reference(t, c) or not pi["code_like"].get(c) or not await prof.is_unique(t, c):
            return 0
        if t in hubs:
            return 2
        return 1 if (c in pi["identifiers"] or c == pi["label"]) else 0

    for t in area_of:
        for c, rt, rc in cat[t]["fks"]:
            if rt in area_of and not ORG_COLUMN_RE.match(c):
                links[(t, c)] = (rt, rc, "foreign key")
    for t in area_of:
        for c in cat[t]["columns"]:
            if (t, c) in links or c in cat[t]["pk"] or ORG_COLUMN_RE.match(c) or not _IDENT_RE.match(c):
                continue
            for o in area_of:
                if o == t or c not in ref_names[o]:
                    continue
                is_code_ref = re.search(r"_(code|tag|number|no|ref)$", c) is not None
                opk = next((x for x in prof_info[o]["identifiers"]), cat[o]["pk"][0]) if is_code_ref else cat[o]["pk"][0]
                vals = await prof.distinct_sample(t, c, 500)
                if vals and await prof.containment(vals, o, opk) >= 0.9:
                    links[(t, c)] = (o, opk, "naming convention, verified on data")
                    break
    code_cols = {}
    for t in area_of:
        for c, is_code in prof_info[t]["code_like"].items():
            if is_code and (t, c) not in links and not ORG_COLUMN_RE.match(c):
                code_cols[(t, c)] = set(await prof.distinct_sample(t, c))
    best = {}
    keys = sorted(code_cols)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if a[0] == b[0]:
                continue
            shared = len(code_cols[a] & code_cols[b])
            if shared < 2 and not (shared and min(len(code_cols[a]), len(code_cols[b])) == 1):
                continue
            if shared / max(1, min(len(code_cols[a]), len(code_cols[b]))) < 0.2:
                continue
            ra, rb = await owner_rank(*a), await owner_rank(*b)
            if ra == rb:
                continue
            ref, owner, rank = (a, b, rb) if rb > ra else (b, a, ra)
            if ref not in best or rank > best[ref][1]:
                best[ref] = (owner, rank)
    for ref, (owner, _) in best.items():
        if owner not in best and ref not in links:
            links[ref] = (owner[0], owner[1], "shared codes")

    # ---- concepts ------------------------------------------------------------------------------
    concepts = {}
    for t, name in sorted(name_of.items(), key=lambda x: x[1]):
        m, pi = cat[t], prof_info[t]
        types, pk = m["types"], m["pk"][0]
        dates = [c for c in pi["usable"] if types[c] in DATE_TYPES]
        prio = [pk, pi["label"]] + pi["identifiers"] + list(pi["categorical"]) + dates + \
               [c for c in pi["usable"] if types[c] in NUMERIC_TYPES and not c.endswith("_id")] + \
               [c for c in pi["usable"] if not (c.endswith("_id") or c.endswith("_uuid"))]
        attrs = []
        for c in prio:
            if c not in attrs:
                attrs.append(c)
        attrs = attrs[:MAX_ATTRIBUTES]

        states = {}
        for c, counts in pi["categorical"].items():
            if not STATE_COL_RE.search(c):
                continue
            values = [v for v, _ in counts]
            first = not any(s.get("attribute") == c for s in states.values())
            prefix = "" if not states else col_base(c) + "_"
            closed = [v for v in values if re.search(CLOSED_VALUES_RE, v, re.I)]
            blocked = [v for v in values if re.search(BLOCKED_VALUES_RE, v, re.I)]
            if first and not prefix:
                if closed and len(closed) < len(values):
                    not_in = sorted(set(closed) | set(CANONICAL_CLOSED))
                    states["open"] = {"description": "not finished (" + c + " not in " + ", ".join(closed) + ")",
                                      "attribute": c, "not_in": not_in}
                    states["closed"] = {"description": "finished (" + c + " in " + ", ".join(closed) + ")",
                                        "attribute": c, "in": closed}
                if blocked:
                    states["blocked"] = {"description": "blocked / suspended / on hold (" + c + " in "
                                                        + ", ".join(blocked) + ")", "attribute": c, "in": blocked}
            for v, n in counts:
                s = prefix + slug(v)
                if s not in states:
                    states[s] = {"description": c + " = " + v + " (" + str(n) + " rows)", "attribute": c, "in": [v]}
        blocked_leaves, lapsed_leaves = [], []
        for c, counts in pi["categorical"].items():
            if not STATE_LIKE_COL_RE.search(c):
                continue
            values = [v for v, _ in counts]
            b = [v for v in values if re.search(BLOCKED_VALUES_RE, v, re.I) or re.search(BLOCKED_VALUES_RE_EXTRA, v, re.I)]
            lp = [v for v in values if re.search(LAPSED_VALUES_RE, v, re.I)]
            if b:
                blocked_leaves.append({"attribute": c, "in": b})
            if lp:
                lapsed_leaves.append({"attribute": c, "in": lp})
        if blocked_leaves:
            states["blocked"] = {"description": "blocked / suspended / failed ("
                                 + "; ".join(x["attribute"] + " in " + ", ".join(x["in"]) for x in blocked_leaves) + ")",
                                 "any": blocked_leaves}
        expiry = next((c for c in dates if EXPIRY_RE.search(c)), None)
        if expiry and (blocked_leaves or lapsed_leaves):
            states["blocking"] = {"description": "lapsed, expired or blocked - dependent work cannot proceed ("
                                  + "; ".join(x["attribute"] + " in " + ", ".join(x["in"]) for x in blocked_leaves + lapsed_leaves)
                                  + "; or " + expiry + " passed). USE THIS when the user asks which certificates block / affect work",
                                  "any": blocked_leaves + lapsed_leaves + [{"attribute": expiry, "lt": "today"}]}
        for c in dates:
            if EXPIRY_RE.search(c) and "expired" not in states:
                states["expired"] = {"description": c + " has passed", "attribute": c, "lt": "today"}
                states["expiring_soon"] = {"description": c + " within 60 days", "attribute": c,
                                           "between": ["today", "today+60d"]}
            if DUE_RE.search(c) and "overdue" not in states:
                done = next((d for d in dates if DONE_RE.search(d)), None)
                if done:
                    states["overdue"] = {"description": c + " passed and " + done + " empty",
                                         "all": [{"attribute": c, "lt": "today"}, {"attribute": done, "is_null": True}]}

        def state_leaves(cond):
            if "attribute" in cond:
                yield cond
            for sub in cond.get("all", []) + cond.get("any", []):
                yield from state_leaves(sub)
        for s in states.values():
            for leaf in state_leaves(s):
                if leaf["attribute"] not in attrs:
                    attrs.append(leaf["attribute"])

        u = usage.get(t, {})
        desc = str(u.get("purpose") or ("Table " + t))[:300]
        if u.get("grain"):
            desc += " One row is " + str(u["grain"])[:150] + "."
        if u.get("answers"):
            desc += " Answers: " + str(u["answers"])[:200]
        search = [pi["label"]] + [c for c in pi["identifiers"] if c != pi["label"]]
        search += [c for c in pi["usable"] if types[c] in TEXT_TYPES and not is_reference(t, c)
                   and re.search(r"name|title|description|subject|summary", c, re.I) and c not in search]
        concept = {
            "table": t, "key": pk, "label": pi["label"],
            "description": desc,
            "synonyms": sorted({singular(t).replace("_", " "), t.replace("_", " ")} | set(SYNONYMS.get(t, []))),
            "area": area_of[t],
        }
        if t in hubs:
            concept["hub"] = True
        if pi["identifiers"]:
            concept["identifiers"] = pi["identifiers"]
        concept["search"] = search[:6]
        concept["attributes"] = {c: c for c in attrs}
        vals = {c: [v for v, _ in counts] for c, counts in pi["categorical"].items() if c in attrs}
        if vals:
            concept["values"] = vals
        if states:
            concept["states"] = states
        sort_col = next((c for c in dates if SORT_RE.search(c)), dates[0] if dates else None)
        if sort_col:
            concept["default_sort"] = {"attribute": sort_col, "dir": "desc"}
        concepts[name] = concept

    # ---- relations -----------------------------------------------------------------------------
    relations = {}
    for (ft, fc), (tt, tc, kind) in sorted(links.items()):
        base = col_base(fc)
        tgt = snake(name_of[tt])
        rname = (snake(name_of[ft]) + "_" + (tgt if base in (singular(tt), tgt, tt, "id") else base)).upper()
        n, cand = 2, rname
        while cand in relations:
            cand, n = rname + "_" + str(n), n + 1
        many_to_one = tc in cat[tt]["pk"] or await prof.is_unique(tt, tc)
        relations[cand] = {"from": name_of[ft], "to": name_of[tt],
                           "cardinality": "many_to_one" if many_to_one else "many_to_many",
                           "join": ["from." + fc + " = to." + tc],
                           "fill": await prof.fill_ratio(ft, fc),
                           "description": name_of[ft] + " -> " + name_of[tt] + " (" + ft + "." + fc + " = "
                                          + tt + "." + tc + ", " + kind + ")"}

    # ---- business rules ------------------------------------------------------------------------
    derived = {}

    def up_path(start, target):
        frontier, seen = [(start, [])], {start}
        while frontier:
            nxt = []
            for c, path in frontier:
                if c == target:
                    return path
                if len(path) >= 4:
                    continue
                for rn, r in sorted(relations.items(), key=lambda x: -x[1].get("fill", 0)):
                    if r["from"] == c and r["cardinality"] == "many_to_one" and r["to"] not in seen:
                        seen.add(r["to"])
                        nxt.append((r["to"], path + [rn]))
            frontier = nxt
        return None

    cert, wo = name_of.get(CERT_TABLE), name_of.get(WORK_ORDER_TABLE)
    if cert and wo:
        ct = table_of[cert]
        scope_col, scope_map = None, {}
        for c, counts in prof_info[ct]["categorical"].items():
            mp = {}
            for v, _ in counts:
                tgt = SCOPE_WORDS.get(slug(v))
                if tgt in area_of:
                    mp.setdefault(tgt, []).append(v)
            if len(mp) >= 2:
                scope_col, scope_map = c, mp
                break
        if scope_map:
            order = [t for t in HUB_ORDER if t in scope_map] + [t for t in scope_map if t not in HUB_ORDER]
            targets = [(t, scope_map[t]) for t in order]
        else:
            targets = [(h, None) for h in hubs]

        def paths_to(start, target):
            direct = [[rn] for rn, r in sorted(relations.items(), key=lambda x: -x[1].get("fill", 0))
                      if r["from"] == start and r["to"] == target and r["cardinality"] == "many_to_one"
                      and r.get("fill", 0) > 0]
            if direct:
                return direct[:3]
            p = up_path(start, target)
            return [p] if p else []

        alternatives, first_hops = [], set()
        for tbl, values in targets:
            hc = name_of[tbl]
            p1s, p2s = paths_to(cert, hc), paths_to(wo, hc)
            if not p1s or not p2s:
                if values:
                    report["rules"].append("BLOCKS: no link from " + cert + " and " + wo + " to " + hc
                                           + " for " + str(scope_col) + " = " + "/".join(values))
                continue
            if not values and all(p1[0] in first_hops for p1 in p1s):
                continue
            what = snake(hc).replace("_", " ")
            base_label = "work orders assigned to the same " + what if tbl == "vendors" else "same " + what
            for p1 in p1s:
                first_hops.add(p1[0])
                for p2 in p2s:
                    alt = {"via": p1 + list(reversed(p2)), "label": base_label}
                    if values:
                        alt["when"] = {"attribute": scope_col, "in": values}
                        alt["label"] += " (" + scope_col + " = " + "/".join(values) + ")"
                    alternatives.append(alt)
        if alternatives:
            labels = list(dict.fromkeys(a["label"] for a in alternatives))
            desc = ("a lapsed / expired / blocked compliance certificate stops OPEN work orders: "
                    + "; ".join(labels)
                    + ". Use for 'which work orders are affected / blocked by certificates'")
            rule = {"from": cert, "to": wo, "via": alternatives[0]["via"], "alternatives": alternatives,
                    "description": desc}
            st = {}
            cert_states = concepts[cert].get("states", {})
            if "blocking" in cert_states:
                st["from"] = ["blocking"]
            elif "blocked" in cert_states:
                st["from"] = ["blocked"]
            if "open" in concepts[wo].get("states", {}):
                st["to"] = ["open"]
            if st:
                rule["states"] = st
            derived["BLOCKS"] = rule
            rel = dict(rule)
            rel.pop("states", None)
            rel["description"] = "work orders linked to a certificate (any status): " + "; ".join(labels)
            derived["CERTIFICATE_WORK"] = rel
        else:
            report["rules"].append("BLOCKS not created: no shared asset/building/vendor path between " + cert + " and " + wo)

    ontology = {"schema": schema, "fingerprint": schema_fingerprint(cat),
                "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "concepts": concepts, "relations": relations, "derived": derived}
    lines = ["Concepts: %d | relations: %d | rules: %s" % (len(concepts), len(relations), ", ".join(derived) or "none")]
    for k in ("empty (skipped)", "missing", "no single-column primary key (skipped)", "rules"):
        if report[k]:
            lines.append(k + ": " + ", ".join(report[k]))
    return ontology, lines


def deep_merge(base, over):
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        elif v is None:
            base.pop(k, None)
        else:
            base[k] = v
    return base


# ---------------------------------------------------------------------------
# Ontology model
# ---------------------------------------------------------------------------
class Concept:
    def __init__(self, name, d):
        self.name = name
        self.table = d["table"]
        self.key = d["key"]
        self.label = d.get("label", self.key)
        self.description = d.get("description", "")
        self.synonyms = d.get("synonyms", []) or []
        self.identifiers = d.get("identifiers", []) or []
        self.search = d.get("search", []) or [self.label]
        self.attributes = dict(d.get("attributes", {}) or {})
        self.states = d.get("states", {}) or {}
        self.default_sort = d.get("default_sort")
        self.hub = bool(d.get("hub", False))
        self.values = d.get("values", {}) or {}

    def col(self, attr):
        if attr in self.attributes:
            return self.attributes[attr]
        if attr in self.attributes.values() or attr in (self.key, self.label):
            return attr
        raise OntologyError(self.name + " has no attribute '" + str(attr) + "'. Valid: " + ", ".join(self.attributes))

    def columns_used(self):
        cols = {self.key, self.label, *self.identifiers, *self.search, *self.attributes.values()}
        return {c for c in cols if c}


class Relation:
    JOIN_RE = re.compile(r"\s*(from|to)\.(\w+)\s*=\s*(from|to)\.(\w+)\s*$")

    def __init__(self, name, d):
        self.name = name
        self.from_ = d["from"]
        self.to = d["to"]
        self.cardinality = d.get("cardinality", "many_to_one")
        self.description = d.get("description", "")
        self.fill = float(d.get("fill") or 0)          # share of rows whose link column is set
        self.join = []
        for j in d["join"]:
            m = self.JOIN_RE.match(j)
            if not m or {m.group(1), m.group(3)} != {"from", "to"}:
                raise OntologyError("Relation " + name + ": join must look like 'from.col = to.col', got: " + j)
            pairs = {m.group(1): m.group(2), m.group(3): m.group(4)}
            self.join.append((pairs["from"], pairs["to"]))


class Derived:
    def __init__(self, name, d):
        self.name = name
        self.from_ = d["from"]
        self.to = d["to"]
        self.alternatives = [{"via": list(a["via"]), "label": a.get("label", ""), "when": a.get("when")}
                             for a in d.get("alternatives") or []]
        self.via = list(d.get("via") or (self.alternatives[0]["via"] if self.alternatives else []))
        if not self.alternatives:
            self.alternatives = [{"via": self.via, "label": ""}]
        self.states = d.get("states", {}) or {}
        self.description = d.get("description", "")


class Ontology:
    def __init__(self, d, source="ontology"):
        self.path = source
        self.schema = d["schema"]
        self.concepts = {n: Concept(n, c) for n, c in (d.get("concepts") or {}).items()}
        self.relations = {n: Relation(n, r) for n, r in (d.get("relations") or {}).items()}
        self.derived = {n: Derived(n, r) for n, r in (d.get("derived") or {}).items()}
        self.col_types: dict = {}
        self._check_internal()

    def _check_internal(self):
        errors = []
        for r in self.relations.values():
            for c in (r.from_, r.to):
                if c not in self.concepts:
                    errors.append("Relation " + r.name + " uses unknown concept " + c)
        for dr in self.derived.values():
            if dr.name in self.relations:
                errors.append("Derived " + dr.name + " clashes with a relation name")
            for alt in dr.alternatives:
                try:
                    _, _, end = self.expand(dr.from_, alt["via"], allow_derived=False)
                    if end != dr.to:
                        errors.append("Derived " + dr.name + ": via chain ends at " + end + ", not " + dr.to)
                except OntologyError as e:
                    errors.append("Derived " + dr.name + ": " + str(e))
            for side, concept in (("from", dr.from_), ("to", dr.to)):
                for s in dr.states.get(side, []) or []:
                    if s not in self.concepts.get(concept, Concept("x", {"table": "x", "key": "x"})).states:
                        errors.append("Derived " + dr.name + ": unknown state " + s + " on " + concept)
        for c in self.concepts.values():
            for sname, cond in c.states.items():
                try:
                    self._walk_condition(c, cond)
                except OntologyError as e:
                    errors.append(c.name + ".states." + sname + ": " + str(e))
        if errors:
            raise OntologyError("Ontology problems in " + self.path + ":\n  - " + "\n  - ".join(errors))

    def _walk_condition(self, concept, cond):
        if "all" in cond or "any" in cond:
            for c in cond.get("all", []) + cond.get("any", []):
                self._walk_condition(concept, c)
            return
        concept.col(cond.get("attribute"))
        ops = [k for k in cond if k in OPS]
        if len(ops) != 1:
            raise OntologyError("condition needs exactly one operator from " + ", ".join(sorted(OPS)) + ": " + str(cond))

    async def bind(self, session, schema: str):
        """Keep the concepts whose columns all exist in `schema` (the scoped views), and record types.

        The views are generated from the tables by a migration; a column added since is missing
        from its view until the migration runs again, and a query naming it would fail. A
        concept like that is dropped with a log line rather than failing every question."""
        rows = await _fetch(session, "SELECT table_name, column_name, data_type FROM information_schema.columns "
                                     "WHERE table_schema = :s", {"s": schema})
        self.col_types = {(r["table_name"], r["column_name"]): r["data_type"] for r in rows}
        tables = {t for t, _ in self.col_types}
        dropped = []
        for c in list(self.concepts.values()):
            missing = [col for col in c.columns_used() if (c.table, col) not in self.col_types]
            if c.table not in tables or missing:
                dropped.append(c.name + (" (no view)" if c.table not in tables else " (missing " + ", ".join(missing) + ")"))
                del self.concepts[c.name]
        for n, r in list(self.relations.items()):
            ok = r.from_ in self.concepts and r.to in self.concepts and all(
                (self.concepts[r.from_].table, fc) in self.col_types and (self.concepts[r.to].table, tc) in self.col_types
                for fc, tc in r.join)
            if not ok:
                del self.relations[n]
        for n, d in list(self.derived.items()):
            try:
                for alt in d.alternatives:
                    self.expand(d.from_, alt["via"], allow_derived=False)
            except (OntologyError, KeyError):
                del self.derived[n]
        if dropped:
            log.warning("ontology.concepts_dropped", schema=schema, dropped=dropped)
        self.schema = schema

    # ---- paths -------------------------------------------------------------------------------
    def _traverse(self, rel, current):
        if rel.from_ == current:
            return (rel, True), rel.to
        if rel.to == current:
            return (rel, False), rel.from_
        raise OntologyError(rel.name + " connects " + rel.from_ + " -> " + rel.to + ", not " + current)

    def expand(self, start, via, allow_derived=True):
        hops, reqs, current = [], [], start
        for name in via:
            if name in self.relations:
                hop, current = self._traverse(self.relations[name], current)
                hops.append(hop)
            elif allow_derived and name in self.derived:
                dr = self.derived[name]
                if current == dr.from_:
                    chain, s_start, s_end = dr.via, dr.states.get("from"), dr.states.get("to")
                elif current == dr.to:
                    chain, s_start, s_end = list(reversed(dr.via)), dr.states.get("to"), dr.states.get("from")
                else:
                    raise OntologyError(name + " connects " + dr.from_ + " -> " + dr.to + ", not " + current)
                start_concept = current
                if s_start:
                    reqs.append((len(hops), start_concept, list(s_start)))
                for base in chain:
                    hop, current = self._traverse(self.relations[base], current)
                    hops.append(hop)
                if s_end:
                    reqs.append((len(hops), current, list(s_end)))
            else:
                raise OntologyError("Unknown relation '" + str(name) + "'")
        return hops, reqs, current

    @staticmethod
    def _direction(hop):
        rel, forward = hop
        if rel.cardinality == "many_to_one":
            return "up" if forward else "down"
        if rel.cardinality == "one_to_many":
            return "down" if forward else "up"
        return "peer"

    def auto_path(self, a, b):
        """Shortest path a -> b. A sibling join (up to a parent, down to its other children) turns
        only at a hub (same site / same equipment), never at a non-hub such as a vendor."""
        if a == b:
            return [], []
        edges = defaultdict(list)
        # Fullest link first. Two relations can join the same pair - work_orders reaches vendors
        # by assigned_vendor and by vendor_id - and the search takes the first it meets, so it
        # took assigned_vendor, empty on every row, and every work-order-by-vendor question
        # answered nothing (1 Oct 2026). An empty link is used only where it is the only one.
        for r in sorted(self.relations.values(), key=lambda r: -r.fill):
            edges[r.from_].append(([r.name], r.to, [self._direction((r, True))]))
            edges[r.to].append(([r.name], r.from_, [self._direction((r, False))]))
        for dr in self.derived.values():
            if not dr.states:
                edges[dr.from_].append(([dr.name], dr.to, ["peer"]))
                edges[dr.to].append(([dr.name], dr.from_, ["peer"]))
        start = (a, False)
        prev, frontier, depth = {start: None}, [start], 0
        while frontier and depth < MAX_PATH_HOPS:
            depth += 1
            nxt = []
            for state in frontier:
                concept, went_up = state
                for names, other, dirs in edges[concept]:
                    up = went_up
                    ok = True
                    for d in dirs:
                        if d == "down" and up:
                            if not self.concepts[concept].hub:
                                ok = False
                            up = False
                        up = up or d == "up"
                    if not ok:
                        continue
                    ns = (other, up)
                    if ns in prev:
                        continue
                    prev[ns] = (state, names)
                    if other == b:
                        via, cur = [], ns
                        while prev[cur]:
                            via = prev[cur][1] + via
                            cur = prev[cur][0]
                        hops, reqs, _ = self.expand(a, via)
                        return hops, reqs
                    nxt.append(ns)
            frontier = nxt
        raise OntologyError("No automatic path from " + a + " to " + b + ". Give 'via' explicitly" + self._via_hint(a, b))

    def _via_hint(self, a, b):
        hints = [n for n, d in self.derived.items() if {d.from_, d.to} == {a, b}]
        hints += [n for n, r in self.relations.items() if {r.from_, r.to} == {a, b}]
        return (", e.g. [" + "] or [".join(hints) + "]") if hints else " as a chain of relation names"

    def derived_variants(self, start, name):
        dr = self.derived[name]
        if start == dr.from_:
            s_start, s_end, rev = dr.states.get("from"), dr.states.get("to"), False
        elif start == dr.to:
            s_start, s_end, rev = dr.states.get("to"), dr.states.get("from"), True
        else:
            raise OntologyError(name + " connects " + dr.from_ + " -> " + dr.to + ", not " + start)
        out = []
        for alt in dr.alternatives:
            chain = list(reversed(alt["via"])) if rev else alt["via"]
            hops, _, end = self.expand(start, chain, allow_derived=False)
            reqs = []
            if s_start:
                reqs.append((0, start, list(s_start)))
            if s_end:
                reqs.append((len(hops), end, list(s_end)))
            when = None
            if alt.get("when"):
                when = (len(hops) if rev else 0, alt["when"])
            out.append((hops, reqs, alt["label"] or name, when))
        return out

    def resolve_path(self, a, b, via):
        if via:
            hops, reqs, end = self.expand(a, via)
            if end != b:
                raise OntologyError("via " + str(via) + " from " + a + " ends at " + end + ", not " + b)
            return hops, reqs
        return self.auto_path(a, b)

    # ---- LLM view ----------------------------------------------------------------------------
    def relevant_concepts(self, question):
        qwords = set(re.findall(r"[a-z0-9]+", question.lower()))
        hit = set()
        for c in self.concepts.values():
            for n in [c.name, c.table] + c.synonyms:
                words = set(re.findall(r"[a-z0-9]+", re.sub(r"(?<!^)(?=[A-Z])", " ", n).lower()))
                sing = {w[:-1] if w.endswith("s") else w for w in words}
                if words and (words <= qwords or sing <= {w[:-1] if w.endswith("s") else w for w in qwords}):
                    hit.add(c.name)
        keep = set(hit) | {c.name for c in self.concepts.values() if c.hub}
        for r in self.relations.values():
            if r.from_ in hit or r.to in hit:
                keep |= {r.from_, r.to}
        for d in self.derived.values():
            if d.from_ in hit or d.to in hit:
                keep |= {d.from_, d.to}
        return keep

    def describe_for_llm(self, question=None, max_full=40):
        keep = set(self.concepts)
        if question and len(self.concepts) > max_full:
            keep = self.relevant_concepts(question)
        lines = ["CONCEPTS (name: description | synonyms | attributes [known values] | states)"]
        for c in self.concepts.values():
            if c.name not in keep:
                continue
            attrs = []
            for a in c.attributes:
                v = c.values.get(a)
                attrs.append(a + (" [" + "|".join(map(str, v[:15])) + "]" if v else ""))
            states = "; ".join(s + " = " + (v.get("description") or "") for s, v in c.states.items())
            lines.append("- " + c.name + ": " + c.description
                         + " | synonyms: " + ", ".join(c.synonyms)
                         + " | attributes: " + ", ".join(attrs)
                         + (" | states: " + states if states else "")
                         + (" | codes in: " + ", ".join(c.identifiers) if c.identifiers else ""))
        others = sorted(set(self.concepts) - keep)
        if others:
            lines.append("(other concepts, ask for them by name if needed: " + ", ".join(others) + ")")
        lines.append("\nRELATIONS (name: from -> to, meaning)")
        for r in self.relations.values():
            if r.from_ in keep and r.to in keep:
                lines.append("- " + r.name + ": " + r.from_ + " -> " + r.to + ", " + r.description)
        if self.derived:
            lines.append("\nDERIVED BUSINESS RULES (usable in via like a relation)")
            for d in self.derived.values():
                if d.from_ in keep and d.to in keep:
                    st = ""
                    if d.states:
                        st = " [only " + ", ".join(k + " " + "/".join(v) for k, v in d.states.items()) + "]"
                    alts = (" | links: " + "; ".join(dict.fromkeys(a["label"] for a in d.alternatives if a["label"]))
                            if len(d.alternatives) > 1 else "")
                    lines.append("- " + d.name + ": " + d.from_ + " -> " + d.to + ", " + d.description + st + alts)
        return "\n".join(lines)

    def explain_state(self, concept, state):
        cond = self.concepts[concept].states[state]

        def fmt(c):
            if "all" in c:
                return "(" + " AND ".join(fmt(x) for x in c["all"]) + ")"
            if "any" in c:
                return "(" + " OR ".join(fmt(x) for x in c["any"]) + ")"
            op = next(k for k in c if k in OPS)
            return c["attribute"] + " " + op + " " + json.dumps(c[op], default=str)
        return concept + "." + state + " := " + fmt({k: v for k, v in cond.items() if k != "description"})


# ---------------------------------------------------------------------------
# Building and caching the ontology
# ---------------------------------------------------------------------------
_ONTOLOGY: dict[str, Ontology] = {}
_BUILD_LOCK = asyncio.Lock()


def _cache_path(fp: str) -> str:
    return os.path.join(tempfile.gettempdir(), "cafm_ontology_" + fp + ".json")


def _session_factory():
    from ..database import _get_session_factory
    return _get_session_factory()


async def get_ontology() -> Ontology:
    """The ontology for the database this service reads, built once per schema fingerprint."""
    factory = _session_factory()
    async with factory() as s:
        async with s.begin():
            await s.execute(text("SET TRANSACTION READ ONLY"))
            cat = await load_catalog(s, BASE_SCHEMA)
    fp = schema_fingerprint(cat)
    if fp in _ONTOLOGY:
        return _ONTOLOGY[fp]
    async with _BUILD_LOCK:
        if fp in _ONTOLOGY:
            return _ONTOLOGY[fp]
        data = None
        try:
            with open(_cache_path(fp), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = None
        if data is None:
            async with factory() as s:
                async with s.begin():
                    await s.execute(text("SET TRANSACTION READ ONLY"))
                    await s.execute(text("SET LOCAL statement_timeout = 60000"))
                    data, lines = await build_ontology(s, BASE_SCHEMA)
            log.info("ontology.built", fingerprint=fp, report=lines)
            try:
                with open(_cache_path(fp), "w", encoding="utf-8") as f:
                    json.dump(data, f, default=str)
            except OSError:
                pass
        over = os.environ.get("ONTOLOGY_OVERRIDES")
        if over and os.path.exists(over):
            with open(over, encoding="utf-8") as f:
                deep_merge(data, json.load(f))
        onto = Ontology(data, source="ontology " + fp)
        async with factory() as s:
            async with s.begin():
                await s.execute(text("SET TRANSACTION READ ONLY"))
                await onto.bind(s, SCOPED_SCHEMA)
        links = add_certificate_links(onto)
        log.info("ontology.certificate_links", rules=links)
        _ONTOLOGY[fp] = onto
        return onto


# ---------------------------------------------------------------------------
# SQL compilation
# ---------------------------------------------------------------------------
#: Calendar periods a filter may name: (start, end) as SQL, end exclusive. "last month" asked on
#: 1 Oct is 1 Sep 00:00 up to 1 Oct 00:00 - every timestamp in September, whatever the column type.
_M, _W, _Q, _Y = ("date_trunc('month', CURRENT_DATE)", "date_trunc('week', CURRENT_DATE)",
                  "date_trunc('quarter', CURRENT_DATE)", "date_trunc('year', CURRENT_DATE)")
PERIODS = {
    "today": ("CURRENT_DATE", "(CURRENT_DATE + INTERVAL '1 day')"),
    "yesterday": ("(CURRENT_DATE - INTERVAL '1 day')", "CURRENT_DATE"),
    "this_week": (_W, "(" + _W + " + INTERVAL '1 week')"),
    "last_week": ("(" + _W + " - INTERVAL '1 week')", _W),
    "this_month": (_M, "(" + _M + " + INTERVAL '1 month')"),
    "last_month": ("(" + _M + " - INTERVAL '1 month')", _M),
    "next_month": ("(" + _M + " + INTERVAL '1 month')", "(" + _M + " + INTERVAL '2 month')"),
    "this_quarter": (_Q, "(" + _Q + " + INTERVAL '3 month')"),
    "last_quarter": ("(" + _Q + " - INTERVAL '3 month')", _Q),
    "this_year": (_Y, "(" + _Y + " + INTERVAL '1 year')"),
    "last_year": ("(" + _Y + " - INTERVAL '1 year')", _Y),
    "last_7_days": ("(CURRENT_DATE - INTERVAL '7 day')", "(CURRENT_DATE + INTERVAL '1 day')"),
    "last_30_days": ("(CURRENT_DATE - INTERVAL '30 day')", "(CURRENT_DATE + INTERVAL '1 day')"),
    "last_90_days": ("(CURRENT_DATE - INTERVAL '90 day')", "(CURRENT_DATE + INTERVAL '1 day')"),
    "year_to_date": (_Y, "(CURRENT_DATE + INTERVAL '1 day')"),
}


def period_of(value):
    """'last_month', 'Last month', 'last-month', 'start_of_last_month', 'last_month_end' -> (name, edge).
    edge is None for the whole period, 'start' or 'end' for one of its bounds."""
    if not isinstance(value, str):
        return None
    v = re.sub(r"[\s\-]+", "_", value.strip().lower())
    edge = None
    for pre, e in (("start_of_", "start"), ("beginning_of_", "start"), ("end_of_", "end")):
        if v.startswith(pre):
            v, edge = v[len(pre):], e
    for suf, e in (("_start", "start"), ("_begin", "start"), ("_end", "end")):
        if v.endswith(suf):
            v, edge = v[: -len(suf)], e
    return (v, edge) if v in PERIODS else None


def date_expr(value):
    per = period_of(value)
    if per:
        start, end = PERIODS[per[0]]
        # An inclusive end (lte, BETWEEN) stops just before the next period begins.
        return start if per[1] != "end" else "(" + end + " - INTERVAL '1 microsecond')"
    m = RELATIVE_DATE_RE.match(str(value).strip())
    if not m:
        return None
    if not m.group(1):
        return "CURRENT_DATE"
    n = int(m.group(2)) * {"d": 1, "w": 7, "m": 30, "y": 365}[m.group(3).lower()]
    return "(CURRENT_DATE " + ("+" if m.group(1) == "+" else "-") + " " + str(int(n)) + " * INTERVAL '1 day')"


def value_expr(value, p: Params, col_type: str = ""):
    d = date_expr(value) if isinstance(value, str) else None
    if d is not None:
        return d
    if isinstance(value, str) and ISO_DATE_RE.match(value):
        return p.add(value, "date")
    if col_type in NUMERIC_TYPES:
        try:
            return p.add(str(float(value)), "numeric")
        except (TypeError, ValueError):
            raise OntologyError("not a number: " + repr(value))
    if col_type == "boolean":
        return p.add(str(value).strip().lower() in ("true", "1", "yes", "t"), "boolean")
    if col_type in DATE_TYPES:
        return p.add(str(value), "timestamptz")
    return p.add(str(value))


def compile_condition(onto, concept, alias, cond, p: Params) -> str:
    if "all" in cond or "any" in cond:
        key = "all" if "all" in cond else "any"
        parts = [compile_condition(onto, concept, alias, c, p) for c in cond[key]]
        return "(" + (" AND " if key == "all" else " OR ").join(parts) + ")"
    col = concept.col(cond["attribute"])
    op = next(k for k in cond if k in OPS)
    v = cond[op]
    if op == "eq" and period_of(v) and not period_of(v)[1]:
        op = "within"                         # "raised_at eq last_month" means within it
    e = qi(alias) + "." + qi(col)
    et = "lower(" + e + "::text)"
    ctype = onto.col_types.get((concept.table, col), "")
    numeric_or_date = ctype not in ("text", "character varying", "character", "")
    if op in ("in", "not_in"):
        ph = p.add([str(x).lower() for x in (v if isinstance(v, list) else [v])], "text[]")
        if op == "in":
            return et + " = ANY(" + ph + ")"
        return "(" + e + " IS NULL OR " + et + " <> ALL(" + ph + "))"
    if op in ("eq", "ne"):
        sym = "=" if op == "eq" else "<>"
        if numeric_or_date:
            return e + " " + sym + " " + value_expr(v, p, ctype)
        return et + " " + sym + " " + p.add(str(v).lower())
    if op in ("lt", "lte", "gt", "gte"):
        sym = {"lt": "<", "lte": "<=", "gt": ">", "gte": ">="}[op]
        return e + " " + sym + " " + value_expr(v, p, ctype)
    if op == "within":
        per = period_of(v)
        if not per:
            raise OntologyError("within needs a period: " + ", ".join(PERIODS))
        start, end = PERIODS[per[0]]
        return "(" + e + " >= " + start + " AND " + e + " < " + end + ")"
    if op == "between":
        if not isinstance(v, list) or len(v) != 2:
            raise OntologyError("between needs [low, high]")
        a, b = period_of(v[0]), period_of(v[1])
        if a and b and a[0] == b[0] and not a[1] and not b[1]:      # ["last_month", "last_month"]
            start, end = PERIODS[a[0]]
            return "(" + e + " >= " + start + " AND " + e + " < " + end + ")"
        return e + " BETWEEN " + value_expr(v[0], p, ctype) + " AND " + value_expr(v[1], p, ctype)
    if op == "contains":
        return e + "::text ~* " + p.add(loose_pattern(str(v)))
    if op == "is_null":
        return e + (" IS NULL" if v in (True, "true", "True", 1) else " IS NOT NULL")
    raise OntologyError("Unsupported operator " + op)


def compile_states(onto, concept, alias, states, p):
    states = [s for s in states if s in concept.states]
    if not states:
        return None
    parts = [compile_condition(onto, concept, alias, {k: v for k, v in concept.states[s].items() if k != "description"}, p)
             for s in states]
    return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"


def compile_filter(onto, concept, alias, f, p):
    return compile_condition(onto, concept, alias, {"attribute": f["attribute"], f["op"]: f.get("value")}, p)


def _join_conds(onto, rel, fa, ta):
    conds = []
    ft, tt = onto.concepts[rel.from_].table, onto.concepts[rel.to].table
    for fc, tc in rel.join:
        left, right = qi(fa) + "." + qi(fc), qi(ta) + "." + qi(tc)
        if onto.col_types.get((ft, fc)) != onto.col_types.get((tt, tc)):
            left, right = left + "::text", right + "::text"
        conds.append(left + " = " + right)
    return conds


class QueryBuilder:
    def __init__(self, onto, focus_concept):
        self.onto = onto
        self.focus = onto.concepts[focus_concept]
        self.joins: list[str] = []
        self.n = 0

    def add_path(self, hops, start_alias="f", start_concept=None):
        start_concept = start_concept or self.focus.name
        nodes = [(start_alias, self.onto.concepts[start_concept])]
        for rel, forward in hops:
            prev_alias, _ = nodes[-1]
            nxt = self.onto.concepts[rel.to if forward else rel.from_]
            self.n += 1
            alias = "j" + str(self.n)
            fa, ta = (prev_alias, alias) if forward else (alias, prev_alias)
            self.joins.append(" JOIN " + qi(self.onto.schema) + "." + qi(nxt.table) + " AS " + qi(alias)
                              + " ON " + " AND ".join(_join_conds(self.onto, rel, fa, ta)))
            nodes.append((alias, nxt))
        return nodes

    def from_clause(self):
        return " FROM " + qi(self.onto.schema) + "." + qi(self.focus.table) + " AS f" + "".join(self.joins)


def attr_select(alias, concept, prefix=""):
    return [qi(alias) + "." + qi(col) + " AS " + qi(prefix + attr) for attr, col in concept.attributes.items()]


def sort_expr(alias, concept, sort):
    sort = sort or concept.default_sort
    if not sort:
        return "NULL", "asc"
    return qi(alias) + "." + qi(concept.col(sort["attribute"])), (sort.get("dir") or "desc").lower()


def key_condition(alias, column, keys, p: Params):
    return qi(alias) + "." + qi(column) + "::text = ANY(" + p.add([str(k) for k in keys], "text[]") + ")"


def where_sql(parts):
    return (" WHERE " + " AND ".join(parts)) if parts else ""


# ---------------------------------------------------------------------------
# Planning (LLM -> JSON plan -> validated)
# ---------------------------------------------------------------------------
PLAN_SCHEMA = """{
  "question_type": "single" | "list" | "count",
  "focus":       {"concept": C, "match": "name or code or null", "states": [..], "filters": [F..]},
  "constraints": [{"concept": C, "match": "name or code or null", "via": [relation names] or [], "states": [], "filters": []}],
  "include":     [{"name": "short_label", "concept": C, "via": [..] or [], "states": [], "filters": [],
                   "sort": {"attribute": A, "dir": "asc" | "desc"} or null, "limit": N}],
  "aggregate":   null | {"count_by": [{"concept": C, "attribute": A}]}
}
F = {"attribute": A, "op": "eq|ne|in|not_in|lt|lte|gt|gte|between|within|contains|is_null", "value": V}"""

PLAN_GUIDE = """How to plan:
- focus = the records the user wants listed, counted or explained.
- constraints = records that RESTRICT the focus (the site / building / asset / meter named in the question).
  Leave "via" empty to let the engine find the path, or name relations / derived rules.
- include = related records to fetch FOR EACH focus record (vendor, last PPM, open work orders, readings,
  inspections, anomalies ...). For "last"/"latest"/"next" use sort + limit 1. Use a separate include per need.
- For the STATUS of one named asset, also include its latest inspection (sort by date, limit 1) and its open
  work orders, when those concepts exist.
- Prefer STATES (blocked, open, overdue, expired ...) over filters whenever a state expresses the condition.
  Several states on one node mean ANY of them. For "blocked certificates that affect work" use the state whose
  description says to use it (e.g. blocking) and the rule BLOCKS.
- Codes such as PT-B-301-A-BOILER-01 or WO-123 go in "match" of the concept that owns them. A name such as
  "Lift Asset-4471" goes in "match" whole.
- Dates in filters may be "today", "today+30d", "today-7d" or YYYY-MM-DD.
- For a calendar period use op "within" with a period name - never invent a date word:
  {"attribute": "raised_at", "op": "within", "value": "last_month"}. Periods: today, yesterday, this_week, last_week, this_month, last_month, next_month, this_quarter, last_quarter, this_year, last_year, last_7_days, last_30_days, last_90_days, year_to_date.
  "How many raised last month and how many are closed" = the work orders raised within last_month, counted by status.
- Use only concept, attribute, state and relation names that exist in the ontology.

Illustrative examples (concept/relation names may differ - always use the names listed in the ontology above):
Q: In Bishopsgate Tower which compliance certificates are blocked and which work orders do they affect?
{"question_type":"list","focus":{"concept":"ComplianceCertificate","match":null,"states":["blocked"],"filters":[]},
 "constraints":[{"concept":"Building","match":"Bishopsgate Tower","via":[],"states":[],"filters":[]}],
 "include":[{"name":"affected_work_orders","concept":"WorkOrder","via":["BLOCKS"],"states":[],"filters":[],"sort":null,"limit":50}],
 "aggregate":null}
Q: How many open work orders does each building have?
{"question_type":"count","focus":{"concept":"WorkOrder","match":null,"states":["open"],"filters":[]},
 "constraints":[{"concept":"Building","match":null,"via":[],"states":[],"filters":[]}],"include":[],
 "aggregate":{"count_by":[{"concept":"Building","attribute":"name"}]}}
Q: List the vendors with the most work orders at Bishopsgate Tower, with the count for each.
(A ranking or a "per X" breakdown is ALWAYS an aggregate counted by X - never a list of rows to count.)
{"question_type":"count","focus":{"concept":"WorkOrder","match":null,"states":[],"filters":[]},
 "constraints":[{"concept":"Building","match":"Bishopsgate Tower","via":[],"states":[],"filters":[]},
                {"concept":"Vendor","match":null,"via":[],"states":[],"filters":[]}],"include":[],
 "aggregate":{"count_by":[{"concept":"Vendor","attribute":"name"}]}}"""


#: "vendors with the most work orders", "top 5 buildings", "how many … per vendor" - a ranking or a
#: breakdown, answered by counting in SQL, never by counting a capped list of rows.
_RANK_RE = re.compile(r"\b(most(?!\s+(?:recent\w*|latest|current|up)\b)|fewest|least(?!\s+recent\w*\b)"
                      r"|top\s+\d+|rank\w*|busiest)\b", re.I)
_GROUP_RE = re.compile(r"\b(?:per|by|for\s+each|each|every|across\s+(?:all\s+)?(?:the\s+)?)\s+([a-z][a-z ]{1,30}?)s?\b"
                       r"(?=[\s,.?!]|$)", re.I)


def _concept_words(onto) -> dict[str, str]:
    """Words a question uses for each concept: its name split ("WorkOrder" -> "work order") and synonyms."""
    out: dict[str, str] = {}
    for name, c in onto.concepts.items():
        words = [re.sub(r"(?<!^)(?=[A-Z])", " ", name).lower(), *[str(s).lower() for s in c.synonyms]]
        for w in words:
            w = w.strip()
            if w:
                out.setdefault(w, name)
                out.setdefault(w + "s", name)
    return out


def ensure_grouping(onto, plan, question: str) -> list[str]:
    """Turn a ranking or a breakdown into a count grouped by the concept it ranks.

    The planner read "List the vendors with the most work orders at Bishopsgate Tower" as a list of work
    orders with Building and Vendor constraints and no aggregate, so the answer was written from the first
    page of rows (1 Oct 2026). When the question ranks ("most", "top", "busiest") or breaks down ("per
    vendor", "by building", "for each asset") by a concept other than the focus, and the plan counts
    nothing, it counts by that concept's label - one SQL GROUP BY, exact. Returns notes for the answer."""
    if plan.get("aggregate") and plan["aggregate"].get("count_by"):
        return []
    if plan["focus"].get("match"):              # one named record is explained, not counted
        return []
    q = question or ""
    words = _concept_words(onto)
    focus = plan["focus"]["concept"]
    target = None
    for m in _GROUP_RE.finditer(q):
        phrase = m.group(1).strip().lower()
        for n in range(len(phrase.split()), 0, -1):        # the longest concept name the phrase starts with
            head = " ".join(phrase.split()[:n])
            if head in words and words[head] != focus:
                target = words[head]
                break
        if target:
            break
    rank = _RANK_RE.search(q)
    after = q[rank.end():].lower() if rank else ""
    counted = any(name == focus and re.search(r"\b" + re.escape(w) + r"\b", after) for w, name in words.items())
    if target is None and rank and counted:
        # "the vendors with the most work orders": the concept named before the ranking word,
        # ranked by how many of the focus records (named after it) each one has.
        before = q[: rank.start()].lower()
        best = -1
        for w, name in words.items():
            i = before.rfind(w)
            if name != focus and i > best and re.search(r"\b" + re.escape(w) + r"\b", before):
                best, target = i, name
    if target is None or target not in onto.concepts:
        return []
    c = onto.concepts[target]
    if target != focus and not any(cn["concept"] == target for cn in plan["constraints"]):
        plan["constraints"].append(normalise_node({"concept": target}))
    plan["aggregate"] = {"count_by": [{"concept": target, "attribute": c.label}]}
    plan["question_type"] = "count"
    return ["Counted " + focus + " records by " + target + " (" + c.label + "), largest first."]


def normalise_node(n, include=False):
    n = dict(n or {})
    out = {
        "concept": n.get("concept"),
        "match": n.get("match") or None,
        "states": list(n.get("states") or []),
        "filters": list(n.get("filters") or []),
        "via": list(n.get("via") or []),
    }
    if include:
        out["name"] = n.get("name") or out["concept"]
        out["sort"] = n.get("sort") or None
        try:
            out["limit"] = max(1, min(int(n.get("limit") or DEFAULT_INCLUDE_LIMIT), MAX_INCLUDE_LIMIT))
        except (TypeError, ValueError):
            out["limit"] = DEFAULT_INCLUDE_LIMIT
    return out


def normalise_plan(raw):
    return {
        "question_type": raw.get("question_type") if raw.get("question_type") in ("single", "list", "count") else "list",
        "focus": normalise_node(raw.get("focus")),
        "constraints": [normalise_node(c) for c in raw.get("constraints") or []],
        "include": [normalise_node(i, include=True) for i in raw.get("include") or []],
        "aggregate": raw.get("aggregate") or None,
    }


def validate_plan(onto, plan):
    errors = []
    focus_name = plan["focus"]["concept"]

    def check_node(n, where, path_from=None):
        c = onto.concepts.get(n["concept"])
        if not c:
            errors.append(where + ": unknown concept '" + str(n["concept"]) + "'. Valid: " + ", ".join(onto.concepts))
            return
        for s in n["states"]:
            if s not in c.states:
                errors.append(where + ": " + c.name + " has no state '" + s + "'. Valid: " + (", ".join(c.states) or "none - use filters"))
        for f in n["filters"]:
            if not isinstance(f, dict) or f.get("op") not in OPS:
                errors.append(where + ": bad filter " + json.dumps(f, default=str) + " (op must be one of " + ", ".join(sorted(OPS)) + ")")
                continue
            try:
                c.col(f.get("attribute"))
            except OntologyError as e:
                errors.append(where + ": " + str(e))
        if n.get("sort"):
            try:
                c.col(n["sort"].get("attribute"))
            except OntologyError as e:
                errors.append(where + " sort: " + str(e))
        if path_from and path_from in onto.concepts:
            try:
                onto.resolve_path(path_from, c.name, n["via"])
            except OntologyError as e:
                errors.append(where + ": " + str(e))

    check_node(plan["focus"], "focus")
    if plan["focus"]["via"]:
        errors.append("focus: 'via' is not used on the focus")
    for i, cn in enumerate(plan["constraints"]):
        check_node(cn, "constraints[" + str(i) + "]", focus_name)
    for i, inc in enumerate(plan["include"]):
        check_node(inc, "include[" + str(i) + "]", focus_name)
    agg = plan["aggregate"]
    if agg:
        allowed = {focus_name} | {c["concept"] for c in plan["constraints"]}
        for g in agg.get("count_by") or []:
            if g.get("concept") not in allowed:
                errors.append("aggregate: count_by concept " + str(g.get("concept"))
                              + " must be the focus or a constraint (add it as a constraint with match null)")
            elif g.get("concept") in onto.concepts:
                try:
                    onto.concepts[g["concept"]].col(g.get("attribute"))
                except OntologyError as e:
                    errors.append("aggregate: " + str(e))
    return errors


async def _call_llm(messages) -> str:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
    from ..llm_factory import create_chat_model
    model = create_chat_model()
    lc = [SystemMessage(content=m["content"]) if m["role"] == "system"
          else AIMessage(content=m["content"]) if m["role"] == "assistant"
          else HumanMessage(content=m["content"]) for m in messages]
    res = await model.ainvoke(lc)
    content = res.content
    if isinstance(content, list):
        content = "".join(str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in content)
    return str(content or "")


async def plan_question(onto, question, llm=_call_llm):
    codes = sorted({m for m in IDENTIFIER_RE.findall(question) if is_identifier(m)})
    messages = [
        {"role": "system", "content": "You convert facility-management questions into JSON query plans over an "
                                      "ontology. Output JSON only."},
        {"role": "user", "content": onto.describe_for_llm(question) + "\n\nPLAN FORMAT\n" + PLAN_SCHEMA + "\n\n" + PLAN_GUIDE
                                    + "\n\nCodes found in the question: " + (", ".join(codes) or "none")
                                    + "\n\nQuestion: " + question + "\nJSON plan:"},
    ]
    last = None
    attempts = []
    for attempt in range(1, MAX_PLAN_ATTEMPTS + 1):
        raw_text = strip_code_fences(await llm(messages))
        try:
            plan = normalise_plan(json.loads(raw_text))
            errors = validate_plan(onto, plan)
        except (json.JSONDecodeError, AttributeError, TypeError) as e:
            plan, errors = None, ["Not valid JSON in the required format: " + str(e)]
        attempts.append({"attempt": attempt, "errors": errors})
        if not errors:
            return plan, attempts
        last = errors
        messages += [{"role": "assistant", "content": raw_text},
                     {"role": "user", "content": "The plan has errors:\n- " + "\n- ".join(errors)
                                                 + "\nReturn the corrected JSON plan only."}]
    raise OntologyError("Could not build a valid plan: " + "; ".join(last or []))


# ---------------------------------------------------------------------------
# Scoped execution
# ---------------------------------------------------------------------------
def scope_settings(principal) -> list[tuple[str, str]]:
    """The settings plenum_scoped's views read - the same values svc-udr sets (scope.py)."""
    if principal is None:
        raise NotAllowed("no signed-in caller for this read")
    if principal.role == "superadmin":
        return [("app.udr_unrestricted", "1"), ("app.udr_org", ""), ("app.udr_buildings", "")]
    if principal.building_ids is not None and not principal.building_ids:
        raise NotAllowed("you are not allocated to any building")
    return [("app.udr_unrestricted", "0"),
            ("app.udr_org", str(principal.organization_id) if principal.organization_id else ""),
            ("app.udr_buildings", "" if principal.building_ids is None
             else ",".join(str(b) for b in principal.building_ids))]


class ScopedReader:
    """One read-only transaction against plenum_scoped with the caller's scope set in it."""

    def __init__(self, principal):
        self.settings = scope_settings(principal)
        self.queries: list[str] = []
        self._cm = None
        self.s = None

    async def __aenter__(self):
        self._cm = _session_factory()()
        self.s = await self._cm.__aenter__()
        await self.s.begin()
        await self.s.execute(text("SET TRANSACTION READ ONLY"))
        await self.s.execute(text("SET LOCAL statement_timeout = " + str(int(STATEMENT_TIMEOUT_MS))))
        for name, value in self.settings:
            # Transaction-local, and every one set every time: a pooled connection must never
            # carry the previous caller's company into this read.
            await self.s.execute(text("SELECT set_config(:n, :v, true)"), {"n": name, "v": value})
        await self.s.execute(text("SET LOCAL search_path TO " + SCOPED_SCHEMA + ", public"))
        return self

    async def __aexit__(self, *exc):
        try:
            await self.s.rollback()
        finally:
            await self._cm.__aexit__(*exc)

    async def fetch(self, sql: str, p: Params | dict | None, label: str = "") -> list[dict]:
        params = p.d if isinstance(p, Params) else (p or {})
        self.queries.append((label + ": " if label else "") + sql)
        res = await self.s.execute(text(sql), params)
        return [dict(r) for r in res.mappings().all()]

    async def try_fetch(self, sql, p, label=""):
        try:
            async with self.s.begin_nested():
                return await self.fetch(sql, p, label)
        except Exception as e:  # noqa: BLE001 - one failed lookup must not end the read
            log.warning("ontology.lookup_failed", label=label, error=str(e).splitlines()[0][:200])
            return []


#: Dates that name the same moment under another column. On hoistra_test work_orders.raised_at and
#: closed_at are empty on every row while reported_at and completed_at carry the dates, so "raised
#: last month" counted nothing (1 Oct 2026). created_at is last: it is often the import time.
DATE_STANDINS = {
    "raised_at": ["reported_at", "requested_at", "logged_at", "created_at"],
    "reported_at": ["raised_at", "requested_at", "created_at"],
    "closed_at": ["completed_at", "resolved_at", "finished_at"],
    "completed_at": ["closed_at", "resolved_at", "finished_at"],
    "completed_date": ["completed_at", "closed_at"],
}
_FILLED: dict[tuple[str, str, str], bool] = {}


async def _filled(r: ScopedReader, onto, table: str, col: str) -> bool:
    # Per scope: a column empty for one company can be filled for another.
    key = (table, col, repr(getattr(r, "settings", None)))
    if key not in _FILLED:
        rows = await r.try_fetch("SELECT EXISTS (SELECT 1 FROM " + qi(onto.schema) + "." + qi(table) + " WHERE "
                                 + qi(col) + " IS NOT NULL) AS f", None, "filled " + table + "." + col)
        _FILLED[key] = bool(rows and rows[0].get("f"))
    return _FILLED[key]


def finished_states_note(onto, plan) -> list[str]:
    """When records are counted by status, say which statuses mean finished - the focus's "open"
    state lists them - so "how many are closed" counts Completed too, not the word Closed alone."""
    agg = plan.get("aggregate") or {}
    c = onto.concepts.get(plan["focus"]["concept"])
    if not c or not any(g.get("concept") == c.name and str(g.get("attribute")) in ("status", c.attributes.get("status", ""))
                        for g in agg.get("count_by") or []):
        return []
    done = (c.states.get("open") or {}).get("not_in")
    if not done:
        return []
    return ["Finished (closed) " + c.name + " statuses here are: " + ", ".join(done)
            + ". 'Closed' in a question means any of them."]


async def fill_date_filters(r: ScopedReader, onto, plan) -> list[str]:
    """Point a date filter at a column that holds dates: an empty one swaps for its stand-in."""
    notes = []
    nodes = [plan["focus"], *plan["constraints"], *plan["include"]]

    def walk(conds):
        for f in conds or []:
            if isinstance(f, dict) and ("all" in f or "any" in f):
                yield from walk(f.get("all") or f.get("any"))
            elif isinstance(f, dict) and f.get("attribute"):
                yield f
    for node in nodes:
        c = onto.concepts.get(node.get("concept"))
        if c is None:
            continue
        for f in walk(node.get("filters")):
            try:
                col = c.col(f["attribute"])
            except OntologyError:
                continue
            if col not in DATE_STANDINS or await _filled(r, onto, c.table, col):
                continue
            for alt in DATE_STANDINS[col]:
                attr = next((a for a, v in c.attributes.items() if v == alt), None)
                if attr and await _filled(r, onto, c.table, alt):
                    notes.append(c.name + "." + col + " is empty in these records; dated by " + alt + " instead.")
                    f["attribute"] = attr
                    break
    return notes


async def _lookup(r: ScopedReader, onto, concept, text_, mode):
    cols = concept.identifiers if mode == "exact" else concept.search
    if not cols:
        return []
    p = Params()
    if mode == "exact":
        cond = " OR ".join("lower(" + qi(c) + "::text) = lower(" + p.add(text_) + ")" for c in cols)
    else:
        cond = " OR ".join(qi(c) + "::text ~* " + p.add(loose_pattern(text_)) for c in cols)
    sql = ("SELECT " + qi(concept.key) + "::text AS k, " + qi(concept.label) + "::text AS l FROM "
           + qi(onto.schema) + "." + qi(concept.table) + " WHERE " + cond + " LIMIT " + str(MAX_MATCH_KEYS))
    return await r.try_fetch(sql, p, "resolve " + concept.name + " (" + mode + ")")


async def resolve_match(r, onto, concept_name, text_):
    concept = onto.concepts[concept_name]
    for mode in ("exact", "search"):
        rows = await _lookup(r, onto, concept, text_, mode)
        if rows:
            return concept_name, [x["k"] for x in rows], [x["l"] for x in rows], None
    candidates = []
    for mode in ("exact", "search"):
        for c in onto.concepts.values():
            if c.name == concept_name:
                continue
            rows = await _lookup(r, onto, c, text_, mode)
            if rows:
                exact_label = any((x["l"] or "").lower() == text_.lower() for x in rows)
                candidates.append((0 if mode == "exact" else 1, 0 if exact_label else 1, len(rows), c.name, rows))
        if candidates:
            break
    if not candidates:
        return concept_name, [], [], "'" + text_ + "' not found in " + concept_name + " or any other concept"
    candidates.sort(key=lambda x: x[:4])
    _, _, _, name, rows = candidates[0]
    return name, [x["k"] for x in rows], [x["l"] for x in rows], (
        "'" + text_ + "' was not a " + concept_name + "; matched " + name + " instead")


async def add_missing_codes(r, onto, plan, question):
    mentioned = " ".join(str(n.get("match") or "") for n in [plan["focus"]] + plan["constraints"]).lower()
    for code in sorted({m for m in IDENTIFIER_RE.findall(question) if is_identifier(m)}):
        if code.lower() in mentioned:
            continue
        for c in onto.concepts.values():
            if await _lookup(r, onto, c, code, "exact"):
                if c.name == plan["focus"]["concept"] and not plan["focus"]["match"]:
                    plan["focus"]["match"] = code
                else:
                    node = normalise_node({"concept": c.name, "match": code})
                    try:
                        onto.resolve_path(plan["focus"]["concept"], c.name, [])
                        plan["constraints"].append(node)
                    except OntologyError:
                        continue
                break
    return plan


async def resolve_plan(r, onto, plan):
    notes, unresolved = [], []
    for where, node in [("focus", plan["focus"])] + [("constraint", c) for c in list(plan["constraints"])]:
        node["keys"], node["labels"] = None, []
        if not node["match"]:
            continue
        concept, keys, labels, note = await resolve_match(r, onto, node["concept"], node["match"])
        if note:
            notes.append(note)
        if not keys:
            unresolved.append(node["match"])
            continue
        if concept != node["concept"]:
            if where == "focus":
                notes.append("focus kept as " + node["concept"] + "; '" + node["match"] + "' used as a constraint on " + concept)
                plan["constraints"].append(dict(normalise_node({"concept": concept, "match": node["match"]}),
                                                keys=keys, labels=labels))
                node["match"] = None
                continue
            node["concept"] = concept
            node["states"] = [s for s in node["states"] if s in onto.concepts[concept].states]
            node["filters"] = []
            node["via"] = []
        node["keys"], node["labels"] = keys, labels
    return plan, notes, unresolved


def exists_path(onto, start_alias, start_concept, hops, p, prefix, node_conds=None):
    froms, where = [], []
    prev_alias = start_alias
    nodes = [(start_alias, onto.concepts[start_concept])]
    for i, (rel, fwd) in enumerate(hops):
        nxt = onto.concepts[rel.to if fwd else rel.from_]
        alias = prefix + str(i)
        fa, ta = (prev_alias, alias) if fwd else (alias, prev_alias)
        conds = _join_conds(onto, rel, fa, ta)
        tbl = qi(onto.schema) + "." + qi(nxt.table) + " AS " + qi(alias)
        if i == 0:
            froms.append(tbl)
            where.extend(conds)
        else:
            froms.append(" JOIN " + tbl + " ON " + " AND ".join(conds))
        nodes.append((alias, nxt))
        prev_alias = alias
    if node_conds:
        for idx, (a, c) in enumerate(nodes):
            where.extend(node_conds(idx, a, c, p) or [])
    if not froms:
        return "(" + " AND ".join(where or ["TRUE"]) + ")"
    return "EXISTS (SELECT 1 FROM " + "".join(froms) + " WHERE " + " AND ".join(where or ["TRUE"]) + ")"


def choose_include_rules(onto, plan):
    focus = plan["focus"]["concept"]
    for inc in plan["include"]:
        if not inc["via"]:
            rule = next((n for n, d in onto.derived.items() if not d.states and len(d.alternatives) > 1
                         and {d.from_, d.to} == {focus, inc["concept"]}), None)
            if rule:
                inc["via"] = [rule]


def scope_through_rules(onto, plan):
    focus = plan["focus"]["concept"]
    out = {}
    for ci, c in enumerate(plan["constraints"]):
        if not onto.concepts[c["concept"]].hub or not c.get("keys"):
            continue
        for inc in plan["include"]:
            if not (len(inc["via"]) == 1 and inc["via"][0] in onto.derived
                    and len(onto.derived[inc["via"][0]].alternatives) > 1):
                continue
            try:
                p_tc, _ = onto.auto_path(inc["concept"], c["concept"])
            except OntologyError:
                continue
            variants = onto.derived_variants(focus, inc["via"][0])
            out.setdefault(ci, []).append((inc, variants, p_tc))
            inc.setdefault("_scope", []).append((c, p_tc))
    return out


def build_main(onto, plan, p: Params):
    qb = QueryBuilder(onto, plan["focus"]["concept"])
    focus = qb.focus
    where = []
    node_alias = {focus.name: "f"}
    f = plan["focus"]
    if f.get("keys"):
        where.append(key_condition("f", focus.key, f["keys"], p))
    cond = compile_states(onto, focus, "f", f["states"], p)
    if cond is not None:
        where.append(cond)
    for flt in f["filters"]:
        where.append(compile_filter(onto, focus, "f", flt, p))
    paths_used = []
    scoped = scope_through_rules(onto, plan)
    for ci, c in enumerate(plan["constraints"]):
        if ci in scoped:
            concept = onto.concepts[c["concept"]]
            options = []
            hops, reqs = onto.resolve_path(focus.name, concept.name, c["via"])

            def direct_conds(idx, a, cn, prm, n_last=len(hops), c=c, concept=concept):
                if idx != n_last:
                    return []
                conds = [key_condition(a, concept.key, c["keys"], prm)]
                st = compile_states(onto, cn, a, c["states"], prm)
                return conds + ([st] if st is not None else [])
            options.append(exists_path(onto, "f", focus.name, hops, p, "d" + str(ci) + "_", direct_conds))
            txt = [concept.name + " directly (" + (" > ".join(r.name for r, _ in hops) or "same") + ")"]
            for k, (inc, variants, p_tc) in enumerate(scoped[ci]):
                for v, (vhops, vreqs, label, when) in enumerate(variants):
                    chain = list(vhops) + list(p_tc)
                    n_target, n_last = len(vhops), len(chain)

                    def rule_conds(idx, a, cn, prm, when=when, vreqs=vreqs, n_target=n_target, n_last=n_last, inc=inc,
                                   c=c, concept=concept):
                        conds = []
                        if when and when[0] == idx:
                            conds.append(compile_condition(onto, cn, a, when[1], prm))
                        if idx == n_target:
                            wanted = inc["states"] or [s for i2, _, sts in vreqs if i2 == n_target for s in sts]
                            st = compile_states(onto, cn, a, wanted, prm)
                            if st is not None:
                                conds.append(st)
                        if idx == 0 and not plan["focus"]["states"]:
                            for i2, _, sts in vreqs:
                                if i2 == 0:
                                    st = compile_states(onto, cn, a, sts, prm)
                                    if st is not None:
                                        conds.append(st)
                        if idx == n_last:
                            conds.append(key_condition(a, concept.key, c["keys"], prm))
                        return conds
                    options.append(exists_path(onto, "f", focus.name, chain, p,
                                               "r" + str(ci) + "_" + str(k) + "_" + str(v) + "_", rule_conds))
                    txt.append(inc["concept"] + " in " + concept.name + " via " + label)
            where.append("(" + " OR ".join(options) + ")")
            paths_used.append(focus.name + " restricted to " + concept.name + " = " + ", ".join(map(str, c.get("labels") or []))
                              + ": " + " OR ".join(txt))
            continue
        hops, reqs = onto.resolve_path(focus.name, c["concept"], c["via"])
        nodes = qb.add_path(hops)
        alias, concept = nodes[-1]
        node_alias.setdefault(concept.name, alias)
        paths_used.append(focus.name + " -> " + concept.name + " via " + (" > ".join(r.name for r, _ in hops) or "(same)"))
        if c.get("keys"):
            where.append(key_condition(alias, concept.key, c["keys"], p))
        cond = compile_states(onto, concept, alias, c["states"], p)
        if cond is not None:
            where.append(cond)
        for flt in c["filters"]:
            where.append(compile_filter(onto, concept, alias, flt, p))
        for idx, _cname, states in reqs:
            a, cn = nodes[idx]
            cond = compile_states(onto, cn, a, states, p)
            if cond is not None:
                where.append(cond)
    return qb, where, node_alias, paths_used


async def execute_plan(r: ScopedReader, onto, plan):
    focus = onto.concepts[plan["focus"]["concept"]]
    choose_include_rules(onto, plan)
    p = Params()
    qb, where, node_alias, paths_used = build_main(onto, plan, p)

    if plan["aggregate"] and plan["aggregate"].get("count_by"):
        groups = []
        for g in plan["aggregate"]["count_by"]:
            c = onto.concepts[g["concept"]]
            groups.append((qi(node_alias[c.name]) + "." + qi(c.col(g["attribute"])), c.name + "." + g["attribute"]))
        sql = ("SELECT " + ", ".join(e + " AS " + qi(re.sub(r"\W", "_", n)) for e, n in groups)
               + ", count(DISTINCT f." + qi(focus.key) + ") AS count"
               + qb.from_clause() + where_sql(where)
               + " GROUP BY " + ", ".join(e for e, _ in groups) + " ORDER BY count DESC LIMIT 500")
        rows = await r.fetch(sql, p, "aggregate")
        return {"kind": "aggregate", "rows": rows, "paths": paths_used}

    rows = await r.fetch("SELECT count(DISTINCT f." + qi(focus.key) + ") AS n" + qb.from_clause() + where_sql(where),
                         p, "count")
    total = rows[0]["n"]

    context_cols = []
    for cname, alias in node_alias.items():
        if cname != focus.name:
            c = onto.concepts[cname]
            context_cols.append(qi(alias) + "." + qi(c.label) + "::text AS " + qi("__ctx__" + cname))
    s_expr, s_dir = sort_expr("f", focus, None)
    sql = ("SELECT f." + qi(focus.key) + "::text AS __key, "
           + ", ".join(attr_select("f", focus) + context_cols + [s_expr + " AS __sort"])
           + qb.from_clause() + where_sql(where)
           + " ORDER BY __sort " + ("ASC" if s_dir == "asc" else "DESC") + " NULLS LAST, __key LIMIT "
           + str(MAX_FOCUS_RECORDS * 5))
    rows = await r.fetch(sql, p, "focus " + focus.name)

    records, order = {}, []
    for row in rows:
        k = row["__key"]
        if k not in records:
            if len(order) >= MAX_FOCUS_RECORDS:
                continue
            order.append(k)
            records[k] = {"key": k, "attributes": {a: row.get(a) for a in focus.attributes},
                          "context": defaultdict(set), "related": {}}
        for col, v in row.items():
            if col.startswith("__ctx__") and v is not None:
                records[k]["context"][col[7:]].add(v)

    for inc in plan["include"]:
        if not order:
            break
        target = onto.concepts[inc["concept"]]
        if not inc["via"]:
            rule = next((n for n, d in onto.derived.items() if not d.states and len(d.alternatives) > 1
                         and {d.from_, d.to} == {focus.name, target.name}), None)
            if rule:
                inc["via"] = [rule]
        if len(inc["via"]) == 1 and inc["via"][0] in onto.derived and len(onto.derived[inc["via"][0]].alternatives) > 1:
            variants = onto.derived_variants(focus.name, inc["via"][0])
        else:
            hops, reqs = onto.resolve_path(focus.name, target.name, inc["via"])
            variants = [(hops, reqs, " > ".join(rl.name for rl, _ in hops) or "(same record)", None)]
        rel_key = inc["name"] + " (" + target.name + ")"
        for k in order:
            records[k]["related"].setdefault(rel_key, [])
        earlier_first_hops = []
        seen = set()
        scoped = any(v[3] for v in variants)
        for hops, reqs, link_label, when in variants:
            bq = QueryBuilder(onto, focus.name)
            nodes = bq.add_path(hops)
            t_alias = nodes[-1][0]
            bp = Params()
            bwhere = [key_condition("f", focus.key, order, bp)]
            first = hops[0] if hops else None
            if when:
                w_alias, w_concept = nodes[when[0]]
                bwhere.append(compile_condition(onto, w_concept, w_alias, when[1], bp))
            for (rel, fwd) in ([] if scoped else earlier_first_hops):
                if first and rel.name == first[0].name:
                    continue
                for fc, tc in rel.join:
                    bwhere.append("f." + qi(fc if fwd else tc) + " IS NULL")
            if first:
                earlier_first_hops.append(first)
            cond = compile_states(onto, target, t_alias, inc["states"], bp)
            if cond is not None:
                bwhere.append(cond)
            for sc, p_tc in inc.get("_scope", []):
                sconcept = onto.concepts[sc["concept"]]
                bwhere.append(exists_path(onto, t_alias, target.name, p_tc, bp, "s" + str(len(bwhere)) + "_",
                                          lambda idx, a, cn, prm, n=len(p_tc), sc=sc, sconcept=sconcept:
                                          [key_condition(a, sconcept.key, sc["keys"], prm)] if idx == n else []))
            for flt in inc["filters"]:
                bwhere.append(compile_filter(onto, target, t_alias, flt, bp))
            for idx, _cname, states in reqs:
                if idx == 0 and plan["focus"]["states"]:
                    continue
                if idx == len(hops) and inc["states"]:
                    continue
                a, cn = nodes[idx]
                cond = compile_states(onto, cn, a, states, bp)
                if cond is not None:
                    bwhere.append(cond)
            via_cols = [qi(a) + "." + qi(c.label) + "::text AS " + qi("__via__" + c.name) for a, c in nodes[1:-1]]
            s_expr, s_dir = sort_expr(t_alias, target, inc["sort"])
            inner = ("SELECT DISTINCT f." + qi(focus.key) + "::text AS __focus_key, " + qi(t_alias) + "."
                     + qi(target.key) + "::text AS __target_key, "
                     + ", ".join(attr_select(t_alias, target) + via_cols + [s_expr + " AS __sort"])
                     + bq.from_clause() + where_sql(bwhere))
            sql = ("SELECT * FROM (SELECT d.*, ROW_NUMBER() OVER (PARTITION BY d.__focus_key ORDER BY d.__sort "
                   + ("ASC" if s_dir == "asc" else "DESC") + " NULLS LAST, d.__target_key) AS __rn FROM ("
                   + inner + ") d) w WHERE w.__rn <= " + str(int(inc["limit"])) + " ORDER BY w.__focus_key, w.__rn")
            brows = await r.fetch(sql, bp, "include " + inc["name"] + " [" + link_label + "]")
            for row in brows:
                fk, tk = row["__focus_key"], row["__target_key"]
                if (fk, tk) in seen or fk not in records:
                    continue
                seen.add((fk, tk))
                item = {a: row.get(a) for a in target.attributes}
                item.update({"via " + k[7:]: v for k, v in row.items() if k.startswith("__via__") and v is not None})
                item["linked by"] = link_label
                records[fk]["related"][rel_key].append(item)

    out = []
    for k in order:
        rec = records[k]
        rec["context"] = {c: sorted(v) for c, v in rec["context"].items()}
        out.append(rec)
    return {"kind": "records", "focus": focus.name, "total": total, "records": out, "paths": paths_used}


# ---------------------------------------------------------------------------
# Evidence for the agent
# ---------------------------------------------------------------------------
def clean(v):
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    s = str(v)
    return s if len(s) <= MAX_CELL_CHARS else s[:MAX_CELL_CHARS] + "..."


def strip_nulls(o):
    if isinstance(o, dict):
        return {k: strip_nulls(v) for k, v in o.items() if v is not None and v != {}}
    if isinstance(o, list):
        return [strip_nulls(x) for x in o]
    return clean(o)


def interpretation(onto, plan, notes):
    lines = []
    f = plan["focus"]
    lines.append("Focus: " + f["concept"] + (" matching '" + f["match"] + "'" if f["match"] else ""))
    for s in f["states"]:
        lines.append("  state " + onto.explain_state(f["concept"], s))
    for flt in f["filters"]:
        lines.append("  filter " + json.dumps(flt, default=str))
    for c in plan["constraints"]:
        lines.append("Restricted to " + c["concept"] + (" = " + ", ".join(map(str, c.get("labels") or [])) if c.get("labels") else "")
                     + (" via " + ", ".join(c["via"]) if c["via"] else ""))
        for s in c["states"]:
            lines.append("  state " + onto.explain_state(c["concept"], s))
    for inc in plan["include"]:
        lines.append("Related: " + inc["name"] + " = " + inc["concept"] + (" via " + ", ".join(inc["via"]) if inc["via"] else "")
                     + (" states " + ", ".join(inc["states"]) if inc["states"] else ""))
        for s in inc["states"]:
            lines.append("  state " + onto.explain_state(inc["concept"], s))
        for v in inc["via"]:
            if v in onto.derived:
                d = onto.derived[v]
                lines.append("  rule " + v + ": " + d.description + (" " + json.dumps(d.states) if d.states else ""))
    lines += ["Note: " + n for n in notes]
    return "\n".join(lines)


ANSWER_RULES = (
    "Answer as a facilities management engineer, from these records only:\n"
    "- Answer exactly what was asked first (counts, names, dates, statuses, codes), then supporting detail.\n"
    "- 'related' lists records linked to each focus record through the named path; an empty list means none "
    "exist - say so (e.g. 'no open work orders').\n"
    "- Explain business impact using the rules shown (a blocked certificate stops open work on the same equipment).\n"
    "- Give recommended next actions.\n"
    "- If the interpretation notes show an assumption or a name that was not found, say so.\n"
    "- For one asset, lead with its status and anything pending (open work orders, the next PPM and its due date, "
    "open energy anomalies with their annual cost, certificates expiring), then its key fields: code, criticality, "
    "make and model, installed, design life, replacement value, condition. Say 'none' for an empty list.\n"
    "- Show codes and names, not raw ids (uuids), unless the id was asked for. Tables are welcome. When you call "
    "another tool about the same record, pass it the record's `id` from here, not its name.\n"
    "- A condition grade is quoted with its inspection's risk level and date when an inspection is given; never "
    "call a grade good or poor from the number alone.\n"
    "- Close with 'Cost-saving options' (two to four lines) for a maintenance, work-order, asset or energy answer: "
    "the open energy anomalies' annual cost and the job or fix that removes them, a predictive job and the failure "
    "it prevents (against the asset's replacement value), repeat repairs approaching replacement value. Name the "
    "work order or asset and the £; detected is not saved; never add overlapping energy findings; invent nothing."
)

#: Added when the answer carries `pending` - a question about a set of work orders.
PENDING_RULES = """
- `pending` is what is still open in the set the question asked about. After the direct answer, say what
  that open work IS - do not stop at a status table:
  * group it by trade (`open_by_trade`, e.g. "4 HVAC, 2 fire detection, 2 lifts"), naming the jobs in each
    from `open_items` (code, what it is, its vendor);
  * say what is late (`hours_past_sla`, `overdue_open`) and what is blocked and why (`blocked_open`, a title
    that says "Blocked", status Held) - a blocked job usually waits on a lapsed vendor accreditation;
  * point out patterns: one vendor holding several late jobs, predictive drafts not yet raised, a statutory
    inspection (LOLER, gas, fire) due before its certificate lapses.
- `risk_to_life` on an open item is a reason it can endanger people (fire alarm, gas / CO, lifts, Legionella,
  emergency power). When the question asks about risk to life, safety, danger or harm, lead with a
  "Risk to life" section: every flagged job, its WO number, vendor, status, SLA and the reason in plain
  words, worst first (blocked or furthest past SLA first), and say which open jobs carry no such risk.
  Otherwise mark those rows in the table with "(life safety)". It is a judgement aid: give the reason,
  do not overstate it, and never flag a job the records do not support.
- When `open_items` holds every open job (no `open_items_note`), list EVERY one in an "Open work orders"
  table, most urgent first (blocked, then most hours past SLA, then priority), one row per job:
  | WO number | What | Trade | Vendor | Status | SLA | Action |
  SLA is "N h late" from `hours_past_sla`, else "due <date>". Action is the next step for that vendor on
  that job, from its status and title: Held/blocked -> reassign to an accredited vendor or get the
  accreditation renewed; In progress and late -> chase the vendor for an attend/finish time; Draft ->
  approve and issue to the vendor; Scheduled -> confirm the booked date. When `open_items_note` says
  only some are shown, list those and say how many more are open.
- Close with "Cost-saving options" (two to four lines): predictive and energy-fix jobs in `open_items`
  (a bearing, a stuck actuator, a schedule or setback fault, a burner) and what each prevents; if a
  `get_cost_savings` read is available, its priced open jobs, repeat failures and overcharges.
- Then "Next actions by vendor": one line per vendor with open work - the vendor, its job numbers, and what
  to ask of them - most urgent vendor first. Every line names a WO number and a vendor someone can act on.
- Keep it scannable: a one-line answer, the status counts, "What is still open" by trade (one line), the
  open work orders table, then next actions by vendor. Never invent a reason the records do not give.
"""


#: Open items handed to the answer with the counts: enough to say what they are, never a dump.
PENDING_ITEMS = 25

#: Open work that can endanger people, and why - read from the job's trade and its own words.
#: A judgement aid for the answer, stated with its reason, never a verdict on its own.
LIFE_RISK_RULES: list[tuple[str, str]] = [
    (r"fire|smoke|sprinkler|alarm|detection|bafe|evacuat|refuge|dry riser|fire door",
     "fire detection / alarm - people may not be warned of a fire"),
    (r"\bco\b|carbon monoxide|flue|gas\b|combustion|burner",
     "gas / combustion - carbon monoxide or gas-escape risk"),
    (r"\blift\b|lifts|vertical transport|loler|escalator|hoist|entrap|levelling",
     "lift / lifting equipment - entrapment, trip or fall risk; LOLER is a statutory safety examination"),
    # The job, not the category: a closed-system inhibitor top-up is filed under Water Hygiene and
    # carries no Legionella or scald risk; a calorifier, TMV or outlet job does.
    (r"legionella|calorifier|\btmv\b|cooling tower|scald|hot water outlet|shower|dead.?leg",
     "water hygiene - Legionella or scalding risk"),
    (r"standby power|generator|emergency light|ups\b|life safety",
     "emergency / standby power - life-safety systems may fail in an outage"),
    (r"electric shock|exposed (live|conductor)|arc flash|\bshock\b",
     "electrical - shock risk"),
]


def life_risk(item: dict) -> str | None:
    text_ = " ".join(str(item.get(k) or "") for k in ("category", "trade", "title", "type")).lower()
    for pattern, reason in LIFE_RISK_RULES:
        if re.search(pattern, text_):
            return reason
    return None


def _trade(category: str | None) -> str:
    """'HVAC · Boilers' -> 'HVAC'; the register's categories carry the trade before the separator."""
    if not category:
        return "Unclassified"
    return re.split(r"\s+[—–·|/-]\s+|\s*:\s+", str(category), maxsplit=1)[0].strip() or "Unclassified"


async def pending_context(r: ScopedReader, onto, plan) -> dict | None:
    """What is still open within a question about a set of work orders - by trade, vendor and type,
    how much is past its SLA or blocked, and the open items themselves.

    "How many work orders were raised at Bishopsgate last month and how many are closed?" was
    answered 794 / 781 and a status table (1 Oct 2026): right, and no use to an FM, who wants to
    know that the 13 still open are four HVAC jobs, two fire jobs held on a lapsed BAFE, a lift
    LOLER... The question's own constraints and filters are kept; its status is replaced by open."""
    import copy

    wo = onto.concepts.get(plan["focus"]["concept"])
    if wo is None or wo.name != "WorkOrder" or "open" not in wo.states:
        return None
    if plan["focus"].get("match") or len(plan["focus"].get("keys") or []) == 1:
        return None
    status_col = wo.attributes.get("status")

    def variant(states=("open",), aggregate=None, include=()):
        p = copy.deepcopy(plan)
        p["focus"]["states"] = list(states)
        p["focus"]["filters"] = [f for f in p["focus"].get("filters") or []
                                 if not (isinstance(f, dict) and wo.attributes.get(f.get("attribute")) == status_col)]
        p["include"] = [normalise_node(i, include=True) for i in include]
        p["aggregate"] = aggregate
        p["question_type"] = "count" if aggregate else "list"
        return p

    async def run(p):
        try:
            async with r.s.begin_nested():
                p, _, unresolved = await resolve_plan(r, onto, p)
                if unresolved or validate_plan(onto, p):
                    return None
                return await execute_plan(r, onto, p)
        except Exception as e:  # noqa: BLE001 - the context is extra; the answer stands without it
            log.warning("ontology.pending_context_failed", error=str(e).splitlines()[0][:200])
            return None

    def counts(res, key):
        return [{"name": row.get(key) or "Unassigned", "count": row["count"]} for row in (res or {}).get("rows") or []]

    out: dict[str, Any] = {}
    items = await run(variant(include=[
        *([{"name": "vendor", "concept": "Vendor", "limit": 1}] if "Vendor" in onto.concepts else []),
        *([{"name": "trade", "concept": "AssetCategory", "limit": 1}] if "AssetCategory" in onto.concepts else [])]))
    if items is None:
        return None
    out["open_total"] = items["total"]
    if not items["total"]:
        return out
    if "AssetCategory" in onto.concepts:
        p = variant(aggregate={"count_by": [{"concept": "AssetCategory", "attribute": onto.concepts["AssetCategory"].label}]})
        p["constraints"].append(normalise_node({"concept": "AssetCategory"}))
        cats = counts(await run(p), "AssetCategory_" + onto.concepts["AssetCategory"].label)
        trades: dict[str, int] = {}
        for c in cats:
            trades[_trade(c["name"])] = trades.get(_trade(c["name"]), 0) + c["count"]
        out["open_by_trade"] = dict(sorted(trades.items(), key=lambda x: -x[1]))
        out["open_by_category"] = cats
    if "Vendor" in onto.concepts and not any(c["concept"] == "Vendor" and c.get("keys") for c in plan["constraints"]):
        p = variant(aggregate={"count_by": [{"concept": "Vendor", "attribute": onto.concepts["Vendor"].label}]})
        p["constraints"].append(normalise_node({"concept": "Vendor"}))
        out["open_by_vendor"] = counts(await run(p), "Vendor_" + onto.concepts["Vendor"].label)
    for attr in ("wo_type", "priority"):
        if attr in wo.attributes:
            out["open_by_" + attr] = counts(await run(variant(aggregate={"count_by": [{"concept": wo.name, "attribute": attr}]})),
                                            wo.name + "_" + attr)
    for state in ("overdue", "blocked"):
        if state in wo.states:
            res = await run(variant(states=[state]))
            out[state + "_open"] = (res or {}).get("total")
    now = datetime.datetime.now(datetime.timezone.utc)
    rows = []
    for rec in items["records"][:PENDING_ITEMS]:
        a = rec["attributes"]
        rel = rec["related"]
        vendor = next((x.get(onto.concepts["Vendor"].label) for k, v in rel.items() if k.startswith("vendor") for x in v), None) \
            if "Vendor" in onto.concepts else None
        cat = next((x.get(onto.concepts["AssetCategory"].label) for k, v in rel.items() if k.startswith("trade") for x in v), None) \
            if "AssetCategory" in onto.concepts else None
        due = a.get("sla_due_at")
        late = None
        if isinstance(due, datetime.datetime):
            due_utc = due if due.tzinfo else due.replace(tzinfo=datetime.timezone.utc)
            late = int((now - due_utc).total_seconds() // 3600) if due_utc < now else None
        rows.append(strip_nulls({"wo_code": a.get("wo_code"), "title": a.get("title"), "status": a.get("status"),
                                 "priority": a.get("priority"), "type": a.get("wo_type"), "trade": _trade(cat) if cat else None,
                                 "category": cat, "vendor": vendor, "reported": a.get("reported_at"),
                                 "sla_due": due, "hours_past_sla": late}))
    for x in rows:
        why = life_risk(x)
        if why:
            x["risk_to_life"] = why
    risky = [x for x in rows if x.get("risk_to_life")]
    if risky:
        out["risk_to_life_open"] = len(risky)
    # Most urgent first: blocked (held), then the longest past SLA, then priority (P1 before P2).
    rows.sort(key=lambda x: (0 if str(x.get("status", "")).lower() in ("held", "blocked")
                             or str(x.get("title", "")).lower().startswith("blocked") else 1,
                             -(x.get("hours_past_sla") or -1), str(x.get("priority") or "zz")))
    out["open_items"] = rows
    if items["total"] > len(rows):
        out["open_items_note"] = "showing " + str(len(rows)) + " of " + str(items["total"]) + " open"
    return out


def evidence(onto, question, plan, result, notes):
    if result["kind"] == "aggregate":
        data = json.dumps(strip_nulls(result["rows"]), default=str)
        header = "Aggregated counts (" + str(len(result["rows"])) + " groups):"
    else:
        payload = [{result["focus"]: rec["attributes"], "matched_context": rec["context"], "related": rec["related"]}
                   for rec in result["records"]]
        data = json.dumps(strip_nulls(payload), default=str)
        header = (result["focus"] + " records: " + str(result["total"]) + " match"
                  + (" (showing " + str(len(result["records"])) + ")" if result["total"] > len(result["records"]) else ""))
    truncated = len(data) > MAX_EVIDENCE_CHARS
    if truncated:
        data = data[:MAX_EVIDENCE_CHARS] + " ...(truncated)"
    return {"ok": True, "question": question, "interpretation": interpretation(onto, plan, notes),
            "header": header, "records": data, "truncated": truncated,
            "total": result.get("total"), "paths": result.get("paths", []), "answer_rules": ANSWER_RULES}


#: What "the status of one asset" always includes, whatever the plan asked for. The first live
#: run (30 Sep 2026, "status of Boiler 2 and anything pending") planned the inspection and the
#: open work orders only, and the answer said "nothing pending" over a PPM due in nine days and
#: four open energy anomalies. What an asset's status covers is a rule, so it is applied here
#: rather than hoped for from the plan. (table, include name, states wanted, sort, limit) - a
#: concept or state this deployment does not have is skipped.
STATUS_BUNDLE = [
    ("inspections", "latest_inspection", [], ("inspection_date", "desc"), 2),
    ("work_orders", "open_work_orders", ["open"], None, 20),
    ("ppm_visits", "ppm_not_done", ["scheduled", "missed", "deferred", "open"], ("scheduled_date", "asc"), 5),
    ("ppm_visits", "last_ppm_done", ["completed"], ("scheduled_date", "desc"), 1),
    ("maintenance_plans", "maintenance_plan", [], ("next_due_date", "asc"), 5),
    ("energy_anomalies", "open_energy_anomalies", ["open"], None, 10),
    ("compliance_certificates", "certificates", [], ("expiry_date", "asc"), 10),
]


def ensure_status_includes(onto, plan) -> list[str]:
    """Add the status bundle when the question is about one or a few named assets."""
    focus = onto.concepts.get(plan["focus"]["concept"])
    keys = plan["focus"].get("keys") or []
    if not focus or focus.table != "assets" or not keys or len(keys) > 3 or plan.get("aggregate"):
        return []
    by_table = {c.table: c for c in onto.concepts.values()}
    have = {(i["concept"], tuple(sorted(i["states"]))) for i in plan["include"]}
    have_concepts = {i["concept"] for i in plan["include"]}
    added = []
    for table, name, states, sort, limit in STATUS_BUNDLE:
        c = by_table.get(table)
        if not c:
            continue
        st = [x for x in states if x in c.states]
        if states and not st:
            continue
        if (c.name, tuple(sorted(st))) in have or (not st and c.name in have_concepts):
            continue
        try:
            onto.resolve_path(focus.name, c.name, [])
        except OntologyError:
            continue
        srt = {"attribute": sort[0], "dir": sort[1]} if sort and sort[0] in c.attributes else None
        plan["include"].append(normalise_node({"name": name, "concept": c.name, "states": st,
                                               "sort": srt, "limit": limit}, include=True))
        have.add((c.name, tuple(sorted(st))))
        added.append(name)
    return added


# ---------------------------------------------------------------------------
# A certificate's reach: the assets it covers, their maintenance, and their energy
# ---------------------------------------------------------------------------
#: A compliance answer should say what a certificate touches, not only its own dates: a
#: vendor's lapsed gas-safe registration matters because of the boilers that vendor maintains,
#: their open work, their next PPM, and whether those boilers are raising energy anomalies.
#: These rules are derived from the ontology the schema produced, so they follow its real join
#: columns: an asset-scope certificate reaches its own asset; a vendor-scope certificate reaches
#: every asset that vendor maintains; a building-scope certificate reaches the building's open
#: energy anomalies (an EPC or DEC is about the building's energy). Each is a rule with one
#: alternative per scope, selected by the certificate's own scope column.
CERT_LINK_RULES = {
    # rule name        -> (child table reached from the asset or None for the asset itself,
    #                      also via the building for building-scope certificates)
    "CERTIFICATE_ASSETS": (None, False),
    "CERTIFICATE_ASSET_WORK": ("work_orders", False),
    "CERTIFICATE_ASSET_PPM": ("ppm_visits", False),
    "CERTIFICATE_ASSET_PLANS": ("maintenance_plans", False),
    "CERTIFICATE_ENERGY": ("energy_anomalies", True),
}

#: What a compliance answer always reads for each certificate. (rule, include name, states, sort, limit)
CERT_BUNDLE = [
    ("CERTIFICATE_ASSETS", "linked_assets", [], None, 10),
    ("CERTIFICATE_ASSET_WORK", "asset_open_work_orders", ["open"], None, 10),
    ("CERTIFICATE_ASSET_PPM", "asset_ppm_not_done", ["scheduled", "missed", "deferred", "open"], ("scheduled_date", "asc"), 10),
    ("CERTIFICATE_ASSET_PLANS", "asset_maintenance_plans", [], ("next_due_date", "asc"), 10),
    ("CERTIFICATE_ENERGY", "open_energy_anomalies", ["open"], None, 10),
]


def _best_relation(onto, frm: str, to: str):
    """The relation from `frm` to `to` whose link column is most often filled."""
    cands = [r for r in onto.relations.values() if r.from_ == frm and r.to == to and r.cardinality == "many_to_one"]
    return max(cands, key=lambda r: r.fill, default=None)


def add_certificate_links(onto) -> list[str]:
    """Derive CERT_LINK_RULES from this ontology. Returns the rule names added."""
    by_table = {c.table: c for c in onto.concepts.values()}
    cert, asset = by_table.get("compliance_certificates"), by_table.get("assets")
    vendor, building = by_table.get("vendors"), by_table.get("buildings")
    blocks = onto.derived.get("BLOCKS")
    if not cert or not asset or not blocks:
        return []
    # The certificate's scope column and its values, and its first hop per scope, as BLOCKS found them.
    first_hop: dict[str, tuple[list[str], dict]] = {}
    for alt in blocks.alternatives:
        when = alt.get("when") or {}
        if not alt["via"] or not when:
            continue
        rel = onto.relations.get(alt["via"][0])
        if not rel:
            continue
        target = rel.to if rel.from_ == cert.name else rel.from_
        hops = first_hop.setdefault(target, ([], when))[0]
        if rel.name not in hops:
            hops.append(rel.name)
    asset_vendor = _best_relation(onto, asset.name, vendor.name) if vendor else None
    added = []
    for rule, (child_table, via_building) in CERT_LINK_RULES.items():
        child = by_table.get(child_table) if child_table else None
        if child_table and not child:
            continue
        to_asset = _best_relation(onto, child.name, asset.name) if child else None
        if child and not to_asset:
            continue
        tail = [to_asset.name] if to_asset else []
        alts = []
        for hop in first_hop.get(asset.name, ([], None))[0]:
            alts.append({"via": [hop] + tail, "label": "the certificate's own asset",
                         "when": first_hop[asset.name][1]})
        if vendor and asset_vendor and vendor.name in first_hop:
            for hop in first_hop[vendor.name][0]:
                alts.append({"via": [hop, asset_vendor.name] + tail, "label": "assets the certificate's vendor maintains",
                             "when": first_hop[vendor.name][1]})
        if via_building and building and building.name in first_hop:
            to_building = _best_relation(onto, child.name, building.name)
            if to_building:
                for hop in first_hop[building.name][0]:
                    alts.append({"via": [hop, to_building.name], "label": "the certificate's building",
                                 "when": first_hop[building.name][1]})
        if not alts:
            continue
        end = child.name if child else asset.name
        d = Derived(rule, {"from": cert.name, "to": end, "alternatives": alts})
        try:
            for a in d.alternatives:
                _, _, got = onto.expand(cert.name, a["via"], allow_derived=False)
                if got != end:
                    raise OntologyError(rule + " ends at " + got)
        except OntologyError as e:
            log.warning("ontology.certificate_link_skipped", rule=rule, error=str(e))
            continue
        onto.derived[rule] = d
        added.append(rule)
    return added


def ensure_compliance_includes(onto, plan) -> list[str]:
    """Add each certificate's reach when the question is about certificates."""
    focus = onto.concepts.get(plan["focus"]["concept"])
    if not focus or focus.table != "compliance_certificates" or plan.get("aggregate"):
        return []
    have = {tuple(i["via"]) for i in plan["include"] if i["via"]}
    added = []
    for rule, name, states, sort, limit in CERT_BUNDLE:
        d = onto.derived.get(rule)
        if not d or (rule,) in have:
            continue
        target = onto.concepts[d.to]
        st = [x for x in states if x in target.states]
        if states and not st:
            continue
        srt = {"attribute": sort[0], "dir": sort[1]} if sort and sort[0] in target.attributes else None
        plan["include"].append(normalise_node({"name": name, "concept": target.name, "via": [rule],
                                               "states": st, "sort": srt, "limit": limit}, include=True))
        added.append(name)
    return added


async def linked_for_certificates(certificate_ids: list[str], principal=None,
                                  budget: int = MAX_EVIDENCE_CHARS) -> dict:
    """What each certificate reaches - assets, their maintenance, energy - read in the caller's
    scope, with no model call. The compliance pipeline calls this with the certificates it has
    already fetched, so the analyst can say what a lapsed certificate puts at risk."""
    ids = [str(i) for i in certificate_ids if i][:MAX_FOCUS_RECORDS]
    if not ids:
        return {"ok": True, "certificates": 0, "records": "[]"}
    if principal is None:
        from ..services.principal import caller_principal
        principal = caller_principal.get()
    try:
        scope_settings(principal)
    except NotAllowed as e:
        return {"ok": False, "error": "Nothing can be read: " + str(e) + "."}
    onto = await get_ontology()
    cert = next((c for c in onto.concepts.values() if c.table == "compliance_certificates"), None)
    if not cert:
        return {"ok": False, "error": "No certificate concept in this deployment."}
    plan = normalise_plan({"question_type": "list", "focus": {"concept": cert.name}})
    plan["focus"]["keys"], plan["focus"]["labels"] = ids, []
    added = ensure_compliance_includes(onto, plan)
    async with ScopedReader(principal) as r:
        result = await execute_plan(r, onto, plan)
    return shape_certificate_reach(result["records"], budget=budget)


#: The fields of each linked record the analyst needs; the rest is left out. Five vendor
#: certificates reach the same ten assets, and every row carried ~20 columns: the first run
#: came to 40K characters for 44 certificates and was cut off mid-record.
_REACH_FIELDS = {
    "linked_assets": ("asset_code", "asset_name", "status", "criticality", "criticality_level", "condition_score", "is_online"),
    "asset_open_work_orders": ("wo_code", "title", "status", "priority", "sla_due_at", "vendor_name"),
    "asset_ppm_not_done": ("ppm_ref", "task", "status", "scheduled_date", "vendor_name"),
    "asset_maintenance_plans": ("sm_code", "status", "next_due_date", "frequency_type", "frequency_value", "vendor_name"),
    "open_energy_anomalies": ("anomaly_type", "status", "detected_at", "metric_pct", "financial_gbp", "currency"),
}
_CERT_FIELDS = ("certificate_number", "certificate_type_code", "status", "cert_scope", "expiry_date",
                "vendor_name", "building_name")


def _cost(line) -> float:
    """The annual cost out of an anomaly line ("... GBP 7,605/yr ...")."""
    m = re.search(r"([\d,]+)/yr", str(line))
    return float(m.group(1).replace(",", "")) if m else 0.0


def shape_certificate_reach(records: list[dict], budget: int = MAX_EVIDENCE_CHARS) -> dict:
    """Group what the certificates reach by asset: each asset once, with its open work, PPMs,
    plans and anomalies; each certificate with the assets it covers; and a building's anomalies
    under the building. Only certificates that reach something are listed."""
    assets: dict[str, dict] = {}
    building_energy: dict[str, list] = {}
    certs = []

    def slim(kind, item):
        """One linked record as one line - the analyst reads a sentence per record, not an object."""
        g = lambda k: item.get(k)  # noqa: E731
        day = lambda v: str(v)[:10] if v else ""  # noqa: E731
        if kind == "linked_assets":
            return {k: g(k) for k in _REACH_FIELDS[kind] if g(k) is not None}
        if kind == "asset_open_work_orders":
            return " ".join(x for x in [g("wo_code"), "[" + str(g("status") or "") + "]", g("priority") or "",
                                        ("due " + day(g("sla_due_at"))) if g("sla_due_at") else "",
                                        "- " + str(g("title") or "")[:90]] if x)
        if kind == "asset_ppm_not_done":
            return " ".join(x for x in [g("ppm_ref"), "[" + str(g("status") or "") + "]", day(g("scheduled_date")),
                                        g("vendor_name") or ""] if x)
        if kind == "asset_maintenance_plans":
            every = ("every " + str(g("frequency_value")) + " " + str(g("frequency_type"))) if g("frequency_value") else ""
            return " ".join(x for x in [g("sm_code"), "next due " + day(g("next_due_date")), every,
                                        g("vendor_name") or ""] if x)
        cost = float(g("financial_gbp") or 0)
        pct = float(g("metric_pct") or 0)
        return (str(g("anomaly_type")) + " " + ("+" if pct >= 0 else "") + str(round(pct)) + "%, "
                + (g("currency") or "GBP") + " " + format(round(cost), ",") + "/yr"
                + ((", detected " + day(g("detected_at"))) if g("detected_at") else ""))

    for rec in records:
        attrs = rec["attributes"]
        covered, reached, vendor = set(), False, None
        for key, items in rec["related"].items():
            kind = key.split(" (")[0]
            if kind not in _REACH_FIELDS or not items:
                continue
            reached = True
            for it in items:
                vendor = vendor or it.get("via Vendor")
                if kind == "linked_assets":
                    name = it.get("asset_name") or it.get("asset_code")
                else:
                    name = it.get("via Asset")
                if name:
                    covered.add(name)
                    a = assets.setdefault(name, {"asset": {}, "open_work_orders": [], "ppm_not_done": [],
                                                 "maintenance_plans": [], "open_energy_anomalies": []})
                    if kind == "linked_assets":
                        a["asset"] = slim(kind, it)
                    else:
                        bucket = {"asset_open_work_orders": "open_work_orders", "asset_ppm_not_done": "ppm_not_done",
                                  "asset_maintenance_plans": "maintenance_plans",
                                  "open_energy_anomalies": "open_energy_anomalies"}[kind]
                        row = slim(kind, it)
                        if row not in a[bucket]:
                            a[bucket].append(row)
                elif kind == "open_energy_anomalies":
                    b = it.get("via Building") or attrs.get("building_name") or "building"
                    row = slim(kind, it)
                    if row not in building_energy.setdefault(b, []):
                        building_energy[b].append(row)
        if reached:
            c = {k: attrs.get(k) for k in _CERT_FIELDS if attrs.get(k) is not None}
            if vendor and not c.get("vendor_name"):
                c["vendor_name"] = vendor
            c["covers_assets"] = sorted(covered)
            certs.append(c)
    for name, a in list(assets.items()):
        a["summary"] = {"open_work_orders": len(a["open_work_orders"]), "ppm_not_done": len(a["ppm_not_done"]),
                        "next_plan_due": min((m.group(1) for p in a["maintenance_plans"]
                                              for m in [re.search(r"next due (\d{4}-\d{2}-\d{2})", p)] if m),
                                             default=None),
                        "open_energy_anomalies": len(a["open_energy_anomalies"]),
                        "anomaly_cost_per_year": round(sum(_cost(x) for x in a["open_energy_anomalies"]))}
        # The counts above are whole; the lists are the first few of each. An asset with
        # nothing open is its row and summary only - "nothing pending" needs no list.
        for bucket, cap in (("open_work_orders", 5), ("ppm_not_done", 3), ("maintenance_plans", 1),
                            ("open_energy_anomalies", 5)):
            a[bucket] = a[bucket][:cap]
        if not (a["open_work_orders"] or a["ppm_not_done"] or a["open_energy_anomalies"]):
            assets[name] = {"asset": a["asset"], "summary": a["summary"]}
    for b in building_energy:
        rows = building_energy[b]
        building_energy[b] = {"open_anomalies": len(rows), "cost_per_year": round(sum(_cost(x) for x in rows)),
                              "largest": sorted(rows, key=lambda x: -_cost(x))[:5]}
    body = {"certificates": certs, "assets": assets, "building_energy": building_energy}
    data = json.dumps(strip_nulls(body), default=str)
    detail = "full"
    if len(data) > budget:
        # Over budget: every asset keeps its row and its counts, and the lists go - a count of
        # open work orders is still the fact; which ones is the detail that can be asked for.
        body["assets"] = {n: {"asset": a.get("asset", {}), "summary": a["summary"]} for n, a in assets.items()}
        data, detail = json.dumps(strip_nulls(body), default=str), "summaries"
    truncated = len(data) > budget
    return {"ok": True, "certificates_reaching_something": len(certs), "assets": len(assets),
            "buildings_with_open_anomalies": len(building_energy), "detail": detail, "truncated": truncated,
            "records": data if not truncated else data[:budget] + " ...(truncated)"}


async def answer_question(question: str, principal=None, llm=_call_llm) -> dict:
    """The whole pipeline for one question, read inside the caller's scope."""
    if principal is None:
        from ..services.principal import caller_principal
        principal = caller_principal.get()
    try:
        scope_settings(principal)
    except NotAllowed as e:
        return {"ok": False, "error": "Nothing can be read: " + str(e) + "."}
    onto = await get_ontology()
    plan, attempts = await plan_question(onto, question, llm=llm)
    grouped = ensure_grouping(onto, plan, question)
    async with ScopedReader(principal) as r:
        plan = await add_missing_codes(r, onto, plan, question)
        plan, notes, unresolved = await resolve_plan(r, onto, plan)
        if unresolved:
            return {"ok": True, "found": False, "question": question,
                    "answer_hint": "Could not find " + ", ".join("'" + u + "'" for u in unresolved)
                                   + " in the records you can see. Ask for the exact code (asset code, work order "
                                     "number, site code) or check the spelling.",
                    "notes": notes, "queries": r.queries}
        notes.extend(grouped)
        notes.extend(await fill_date_filters(r, onto, plan))
        notes.extend(finished_states_note(onto, plan))
        added =[] if plan.get("aggregate") else (ensure_status_includes(onto, plan)
                                                  + ensure_compliance_includes(onto, plan))
        if added:
            notes.append("Also read, as always for this kind of record: " + ", ".join(added))
        log.info("ontology.plan", focus=plan["focus"]["concept"], match=plan["focus"]["match"],
                 keys=len(plan["focus"].get("keys") or []),
                 constraints=[c["concept"] for c in plan["constraints"]],
                 include=[(i["name"], i["concept"], i["states"]) for i in plan["include"]], added=added)
        errors = validate_plan(onto, plan)
        if errors:
            raise OntologyError("Plan invalid after resolution: " + "; ".join(errors))
        result = await execute_plan(r, onto, plan)
        out = evidence(onto, question, plan, result, notes)
        pending = await pending_context(r, onto, plan)
        if pending:
            out["pending"] = pending
            out["answer_rules"] = ANSWER_RULES + PENDING_RULES
        out["queries"] = r.queries
        out["plan_attempts"] = attempts
        return out


@tool
async def answer_from_records(question: str) -> dict:
    """Answer a question about specific records - an asset, building, work order, certificate, vendor,
    meter, PPM visit - and the records linked to them, from SQL compiled from the CAFM ontology.

    USE THIS FIRST for: the status or details of a named or coded record ("status of Lift Asset-4471",
    "what is B-301-CHILLER-101"), lists and counts of records by state ("open work orders at Bishopsgate",
    "blocked certificates and the work they affect"), and "for X, give me its vendor / last PPM / open WOs".
    Pass the user's question as asked, including the building or site they named.

    It resolves names and codes to records (codes exactly, names loosely, then every other kind of record),
    reads only what the caller may see, and returns the matched records, what they are linked to, how the
    question was interpreted, and `answer_rules` for writing the answer. Write the answer from `records`
    following `answer_rules`. If `found` is false, pass `answer_hint` on - do not guess a record.

    Not for: judgements an engine computes (compliance status against a country pack, vendor SLA scores,
    energy benchmarks and anomalies) - hand those to their agent.
    """
    try:
        return await answer_question(question)
    except OntologyError as e:
        return {"ok": False, "error": "Could not plan this question against the ontology: " + str(e)[:800]}
    except Exception as e:  # noqa: BLE001 - a tool must answer, not raise into the agent loop
        log.error("ontology.answer_failed", error=str(e)[:500])
        return {"ok": False, "error": "The record query failed: " + str(e).splitlines()[0][:300]}
