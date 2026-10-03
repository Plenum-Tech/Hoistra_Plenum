package write

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"

	"hoistra/engine/internal/arrowtab"
	"hoistra/engine/internal/protocol"
	"hoistra/engine/internal/pystr"
	"hoistra/engine/internal/resolve"
	"hoistra/engine/internal/rules"
)

// writer is one run of the write: the job, its connection, and the counters and caches
// _apply_records_with_schema_alignment keeps for the length of a run.
type writer struct {
	job     Job
	spec    rules.Spec
	em      *protocol.Emitter
	conn    *pgx.Conn
	sources []*source
	org     string
	ids     idGen

	res             *resolve.Set
	defaultBuilding string

	prepared map[string]error
	nkSeen   map[string]bool
	assetIDs map[string]string
	assetIx  *assetIndex

	rowsInserted       int
	tablesWritten      int
	rowsSkipped        int
	rowsMerged         int
	buildingsLinked    int
	metersLinked       int
	metersMatched      int
	unlinkedReported   bool
	rowErrors          []string
	tables             []TableResult
	bulkRows           int
	bulkOff            bool // the server would not make the bulk load's temporary tables
	serverIDs          int
	drawsIDs           int // pg_catalog.gen_random_uuid() exists: 0 not asked yet, 1 yes, -1 no
	bulkSets           map[string]*bulkSet
	eventTriggersAsked bool
	bulkSeq            int
	bulkLocks          int
}

// beforeCommit, when a test sets it, sees the write's transaction just before it commits.
var beforeCommit func(context.Context, pgx.Tx)

func (w *writer) logf(level, msg string) {
	if w.em != nil {
		w.em.Log(level, msg)
	}
}

// rowError is `if len(row_errors) < 20: row_errors.append(msg)`.
func (w *writer) rowError(msg string) {
	if len(w.rowErrors) < 20 {
		w.rowErrors = append(w.rowErrors, msg)
	}
}

// progress reports a stage of one destination table: rows done of the table's rows (for ddl,
// tables done of the run's tables).
func (w *writer) progress(stage, table string, done, total int) {
	if w.em != nil {
		w.em.Progress(stage, table, int64(done), int64(total))
	}
}

// Run is one `hoist-engine write` job against the database at dsn.
func Run(ctx context.Context, job Job, dsn string, em *protocol.Emitter) (*Result, error) {
	if job.Mode != "plan" && job.Mode != "apply" {
		return nil, protocol.Errorf(protocol.CodeBadJob, "mode must be plan or apply, not %q", job.Mode)
	}
	if s, ok := rules.SafeIdent(job.Schema); !ok || s != job.Schema {
		return nil, protocol.Errorf(protocol.CodeBadJob, "unsafe schema name %q", job.Schema)
	}
	if job.Rules.WriteChunk <= 0 || job.Rules.MaxConsecutiveRowFailures <= 0 || job.Rules.WidenScanCap <= 0 {
		return nil, protocol.Errorf(protocol.CodeBadJob, "the job's write rules are incomplete")
	}
	tables, err := arrowtab.ReadDir(job.CleanedDir)
	if err != nil {
		return nil, protocol.Errorf(protocol.CodeData, "cannot read the cleaned tables: %v", err)
	}
	w := &writer{job: job, spec: job.Rules, em: em, ids: idGen{deterministic: job.DeterministicIDs, n: job.DeterministicIDsFrom},
		prepared: map[string]error{}, nkSeen: map[string]bool{}, assetIDs: map[string]string{}}
	w.sources = loadSources(tables, job.ColumnRenames)
	conn, err := connect(ctx, dsn)
	if err != nil {
		return nil, err
	}
	defer conn.Close(context.Background())
	w.conn = conn
	if w.org, err = w.resolveOrg(ctx); err != nil {
		return nil, err
	}
	if job.Mode == "plan" {
		return w.plan(ctx)
	}
	return w.apply(ctx)
}

// resolveOrg is _resolve_valid_organization_id: the requested organisation when it exists,
// otherwise the oldest one.
func (w *writer) resolveOrg(ctx context.Context) (string, error) {
	requested := pystr.Strip(w.job.OrganizationID)
	if requested != "" {
		var one int
		err := w.conn.QueryRow(ctx, fmt.Sprintf("SELECT 1 FROM %s.organizations WHERE id::text = $1 LIMIT 1",
			w.job.Schema), requested).Scan(&one)
		switch {
		case err == nil:
			return requested, nil
		case isConnLost(err):
			return "", connLost(err)
		case !errors.Is(err, pgx.ErrNoRows):
			w.logf("warning", fmt.Sprintf("[Node 9] org_id lookup failed (%v); falling back to first org", err))
		}
	}
	var id *string
	err := w.conn.QueryRow(ctx, fmt.Sprintf("SELECT id::text FROM %s.organizations ORDER BY created_at ASC LIMIT 1",
		w.job.Schema)).Scan(&id)
	switch {
	case isConnLost(err):
		return "", connLost(err)
	case errors.Is(err, pgx.ErrNoRows) || (err == nil && id == nil):
		return "", protocol.Errorf(protocol.CodeDB, "No organizations found in target DB; cannot satisfy assets.organization_id FK")
	case err != nil:
		return "", protocol.Errorf(protocol.CodeDB, "org fallback query failed — no organizations found in target DB: %v", err)
	}
	w.logf("warning", "[Node 9] Requested organization_id missing; using existing organization_id="+*id)
	return *id, nil
}

func (w *writer) siteNames() map[string]string {
	names := make([]string, 0, len(w.sources))
	tables := map[string][]*rules.Row{}
	for _, s := range w.sources {
		names = append(names, s.name)
		tables[s.name] = s.rows
	}
	_, m := rules.SiteNamesFromRun(&w.spec, names, tables, w.job.Routing)
	return m
}

// apply writes: the review gates' DDL, then the table DDL, each committed on its own, then every
// row in one transaction — a lost connection keeps nothing of the rows.
func (w *writer) apply(ctx context.Context) (*Result, error) {
	if err := w.extraFieldDDL(ctx); err != nil {
		return nil, err
	}
	ddl := &ddlSession{conn: w.conn}
	plans, err := w.prepare(ctx, w.conn, ddl)
	if err != nil {
		ddl.rollback()
		return nil, err
	}

	tx, err := w.conn.Begin(ctx)
	if err != nil {
		return nil, wrapDB(err)
	}
	defer func() { _ = tx.Rollback(context.Background()) }()
	// Prepared now, while the transaction is healthy: a batch that fails leaves it aborted, and
	// pgx would otherwise prepare the RELEASE that follows the rollback in that state.
	for _, s := range controlStatements {
		if _, err := tx.Prepare(ctx, s, s); err != nil {
			return nil, wrapDB(err)
		}
	}
	w.res = resolve.New(tx, &w.spec, w.org, w.job.Schema, w.siteNames(), func(msg string) { w.logf("warning", msg) })
	if b := w.job.DefaultBuildingID; b != "" {
		rows, err := w.fetch(ctx, tx, fmt.Sprintf("SELECT building_id::text FROM %s.buildings WHERE building_id::text = $1 "+
			"AND organization_id::text = $2", w.job.Schema), b, w.org)
		if err != nil {
			return nil, err
		}
		if len(rows) > 0 && rows[0][0] != nil {
			w.defaultBuilding = fmt.Sprint(rows[0][0])
			w.logf("info", "[Node 9] rows naming no site will be filed against building "+w.defaultBuilding)
		} else {
			w.logf("warning", fmt.Sprintf("[Node 9] selected building %s is not a building of org %s - ignored", b, w.org))
		}
	}
	for _, p := range plans {
		started := time.Now()
		w.logf("info", fmt.Sprintf("[Node 9] ► Table '%s': %d records", p.dest, len(p.src.rows)))
		rows := w.resolveRows(ctx, p)
		o, err := w.insertTable(ctx, tx, p, rows)
		if err != nil {
			return nil, err
		}
		mode := "batch"
		if o.perRow {
			mode = "row_by_row"
		}
		w.tables = append(w.tables, TableResult{Source: p.src.name, Dest: p.dest, Inserted: o.inserted, Merged: o.merged,
			Skipped: o.skipped, AlreadyPresent: o.dupes, Seconds: time.Since(started).Seconds(), Mode: mode})
	}
	if beforeCommit != nil {
		beforeCommit(ctx, tx)
	}
	res := w.result()
	w.logf("info", fmt.Sprintf("[Node 9] Schema-aligned write done — %d row(s) across %d table(s), %d skipped, %d merged "+
		"into existing assets, %d building link(s) resolved, %d reading(s) placed on a meter (%d meter(s) created), %d "+
		"reference(s) resolved, %d meter(s) matched to one already on record", res.RowsInserted, res.TablesWritten,
		res.RowsSkipped, res.RowsMerged, res.BuildingsLinked, res.MetersLinked, res.MetersCreated, w.res.RefResolved,
		res.MetersMatched))
	if err := tx.Commit(ctx); err != nil {
		return nil, wrapDB(err)
	}
	return res, nil
}

// result is the Python writer's return value, with its closing reports in row_errors.
func (w *writer) result() *Result {
	if amb := uniq(w.res.BuildingAmbiguous); len(amb) > 0 {
		w.rowError("building: " + strings.Join(first(amb, 5), ", ") +
			" matched more than one building — the rows were written without a building link")
	}
	unlinked := uniq(w.res.MetersUnlinked)
	if len(unlinked) > 0 {
		w.rowError("meter: " + strings.Join(first(unlinked, 5), ", ") + " named no meter already on record, and the " +
			"rows named no building to create one against — those readings were skipped rather than written to a " +
			"meter that belongs to no building")
	}
	if amb := uniq(w.res.MeterAmbiguous); len(amb) > 0 {
		w.rowError("meter: " + strings.Join(first(amb, 5), ", ") + " matched more than one meter — those readings " +
			"were skipped")
	}
	errs := w.rowErrors
	if errs == nil {
		errs = []string{}
	}
	return &Result{RowsInserted: w.rowsInserted, TablesWritten: w.tablesWritten, RowsSkipped: w.rowsSkipped,
		RowsMerged: w.rowsMerged, BuildingsLinked: w.buildingsLinked, MetersLinked: w.metersLinked,
		MetersCreated: w.res.MetersCreated, MetersMatched: w.metersMatched, MetersUnlinked: unlinked,
		References: w.res.ReferenceReport(), RowErrors: errs, Tables: w.tables, OrganizationID: w.org,
		BulkRows: w.bulkRows, ServerIDs: w.serverIDs}
}

// uniq is sorted(set(xs)).
func uniq(xs []string) []string {
	seen := map[string]bool{}
	out := []string{}
	for _, x := range xs {
		if !seen[x] {
			seen[x] = true
			out = append(out, x)
		}
	}
	sort.Strings(out)
	return out
}

func first(xs []string, n int) []string {
	if len(xs) > n {
		return xs[:n]
	}
	return xs
}
