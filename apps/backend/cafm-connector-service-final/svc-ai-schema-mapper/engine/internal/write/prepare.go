package write

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"

	"hoistra/engine/internal/arrowtab"
	"hoistra/engine/internal/coerce"
	"hoistra/engine/internal/pgschema"
	"hoistra/engine/internal/protocol"
	"hoistra/engine/internal/pystr"
	"hoistra/engine/internal/rules"
)

// source is one cleaned table, its rows as the Python writer receives them.
type source struct {
	name string
	rows []*rules.Row
}

func loadSources(tables []*arrowtab.Table, renames []Rename) []*source {
	out := make([]*source, 0, len(tables))
	byName := map[string]*source{}
	for _, t := range tables {
		s := &source{name: t.Name, rows: make([]*rules.Row, t.Rows)}
		for r := 0; r < t.Rows; r++ {
			row := rules.NewRow(len(t.Columns))
			for c, name := range t.Columns {
				row.Set(name, t.Cell(c, r))
			}
			s.rows[r] = row
		}
		out = append(out, s)
		byName[t.Name] = s
	}
	// write_node's new-column collision safety net: the values move to the canonical key.
	for _, rn := range renames {
		s := byName[rn.Table]
		if s == nil {
			continue
		}
		for _, row := range s.rows {
			if row.Has(rn.From) && !row.Has(rn.To) {
				v := row.Pop(rn.From)
				row.Set(rn.To, v)
			}
		}
	}
	return out
}

type colDef struct{ name, sqlType string }

// colView is one destination column as information_schema shows it.
type colView struct {
	col       *pgschema.Column
	nullable  bool
	defaulted bool
}

// tableView is a destination table as the writer reads it at the start of a source table. The
// writer reads it again for every source, so a later source routed to the same destination sees
// what an earlier one created, added or widened.
type tableView struct {
	exists bool
	order  []string
	cols   map[string]*colView
	unique [][]string
}

func viewOf(t *pgschema.Table) *tableView {
	v := &tableView{cols: map[string]*colView{}}
	if t == nil || !t.Exists || len(t.Columns) == 0 {
		return v
	}
	v.exists = true
	for _, c := range t.Columns {
		v.order = append(v.order, c.Name)
		v.cols[c.Name] = &colView{col: c, nullable: c.Nullable, defaulted: c.Default != nil}
	}
	v.unique = t.UniqueSets
	return v
}

// madeColumn is a column this run creates, as information_schema will then describe it.
func madeColumn(name, sqlType string) *pgschema.Column {
	switch sqlType {
	case "UUID PRIMARY KEY", "UUID":
		return &pgschema.Column{Name: name, DataType: "uuid", UDT: "uuid", FormatType: "uuid", BaseType: "uuid"}
	case "BIGINT":
		return &pgschema.Column{Name: name, DataType: "bigint", UDT: "int8", FormatType: "bigint", BaseType: "bigint",
			IntBits: 64}
	}
	return &pgschema.Column{Name: name, DataType: "text", UDT: "text", FormatType: "text", BaseType: "text"}
}

// textColumn is a numeric column after the writer widened it to TEXT.
func textColumn(c *pgschema.Column) *pgschema.Column {
	return &pgschema.Column{Name: c.Name, DataType: "text", UDT: "text", FormatType: "text", BaseType: "text",
		Nullable: c.Nullable, Default: c.Default, Position: c.Position}
}

// tablePlan is everything decided about one source table before its rows are written, with the
// per-table state write_node holds while it writes them.
type tablePlan struct {
	src        *source
	dest       string
	created    bool
	createCols []colDef
	add        []colDef
	dropped    []string
	widen      []string // numeric columns whose values are not numbers
	widened    []string // of those, the ones now TEXT
	discard    map[string]bool
	norm       []*rules.Row // normalised, safe-keyed rows, before resolution

	resolveCols map[string]bool             // db_cols while rows are resolved (no column added yet)
	cols        map[string]bool             // db_cols while rows are inserted
	types       map[string]string           // db_col_type_map: the type each value is coerced for
	colInfo     map[string]*pgschema.Column // how asyncpg encodes a value for the column
	nullable    map[string]bool             // db_nullable_cols, read before any DDL of this table
	required    []string                    // db_required_undefaulted, same read
	unique      [][]string                  // unique_sets, same read
	idSerial    bool
	tab         *pgschema.Table // the destination as read before any DDL (nil until then)
}

func (w *writer) destOf(src string) string {
	if d, ok := w.job.Routing[src]; ok {
		return d
	}
	return src
}

var numericTokens = []string{"int", "bigint", "smallint", "numeric", "decimal", "double", "real", "serial"}

func isNumericDBType(t string) bool {
	t = strings.ToLower(t)
	for _, tok := range numericTokens {
		if strings.Contains(t, tok) {
			return true
		}
	}
	return false
}

func idIsSerial(t string) bool {
	t = strings.ToLower(t)
	return strings.Contains(t, "int") || strings.Contains(t, "serial") || strings.Contains(t, "bigint")
}

// prepare plans every source table in write order — destination, the CREATE TABLE a missing
// destination needs, the columns to add, the numeric columns to widen — and, given a DDL
// session, runs that DDL as it goes, each destination's committed before the next starts (as
// Python commits it before loading rows), so each later source sees the schema the earlier ones
// left and no table stays locked while a later one's DDL runs or waits. With ddl nil (plan mode)
// nothing runs and every statement is assumed to succeed.
func (w *writer) prepare(ctx context.Context, q pgschema.Querier, ddl *ddlSession) ([]*tablePlan, error) {
	var withRows []string
	bySrc := map[string]*source{}
	for _, s := range w.sources {
		bySrc[s.name] = s
		if len(s.rows) > 0 {
			withRows = append(withRows, s.name)
		}
	}
	order := rules.WriteOrder(&w.spec, withRows, w.job.Routing, w.job.Hierarchies)
	var dests []string
	seen := map[string]bool{}
	for _, name := range order {
		if d, ok := rules.SafeIdent(w.destOf(name)); ok && !seen[d] {
			seen[d] = true
			dests = append(dests, d)
		}
	}
	info, err := pgschema.Load(ctx, q, w.job.Schema, dests)
	if err != nil {
		return nil, wrapDB(err)
	}
	views := map[string]*tableView{}
	var plans []*tablePlan
	for _, name := range order {
		s := bySrc[name]
		dest, ok := rules.SafeIdent(w.destOf(name))
		if !ok {
			w.logf("warning", fmt.Sprintf("[Node 9] Skipping unsafe table name for DB sync: %s → %s",
				pyRepr(name), pyRepr(w.destOf(name))))
			continue
		}
		if name != w.destOf(name) {
			w.logf("info", fmt.Sprintf("[Node 9] Routing source '%s' → target '%s'", name, dest))
		}
		v := views[dest]
		if v == nil {
			v = viewOf(info[dest])
			views[dest] = v
		}
		p := &tablePlan{src: s, dest: dest, discard: map[string]bool{}, resolveCols: map[string]bool{},
			cols: map[string]bool{}, types: map[string]string{}, colInfo: map[string]*pgschema.Column{},
			nullable: map[string]bool{}, tab: info[dest]}
		var typeOrder []string
		if !v.exists {
			if err := w.createTable(ctx, ddl, p, views); err != nil {
				return nil, err
			}
			v = views[dest]
			for _, c := range v.order {
				typeOrder = append(typeOrder, c)
			}
		} else {
			for _, c := range v.order {
				cv := v.cols[c]
				p.types[c] = cv.col.DataType
				p.colInfo[c] = cv.col
				p.resolveCols[c] = true
				typeOrder = append(typeOrder, c)
				if cv.nullable {
					p.nullable[c] = true
				} else if !cv.defaulted {
					p.required = append(p.required, c)
				}
			}
			p.unique = v.unique
		}

		// Normalise every row and collect the columns the destination lacks, first seen first.
		p.norm = make([]*rules.Row, len(s.rows))
		missing := map[string]string{}
		var missingOrder []string
		for i, raw := range s.rows {
			n := rules.Normalize(&w.spec, dest, raw, w.org)
			safe := rules.NewRow(n.Len())
			for j, k := range n.K {
				sk, ok := rules.SafeIdent(k)
				if !ok {
					continue
				}
				safe.Set(sk, n.V[j])
				if !p.resolveCols[sk] {
					if _, dup := missing[sk]; !dup && !n.V[j].IsNone() && n.V[j].PyStr() != "" {
						missing[sk] = coerce.InferSQLType(n.V[j])
						missingOrder = append(missingOrder, sk)
					}
				}
			}
			p.norm[i] = safe
		}
		if len(missingOrder) > 0 && w.spec.IsKnownCore(dest) {
			approved := map[string]bool{}
			for _, c := range w.job.ApprovedNewColumns[dest] {
				approved[pystr.Lower(c)] = true
			}
			var keep []string
			for _, c := range missingOrder {
				if approved[pystr.Lower(c)] {
					keep = append(keep, c)
				} else {
					p.dropped = append(p.dropped, c)
				}
			}
			if len(p.dropped) > 0 {
				w.logf("warning", fmt.Sprintf("[Node 9] Dropping %d unknown column(s) from core table %s: %s "+
					"— schema is ORM-managed, ALTER TABLE skipped", len(p.dropped), dest, pyList(sortedCopy(p.dropped))))
			}
			missingOrder = keep
		}
		for _, c := range missingOrder {
			p.add = append(p.add, colDef{c, missing[c]})
		}
		for _, c := range p.add {
			if ddl != nil {
				if err := ddl.exec(ctx, fmt.Sprintf("ALTER TABLE %s.%s ADD COLUMN IF NOT EXISTS %s %s",
					w.job.Schema, dest, c.name, c.sqlType)); err != nil {
					return nil, err
				}
				w.logf("info", fmt.Sprintf("[Node 9] Added missing column %s.%s.%s (%s)", w.job.Schema, dest, c.name, c.sqlType))
			}
			mc := madeColumn(c.name, c.sqlType)
			p.types[c.name] = c.sqlType // db_col_type_map.update(missing_columns) keeps the inferred spelling
			p.colInfo[c.name] = mc
			typeOrder = append(typeOrder, c.name)
			v.order = append(v.order, c.name)
			v.cols[c.name] = &colView{col: mc, nullable: true}
		}

		// Numeric columns whose values are codes, not numbers.
		p.idSerial = idIsSerial(p.types["id"])
		for _, c := range typeOrder {
			if (c == "id" && p.idSerial) || !isNumericDBType(p.types[c]) {
				continue
			}
			needsText := false
			for i, rec := range p.norm {
				if i >= w.spec.WidenScanCap {
					break
				}
				val := rec.Get(c)
				if val.IsNone() || pystr.Strip(val.PyStr()) == "" {
					continue
				}
				if _, ok := pystr.ParseFloat(strings.ReplaceAll(val.PyStr(), ",", "")); !ok {
					needsText = true
					break
				}
			}
			if !needsText {
				continue
			}
			if w.spec.IsSystemSupplied(c) {
				p.discard[c] = true
				w.logf("info", fmt.Sprintf("[Node 9]   %s.%s is %s; the run's organisation id does not fit it, so it "+
					"is left unset rather than retyped", dest, c, p.types[c]))
				continue
			}
			p.widen = append(p.widen, c)
		}
		for _, c := range p.widen {
			done := true
			if ddl != nil {
				tx, err := ddl.tx(ctx)
				if err != nil {
					return nil, err
				}
				if done, err = w.widenColumn(ctx, tx, dest, c, p.types[c]); err != nil {
					return nil, err
				}
			} else if p.colInfo[c].WidenBlocked {
				done = false // a plan cannot try the ALTER; a view or constraint on the column would stop it
			}
			if done {
				tc := textColumn(p.colInfo[c])
				p.types[c] = "text"
				p.colInfo[c] = tc
				v.cols[c].col = tc
				p.widened = append(p.widened, c)
			}
		}
		for c := range p.resolveCols {
			p.cols[c] = true
		}
		for _, c := range p.add {
			p.cols[c.name] = true
		}
		for c := range p.discard {
			delete(p.cols, c)
		}
		plans = append(plans, p)
		if ddl != nil {
			if err := ddl.commit(ctx); err != nil {
				return nil, err
			}
			w.progress("ddl", dest, len(plans), len(order))
		}
	}
	return plans, nil
}

// createTable is the writer's CREATE TABLE for a destination that does not exist, its columns
// inferred from the first non-empty raw row. The writer read the table's nullable, required and
// unique columns before it existed, so those stay empty for this source.
func (w *writer) createTable(ctx context.Context, ddl *ddlSession, p *tablePlan, views map[string]*tableView) error {
	p.created = true
	p.createCols = []colDef{{"id", "UUID PRIMARY KEY"}, {"organization_id", "UUID"}}
	for _, r := range p.src.rows {
		if r.Len() == 0 {
			continue
		}
		for i, k := range r.K {
			sk, ok := rules.SafeIdent(k)
			if !ok || sk == "id" || sk == "organization_id" {
				continue
			}
			p.createCols = setColDef(p.createCols, sk, coerce.InferSQLType(r.V[i]))
		}
		break
	}
	if ddl != nil {
		defs := make([]string, len(p.createCols))
		for i, c := range p.createCols {
			defs[i] = c.name + " " + c.sqlType
		}
		if err := ddl.exec(ctx, fmt.Sprintf("CREATE TABLE IF NOT EXISTS %s.%s (%s)", w.job.Schema, p.dest,
			strings.Join(defs, ", "))); err != nil {
			return err
		}
		w.logf("warning", fmt.Sprintf("[Node 9] Created missing table %s.%s with %d columns", w.job.Schema, p.dest,
			len(p.createCols)))
	}
	v := &tableView{exists: true, cols: map[string]*colView{}, unique: [][]string{{"id"}}}
	for _, c := range p.createCols {
		mc := madeColumn(c.name, c.sqlType)
		v.order = append(v.order, c.name)
		v.cols[c.name] = &colView{col: mc, nullable: c.sqlType != "UUID PRIMARY KEY"}
		p.types[c.name] = mc.DataType
		p.colInfo[c.name] = mc
		p.resolveCols[c.name] = true
	}
	views[p.dest] = v
	return nil
}

func setColDef(defs []colDef, name, t string) []colDef {
	for i := range defs {
		if defs[i].name == name {
			defs[i].sqlType = t
			return defs
		}
	}
	return append(defs, colDef{name, t})
}

// widenColumn is the writer's numeric → TEXT widening: in a savepoint, waiting at most five
// seconds for the lock. A widening that fails is logged and the column keeps its type.
func (w *writer) widenColumn(ctx context.Context, tx pgx.Tx, table, col, from string) (bool, error) {
	sp, err := tx.Begin(ctx)
	if err != nil {
		return false, wrapDB(err)
	}
	_, err = sp.Exec(ctx, "SET LOCAL lock_timeout = '5s'")
	if err == nil {
		_, err = sp.Exec(ctx, fmt.Sprintf(`ALTER TABLE %s.%s ALTER COLUMN "%s" TYPE TEXT USING "%s"::text`,
			w.job.Schema, table, col, col))
	}
	if err == nil {
		_, err = sp.Exec(ctx, "SET LOCAL lock_timeout = DEFAULT")
	}
	if err != nil {
		_ = sp.Rollback(ctx)
		if isConnLost(err) {
			return false, connLost(err)
		}
		w.logf("warning", fmt.Sprintf("[Node 9] Could not widen %s.%s to TEXT: %v", table, col, err))
		return false, nil
	}
	if err := sp.Commit(ctx); err != nil {
		return false, wrapDB(err)
	}
	w.logf("warning", fmt.Sprintf("[Node 9] Widened %s.%s (%s → TEXT) — source data is non-numeric (e.g. code "+
		"values); rows kept instead of skipped", table, col, from))
	return true, nil
}

// ddlStatementTimeout caps each DDL statement, lock wait included, as asyncpg's command_timeout
// (45 s, src/db.py) capped every statement of the Python writer.
var ddlStatementTimeout = 45 * time.Second

// beginDDL opens a DDL transaction whose every statement gives up at ddlStatementTimeout.
func beginDDL(ctx context.Context, conn *pgx.Conn) (pgx.Tx, error) {
	tx, err := conn.Begin(ctx)
	if err != nil {
		return nil, wrapDB(err)
	}
	if _, err := tx.Exec(ctx, fmt.Sprintf("SET LOCAL statement_timeout = %d", ddlStatementTimeout.Milliseconds())); err != nil {
		_ = tx.Rollback(context.Background())
		return nil, wrapDB(err)
	}
	return tx, nil
}

// ddlSession runs one destination table's DDL in its own transaction: begun on its first
// statement, committed when the table is done (commit), so an ACCESS EXCLUSIVE lock never
// outlives its table.
type ddlSession struct {
	conn *pgx.Conn
	cur  pgx.Tx
}

func (d *ddlSession) tx(ctx context.Context) (pgx.Tx, error) {
	if d.cur == nil {
		tx, err := beginDDL(ctx, d.conn)
		if err != nil {
			return nil, err
		}
		d.cur = tx
	}
	return d.cur, nil
}

func (d *ddlSession) exec(ctx context.Context, sql string) error {
	tx, err := d.tx(ctx)
	if err != nil {
		return err
	}
	if _, err := tx.Exec(ctx, sql); err != nil {
		return wrapDB(err)
	}
	return nil
}

func (d *ddlSession) commit(ctx context.Context) error {
	if d.cur == nil {
		return nil
	}
	tx := d.cur
	d.cur = nil
	return wrapDB(tx.Commit(ctx))
}

// rollback ends a table's DDL transaction that failed part way.
func (d *ddlSession) rollback() {
	if d.cur != nil {
		_ = d.cur.Rollback(context.Background())
		d.cur = nil
	}
}

// extraFieldDDL is write_node's Phase 0: the DDL the review gates asked for, all or nothing.
func (w *writer) extraFieldDDL(ctx context.Context) error {
	if len(w.job.DDL) == 0 {
		return nil
	}
	tx, err := beginDDL(ctx, w.conn)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(context.Background()) }()
	for i, st := range w.job.DDL {
		w.logf("info", "[Node 9] Executing DDL: "+st.Description)
		if err := execOneStatement(ctx, tx, st.SQL); err != nil {
			if isConnLost(err) {
				return connLost(err)
			}
			text := err.Error()
			if pe, ok := pgPyErr(err, st.SQL, nil); ok {
				text = pe.str
			}
			return protocol.Errorf(protocol.CodeDDL,
				"DDL execution failed at statement %d/%d: '%s'. Database error: %s. All %d previously executed "+
					"statements were rolled back.", i+1, len(w.job.DDL), st.Description, runes(text, 300), i)
		}
	}
	if err := tx.Commit(ctx); err != nil {
		return wrapDB(err)
	}
	w.logf("info", fmt.Sprintf("[Node 9] ✓ DDL transaction committed (%d statements)", len(w.job.DDL)))
	return nil
}

// execOneStatement runs sql as one unnamed prepared statement (the extended protocol), as
// SQLAlchemy + asyncpg ran the gates' DDL in Python: the server refuses a string holding more than
// one command ("cannot insert multiple commands into a prepared statement"). pgx's Exec sends a
// string with no arguments as a simple query, which runs every statement in it.
func execOneStatement(ctx context.Context, tx pgx.Tx, sql string) error {
	return tx.Conn().PgConn().ExecParams(ctx, sql, nil, nil, nil, nil).Read().Err
}

func wrapDB(err error) error {
	if err == nil {
		return nil
	}
	if _, ok := err.(*protocol.Error); ok {
		return err
	}
	if isConnLost(err) {
		return connLost(err)
	}
	return protocol.Errorf(protocol.CodeDB, "%v", err)
}

func sortedCopy(xs []string) []string {
	out := append([]string(nil), xs...)
	for i := 1; i < len(out); i++ {
		for j := i; j > 0 && out[j] < out[j-1]; j-- {
			out[j], out[j-1] = out[j-1], out[j]
		}
	}
	return out
}

// pyList is repr(list_of_str).
func pyList(xs []string) string {
	parts := make([]string, len(xs))
	for i, x := range xs {
		parts[i] = pyRepr(x)
	}
	return "[" + strings.Join(parts, ", ") + "]"
}
