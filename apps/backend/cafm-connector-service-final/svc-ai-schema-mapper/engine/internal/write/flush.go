package write

import (
	"context"
	"crypto/sha1"
	"encoding/hex"
	"errors"
	"fmt"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"

	"hoistra/engine/internal/rules"
)

// pendRow is one row of a batch: _build_dml_for_row's statement, the Python objects bound to it,
// the text the engine sends for each, and — when asyncpg would refuse one — which and why.
type pendRow struct {
	f       *rules.Filtered
	cols    []string
	sql     string // as SQLAlchemy compiles it for asyncpg; also the text errors print
	stmt    string // the name it is prepared under
	vals    []pyVal
	args    []any
	bindAt  int // 1-based parameter asyncpg refuses; 0 when none
	bindWhy string
	genID   bool // its id is a random one the writer drew (bulk.go may let the server draw it)
}

// controlStatements are the savepoint statements batches and bulk loads roll back with.
var controlStatements = []string{
	"SAVEPOINT hoist_batch", "RELEASE SAVEPOINT hoist_batch", "ROLLBACK TO SAVEPOINT hoist_batch",
	"SAVEPOINT hoist_row", "RELEASE SAVEPOINT hoist_row", "ROLLBACK TO SAVEPOINT hoist_row",
	"RELEASE SAVEPOINT hoist_bulk", "ROLLBACK TO SAVEPOINT hoist_bulk", // the bulk load's (bulk.go)
}

func stmtName(sql string) string {
	h := sha1.Sum([]byte(sql))
	return "hoist_" + hex.EncodeToString(h[:12])
}

// flushOut is _insert_rows' result. inserted counts rows that went in; insertedPy is the count
// Python keeps (a whole batch that succeeded, conflicts included), which its "remaining rows"
// arithmetic uses.
type flushOut struct {
	attempted  int
	inserted   int
	insertedPy int
	skipped    int
	errors     []string
	orphans    counts
	abandoned  string
	perRow     bool
}

// flush is _insert_rows: rows that share a statement go together, in the order their statement
// first appears, as one pipelined round trip (asyncpg's executemany); a batch that fails is redone
// a row at a time, each row in its own savepoint, with the orphan-foreign-key retry and the rule
// that a hundred identical failures in a row abandon the table.
func (w *writer) flush(ctx context.Context, tx pgx.Tx, p *tablePlan, pending []*pendRow) (flushOut, error) {
	out := flushOut{attempted: len(pending)}
	var order []string
	groups := map[string][]*pendRow{}
	for _, r := range pending {
		if _, seen := groups[r.sql]; !seen {
			order = append(order, r.sql)
		}
		groups[r.sql] = append(groups[r.sql], r)
	}
	st := &streak{}
	for _, sql := range order {
		g := groups[sql]
		broken, err := w.ensurePrepared(ctx, tx, g[0].stmt, sql)
		if err != nil {
			return out, err
		}
		clean := broken == nil
		for _, r := range g {
			if r.bindAt != 0 {
				clean = false
				break
			}
		}
		if clean {
			n, failed, err := w.execBatch(ctx, tx, g)
			if err != nil {
				return out, err
			}
			if !failed {
				out.inserted += n
				out.insertedPy += len(g)
				continue
			}
		}
		out.perRow = true
		for i := 0; i < len(g); {
			var e pyErr
			switch {
			case broken != nil:
				e, _ = pgPyErr(broken, g[i].sql, g[i].vals)
			case g[i].bindAt != 0:
				r := g[i]
				e = bindPyErr(r.bindAt, r.vals[r.bindAt-1], r.bindWhy, r.sql, r.vals)
			default:
				j := i
				for j < len(g) && g[j].bindAt == 0 {
					j++
				}
				n, at, perr, err := w.execRows(ctx, tx, g[i:j])
				if err != nil {
					return out, err
				}
				out.inserted += n
				out.insertedPy += n
				// A row that ran without an error ends the streak, written or not: a row ON
				// CONFLICT DO NOTHING skips affects nothing and still ends it, as in Python.
				if at > 0 || (at < 0 && j > i) {
					st.reset()
				}
				if at < 0 {
					i = j
					continue
				}
				i += at
				pe, ok := pgPyErr(perr, g[i].sql, g[i].vals)
				if !ok {
					return out, wrapDB(perr)
				}
				e = pe
			}
			if err := w.rowFailed(ctx, tx, p, g[i], e, &out, st, len(pending)); err != nil {
				return out, err
			}
			if out.abandoned != "" {
				return out, nil
			}
			i++
		}
	}
	return out, nil
}

type streak struct {
	n   int
	sig string
}

func (s *streak) reset() { s.n, s.sig = 0, "" }

// rowFailed is the per-row path after a row raised: an orphan foreign key on a nullable column is
// nulled and the row retried once; otherwise the row is skipped and counts toward the streak.
func (w *writer) rowFailed(ctx context.Context, tx pgx.Tx, p *tablePlan, r *pendRow, e pyErr, out *flushOut,
	st *streak, total int) error {
	var nullFK []string
	for _, c := range e.fkColumns() {
		if p.nullable[c] && containsStr(r.cols, c) {
			nullFK = append(nullFK, c)
		}
	}
	if len(nullFK) > 0 {
		retry := &rules.Filtered{}
		for i, c := range r.f.K {
			if !containsStr(nullFK, c) {
				retry.K = append(retry.K, c)
				retry.V = append(retry.V, r.f.V[i])
			}
		}
		rr := w.pendingRow(p, retry)
		if rr.bindAt == 0 {
			broken, err := w.ensurePrepared(ctx, tx, rr.stmt, rr.sql)
			if err != nil {
				return err
			}
			if broken == nil {
				n, at, _, err := w.execRows(ctx, tx, []*pendRow{rr})
				if err != nil {
					return err
				}
				if at < 0 {
					out.inserted += n
					out.insertedPy += n
					for _, c := range nullFK {
						out.orphans.add(c, 1)
					}
					st.reset()
					return nil
				}
			}
		}
	}
	out.skipped++
	if len(out.errors) < 20 {
		out.errors = append(out.errors, p.dest+": "+runes(e.str, 220))
	}
	if sig := e.sig(); sig == st.sig {
		st.n++
	} else {
		st.n, st.sig = 1, sig
	}
	if st.n >= w.spec.MaxConsecutiveRowFailures {
		left := total - out.insertedPy - out.skipped
		if left < 0 {
			left = 0
		}
		out.abandoned = fmt.Sprintf("%s: stopped after %d consecutive identical failures — every row is failing the "+
			"same way, so the remaining %d were not attempted. Fix the cause and re-run: %s", p.dest, st.n, left,
			runes(e.str, 200))
		out.skipped += left
	}
	return nil
}

// ensurePrepared prepares a statement the first time it is used, inside a savepoint: Postgres
// rejecting the statement itself (a reserved word for a column, a type that cannot take the
// value) must not take the transaction with it. It returns that rejection, if any, which then
// fails every row of the statement exactly as executing it would.
func (w *writer) ensurePrepared(ctx context.Context, tx pgx.Tx, name, sql string) (error, error) {
	if e, ok := w.prepared[name]; ok {
		return e, nil
	}
	sp, err := tx.Begin(ctx)
	if err != nil {
		return nil, wrapDB(err)
	}
	if _, perr := sp.Prepare(ctx, name, sql); perr != nil {
		_ = sp.Rollback(ctx)
		if isConnLost(perr) {
			return nil, connLost(perr)
		}
		var pe *pgconn.PgError
		if !errors.As(perr, &pe) {
			return nil, wrapDB(perr)
		}
		w.prepared[name] = perr
		return perr, nil
	}
	if err := sp.Commit(ctx); err != nil {
		return nil, wrapDB(err)
	}
	w.prepared[name] = nil
	return nil, nil
}

// execBatch is executemany inside begin_nested(): every row in one pipeline, all or nothing.
func (w *writer) execBatch(ctx context.Context, tx pgx.Tx, g []*pendRow) (int, bool, error) {
	b := &pgx.Batch{}
	b.Queue("SAVEPOINT hoist_batch")
	for _, r := range g {
		b.Queue(r.stmt, r.args...)
	}
	b.Queue("RELEASE SAVEPOINT hoist_batch")
	br := tx.SendBatch(ctx, b)
	n := 0
	_, ferr := br.Exec()
	if ferr == nil {
		for range g {
			tag, err := br.Exec()
			if err != nil {
				ferr = err
				break
			}
			n += int(tag.RowsAffected())
		}
	}
	if ferr == nil {
		_, ferr = br.Exec()
	}
	if cerr := br.Close(); ferr == nil {
		ferr = cerr
	}
	if ferr == nil {
		return n, false, nil
	}
	if isConnLost(ferr) {
		return 0, false, connLost(ferr)
	}
	if err := w.rollbackTo(ctx, tx, "hoist_batch"); err != nil {
		return 0, false, err
	}
	return 0, true, nil
}

// execRows runs rows the way the per-row path does — each in its own savepoint — pipelined; it
// stops at the first row Postgres rejects, rolls that row back, and says which it was.
func (w *writer) execRows(ctx context.Context, tx pgx.Tx, rows []*pendRow) (int, int, error, error) {
	b := &pgx.Batch{}
	for _, r := range rows {
		b.Queue("SAVEPOINT hoist_row")
		b.Queue(r.stmt, r.args...)
		b.Queue("RELEASE SAVEPOINT hoist_row")
	}
	br := tx.SendBatch(ctx, b)
	n, at := 0, -1
	var perr error
	for i := range rows {
		if _, err := br.Exec(); err != nil {
			perr, at = err, i
			break
		}
		tag, err := br.Exec()
		if err != nil {
			perr, at = err, i
			break
		}
		n += int(tag.RowsAffected())
		if _, err := br.Exec(); err != nil {
			perr, at = err, i
			break
		}
	}
	cerr := br.Close()
	if perr == nil && cerr != nil {
		perr, at = cerr, len(rows)-1
	}
	if perr == nil {
		return n, -1, nil, nil
	}
	if isConnLost(perr) {
		return 0, 0, nil, connLost(perr)
	}
	if err := w.rollbackTo(ctx, tx, "hoist_row"); err != nil {
		return 0, 0, nil, err
	}
	return n, at, perr, nil
}

// execOne runs one statement in a savepoint, preparing it first; perr is Postgres rejecting it.
func (w *writer) execOne(ctx context.Context, tx pgx.Tx, sql string, args []any) (int, error, error) {
	name := stmtName(sql)
	broken, err := w.ensurePrepared(ctx, tx, name, sql)
	if err != nil || broken != nil {
		return 0, broken, err
	}
	n, at, perr, err := w.execRows(ctx, tx, []*pendRow{{stmt: name, args: args}})
	if err != nil {
		return 0, nil, err
	}
	if at >= 0 {
		return 0, perr, nil
	}
	return n, nil, nil
}

func (w *writer) rollbackTo(ctx context.Context, tx pgx.Tx, name string) error {
	b := &pgx.Batch{}
	b.Queue("ROLLBACK TO SAVEPOINT " + name)
	b.Queue("RELEASE SAVEPOINT " + name)
	br := tx.SendBatch(ctx, b)
	_, err1 := br.Exec()
	_, err2 := br.Exec()
	err3 := br.Close()
	for _, err := range []error{err1, err2, err3} {
		if err != nil {
			return wrapDB(err)
		}
	}
	return nil
}
