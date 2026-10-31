// Package sqlident is src/sql_identifiers.py: the one rule for a name the engine puts into SQL.
//
// A sheet's "end" column stopped a whole write on `syntax error at or near "end"` (29 Oct 2026).
// A name the system makes is repaired, never refused: a reserved word gets "_value", a leading
// digit gets "col_", and past 63 characters the name is cut and given 8 characters of its hash.
package sqlident

import (
	"crypto/sha1"
	"encoding/hex"
)

// MaxLength is PostgreSQL's identifier length.
const MaxLength = 63

func set(words ...string) map[string]bool {
	m := make(map[string]bool, len(words))
	for _, w := range words {
		m[w] = true
	}
	return m
}

// Reserved is pg_get_keywords() categories R and T on PostgreSQL 16: none can be a bare column.
var Reserved = set(
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
)

// columnNameKeywords is category C: fine bare as a column, but quote_ident quotes them.
var columnNameKeywords = set(
	"between", "bigint", "bit", "boolean", "char", "character", "coalesce", "dec", "decimal",
	"exists", "extract", "float", "greatest", "grouping", "inout", "int", "integer", "interval",
	"json_array", "json_arrayagg", "json_object", "json_objectagg", "least", "national", "nchar",
	"none", "normalize", "nullif", "numeric", "out", "overlay", "position", "precision", "real",
	"row", "setof", "smallint", "substring", "time", "timestamp", "treat", "trim", "values",
	"varchar", "xmlattributes", "xmlconcat", "xmlelement", "xmlexists", "xmlforest",
	"xmlnamespaces", "xmlparse", "xmlpi", "xmlroot", "xmlserialize", "xmltable",
)

// Quoted says whether a (lower-cased) name is a keyword quote_ident would quote.
func Quoted(lower string) bool {
	return Reserved[lower] || columnNameKeywords[lower]
}

// Repair is sql_identifiers.repair: name is already [a-z0-9_]; "" stays "".
func Repair(name, reservedSuffix string) string {
	if name == "" {
		return name
	}
	if name[0] >= '0' && name[0] <= '9' {
		name = "col_" + name
	}
	if Reserved[name] {
		name += reservedSuffix
	}
	if len(name) > MaxLength {
		sum := sha1.Sum([]byte(name))
		name = name[:54] + "_" + hex.EncodeToString(sum[:])[:8]
	}
	return name
}
