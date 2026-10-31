package write

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"regexp"
	"strconv"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"

	"hoistra/engine/internal/pgschema"
)

// The bulk load. A run of consecutive chunks that the chunked path would each send as one
// statement — every row clean and of one shape — reaches the server in one piece: COPY into a
// temporary table, then one INSERT … SELECT in the rows' order with the chunked path's ON CONFLICT
// clause. A value a column repeats travels once, in a small dictionary table, and each row carries
// its code. The server parses every value with the input function a bound parameter goes through,
// into a column of the destination's own type, so the rows that land are the chunked path's rows.
// Any error rolls the attempt back; the run is then halved and retried, and a single chunk that
// still fails goes the chunked way — which decides that chunk's rows, errors and counts exactly as
// before. What changes is the bytes on the wire: on a slow uplink they are the write's whole cost.

const (
	defaultBulkMinRows = 2000     // a shorter run goes the chunked way
	bulkMaxRows        = 50000    // rows per bulk statement
	bulkMaxBytes       = 64 << 20 // and about this much text: one statement's COPY data is held in memory
	bulkMaxFailures    = 12       // failed attempts a table may make; the rest of it then goes chunked
)

func (w *writer) bulkMinRows() int {
	if w.job.BulkMinRows > 0 {
		return w.job.BulkMinRows
	}
	return defaultBulkMinRows
}

// bulkReady says whether a table may take the bulk load at all: a plain table that existed before
// the write — not partitioned, not a partition, in no inheritance — with nothing that sees the rows
// one statement at a time: no trigger or rule of its own (the chunked path fires those once per row
// statement), no row-level security, no CHECK, exclusion constraint or index that uses a function
// or operator of the database's own (row by row they see the rows written before; in one statement
// they would not), no foreign key back to itself (in one statement a row may name a row after it,
// which the chunked path refuses), and no domain-typed column. Assets merge row by row and never
// take it.
func (w *writer) bulkReady(p *tablePlan) bool {
	t := p.tab
	if w.job.BulkMinRows < 0 || w.bulkOff || p.dest == "assets" || t == nil || !t.Exists || t.Kind != "r" ||
		t.Partition || t.Inherits || t.UserTriggers || t.Rules || t.RowSecurity || t.CallsUserCode {
		return false
	}
	for _, fk := range t.FKs {
		if fk.RefSchema == t.Schema && fk.RefTable == t.Name {
			return false
		}
	}
	for _, c := range t.Columns {
		if c.Domain != "" {
			// A domain may carry a default of its own, which the column's default does not show
			// and which a staging column of the domain's type would draw for every staged row.
			return false
		}
	}
	return true
}

var (
	sqlLiteral = regexp.MustCompile(`'(?:[^']|'')*'`)
	sqlCall    = regexp.MustCompile(`([A-Za-z_][A-Za-z0-9_$.]*)\s*\(`)
)

// clockDefaults are the calls an omitted column's default may make. A rolled-back attempt leaves
// nothing behind for them, where a sequence — the parity database's id function included — would
// have moved on and the chunked retry would then draw different values; and each gives the same
// value in one statement as row by row (now() is the transaction's, clock_timestamp() each row's;
// statement_timestamp() is the statement's, so it is not one of them).
var clockDefaults = map[string]bool{"now": true, "timezone": true, "clock_timestamp": true,
	"transaction_timestamp": true}

// omittedDefaultsAreSafe says whether the columns a row of this shape leaves to their defaults
// draw on nothing a failed attempt would use up: no identity column, nothing of the database's own
// (DefaultUsesObject — a sequence, a user function or operator, however it is spelled), and in the
// text no call but a clock (a typmod in a cast reads as a call: refused, to be safe).
func omittedDefaultsAreSafe(t *pgschema.Table, present []string) bool {
	have := make(map[string]bool, len(present))
	for _, c := range present {
		have[c] = true
	}
	for _, c := range t.Columns {
		if have[c.Name] {
			continue
		}
		if c.Identity || c.DefaultUsesObject {
			return false
		}
		if c.Default == nil {
			continue
		}
		expr := sqlLiteral.ReplaceAllString(*c.Default, "''")
		for _, m := range sqlCall.FindAllStringSubmatch(expr, -1) {
			name := strings.ToLower(m[1])
			if i := strings.LastIndex(name, "."); i >= 0 {
				name = name[i+1:]
			}
			if !clockDefaults[name] {
				return false
			}
		}
	}
	return true
}

// uniform says whether the chunked path would send these rows as one statement the bulk load can
// carry: one column set, nothing asyncpg refuses, every value text or NULL.
func uniform(rows []*pendRow) bool {
	sql := rows[0].sql
	for _, r := range rows {
		if r.sql != sql || r.bindAt != 0 {
			return false
		}
		for _, a := range r.args {
			if _, ok := a.(string); !ok && a != nil {
				return false
			}
		}
	}
	return true
}

const onConflict = " ON CONFLICT DO NOTHING"

// bulkShape says whether rows of this statement may go in bulk: the chunked path's conflict
// clause, defaults that draw on nothing, and a statement Postgres accepts (prepared here exactly as
// the chunked path prepares it, so a statement it refuses is refused the chunked way).
func (w *writer) bulkShape(ctx context.Context, tx pgx.Tx, p *tablePlan, r *pendRow) (bool, error) {
	if !strings.HasSuffix(r.sql, onConflict) || !omittedDefaultsAreSafe(p.tab, r.cols) || !w.bulkRoom(p, r.cols) {
		return false, nil
	}
	if !w.eventTriggersAsked {
		// An event trigger fires on the bulk load's own DDL (a DDL audit would record its
		// temporary tables): with one enabled anywhere in the database, the write stays chunked.
		w.eventTriggersAsked = true
		got, ok, err := w.tryFetch(ctx, tx, "SELECT EXISTS (SELECT 1 FROM pg_event_trigger WHERE evtenabled <> 'D')")
		if err != nil {
			return false, err
		}
		if !ok || len(got) != 1 || got[0][0] != false {
			w.bulkOff = true
			return false, nil
		}
	}
	broken, err := w.ensurePrepared(ctx, tx, r.stmt, r.sql)
	return broken == nil, err
}

// dictionary is one column's distinct values, each with its code (1-based), in first-seen order.
type dictionary struct {
	code map[string]int
	null int       // NULL's code; 0 when no row is NULL
	vals []*string // by code-1; nil is NULL
}

// dictionaryFor returns the column's dictionary when codes would be shorter than its values.
func dictionaryFor(rows []*pendRow, j int) *dictionary {
	d := &dictionary{code: map[string]int{}}
	raw := 0
	for _, r := range rows {
		a := r.args[j]
		if a == nil {
			raw += 2
			if d.null == 0 {
				d.vals = append(d.vals, nil)
				d.null = len(d.vals)
			}
			continue
		}
		s := a.(string)
		raw += len(s)
		if _, ok := d.code[s]; !ok {
			d.vals = append(d.vals, &s)
			d.code[s] = len(d.vals)
			if len(d.vals)*4 > len(rows) {
				return nil
			}
		}
	}
	if len(strconv.Itoa(len(d.vals)))*len(rows) >= raw {
		return nil
	}
	return d
}

// copyField writes one value in COPY's text format.
func copyField(b *bytes.Buffer, a any) {
	if a == nil {
		b.WriteString(`\N`)
		return
	}
	s := a.(string)
	for i := 0; i < len(s); i++ {
		switch c := s[i]; c {
		case '\\':
			b.WriteString(`\\`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		default:
			b.WriteByte(c)
		}
	}
}

// rowsReader hands COPY its data and says how many rows have gone.
type rowsReader struct {
	data     []byte
	ends     []int // the offset after each row
	pos, row int
	reported int
	sent     func(int)
}

func (r *rowsReader) Read(p []byte) (int, error) {
	if r.pos >= len(r.data) {
		return 0, io.EOF
	}
	n := copy(p, r.data[r.pos:])
	r.pos += n
	for r.row < len(r.ends) && r.ends[r.row] <= r.pos {
		r.row++
	}
	if r.sent != nil && (r.row-r.reported >= 2000 || r.row == len(r.ends)) {
		r.reported = r.row
		r.sent(r.row)
	}
	return n, nil
}

// bulkSet is the temporary tables one destination shape loads through: made by its first
// statement, emptied by each one after, gone at commit. Tables made and dropped by every statement
// left ~30 lock-table entries each — a subtransaction's locks stay held until the write commits —
// and on a big file that fills the server's shared lock table, which every session on it uses.
type bulkSet struct {
	n     int
	dicts map[int]bool // the columns that have a dictionary table
}

// bulkLockBudget bounds the lock-table entries the bulk load's own tables hold until commit, as
// measured on Postgres 16: a new staging table holds up to ~18 (table, TOAST table and index,
// identity sequence, row types), a new dictionary table up to ~5.
const (
	bulkLockBudget = 400
	stageLocks     = 18
	dictLocks      = 5
)

func bulkKey(p *tablePlan, cols []string) string { return p.dest + "\x00" + strings.Join(cols, ",") }

// bulkRoom says whether rows of this shape can be loaded: their tables exist, or there is room
// in the lock budget for new ones.
func (w *writer) bulkRoom(p *tablePlan, cols []string) bool {
	return w.bulkSets[bulkKey(p, cols)] != nil || w.bulkLocks+stageLocks <= bulkLockBudget
}

// tryBulk loads rows — all of one shape, see uniform and bulkShape — in a savepoint. ok is false
// when the server refused any of it, and then nothing of the attempt is left; err is a lost
// connection or a savepoint that would not roll back.
func (w *writer) tryBulk(ctx context.Context, tx pgx.Tx, p *tablePlan, rows []*pendRow, sent func(int)) (int, bool, error) {
	cols := rows[0].cols
	key := bulkKey(p, cols)
	set := w.bulkSets[key]
	fresh := set == nil
	if fresh {
		if w.bulkLocks+stageLocks > bulkLockBudget {
			return 0, false, nil
		}
		w.bulkSeq++
		set = &bulkSet{n: w.bulkSeq, dicts: map[int]bool{}}
	}
	locks := 0
	if fresh {
		locks = stageLocks
	}
	drawn := w.serverDrawnID(ctx, tx, p, rows) // the id column the server fills, or -1
	dicts := make([]*dictionary, len(cols))
	var newDicts []int
	for j := range cols {
		if j == drawn {
			continue
		}
		d := dictionaryFor(rows, j)
		if d != nil && !set.dicts[j] {
			if w.bulkLocks+locks+dictLocks > bulkLockBudget {
				d = nil // no room for its table: the column's values travel as they are
			} else {
				locks += dictLocks
				newDicts = append(newDicts, j)
			}
		}
		dicts[j] = d
	}
	dest := w.job.Schema + "." + p.dest
	stage := fmt.Sprintf("pg_temp.hoist_bulk_%d_s", set.n)
	dictTable := func(j int) string { return fmt.Sprintf("pg_temp.hoist_bulk_%d_d%d", set.n, j) }
	setup := []string{"SAVEPOINT hoist_bulk"}
	var empty []string
	if fresh {
		sel := make([]string, 0, 2*len(cols))
		for j, c := range cols {
			sel = append(sel, fmt.Sprintf("t.%s AS c%d", c, j), fmt.Sprintf("NULL::int4 AS k%d", j))
		}
		setup = append(setup,
			fmt.Sprintf("CREATE TEMP TABLE hoist_bulk_%d_s ON COMMIT DROP AS SELECT %s FROM %s t WITH NO DATA",
				set.n, strings.Join(sel, ", "), dest),
			fmt.Sprintf("ALTER TABLE %s ADD COLUMN hoist_seq bigint GENERATED ALWAYS AS IDENTITY", stage))
	} else {
		empty = append(empty, stage)
	}
	ins := make([]string, len(cols))
	var staged []string
	var joins strings.Builder
	for j, c := range cols {
		switch {
		case j == drawn:
			ins[j] = "pg_catalog.gen_random_uuid()"
		case dicts[j] == nil:
			staged = append(staged, fmt.Sprintf("c%d", j))
			ins[j] = fmt.Sprintf("s.c%d", j)
		default:
			staged = append(staged, fmt.Sprintf("k%d", j))
			ins[j] = fmt.Sprintf("d%d.v", j)
			fmt.Fprintf(&joins, " JOIN %s d%d ON d%d.k = s.k%d", dictTable(j), j, j, j)
			if set.dicts[j] {
				empty = append(empty, dictTable(j))
			} else {
				setup = append(setup, fmt.Sprintf("CREATE TEMP TABLE hoist_bulk_%d_d%d ON COMMIT DROP AS SELECT NULL::int4 AS k, "+
					"t.%s AS v FROM %s t WITH NO DATA", set.n, j, c, dest))
			}
		}
	}
	if len(empty) > 0 {
		setup = append(setup, "TRUNCATE "+strings.Join(empty, ", "))
	}

	pc := tx.Conn().PgConn()
	failed := func(err error) (int, bool, error) {
		if isConnLost(err) {
			return 0, false, connLost(err)
		}
		if rerr := w.rollbackTo(ctx, tx, "hoist_bulk"); rerr != nil {
			return 0, false, rerr
		}
		return 0, false, nil
	}
	if err := execAll(ctx, pc, setup); err != nil {
		// Nothing about the rows has been sent yet: the server will not make or empty the tables
		// (no TEMP privilege, say), and will not for the next table either.
		w.bulkOff = true
		w.logf("warning", "[Node 9] bulk-load unavailable, writing in batches: "+runes(err.Error(), 200))
		return failed(err)
	}

	var data bytes.Buffer
	ends := make([]int, len(rows))
	for i, r := range rows {
		first := true
		for j := range cols {
			if j == drawn {
				continue
			}
			if !first {
				data.WriteByte('\t')
			}
			first = false
			if d := dicts[j]; d != nil {
				code := d.null
				if r.args[j] != nil {
					code = d.code[r.args[j].(string)]
				}
				data.WriteString(strconv.Itoa(code))
				continue
			}
			copyField(&data, r.args[j])
		}
		data.WriteByte('\n')
		ends[i] = data.Len()
	}
	for j, d := range dicts {
		if d == nil {
			continue
		}
		var b bytes.Buffer
		for k, v := range d.vals {
			b.WriteString(strconv.Itoa(k + 1))
			b.WriteByte('\t')
			if v == nil {
				copyField(&b, nil)
			} else {
				copyField(&b, *v)
			}
			b.WriteByte('\n')
		}
		if _, err := pc.CopyFrom(ctx, &b, fmt.Sprintf("COPY %s (k, v) FROM STDIN", dictTable(j))); err != nil {
			return failed(err)
		}
	}
	if _, err := pc.CopyFrom(ctx, &rowsReader{data: data.Bytes(), ends: ends, sent: sent},
		fmt.Sprintf("COPY %s (%s) FROM STDIN", stage, strings.Join(staged, ", "))); err != nil {
		return failed(err)
	}
	tag, err := pc.ExecParams(ctx, fmt.Sprintf("INSERT INTO %s (%s) SELECT %s FROM %s s%s ORDER BY s.hoist_seq%s",
		dest, strings.Join(cols, ", "), strings.Join(ins, ", "), stage, joins.String(), onConflict), nil, nil, nil, nil).Close()
	if err != nil {
		return failed(err)
	}
	if err := execAll(ctx, pc, []string{"RELEASE SAVEPOINT hoist_bulk"}); err != nil {
		return failed(err)
	}
	// The tables made by this statement outlive it now; one that failed took its tables with it.
	if fresh {
		if w.bulkSets == nil {
			w.bulkSets = map[string]*bulkSet{}
		}
		w.bulkSets[key] = set
	}
	for _, j := range newDicts {
		set.dicts[j] = true
	}
	w.bulkLocks += locks
	if drawn >= 0 {
		w.serverIDs += len(rows)
	}
	return int(tag.RowsAffected()), true, nil
}

func nonEmpty(xs []string) []string {
	out := xs[:0:0]
	for _, x := range xs {
		if x != "" {
			out = append(out, x)
		}
	}
	return out
}

// serverDrawnID is the index of the id column when the server may draw every row's id: the ids
// are random ones the writer drew (uuid4 — a parity run's counted ids always travel), the column
// is a uuid, and the server has pg_catalog.gen_random_uuid (version 4, as uuid4). Nothing reads a
// drawn id back, so which random id a row gets is the only difference, and 37 bytes a row stay home.
func (w *writer) serverDrawnID(ctx context.Context, tx pgx.Tx, p *tablePlan, rows []*pendRow) int {
	if w.job.DeterministicIDs || p.colInfo["id"] == nil || p.colInfo["id"].DataType != "uuid" || len(rows[0].cols) < 2 {
		return -1 // (a row of nothing but its id still sends it: COPY needs a column)
	}
	j := -1
	for k, c := range rows[0].cols {
		if c == "id" {
			j = k
		}
	}
	if j < 0 {
		return -1
	}
	for _, r := range rows {
		if !r.genID {
			return -1
		}
	}
	if w.drawsIDs == 0 {
		w.drawsIDs = -1
		got, ok, err := w.tryFetch(ctx, tx, "SELECT to_regprocedure('pg_catalog.gen_random_uuid()') IS NOT NULL")
		if err == nil && ok && len(got) == 1 && got[0][0] == true {
			w.drawsIDs = 1
		}
	}
	if w.drawsIDs < 0 {
		return -1
	}
	return j
}

// execAll runs generated statements one after another in one round trip, each on its own (never
// several statements in one string); the first error stops the rest.
func execAll(ctx context.Context, pc *pgconn.PgConn, sqls []string) error {
	b := &pgconn.Batch{}
	for _, s := range sqls {
		b.ExecParams(s, nil, nil, nil, nil)
	}
	_, err := pc.ExecBatch(ctx, b).ReadAll()
	return err
}

// encodedBytes is about what a chunk's rows weigh as COPY text.
func encodedBytes(rows []*pendRow) int {
	n := 0
	for _, r := range rows {
		for _, a := range r.args {
			if s, ok := a.(string); ok {
				n += len(s)
			}
			n++
		}
	}
	return n
}

// runEnd is where the run of chunks that starts at flushes[k] ends (exclusive): the chunks after it
// that are one clean statement of the same shape, while the run stays within maxRows rows and about
// maxBytes of COPY text. The first chunk makes a run of one whatever it weighs.
func runEnd(fl []flushPoint, k, maxRows, maxBytes int) int {
	j, n, b := k+1, len(fl[k].rows), encodedBytes(fl[k].rows)
	for j < len(fl) && uniform(fl[j].rows) && fl[j].rows[0].sql == fl[k].rows[0].sql {
		nb := encodedBytes(fl[j].rows)
		if n+len(fl[j].rows) > maxRows || b+nb > maxBytes {
			break
		}
		n, b = n+len(fl[j].rows), b+nb
		j++
	}
	return j
}

// runState is insertTable's bookkeeping, which the bulk path keeps exactly as its loop does.
type runState struct {
	out       *tableOutcome
	sysFilled *counts
	tick      func(int)
	collect   func(flushOut)
	warn      func(flushOut) // the warning after the loop's last flush
	done      func() int
	inserted  func(int)
	raise     func(int) // progress while a bulk statement's rows go up; it never goes back after
}

// step is what the loop decides for one row before anything is written: nothing to write (it is
// nil), a duplicate of a row already on file, or a row to send.
type step struct {
	it   *built
	dupe bool
	row  *pendRow
}

// flushPoint is one flush of the loop: the steps it passes before flushing, and the rows it sends.
// The last is the flush after the loop, when rows are left over.
type flushPoint struct {
	from, to int
	rows     []*pendRow
	last     bool
}

// answeredUpFront says whether every duplicate check of the table is answered already — by the
// run's cache or the table's prefetch — so deciding all its rows before writing any asks the
// database nothing. A key the prefetch could not answer is asked as the loop reaches its row,
// after the chunks before it went in, and may then find one of them; such a table takes the loop.
func (w *writer) answeredUpFront(p *tablePlan, items []*built, known map[string]bool) bool {
	for _, it := range items {
		if it.skipped {
			continue
		}
		for _, km := range it.keys {
			ck := nkCacheKey(p.dest, km)
			if _, ok := w.nkSeen[ck]; ok {
				continue
			}
			if _, ok := known[ck]; !ok {
				return false
			}
		}
	}
	return true
}

// insertInRuns is insertTable's loop for a table that may take the bulk load, when every row's
// duplicate check is answered up front (answeredUpFront). Every row's fate is decided first, then
// the flushes the loop would make are made in order: runs of chunks that are each one clean
// statement of the same shape go in bulk, everything else, and any run the server refuses, goes
// through flush exactly as the loop sends it. The counts the loop keeps as it passes rows are kept
// the same way, up to the flush where a failing table stops — and the run's cache keeps no answer
// for a row the loop would never have reached.
func (w *writer) insertInRuns(ctx context.Context, tx pgx.Tx, p *tablePlan, items []*built, known map[string]bool,
	total int, st *runState) error {
	steps := make([]step, 0, len(items))
	type firstSeen struct {
		step int
		key  string
	}
	var cached []firstSeen // keys deciding the rows put into the run's cache, by the step that did
	for _, it := range items {
		if it.skipped {
			steps = append(steps, step{})
			continue
		}
		dupe := false
		for _, km := range it.keys {
			ck := nkCacheKey(p.dest, km)
			_, had := w.nkSeen[ck]
			hit, err := w.alreadyWritten(ctx, tx, p, km, known)
			if err != nil {
				return err
			}
			if !had {
				cached = append(cached, firstSeen{len(steps), ck})
			}
			if hit {
				dupe = true
				break
			}
		}
		if dupe {
			steps = append(steps, step{it: it, dupe: true})
			continue
		}
		r := w.pendingRow(p, it.full)
		r.genID = it.genID
		steps = append(steps, step{it: it, row: r})
	}
	var flushes []flushPoint
	from := 0
	var rows []*pendRow
	for i, s := range steps {
		if s.row == nil {
			continue
		}
		rows = append(rows, s.row)
		if len(rows) >= w.spec.WriteChunk {
			flushes = append(flushes, flushPoint{from: from, to: i + 1, rows: rows})
			from, rows = i+1, nil
		}
	}
	if len(rows) > 0 {
		flushes = append(flushes, flushPoint{from: from, to: len(steps), rows: rows, last: true})
		from = len(steps)
	}

	pass := func(ss []step) {
		for _, s := range ss {
			if s.it == nil {
				st.tick(1)
				continue
			}
			for _, c := range s.it.filled {
				st.sysFilled.add(c, 1)
			}
			if s.dupe {
				st.out.dupes++
				st.tick(1)
			}
		}
	}
	stoppedAt := -1 // the steps from here on are the ones the loop never reached
	forget := func() {
		if stoppedAt < 0 {
			return
		}
		for _, c := range cached {
			if c.step >= stoppedAt {
				delete(w.nkSeen, c.key)
			}
		}
	}
	chunked := func(f flushPoint) (bool, error) {
		pass(steps[f.from:f.to])
		fo, err := w.flush(ctx, tx, p, f.rows)
		if err != nil {
			return false, err
		}
		st.collect(fo)
		if f.last {
			st.warn(fo)
		}
		if fo.abandoned != "" {
			stoppedAt = f.to
			return true, nil
		}
		return false, nil
	}
	failures := 0
	// load sends flushes[a:b], in bulk when attempt allows; a run the server refuses is halved.
	var load func(a, b int, attempt bool) (bool, error)
	load = func(a, b int, attempt bool) (bool, error) {
		if attempt && failures < bulkMaxFailures && !w.bulkOff {
			var run []*pendRow
			for _, f := range flushes[a:b] {
				run = append(run, f.rows...)
			}
			base := st.done()
			n, ok, err := w.tryBulk(ctx, tx, p, run, func(k int) { st.raise(base + k) })
			if err != nil {
				return false, err
			}
			if ok {
				for _, f := range flushes[a:b] {
					pass(steps[f.from:f.to])
				}
				st.inserted(n)
				st.tick(len(run))
				w.bulkRows += len(run)
				return false, nil
			}
			failures++
			if b-a > 1 {
				mid := (a + b) / 2
				if stop, err := load(a, mid, true); stop || err != nil {
					return stop, err
				}
				return load(mid, b, true)
			}
		}
		for k := a; k < b; k++ {
			if stop, err := chunked(flushes[k]); stop || err != nil {
				return stop, err
			}
		}
		return false, nil
	}
	for k := 0; k < len(flushes); {
		f := flushes[k]
		ok := uniform(f.rows)
		if ok {
			var err error
			if ok, err = w.bulkShape(ctx, tx, p, f.rows[0]); err != nil {
				return err
			}
		}
		if !ok {
			if stop, err := chunked(f); stop || err != nil {
				forget()
				return err
			}
			k++
			continue
		}
		j := runEnd(flushes, k, bulkMaxRows, bulkMaxBytes)
		n := 0
		for _, g := range flushes[k:j] {
			n += len(g.rows)
		}
		if stop, err := load(k, j, n >= w.bulkMinRows()); stop || err != nil {
			forget()
			return err
		}
		k = j
	}
	pass(steps[from:])
	return nil
}
