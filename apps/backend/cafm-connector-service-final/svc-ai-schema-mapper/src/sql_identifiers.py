"""The one rule for a table or column name the mapper puts into SQL.

29 Oct 2026: a sheet's "end" column became ``ADD COLUMN IF NOT EXISTS end VARCHAR(255)`` and
the whole write rolled back on ``syntax error at or near "end"``. Every name check looked only
at the characters, and "end" is made of good characters.

A name the system makes is repaired here, never refused for a person to fix:

* a word PostgreSQL reserves gets ``_value`` (``end`` → ``end_value``),
* a leading digit gets ``col_`` (``2024 Cost`` → ``col_2024_cost``),
* past PostgreSQL's 63 characters the name is cut and given 8 characters of its hash, so two
  long headers that share a start never become one column.

Repairing is safe on names already in plenum_cafm: none of them is a reserved word (the only
keyword columns there, ``timestamp``, ``row`` and ``precision``, stand bare without trouble),
and a repaired name repairs to itself. The engine's ``rules.SafeIdent`` is the same rule.

``quote_ident`` is PostgreSQL's own rule for the emitters: quote a name that is not a plain
identifier or is any keyword that is not unreserved.
"""
from __future__ import annotations

import hashlib
import re

#: pg_get_keywords() categories R and T on PostgreSQL 16: none can be a bare column name.
RESERVED_WORDS = frozenset({
    "all", "analyse", "analyze", "and", "any", "array", "as", "asc", "asymmetric",
    "authorization", "binary", "both", "case", "cast", "check", "collate", "collation", "column",
    "concurrently", "constraint", "create", "cross", "current_catalog", "current_date",
    "current_role", "current_schema", "current_time", "current_timestamp", "current_user",
    "default", "deferrable", "desc", "distinct", "do", "else", "end", "except", "false", "fetch",
    "for", "foreign", "freeze", "from", "full", "grant", "group", "having", "ilike", "in",
    "initially", "inner", "intersect", "into", "is", "isnull", "join", "lateral", "leading",
    "left", "like", "limit", "localtime", "localtimestamp", "natural", "not", "notnull", "null",
    "offset", "on", "only", "or", "order", "outer", "overlaps", "placing", "primary",
    "references", "returning", "right", "select", "session_user", "similar", "some", "symmetric",
    "system_user", "table", "tablesample", "then", "to", "trailing", "true", "union", "unique",
    "user", "using", "variadic", "verbose", "when", "where", "window", "with",
})

#: Category C: fine as a bare column name, but quote_ident quotes them, and so do we.
_COLUMN_NAME_KEYWORDS = frozenset({
    "between", "bigint", "bit", "boolean", "char", "character", "coalesce", "dec", "decimal",
    "exists", "extract", "float", "greatest", "grouping", "inout", "int", "integer", "interval",
    "json_array", "json_arrayagg", "json_object", "json_objectagg", "least", "national", "nchar",
    "none", "normalize", "nullif", "numeric", "out", "overlay", "position", "precision", "real",
    "row", "setof", "smallint", "substring", "time", "timestamp", "treat", "trim", "values",
    "varchar", "xmlattributes", "xmlconcat", "xmlelement", "xmlexists", "xmlforest",
    "xmlnamespaces", "xmlparse", "xmlpi", "xmlroot", "xmlserialize", "xmltable",
})

QUOTED_KEYWORDS = RESERVED_WORDS | _COLUMN_NAME_KEYWORDS

MAX_LENGTH = 63
_PLAIN = re.compile(r"^[a-z_][a-z0-9_]*$", re.IGNORECASE)


def repair(name: str, reserved_suffix: str = "_value") -> str:
    """``name`` (already [a-z0-9_]) made into an identifier PostgreSQL takes bare. "" stays ""."""
    if not name:
        return name
    if name[0].isdigit():
        name = "col_" + name
    if name in RESERVED_WORDS:
        name += reserved_suffix
    if len(name) > MAX_LENGTH:
        name = name[:54] + "_" + hashlib.sha1(name.encode()).hexdigest()[:8]
    return name


def clean(raw: object) -> str:
    """Lower-cased, spaces and hyphens to ``_``, every other character outside [a-z0-9_] dropped."""
    n = str(raw).strip().lower().replace(" ", "_").replace("-", "_")
    return re.sub(r"[^a-z0-9_]", "", n)


def safe_identifier(raw: object) -> str | None:
    """``raw`` as a table or column name, or None when no letter or digit of it is left."""
    if raw is None:
        return None
    return repair(clean(raw)) or None


#: The column types a migration may ask for, and the only strings allowed into a DDL statement.
#: A type is not user prose: it is one of these, optionally with a length or precision.
_ALLOWED_TYPES = frozenset({
    "text", "varchar", "character varying", "char", "character",
    "smallint", "integer", "int", "int2", "int4", "int8", "bigint",
    "numeric", "decimal", "real", "double precision", "float", "float4", "float8",
    "boolean", "bool",
    "date", "time", "timetz", "timestamp", "timestamptz",
    "timestamp with time zone", "timestamp without time zone",
    "time with time zone", "time without time zone",
    "uuid", "json", "jsonb", "bytea", "inet", "interval",
})

#: "varchar(255)", "numeric(12, 2)" — a name from the list above and nothing but digits inside.
_TYPE_RE = re.compile(r"^([a-z][a-z ]*?)\s*(\(\s*\d+\s*(?:,\s*\d+\s*)?\))?$")


def safe_data_type(raw_type: object, default: str = "TEXT") -> str | None:
    """A column type fit to interpolate, or None when it is not a type at all."""
    if raw_type is None or not str(raw_type).strip():
        return default
    m = _TYPE_RE.match(str(raw_type).strip().lower())
    if not m:
        return None
    base, size = m.group(1).strip(), (m.group(2) or "")
    if base not in _ALLOWED_TYPES:
        return None
    # Re-emitted from the parts that were validated, never the caller's original string.
    return (base + re.sub(r"\s+", "", size)).upper()


def quote_ident(name: str) -> str:
    """``name`` as it may be written in a statement: bare when plain and not a keyword."""
    s = str(name)
    if _PLAIN.match(s):
        if s.lower() not in QUOTED_KEYWORDS:
            return s
        # A keyword written bare would have folded to lower case; quoted, it must be spelt so.
        s = s.lower()
    return '"' + s.replace('"', '""') + '"'
