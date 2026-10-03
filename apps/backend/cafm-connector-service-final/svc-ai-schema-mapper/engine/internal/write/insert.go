package write

import (
	"context"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"

	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/coerce"
	"hoistra/engine/internal/pgschema"
	"hoistra/engine/internal/rules"
)

// mismatchTally is type_mismatch_by_col: values that did not fit their column, dropped from the row.
type mismatchTally struct {
	order []string
	count map[string]int
	dtype map[string]string
	first map[string]string
}

func (m *mismatchTally) add(col, dtype, sample string) {
	if m.count == nil {
		m.count, m.dtype, m.first = map[string]int{}, map[string]string{}, map[string]string{}
	}
	if _, seen := m.count[col]; !seen {
		m.order = append(m.order, col)
		m.first[col] = sample
	}
	m.count[col]++
	m.dtype[col] = dtype
}

// counts is an insertion-ordered tally.
type counts struct {
	order []string
	n     map[string]int
}

func (c *counts) add(k string, by int) {
	if c.n == nil {
		c.n = map[string]int{}
	}
	if _, seen := c.n[k]; !seen {
		c.order = append(c.order, k)
	}
	c.n[k] += by
}

// built is one row after the pure part of write_node's insert loop.
type built struct {
	f       *rules.Filtered // filtered and coerced
	code    string          // assets: asset_match_code
	full    *rules.Filtered // with system defaults filled in
	filled  []string
	keys    []rules.KeyMatch
	skipped bool // nothing left to write
	genID   bool // the id is one the writer drew (uuid4), not the source's
}

// build runs the parts of the insert loop that depend only on the row: column filter, generated
// id, coercion (a value that does not fit is dropped and tallied), the asset code, system
// defaults and natural keys. Ids are drawn in row order, as uuid4() is.
func (w *writer) build(p *tablePlan, rows []*rules.Row, tally *mismatchTally) []*built {
	out := make([]*built, len(rows))
	for i, row := range rows {
		var keys []string
		var raw []cell.Cell
		for j, k := range row.K {
			v := row.V[j]
			if !p.cols[k] || v.IsNone() || v.PyStr() == "" || (k == "id" && p.idSerial) {
				continue
			}
			keys = append(keys, k)
			raw = append(raw, v)
		}
		genID := false
		if p.cols["id"] && !p.idSerial && !containsStr(keys, "id") {
			keys = append(keys, "id")
			raw = append(raw, cell.Of(w.ids.next()))
			genID = true
		}
		b := &built{genID: genID}
		out[i] = b
		if len(keys) == 0 {
			b.skipped = true
			continue
		}
		kept := &rules.Filtered{K: make([]string, 0, len(keys)), V: make([]coerce.Value, 0, len(keys))}
		for j, k := range keys {
			dt := p.types[k]
			v := coerce.ForType(raw[j], dt)
			if v.K == coerce.Mismatch {
				tally.add(k, dt, runes(raw[j].PyStr(), 40))
				continue
			}
			kept.K = append(kept.K, k)
			kept.V = append(kept.V, v)
		}
		if len(kept.K) == 0 {
			b.skipped = true
			continue
		}
		b.f = kept
		if p.dest == "assets" {
			b.code, _ = rules.AssetMatchCode(kept)
		}
		full := &rules.Filtered{K: append([]string(nil), kept.K...), V: append([]coerce.Value(nil), kept.V...)}
		for _, rc := range p.required {
			if hasKey(full, rc) || !p.cols[rc] {
				continue
			}
			if d, ok := rules.SystemDefault(rc, p.types[rc], w.org); ok {
				full.Set(rc, defaultValue(d))
				b.filled = append(b.filled, rc)
			}
		}
		b.full = full
		b.keys = rules.NaturalKeys(&w.spec, p.dest, full, p.cols)
	}
	return out
}

func hasKey(f *rules.Filtered, k string) bool { return containsStr(f.K, k) }

func containsStr(xs []string, s string) bool {
	for _, x := range xs {
		if x == s {
			return true
		}
	}
	return false
}

// defaultValue carries a raw system default through the filtered row as the Python object it is.
func defaultValue(c cell.Cell) coerce.Value {
	switch c.K {
	case cell.Int:
		return coerce.Value{K: coerce.Int, S: c.PyStr()}
	case cell.Bool:
		if c.I != 0 {
			return coerce.Value{K: coerce.Bool, S: "true"}
		}
		return coerce.Value{K: coerce.Bool, S: "false"}
	}
	return coerce.Value{K: coerce.Raw, S: c.S}
}

// tableOutcome is what one source table did.
type tableOutcome struct {
	inserted  int
	skipped   int
	merged    int
	dupes     int
	perRow    bool
	abandoned string
}

// insertTable is the insert loop of write_node for one source table: merge assets into the row
// they already have, skip rows already written, and send the rest in chunks of WriteChunk.
func (w *writer) insertTable(ctx context.Context, tx pgx.Tx, p *tablePlan, rows []*rules.Row) (*tableOutcome, error) {
	started := time.Now()
	out := &tableOutcome{}
	tally := &mismatchTally{}
	orphans := &counts{}
	sysFilled := &counts{}
	items := w.build(p, rows, tally)

	if p.dest == "assets" {
		if err := w.prefetchAssets(ctx, tx, p, items); err != nil {
			return nil, err
		}
	}
	known, err := w.prefetchKeys(ctx, tx, p, items)
	if err != nil {
		return nil, err
	}

	var pending []*pendRow
	tableRows := 0
	done := 0
	shown := 0 // the bulk load may have shown more than done while its rows went up
	tick := func(n int) {
		done += n
		w.progress("insert", p.dest, max(done, shown), len(rows))
	}
	collect := func(fo flushOut) {
		tableRows += fo.inserted
		out.skipped += fo.skipped
		w.rowsSkipped += fo.skipped
		for _, c := range fo.orphans.order {
			orphans.add(c, fo.orphans.n[c])
		}
		if fo.abandoned != "" {
			// Python sets _table_abandoned and never reports it; the twenty row errors it keeps
			// would all be the same failure. The line that says why the table stopped goes first.
			w.logf("error", "[Node 9] "+fo.abandoned)
			w.rowError(fo.abandoned)
			out.abandoned = fo.abandoned
		}
		for _, e := range fo.errors {
			w.rowError(e)
		}
		if fo.perRow {
			out.perRow = true
		}
		tick(fo.attempted)
	}
	warnSkipped := func(fo flushOut) {
		if fo.skipped > 0 {
			first := "-"
			if len(fo.errors) > 0 {
				first = fo.errors[0]
			}
			w.logf("warning", fmt.Sprintf("[Node 9] %s: %d row(s) skipped; first: %s", p.dest, fo.skipped, first))
		}
	}
	bulkBefore := w.bulkRows
	if w.bulkReady(p) && w.answeredUpFront(p, items, known) {
		raise := func(v int) {
			if v > shown {
				shown = v
				w.progress("insert", p.dest, shown, len(rows))
			}
		}
		if err := w.insertInRuns(ctx, tx, p, items, known, len(rows), &runState{out: out, sysFilled: sysFilled,
			tick: tick, collect: collect, warn: warnSkipped, done: func() int { return done },
			inserted: func(n int) { tableRows += n }, raise: raise}); err != nil {
			return nil, err
		}
	} else {
		for _, it := range items {
			if it.skipped {
				tick(1)
				continue
			}
			if p.dest == "assets" && it.code != "" {
				existing, err := w.existingAsset(ctx, tx, p, it.code)
				if err != nil {
					return nil, err
				}
				if existing != "" {
					n, ok, err := w.mergeAsset(ctx, tx, p, it.f, existing, it.code)
					if err != nil {
						return nil, err
					}
					if ok {
						tableRows += n
						out.merged++
						w.rowsMerged++
						tick(1)
						continue
					}
				}
			}
			for _, c := range it.filled {
				sysFilled.add(c, 1)
			}
			dupe := false
			for _, km := range it.keys {
				hit, err := w.alreadyWritten(ctx, tx, p, km, known)
				if err != nil {
					return nil, err
				}
				if hit {
					dupe = true
					break
				}
			}
			if dupe {
				out.dupes++
				tick(1)
				continue
			}
			pending = append(pending, w.pendingRow(p, it.full))
			if len(pending) >= w.spec.WriteChunk {
				fo, err := w.flush(ctx, tx, p, pending)
				if err != nil {
					return nil, err
				}
				pending = nil
				collect(fo)
				if fo.abandoned != "" {
					break // the whole table is failing the same way; the next chunk would too
				}
			}
		}
		if len(pending) > 0 {
			fo, err := w.flush(ctx, tx, p, pending)
			if err != nil {
				return nil, err
			}
			collect(fo)
			warnSkipped(fo)
		}
	}
	for _, c := range tally.order {
		msg := fmt.Sprintf("%s.%s: %d value(s) did not fit column type '%s' (e.g. '%s') — dropped; re-map this "+
			"column to a compatible destination at the column-mapping gate.", p.dest, c, tally.count[c], tally.dtype[c],
			tally.first[c])
		w.rowError(msg)
		w.logf("warning", "[Node 9] Type mismatch — "+msg)
	}
	for _, c := range orphans.order {
		msg := fmt.Sprintf("%s.%s: %d row(s) referenced a parent not present in the target — foreign key set to "+
			"NULL so the row was kept. Load the parent rows (or confirm these are expected orphans) if the link is "+
			"required.", p.dest, c, orphans.n[c])
		w.rowError(msg)
		w.logf("warning", "[Node 9] Orphan FK — "+msg)
	}
	w.progress("insert", p.dest, len(rows), len(rows)) // the rows after an abandoned chunk are done too
	elapsed := time.Since(started).Seconds()
	out.inserted = tableRows
	switch {
	case tableRows > 0:
		w.tablesWritten++
		w.rowsInserted += tableRows
		w.logf("info", fmt.Sprintf("[Node 9]   %s: ✓ %d inserted, %d skipped (%.1fs)", p.dest, tableRows, out.skipped, elapsed))
	case out.skipped > 0:
		w.logf("warning", fmt.Sprintf("[Node 9]   %s: 0 inserted, %d skipped (%.1fs)", p.dest, out.skipped, elapsed))
	default:
		w.logf("info", fmt.Sprintf("[Node 9]   %s: 0 rows to insert (%.1fs)", p.dest, elapsed))
	}
	if n := w.bulkRows - bulkBefore; n > 0 {
		w.logf("info", fmt.Sprintf("[Node 9]   %s: %d row(s) bulk-loaded", p.dest, n))
	}
	if out.dupes > 0 {
		w.logf("info", fmt.Sprintf("[Node 9]   %s: %d row(s) already present from an earlier run — skipped, not "+
			"duplicated", p.dest, out.dupes))
	}
	if len(sysFilled.order) > 0 {
		parts := make([]string, 0, len(sysFilled.order))
		for _, c := range sortedCopy(sysFilled.order) {
			parts = append(parts, fmt.Sprintf("%s x%d", c, sysFilled.n[c]))
		}
		w.logf("info", fmt.Sprintf("[Node 9]   %s: filled required column(s) the source does not carry — %s",
			p.dest, strings.Join(parts, ", ")))
	}
	return out, nil
}

// pendingRow is one row ready for _build_dml_for_row: its statement, the Python objects bound to
// it, and what the engine sends for each — or the first parameter asyncpg would refuse.
func (w *writer) pendingRow(p *tablePlan, f *rules.Filtered) *pendRow {
	r := &pendRow{f: f, cols: f.K, sql: w.insertSQL(p, f.K)}
	r.stmt = stmtName(r.sql)
	r.vals = make([]pyVal, len(f.K))
	r.args = make([]any, len(f.K))
	for i, k := range f.K {
		r.vals[i] = pyOf(f.V[i])
		arg, why := r.vals[i].encode(p.colInfo[k])
		if why != "" && r.bindAt == 0 {
			r.bindAt, r.bindWhy = i+1, why
		}
		r.args[i] = arg
	}
	return r
}

// insertSQL is _build_dml_for_row as SQLAlchemy compiles it for asyncpg.
func (w *writer) insertSQL(p *tablePlan, cols []string) string {
	ph := make([]string, len(cols))
	for i := range cols {
		ph[i] = fmt.Sprintf("$%d", i+1)
	}
	head := fmt.Sprintf("INSERT INTO %s.%s (%s) VALUES (%s) ", w.job.Schema, p.dest, strings.Join(cols, ", "),
		strings.Join(ph, ", "))
	if p.dest == "assets" {
		var upd []string
		for _, c := range cols {
			switch c {
			case "id", "organization_id", "serial_number", "asset_code":
			default:
				upd = append(upd, c+" = EXCLUDED."+c)
			}
		}
		for _, key := range [][]string{{"organization_id", "serial_number"}, {"organization_id", "asset_code"}} {
			if containsAll(cols, key) && hasUniqueSet(p.unique, key) {
				target := strings.Join(key, ", ")
				if len(upd) > 0 {
					return head + "ON CONFLICT (" + target + ") DO UPDATE SET " + strings.Join(upd, ", ")
				}
				return head + "ON CONFLICT (" + target + ") DO NOTHING"
			}
		}
	}
	return head + "ON CONFLICT DO NOTHING"
}

func containsAll(cols, want []string) bool {
	for _, w := range want {
		found := false
		for _, c := range cols {
			if c == w {
				found = true
				break
			}
		}
		if !found {
			return false
		}
	}
	return true
}

// hasUniqueSet is `frozenset(key) in unique_sets`.
func hasUniqueSet(sets [][]string, key []string) bool {
	k := sortedCopy(key)
	for _, s := range sets {
		if len(s) != len(k) {
			continue
		}
		same := true
		for i := range s {
			if s[i] != k[i] {
				same = false
				break
			}
		}
		if same {
			return true
		}
	}
	return false
}

// ── natural keys ──────────────────────────────────────────────────────────────────────────────

func nkCacheKey(table string, km rules.KeyMatch) string {
	var b strings.Builder
	b.WriteString(table)
	for _, c := range km.Cols {
		b.WriteString("\x00")
		b.WriteString(c)
	}
	b.WriteString("\x01")
	for _, v := range km.Vals {
		b.WriteString("\x00")
		b.WriteString(v.PyStr())
	}
	return b.String()
}

// alreadyWritten is _already_written: cached per key, misses included, for the whole run.
func (w *writer) alreadyWritten(ctx context.Context, tx pgx.Tx, p *tablePlan, km rules.KeyMatch,
	known map[string]bool) (bool, error) {
	ck := nkCacheKey(p.dest, km)
	if v, ok := w.nkSeen[ck]; ok {
		return v, nil
	}
	v, ok := known[ck]
	if !ok {
		var err error
		if v, err = w.keyLookup(ctx, tx, p, km); err != nil {
			return false, err
		}
	}
	w.nkSeen[ck] = v
	return v, nil
}

// keyLookup is the query _already_written sends, one key at a time, in a savepoint.
func (w *writer) keyLookup(ctx context.Context, tx pgx.Tx, p *tablePlan, km rules.KeyMatch) (bool, error) {
	conds := make([]string, len(km.Cols))
	args := make([]any, 0, len(km.Cols)+1)
	for i, c := range km.Cols {
		if km.Vals[i].IsNull() {
			conds[i] = c + " IS NULL" // an empty optional column matches only an empty one
			continue
		}
		args = append(args, nil)
		conds[i] = keyCond(c, "$"+strconv.Itoa(len(args)), p.colInfo[c])
		arg, why := pyOf(km.Vals[i]).encode(p.colInfo[c])
		if why != "" {
			return false, nil // asyncpg refuses the value: the lookup raises and counts as no match
		}
		args[len(args)-1] = arg
	}
	if p.cols["organization_id"] {
		args = append(args, w.org)
		conds = append(conds, fmt.Sprintf("organization_id::text = $%d", len(args)))
	}
	rows, err := w.fetch(ctx, tx, fmt.Sprintf("SELECT 1 FROM %s.%s WHERE %s LIMIT 1", w.job.Schema, p.dest,
		strings.Join(conds, " AND ")), args...)
	if err != nil {
		return false, err
	}
	return len(rows) > 0, nil
}

// prefetchKeys answers, in one query per natural key, every key of the table that the run has not
// looked up yet. Python asks one key at a time as it reaches each row; nothing this loop writes can
// change an answer before Python would ask (assets, the one table it merges into, has no natural
// key, and every key an inserted row carries was asked for before the row went in).
func (w *writer) prefetchKeys(ctx context.Context, tx pgx.Tx, p *tablePlan, items []*built) (map[string]bool, error) {
	known := map[string]bool{}
	groups := w.spec.NaturalKeys[p.dest]
	for _, spec := range groups {
		g := make([]string, len(spec))
		optional := make([]bool, len(spec))
		for j, c := range spec {
			g[j], optional[j] = rules.KeyCol(c)
		}
		var keys []rules.KeyMatch
		var cks []string
		seen := map[string]bool{}
		for _, it := range items {
			if it.skipped {
				continue
			}
			for _, km := range it.keys {
				if !sameCols(km.Cols, g) {
					continue
				}
				ck := nkCacheKey(p.dest, km)
				if _, cached := w.nkSeen[ck]; cached || seen[ck] {
					continue
				}
				seen[ck] = true
				keys = append(keys, km)
				cks = append(cks, ck)
			}
		}
		if len(keys) == 0 {
			continue
		}
		cols := make([][]*string, len(g))
		var idx []int
		for i, km := range keys {
			texts := make([]*string, len(g))
			refused := false
			for j, c := range g {
				if km.Vals[j].IsNull() {
					continue // only an optional column can be empty: it stays NULL
				}
				arg, why := pyOf(km.Vals[j]).encode(p.colInfo[c])
				if why != "" {
					refused = true
					break
				}
				t := arg.(string)
				texts[j] = &t
			}
			if refused {
				known[cks[i]] = false
				continue
			}
			for j := range g {
				cols[j] = append(cols[j], texts[j])
			}
			idx = append(idx, i)
		}
		if len(idx) == 0 {
			continue
		}
		unn := make([]string, len(g))
		names := make([]string, len(g))
		conds := make([]string, len(g))
		args := make([]any, 0, len(g)+1)
		for j, c := range g {
			unn[j] = fmt.Sprintf("$%d::text[]", j+1)
			names[j] = fmt.Sprintf("v%d", j)
			conds[j] = keyCond("t."+c, fmt.Sprintf("k.v%d::%s", j, p.colInfo[c].BaseType), p.colInfo[c])
			if optional[j] {
				// IS NOT DISTINCT FROM: an empty optional column matches only an empty one
				conds[j] = strings.Replace(conds[j], " = ", " IS NOT DISTINCT FROM ", 1)
			}
			args = append(args, cols[j])
		}
		if p.cols["organization_id"] {
			conds = append(conds, fmt.Sprintf("t.organization_id::text = $%d", len(g)+1))
			args = append(args, w.org)
		}
		sql := fmt.Sprintf("SELECT k.i FROM unnest(%s) WITH ORDINALITY AS k(%s, i) WHERE EXISTS (SELECT 1 FROM %s.%s t "+
			"WHERE %s)", strings.Join(unn, ", "), strings.Join(names, ", "), w.job.Schema, p.dest, strings.Join(conds, " AND "))
		rows, ok, err := w.tryFetch(ctx, tx, sql, args...)
		if err != nil {
			return nil, err
		}
		if !ok {
			continue // asked one at a time instead, exactly as Python asks
		}
		hit := map[int]bool{}
		for _, r := range rows {
			if n, ok := r[0].(int64); ok {
				hit[int(n)-1] = true
			}
		}
		for pos, i := range idx {
			known[cks[i]] = hit[pos]
		}
	}
	return known, nil
}

// keyCond compares a key column with a value as _already_written does. A numeric(p,s) column
// holds the value rounded to s places, so the value is rounded the same way first.
func keyCond(col, val string, info *pgschema.Column) string {
	if m := numericScale.FindStringSubmatch(info.FormatType); m != nil {
		return fmt.Sprintf("%s = round(%s::numeric, %s)", col, val, m[1])
	}
	return col + " = " + val
}

var numericScale = regexp.MustCompile(`^numeric\(\d+,(\d+)\)$`)

func sameCols(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// ── asset merges ──────────────────────────────────────────────────────────────────────────────

// assetIndex answers _existing_asset_id from one read of every code the table carries. A code
// the read found on two assets, or one an earlier merge may have taken away from an asset, is
// asked the way Python asks (its LIMIT 1 picks one of the two).
type assetIndex struct {
	asked  map[string]bool
	ids    map[string][]string
	codeOf map[string]string
	stale  map[string]bool
}

func (w *writer) prefetchAssets(ctx context.Context, tx pgx.Tx, p *tablePlan, items []*built) error {
	ix := &assetIndex{asked: map[string]bool{}, ids: map[string][]string{}, codeOf: map[string]string{},
		stale: map[string]bool{}}
	w.assetIx = ix
	col := p.colInfo["asset_code"]
	if col == nil {
		return nil
	}
	var codes []string
	for _, it := range items {
		if it.skipped || it.code == "" || ix.asked[it.code] {
			continue
		}
		if _, cached := w.assetIDs[it.code]; cached {
			continue
		}
		if _, why := (pyVal{k: pyStr, s: it.code}).encode(col); why != "" {
			continue
		}
		ix.asked[it.code] = true
		codes = append(codes, it.code)
	}
	if len(codes) == 0 {
		return nil
	}
	sql := fmt.Sprintf(`SELECT k.code, a.id::text, coalesce(a.asset_code::text, '')
  FROM unnest($1::text[]) AS k(code) JOIN %[1]s.assets a ON a.asset_code = k.code::%[2]s
 WHERE a.organization_id::text = $2
UNION
SELECT k.code, a.id::text, coalesce(a.asset_code::text, '')
  FROM unnest($1::text[]) AS k(code) JOIN %[1]s.assets a ON a.id::text = k.code
 WHERE a.organization_id::text = $2`, w.job.Schema, col.BaseType)
	rows, ok, err := w.tryFetch(ctx, tx, sql, codes, w.org)
	if err != nil {
		return err
	}
	if !ok {
		ix.asked = map[string]bool{}
		return nil
	}
	for _, r := range rows {
		code, id, current := fmt.Sprint(r[0]), fmt.Sprint(r[1]), fmt.Sprint(r[2])
		ix.ids[code] = appendUnique(ix.ids[code], id)
		ix.codeOf[id] = current
	}
	return nil
}

func appendUnique(xs []string, x string) []string {
	for _, y := range xs {
		if y == x {
			return xs
		}
	}
	return append(xs, x)
}

// existingAsset is _existing_asset_id: hits and misses cached by the exact code for the run.
func (w *writer) existingAsset(ctx context.Context, tx pgx.Tx, p *tablePlan, code string) (string, error) {
	if v, ok := w.assetIDs[code]; ok {
		return v, nil
	}
	ix := w.assetIx
	v := ""
	switch {
	case ix != nil && ix.asked[code] && !ix.stale[code] && len(ix.ids[code]) <= 1:
		if len(ix.ids[code]) == 1 {
			v = ix.ids[code][0]
		}
	default:
		if col := p.colInfo["asset_code"]; col != nil {
			if _, why := (pyVal{k: pyStr, s: code}).encode(col); why != "" {
				break // asyncpg refuses the code for this column: the lookup raises, no match
			}
		}
		rows, err := w.fetch(ctx, tx, fmt.Sprintf("SELECT id::text FROM %s.assets WHERE organization_id::text = $1 "+
			"AND (asset_code = $2 OR id::text = $2) LIMIT 1", w.job.Schema), w.org, code)
		if err != nil {
			return "", err
		}
		if len(rows) > 0 && rows[0][0] != nil {
			v = fmt.Sprint(rows[0][0])
		}
	}
	w.assetIDs[code] = v
	return v, nil
}

// mergeAsset is build_asset_merge_update run in a savepoint: the re-imported row folded into the
// asset the organisation already has. A merge that fails is logged and the row is inserted instead.
func (w *writer) mergeAsset(ctx context.Context, tx pgx.Tx, p *tablePlan, f *rules.Filtered, existing,
	code string) (int, bool, error) {
	var sets []string
	var vals []pyVal
	var args []any
	hasUpdatedAt := false
	for i, c := range f.K {
		if w.spec.IsMergeKeep(c) || f.V[i].K == coerce.Null {
			continue
		}
		pv := pyOf(f.V[i])
		vals = append(vals, pv)
		arg, why := pv.encode(p.colInfo[c])
		n := len(vals)
		if c == "building_id" {
			sets = append(sets, fmt.Sprintf("building_id = COALESCE(building_id, $%d)", n))
		} else {
			sets = append(sets, fmt.Sprintf("%s = $%d", c, n))
		}
		if c == "updated_at" {
			hasUpdatedAt = true
		}
		if why != "" {
			e := bindPyErr(n, pv, why, "", nil)
			w.logf("warning", fmt.Sprintf("[Node 9] assets: merge into existing %s for code %s failed (%s); "+
				"inserting instead", existing, pyRepr(code), runes(e.str, 120)))
			return 0, false, nil
		}
		args = append(args, arg)
	}
	if !hasUpdatedAt {
		sets = append(sets, "updated_at = now()")
	}
	vals = append(vals, pyVal{k: pyStr, s: existing})
	args = append(args, existing)
	sql := fmt.Sprintf("UPDATE %s.assets SET %s WHERE id::text = $%d", w.job.Schema, strings.Join(sets, ", "), len(args))
	n, perr, err := w.execOne(ctx, tx, sql, args)
	if err != nil {
		return 0, false, err
	}
	if perr != nil {
		e, _ := pgPyErr(perr, sql, vals)
		w.logf("warning", fmt.Sprintf("[Node 9] assets: merge into existing %s for code %s failed (%s); inserting "+
			"instead", existing, pyRepr(code), runes(e.str, 120)))
		return 0, false, nil
	}
	if ix := w.assetIx; ix != nil {
		if v, ok := f.Get("asset_code"); ok && v.K != coerce.Null {
			newCode := v.PyStr()
			if old := ix.codeOf[existing]; old != "" && old != newCode {
				if _, asked := w.assetIDs[old]; !asked {
					ix.stale[old] = true
				}
			}
			ix.codeOf[existing] = newCode
		}
	}
	return n, true, nil
}

// ── lookups ───────────────────────────────────────────────────────────────────────────────────

// fetch is write_node._fetch: a lookup in a savepoint; one that fails is logged and finds nothing.
// Only a lost connection is an error.
func (w *writer) fetch(ctx context.Context, tx pgx.Tx, sql string, args ...any) ([][]any, error) {
	rows, ok, err := w.tryFetch(ctx, tx, sql, args...)
	if err != nil || ok {
		return rows, err
	}
	return nil, nil
}

func (w *writer) tryFetch(ctx context.Context, tx pgx.Tx, sql string, args ...any) ([][]any, bool, error) {
	sp, err := tx.Begin(ctx)
	if err != nil {
		return nil, false, wrapDB(err)
	}
	rows, err := sp.Query(ctx, sql, args...)
	var out [][]any
	if err == nil {
		for rows.Next() {
			vals, verr := rows.Values()
			if verr != nil {
				err = verr
				break
			}
			out = append(out, vals)
		}
		rows.Close()
		if err == nil {
			err = rows.Err()
		}
	}
	if err != nil {
		_ = sp.Rollback(ctx)
		if isConnLost(err) {
			return nil, false, connLost(err)
		}
		w.logf("warning", "[Node 9] lookup failed (treated as no match): "+runes(err.Error(), 200))
		return nil, false, nil
	}
	if err := sp.Commit(ctx); err != nil {
		return nil, false, wrapDB(err)
	}
	return out, true, nil
}
