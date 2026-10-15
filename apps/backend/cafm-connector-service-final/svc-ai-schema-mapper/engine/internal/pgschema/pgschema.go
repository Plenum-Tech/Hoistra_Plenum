// Package pgschema reads, read-only, what the writer needs to know about destination tables:
// columns and their types exactly as information_schema names them (the Python writer's coercion
// dispatches on those strings), unique sets (the same query write_node uses for ON CONFLICT
// targets), foreign keys in the order Postgres raises their violations, and enum labels.
package pgschema

import (
	"context"
	"fmt"
	"sort"

	"github.com/jackc/pgx/v5"
)

// Querier is the slice of pgx the loader needs (a *pgx.Conn or a pgx.Tx).
type Querier interface {
	Query(ctx context.Context, sql string, args ...any) (pgx.Rows, error)
}

type Column struct {
	Name         string
	DataType     string // information_schema.columns.data_type
	UDT          string // udt_name (an enum's type name for USER-DEFINED)
	FormatType   string // format_type(atttypid, atttypmod): "character varying(8)", "numeric(6,2)"
	BaseType     string // format_type(atttypid, NULL): the type a parameter compared with it takes
	Nullable     bool
	Default      *string
	MaxChars     *int
	NumPrecision *int
	NumScale     *int
	EnumLabels   []string // for USER-DEFINED enum columns, in sort order
	IntBits      int      // 16 / 32 / 64 for smallint / integer / bigint, else 0
	Position     int
	// WidenBlocked: ALTER … TYPE TEXT would fail — a view, rule or policy reads the column, a CHECK or
	// foreign key constrains it, or an index expression/predicate, a generated column or a trigger uses it.
	WidenBlocked bool
	Identity     bool   // GENERATED … AS IDENTITY: like a serial, an omitted value draws from a sequence
	Domain       string // information_schema domain_name: a domain may carry its own default
	// DefaultUsesObject: the server records that the column's default uses a function, operator or
	// sequence of the database's own (built-in functions are not recorded) — a call nothing can see
	// in the default's text, quoted or schema-qualified as it may be.
	DefaultUsesObject bool
}

type FK struct {
	Name       string
	Trigger    string // the child-side insert check trigger; Postgres fires these in name order
	Columns    []string
	RefSchema  string
	RefTable   string
	RefColumns []string
}

type Table struct {
	Schema           string
	Name             string
	Exists           bool
	Columns          []*Column
	ByName           map[string]*Column
	UniqueSets       [][]string // each plain unique index (PK included), as write_node reads them
	NullsNotDistinct []bool
	FKs              []FK
	Kind             string // pg_class.relkind: "r" table, "p" partitioned table, "v" view, "f" foreign table
	UserTriggers     bool   // a trigger that is not a constraint's own (RI checks are internal)
	Rules            bool
	Partition        bool // a partition of a partitioned table
	Inherits         bool // has an inheritance parent or child (partitions included)
	RowSecurity      bool // row-level security is enabled
	// CallsUserCode: something an insert evaluates per row against the table — a CHECK or exclusion
	// constraint, an index expression, predicate or operator class — uses a function or operator of
	// the database's own (built-ins are not recorded), which may read the table: row by row it sees
	// the rows written before; inside one statement it would not.
	CallsUserCode bool
}

const columnsSQL = `
SELECT c.table_name, c.column_name, c.data_type, c.udt_name, c.is_nullable, c.column_default,
       c.character_maximum_length, c.numeric_precision, c.numeric_scale,
       format_type(a.atttypid, a.atttypmod), format_type(a.atttypid, NULL), c.ordinal_position,
       c.is_identity = 'YES', coalesce(c.domain_name::text, ''),
       EXISTS (SELECT 1 FROM pg_attrdef ad
                 JOIN pg_depend d ON d.classid = 'pg_attrdef'::regclass AND d.objid = ad.oid
                WHERE ad.adrelid = t.oid AND ad.adnum = a.attnum
                  AND d.refclassid IN ('pg_proc'::regclass, 'pg_operator'::regclass, 'pg_class'::regclass)
                  AND NOT (d.refclassid = 'pg_class'::regclass AND d.refobjid = t.oid))
  FROM information_schema.columns c
  JOIN pg_namespace n ON n.nspname = c.table_schema
  JOIN pg_class t ON t.relnamespace = n.oid AND t.relname = c.table_name
  JOIN pg_attribute a ON a.attrelid = t.oid AND a.attname = c.column_name
 WHERE c.table_schema = $1 AND c.table_name = ANY($2)
 ORDER BY c.table_name, c.ordinal_position`

// The same pg_index read as write_node.py's unique_sets (plain unique indexes, PK included,
// partial indexes excluded); indnullsnotdistinct read through to_jsonb so servers before
// Postgres 15 (no such column) still answer.
const uniqueSQL = `
SELECT t.relname, i.indexrelid::bigint, a.attname,
       coalesce((to_jsonb(i) ->> 'indnullsnotdistinct')::boolean, false)
  FROM pg_index i
  JOIN pg_class t ON t.oid = i.indrelid
  JOIN pg_namespace n ON n.oid = t.relnamespace
  CROSS JOIN LATERAL unnest(i.indkey) AS k(attnum)
  JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
 WHERE n.nspname = $1 AND t.relname = ANY($2) AND i.indisunique AND i.indpred IS NULL
 ORDER BY t.relname, i.indexrelid`

const fkSQL = `
SELECT t.relname, con.conname, tg.tgname,
       ARRAY(SELECT attname FROM unnest(con.conkey) WITH ORDINALITY k(num, ord)
              JOIN pg_attribute ON attrelid = con.conrelid AND attnum = k.num ORDER BY k.ord),
       rn.nspname, rt.relname,
       ARRAY(SELECT attname FROM unnest(con.confkey) WITH ORDINALITY k(num, ord)
              JOIN pg_attribute ON attrelid = con.confrelid AND attnum = k.num ORDER BY k.ord)
  FROM pg_constraint con
  JOIN pg_class t ON t.oid = con.conrelid
  JOIN pg_namespace n ON n.oid = t.relnamespace
  JOIN pg_class rt ON rt.oid = con.confrelid
  JOIN pg_namespace rn ON rn.oid = rt.relnamespace
  JOIN pg_trigger tg ON tg.tgconstraint = con.oid AND tg.tgrelid = con.conrelid
                    AND tg.tgfoid = 'pg_catalog."RI_FKey_check_ins"'::regproc
 WHERE con.contype = 'f' AND n.nspname = $1 AND t.relname = ANY($2)
 ORDER BY t.relname, tg.tgname`

// What makes ALTER COLUMN … TYPE TEXT fail: the objects that depend on the column and that
// Postgres cannot carry across a type change.
const widenBlockedSQL = `
SELECT DISTINCT c.relname, a.attname
  FROM pg_depend d
  JOIN pg_class c ON c.oid = d.refobjid
  JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = d.refobjsubid
 WHERE n.nspname = $1 AND c.relname = ANY($2) AND d.refobjsubid > 0
   AND (d.classid IN ('pg_rewrite'::regclass, 'pg_policy'::regclass, 'pg_trigger'::regclass)
        OR (d.classid = 'pg_constraint'::regclass
            AND EXISTS (SELECT 1 FROM pg_constraint k WHERE k.oid = d.objid AND k.contype IN ('c', 'f')))
        OR (d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.objsubid > 0)
        OR (d.classid = 'pg_class'::regclass
            AND EXISTS (SELECT 1 FROM pg_index i WHERE i.indexrelid = d.objid
                         AND (i.indexprs IS NOT NULL OR i.indpred IS NOT NULL)
                         AND (NOT (a.attnum = ANY (i.indkey::int2[]))
                              OR coalesce(pg_get_expr(i.indpred, i.indrelid), '') ~ ('\m' || a.attname || '\M')
                              OR coalesce(pg_get_expr(i.indexprs, i.indrelid), '') ~ ('\m' || a.attname || '\M')))))`

const relSQL = `
SELECT c.relname, c.relkind::text, c.relhasrules,
       EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgrelid = c.oid AND NOT t.tgisinternal),
       c.relispartition, EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = c.oid OR i.inhparent = c.oid),
       c.relrowsecurity,
       EXISTS (SELECT 1 FROM pg_constraint k
                 JOIN pg_depend d ON d.classid = 'pg_constraint'::regclass AND d.objid = k.oid
                                 AND d.refclassid IN ('pg_proc'::regclass, 'pg_operator'::regclass)
                WHERE k.conrelid = c.oid AND k.contype IN ('c', 'x'))
       OR EXISTS (SELECT 1 FROM pg_index i
                    JOIN pg_depend d ON d.classid = 'pg_class'::regclass AND d.objid = i.indexrelid
                                    AND d.refclassid IN ('pg_proc'::regclass, 'pg_operator'::regclass,
                                                         'pg_opclass'::regclass, 'pg_opfamily'::regclass)
                   WHERE i.indrelid = c.oid)
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = $1 AND c.relname = ANY($2)`

const enumSQL = `
SELECT t.typname, e.enumlabel
  FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid
 ORDER BY t.typname, e.enumsortorder`

// Load reads the named tables of one schema. A table that does not exist comes back with
// Exists=false (the writer creates it).
func Load(ctx context.Context, q Querier, schema string, tables []string) (map[string]*Table, error) {
	out := make(map[string]*Table, len(tables))
	for _, name := range tables {
		out[name] = &Table{Schema: schema, Name: name, ByName: map[string]*Column{}}
	}
	enums := map[string][]string{}
	if err := each(ctx, q, enumSQL, nil, func(r pgx.Rows) error {
		var typ, label string
		if err := r.Scan(&typ, &label); err != nil {
			return err
		}
		enums[typ] = append(enums[typ], label)
		return nil
	}); err != nil {
		return nil, fmt.Errorf("enum labels: %w", err)
	}
	if err := each(ctx, q, columnsSQL, []any{schema, tables}, func(r pgx.Rows) error {
		var tname, nullable string
		c := &Column{}
		var maxc, prec, scale *int32
		var pos int32
		if err := r.Scan(&tname, &c.Name, &c.DataType, &c.UDT, &nullable, &c.Default, &maxc, &prec, &scale,
			&c.FormatType, &c.BaseType, &pos, &c.Identity, &c.Domain, &c.DefaultUsesObject); err != nil {
			return err
		}
		c.Nullable = nullable == "YES"
		c.MaxChars, c.NumPrecision, c.NumScale = intp(maxc), intp(prec), intp(scale)
		c.Position = int(pos)
		switch c.DataType {
		case "smallint":
			c.IntBits = 16
		case "integer":
			c.IntBits = 32
		case "bigint":
			c.IntBits = 64
		}
		if c.DataType == "USER-DEFINED" {
			c.EnumLabels = enums[c.UDT]
		}
		t := out[tname]
		t.Exists = true
		t.Columns = append(t.Columns, c)
		t.ByName[c.Name] = c
		return nil
	}); err != nil {
		return nil, fmt.Errorf("columns: %w", err)
	}
	type idxKey struct {
		table string
		idx   int64
	}
	sets := map[idxKey][]string{}
	nnd := map[idxKey]bool{}
	var order []idxKey
	if err := each(ctx, q, uniqueSQL, []any{schema, tables}, func(r pgx.Rows) error {
		var k idxKey
		var col string
		var notDistinct bool
		if err := r.Scan(&k.table, &k.idx, &col, &notDistinct); err != nil {
			return err
		}
		if _, seen := sets[k]; !seen {
			order = append(order, k)
		}
		sets[k] = append(sets[k], col)
		nnd[k] = notDistinct
		return nil
	}); err != nil {
		return nil, fmt.Errorf("unique indexes: %w", err)
	}
	for _, k := range order {
		cols := append([]string(nil), sets[k]...)
		sort.Strings(cols)
		t := out[k.table]
		t.UniqueSets = append(t.UniqueSets, cols)
		t.NullsNotDistinct = append(t.NullsNotDistinct, nnd[k])
	}
	if err := each(ctx, q, widenBlockedSQL, []any{schema, tables}, func(r pgx.Rows) error {
		var tname, col string
		if err := r.Scan(&tname, &col); err != nil {
			return err
		}
		if c := out[tname].ByName[col]; c != nil {
			c.WidenBlocked = true
		}
		return nil
	}); err != nil {
		return nil, fmt.Errorf("column dependencies: %w", err)
	}
	if err := each(ctx, q, relSQL, []any{schema, tables}, func(r pgx.Rows) error {
		var tname string
		var kind string
		var rules, triggers, partition, inherits, rls, checks bool
		if err := r.Scan(&tname, &kind, &rules, &triggers, &partition, &inherits, &rls, &checks); err != nil {
			return err
		}
		if t := out[tname]; t != nil {
			t.Kind, t.Rules, t.UserTriggers = kind, rules, triggers
			t.Partition, t.Inherits, t.RowSecurity, t.CallsUserCode = partition, inherits, rls, checks
		}
		return nil
	}); err != nil {
		return nil, fmt.Errorf("relations: %w", err)
	}
	if err := each(ctx, q, fkSQL, []any{schema, tables}, func(r pgx.Rows) error {
		var tname string
		var fk FK
		if err := r.Scan(&tname, &fk.Name, &fk.Trigger, &fk.Columns, &fk.RefSchema, &fk.RefTable, &fk.RefColumns); err != nil {
			return err
		}
		out[tname].FKs = append(out[tname].FKs, fk)
		return nil
	}); err != nil {
		return nil, fmt.Errorf("foreign keys: %w", err)
	}
	return out, nil
}

func each(ctx context.Context, q Querier, sql string, args []any, fn func(pgx.Rows) error) error {
	rows, err := q.Query(ctx, sql, args...)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		if err := fn(rows); err != nil {
			return err
		}
	}
	return rows.Err()
}

func intp(v *int32) *int {
	if v == nil {
		return nil
	}
	x := int(*v)
	return &x
}
