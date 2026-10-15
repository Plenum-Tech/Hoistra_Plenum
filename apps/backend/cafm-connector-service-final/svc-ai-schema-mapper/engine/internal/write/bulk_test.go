package write

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5"

	"hoistra/engine/internal/pgschema"
	"hoistra/engine/internal/protocol"
	"hoistra/engine/internal/testdb"
)

// The bulk load may change nothing but the bytes on the wire. Each test below runs one job through
// the chunked path and through the bulk path, each on a fresh copy of the parity database, and
// requires the same rows, the same result, the same row errors and the same log lines.

type twinRun struct {
	res      *Result
	logs     []string
	dump     map[string][]string
	progress map[string][]int64 // done values per stage/table, in order
}

var elapsed = regexp.MustCompile(`\(\d+\.\ds\)`)

// logLines are the run's log messages, with timings blanked and the bulk path's own line left out.
func logLines(t *testing.T, ndjson string) []string {
	t.Helper()
	var out []string
	for _, line := range strings.Split(strings.TrimSpace(ndjson), "\n") {
		var ev struct {
			Type, Level, Message string
		}
		if err := json.Unmarshal([]byte(line), &ev); err != nil {
			t.Fatalf("event %q: %v", line, err)
		}
		if ev.Type != "log" || strings.Contains(ev.Message, "bulk-load") { // the bulk load's own lines
			continue
		}
		out = append(out, ev.Level+" "+elapsed.ReplaceAllString(ev.Message, "(Ns)"))
	}
	return out
}

// progressOf is each stage/table's progress, in the order it was reported.
func progressOf(t *testing.T, ndjson string) map[string][]int64 {
	t.Helper()
	out := map[string][]int64{}
	for _, line := range strings.Split(strings.TrimSpace(ndjson), "\n") {
		var ev struct {
			Type, Stage, Table string
			Done               int64
		}
		if err := json.Unmarshal([]byte(line), &ev); err != nil {
			t.Fatalf("event %q: %v", line, err)
		}
		if ev.Type == "progress" {
			out[ev.Stage+"/"+ev.Table] = append(out[ev.Stage+"/"+ev.Table], ev.Done)
		}
	}
	return out
}

// dumpAll is every plenum_cafm table's rows, sorted, without the columns a clock fills.
func (e *env) dumpAll() map[string][]string {
	e.t.Helper()
	rows, err := e.conn.Query(e.ctx, `SELECT table_name FROM information_schema.tables
		WHERE table_schema = 'plenum_cafm' AND table_type = 'BASE TABLE' ORDER BY 1`)
	if err != nil {
		e.t.Fatal(err)
	}
	var tables []string
	for rows.Next() {
		var n string
		if err := rows.Scan(&n); err != nil {
			e.t.Fatal(err)
		}
		tables = append(tables, n)
	}
	rows.Close()
	out := map[string][]string{}
	for _, tname := range tables {
		q := fmt.Sprintf(`SELECT (to_jsonb(x) - ARRAY(SELECT column_name::text FROM information_schema.columns
			WHERE table_schema = 'plenum_cafm' AND table_name = '%s'
			  AND (column_name = 'updated_at' OR column_default ILIKE '%%now()%%')))::text
			FROM plenum_cafm.%q x`, tname, tname)
		r, err := e.conn.Query(e.ctx, q)
		if err != nil {
			e.t.Fatalf("%s: %v", tname, err)
		}
		var got []string
		for r.Next() {
			var s string
			if err := r.Scan(&s); err != nil {
				e.t.Fatal(err)
			}
			got = append(got, s)
		}
		r.Close()
		if len(got) > 0 {
			sort.Strings(got)
			out[tname] = got
		}
	}
	return out
}

// twin runs the job with the bulk load off, then forced on (any run of chunks, however short).
func twin(t *testing.T, seed []string, tune func(*Job), routing map[string]string, tables ...tbl) (twinRun, twinRun) {
	t.Helper()
	var runs [2]twinRun
	for i, minRows := range []int{-1, 1} {
		e := newEnv(t, seed...)
		job := e.job("apply", routing, tables...)
		if tune != nil {
			tune(&job)
		}
		job.BulkMinRows = minRows
		var out bytes.Buffer
		res, err := Run(e.ctx, job, testdb.DSN(), protocol.NewEmitter(&out))
		if err != nil {
			t.Fatalf("run (bulk_min_rows %d): %v\n%s", minRows, err, out.String())
		}
		runs[i] = twinRun{res: res, logs: logLines(t, out.String()), dump: e.dumpAll(), progress: progressOf(t, out.String())}
	}
	return runs[0], runs[1]
}

func comparable(r *Result) Result {
	c := *r
	c.BulkRows, c.ServerIDs = 0, 0
	c.Tables = append([]TableResult(nil), r.Tables...)
	for i := range c.Tables {
		c.Tables[i].Seconds = 0
	}
	return c
}

func sameTwin(t *testing.T, chunked, bulk twinRun) {
	t.Helper()
	if !reflect.DeepEqual(chunked.dump, bulk.dump) {
		for tname := range chunked.dump {
			if !reflect.DeepEqual(chunked.dump[tname], bulk.dump[tname]) {
				t.Errorf("%s differs:\n chunked %v\n bulk    %v", tname, chunked.dump[tname], bulk.dump[tname])
			}
		}
		for tname := range bulk.dump {
			if _, ok := chunked.dump[tname]; !ok {
				t.Errorf("%s written only by the bulk run: %v", tname, bulk.dump[tname])
			}
		}
	}
	if a, b := comparable(chunked.res), comparable(bulk.res); !reflect.DeepEqual(a, b) {
		t.Errorf("results differ:\n chunked %+v\n bulk    %+v", a, b)
	}
	if !reflect.DeepEqual(chunked.logs, bulk.logs) {
		t.Errorf("logs differ:\n chunked %q\n bulk    %q", chunked.logs, bulk.logs)
	}
	if chunked.res.BulkRows != 0 {
		t.Errorf("the chunked run bulk-loaded %d row(s)", chunked.res.BulkRows)
	}
	for key, done := range bulk.progress {
		if !monotonic(chunked.progress[key]) {
			continue // e.g. a second source table into the same destination starts again
		}
		for i := 1; i < len(done); i++ {
			if done[i] < done[i-1] {
				t.Errorf("progress of %s went back: %v", key, done)
				break
			}
		}
	}
}

func monotonic(xs []int64) bool {
	for i := 1; i < len(xs); i++ {
		if xs[i] < xs[i-1] {
			return false
		}
	}
	return true
}

func smallChunks(n, streak int) func(*Job) {
	return func(j *Job) {
		j.Rules.WriteChunk = n
		if streak > 0 {
			j.Rules.MaxConsecutiveRowFailures = streak
		}
	}
}

func TestTheBulkLoadWritesWhatTheChunkedWriteWrites(t *testing.T) {
	// Duplicates (the first one in wins), a row of another shape inside a chunk, a vendor already
	// on file, and values COPY must escape.
	seed := []string{"INSERT INTO plenum_cafm.vendors (id, organization_id, vendor_name, vendor_code) VALUES " +
		"('00000000-0000-4000-8000-0000000c0001', '" + org + "', 'On file', 'V-OLD')"}
	var rows [][]any
	odd := []string{"Tab\there", "Line\nbreak", "Back\\slash", "CR\rhere", `\N`, "Ünïcödé — ✓", `quote's "dq"`, `\.`}
	for i := 0; i < 26; i++ {
		city := any("Leeds")
		if i%3 == 0 {
			city = "York"
		}
		if i == 9 {
			city = nil // another column set: its chunk goes the chunked way
		}
		name := fmt.Sprintf("Vendor %02d", i)
		if i < len(odd) {
			name = odd[i]
		}
		rows = append(rows, []any{fmt.Sprintf("V%02d", i), name, city})
	}
	rows = append(rows, []any{"V03", "Second V03", "Leeds"}, []any{"V-OLD", "On file again", "Leeds"})
	chunked, bulk := twin(t, seed, smallChunks(4, 0), map[string]string{"Vendors": "vendors"},
		tbl{"Vendors", []string{"vendor_code", "vendor_name", "city"}, rows})
	sameTwin(t, chunked, bulk)
	if bulk.res.BulkRows == 0 {
		t.Fatal("nothing was bulk-loaded")
	}
	if got := bulk.dump["vendors"]; len(got) < 20 || !strings.Contains(strings.Join(got, "\n"), `Tab\there`) {
		t.Fatalf("vendors: %v", got)
	}
}

func TestRepeatedValuesTravelOnceAndComeBackTheSame(t *testing.T) {
	// Readings on two meters: the meter, the organisation and the building repeat on every row and
	// go in dictionaries; the timestamps repeat once per meter.
	var rows [][]any
	for i := 0; i < 40; i++ {
		mpan := "M1"
		if i%2 == 1 {
			mpan = "M2"
		}
		rows = append(rows, []any{mpan, fmt.Sprintf("2026-01-01T%02d:%02d:00", (i/2)/2, 30*((i/2)%2)),
			fmt.Sprintf("%d.25", i), "B-101"})
	}
	chunked, bulk := twin(t, []string{seedBuilding()}, smallChunks(4, 0), map[string]string{"Readings": "meter_readings"},
		tbl{"Readings", []string{"mpan", "timestamp", "kwh", "site"}, rows})
	sameTwin(t, chunked, bulk)
	if bulk.res.BulkRows != 40 {
		t.Fatalf("bulk-loaded %d of 40", bulk.res.BulkRows)
	}
}

func TestARowTheServerRefusesSendsOnlyItsChunkTheChunkedWay(t *testing.T) {
	// Row 17 overflows numeric(14,6): the bulk statement fails, the run is halved until the
	// failing chunk is alone, and that chunk goes the chunked way (the row skipped, its error kept).
	var rows [][]any
	for i := 0; i < 40; i++ {
		kwh := fmt.Sprintf("%d.5", i)
		if i == 17 {
			kwh = "999999999"
		}
		rows = append(rows, []any{"M1", fmt.Sprintf("2026-02-%02dT%02d:00:00", 1+i/24, i%24), kwh, "B-101"})
	}
	chunked, bulk := twin(t, []string{seedBuilding()}, smallChunks(4, 0), map[string]string{"Readings": "meter_readings"},
		tbl{"Readings", []string{"mpan", "timestamp", "kwh", "site"}, rows})
	sameTwin(t, chunked, bulk)
	if bulk.res.RowsSkipped != 1 || bulk.res.BulkRows != 36 {
		t.Fatalf("skipped %d bulk-loaded %d (want 1 and the 36 rows outside the failing chunk)",
			bulk.res.RowsSkipped, bulk.res.BulkRows)
	}
}

func TestAnAbandonedTableCountsNothingPastWhereItStopped(t *testing.T) {
	// Three identical failures stop the table in its first chunk. The vendor already on file and
	// the good rows after it are never reached: not counted as already present, not written.
	seed := []string{"INSERT INTO plenum_cafm.vendors (id, organization_id, vendor_name, vendor_code) VALUES " +
		"('00000000-0000-4000-8000-0000000c0001', '" + org + "', 'On file', 'V-OLD')"}
	long := strings.Repeat("x", 400)
	var rows [][]any
	for i := 0; i < 4; i++ {
		rows = append(rows, []any{fmt.Sprintf("BAD%d", i), long + fmt.Sprint(i)})
	}
	rows = append(rows, []any{"V-OLD", "On file"})
	for i := 0; i < 8; i++ {
		rows = append(rows, []any{fmt.Sprintf("G%d", i), fmt.Sprintf("Good %d", i)})
	}
	chunked, bulk := twin(t, seed, smallChunks(4, 3), map[string]string{"Vendors": "vendors"},
		tbl{"Vendors", []string{"vendor_code", "vendor_name"}, rows})
	sameTwin(t, chunked, bulk)
	if len(bulk.res.Tables) != 1 || bulk.res.Tables[0].AlreadyPresent != 0 || len(bulk.dump["vendors"]) != 1 {
		t.Fatalf("tables %+v vendors %v", bulk.res.Tables, bulk.dump["vendors"])
	}
}

func TestATriggerOrAnOmittedSequenceKeepsTheChunkedWrite(t *testing.T) {
	seed := []string{
		`CREATE FUNCTION plenum_cafm.bk_noop() RETURNS trigger LANGUAGE plpgsql AS 'BEGIN RETURN NEW; END'`,
		`CREATE TRIGGER bk_t BEFORE INSERT ON plenum_cafm.vendors FOR EACH ROW EXECUTE FUNCTION plenum_cafm.bk_noop()`,
	}
	var vendors, wos [][]any
	for i := 0; i < 12; i++ {
		vendors = append(vendors, []any{fmt.Sprintf("V%02d", i), fmt.Sprintf("Vendor %02d", i)})
		wos = append(wos, []any{fmt.Sprintf("Job %02d", i)}) // no wo_code: the default draws on a sequence
	}
	chunked, bulk := twin(t, seed, smallChunks(4, 0), map[string]string{"Vendors": "vendors", "WOs": "work_orders"},
		tbl{"Vendors", []string{"vendor_code", "vendor_name"}, vendors},
		tbl{"WOs", []string{"description"}, wos})
	sameTwin(t, chunked, bulk)
	if bulk.res.BulkRows != 0 {
		t.Fatalf("bulk-loaded %d row(s) into a table with a trigger or a sequence default", bulk.res.BulkRows)
	}
}

func TestWhatAnOmittedColumnsDefaultMayDo(t *testing.T) {
	def := func(s string) *string { return &s }
	tab := &pgschema.Table{Exists: true, Kind: "r", Columns: []*pgschema.Column{
		{Name: "id"}, {Name: "made", Default: def("now()")}, {Name: "flag", Default: def("false")},
		{Name: "code", Default: def("('WO-'::text || lpad((nextval('work_order_code_seq'::regclass))::text, 6, '0'::text))")},
		{Name: "uid", Default: def("gen_random_uuid()")}, {Name: "n", Identity: true},
		{Name: "j", Default: def("'{}'::jsonb")}, {Name: "at", Default: def("timezone('utc'::text, now())")},
		{Name: "s", Default: def("'nextval(not a call)'::text")}, {Name: "ck", Default: def("clock_timestamp()")},
		// One value per statement, where the chunked path has one per row.
		{Name: "st", Default: def("statement_timestamp()")},
		// A typmod in a cast reads like a call: refused, to be safe.
		{Name: "num", Default: def("0::numeric(10,2)")},
		// A quoted user function the text scan cannot see; the server says it depends on one.
		{Name: "q", Default: def(`"NextRef"()`), DefaultUsesObject: true},
	}}
	risky := ",st,num,q"
	for cols, want := range map[string]bool{
		"id,code,uid,n" + risky:      true,
		"id,uid,n" + risky:           false, // code omitted: nextval
		"id,code,n" + risky:          false, // uid omitted: a random id draws on the parity sequence
		"id,code,uid" + risky:        false, // identity omitted
		"id,code,uid,n,made" + risky: true,
		"id,code,uid,n,num,q":        false, // st omitted
		"id,code,uid,n,st,q":         false, // num omitted
		"id,code,uid,n,st,num":       false, // q omitted
	} {
		if got := omittedDefaultsAreSafe(tab, strings.Split(cols, ",")); got != want {
			t.Errorf("present %s: safe=%v, want %v", cols, got, want)
		}
	}
}

func TestARandomIDTheWriterWouldDrawIsDrawnByTheServerInBulk(t *testing.T) {
	// Outside parity runs a row with no id gets a random one, which nothing reads back: in bulk the
	// server draws it (gen_random_uuid, version 4 as uuid4 is) instead of 37 bytes a row going up.
	var rows [][]any
	for i := 0; i < 24; i++ {
		rows = append(rows, []any{"M1", fmt.Sprintf("2026-03-01T%02d:00:00", i), fmt.Sprintf("%d.5", i), "B-101"})
	}
	random := func(j *Job) { smallChunks(4, 0)(j); j.DeterministicIDs = false }
	chunked, bulk := twin(t, []string{seedBuilding()}, random, map[string]string{"Readings": "meter_readings"},
		tbl{"Readings", []string{"mpan", "timestamp", "kwh", "site"}, rows})
	if bulk.res.BulkRows != 24 || bulk.res.ServerIDs != 24 {
		t.Fatalf("bulk-loaded %d, server ids %d", bulk.res.BulkRows, bulk.res.ServerIDs)
	}
	ids := map[string]bool{}
	strip := func(r twinRun) {
		for i, row := range r.dump["meter_readings"] {
			var m map[string]any
			if err := json.Unmarshal([]byte(row), &m); err != nil {
				t.Fatal(err)
			}
			id, _ := m["id"].(string)
			if r.res == bulk.res {
				if !regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`).MatchString(id) || ids[id] {
					t.Fatalf("server id %q", id)
				}
				ids[id] = true
			}
			delete(m, "id")
			b, _ := json.Marshal(m)
			r.dump["meter_readings"][i] = string(b)
		}
		sort.Strings(r.dump["meter_readings"])
	}
	strip(chunked)
	strip(bulk)
	sameTwin(t, chunked, bulk)
}

func TestManyBulkStatementsHoldFewLocks(t *testing.T) {
	// Every lock a subtransaction takes stays held until the write commits. Temporary tables made
	// and dropped by each bulk statement left ~30 lock-table entries per statement, and the lock
	// table is shared by every session on the server: the statements must reuse their tables.
	// Here the readings alternate between two shapes every chunk: 40 bulk statements, 2 shapes.
	var rows [][]any
	for i := 0; i < 160; i++ {
		src := any(nil)
		if (i/4)%2 == 1 {
			src = "dcc"
		}
		rows = append(rows, []any{"M1", fmt.Sprintf("2026-04-%02dT%02d:00:00", 1+i/24, i%24), fmt.Sprintf("%d.5", i), "B-101", src})
	}
	locks := -1
	beforeCommit = func(ctx context.Context, tx pgx.Tx) {
		if err := tx.QueryRow(ctx, "SELECT count(*) FROM pg_locks WHERE pid = pg_backend_pid()").Scan(&locks); err != nil {
			t.Fatal(err)
		}
	}
	defer func() { beforeCommit = nil }()
	chunked, bulk := twin(t, []string{seedBuilding()}, smallChunks(4, 0), map[string]string{"Readings": "meter_readings"},
		tbl{"Readings", []string{"mpan", "timestamp", "kwh", "site", "source"}, rows})
	sameTwin(t, chunked, bulk)
	if bulk.res.BulkRows != 160 {
		t.Fatalf("bulk-loaded %d of 160", bulk.res.BulkRows)
	}
	if locks < 0 || locks > 120 {
		t.Fatalf("the write held %d locks before commit", locks)
	}
}

func TestRowsAnAbandonedTableNeverReachedLeaveNoAnswersBehind(t *testing.T) {
	// Sheet 1 stops at its third identical failure; its last row — the same building and name as
	// its first, the building's id written in lower case — is never reached. Sheet 2 names that
	// section again: the database already has it (uuids compare by value), so it is a duplicate.
	// Deciding sheet 1's rows up front must not leave an answer for the row the loop never reached.
	up := strings.ToUpper(b101)
	s1 := [][]any{{up, "Floor 1", "10"}}
	for i := 0; i < 3; i++ {
		s1 = append(s1, []any{up, fmt.Sprintf("Floor X%d", i), "99999999999"})
	}
	s1 = append(s1, []any{b101, "Floor 1", "10"})
	chunked, bulk := twin(t, []string{seedBuilding()}, smallChunks(4, 3),
		map[string]string{"S1": "building_sections", "S2": "building_sections"},
		tbl{"S1", []string{"building_id", "name", "gross_area_m2"}, s1},
		tbl{"S2", []string{"building_id", "name", "gross_area_m2"}, [][]any{{b101, "Floor 1", "20"}}})
	sameTwin(t, chunked, bulk)
	if n := len(bulk.dump["building_sections"]); n != 1 {
		t.Fatalf("building_sections: %d rows, want the one section", n)
	}
}

func TestALookupThePrefetchCouldNotAnswerKeepsTheChunkedWrite(t *testing.T) {
	// A NUL byte makes the vendor_name prefetch fail, so the loop asks each name as it reaches
	// the row — and it never reaches the rows after the table stops. Asking them all up front
	// would ask questions the loop never asks (and log the failing one).
	long := strings.Repeat("x", 400)
	var rows [][]any
	for i := 0; i < 4; i++ {
		rows = append(rows, []any{fmt.Sprintf("BAD%d", i), long + fmt.Sprint(i)})
	}
	for i := 0; i < 8; i++ {
		rows = append(rows, []any{fmt.Sprintf("G%d", i), fmt.Sprintf("Good %d", i)})
	}
	rows = append(rows, []any{"G8", "Bad\x00Name"})
	chunked, bulk := twin(t, nil, smallChunks(4, 3), map[string]string{"Vendors": "vendors"},
		tbl{"Vendors", []string{"vendor_code", "vendor_name"}, rows})
	sameTwin(t, chunked, bulk)
}

func TestADomainDefaultKeepsTheChunkedWrite(t *testing.T) {
	// A default carried by the column's domain, not the column, is invisible to the column's own
	// default — and the staging table's column would inherit it too. A domain-typed table is
	// written the chunked way.
	seed := []string{
		`CREATE SEQUENCE plenum_cafm.bk_code_seq`,
		`CREATE DOMAIN plenum_cafm.bk_code AS text DEFAULT ('C-' || nextval('plenum_cafm.bk_code_seq')::text)`,
		`CREATE TABLE plenum_cafm.bk_items (id uuid PRIMARY KEY, name varchar(10) CHECK (name <> 'bad'), code plenum_cafm.bk_code)`,
	}
	var rows [][]any
	for _, n := range []string{"r1", "r2", "r3", "r4", "r5", "bad", "r7"} {
		rows = append(rows, []any{n})
	}
	chunked, bulk := twin(t, seed, smallChunks(2, 0), map[string]string{"Items": "bk_items"},
		tbl{"Items", []string{"name"}, rows})
	sameTwin(t, chunked, bulk)
	if bulk.res.BulkRows != 0 {
		t.Fatalf("bulk-loaded %d row(s) into a table with a domain default", bulk.res.BulkRows)
	}
}

func TestABulkRunStopsGrowingAtItsByteBudget(t *testing.T) {
	chunk := func(n, width int) flushPoint {
		var rows []*pendRow
		for i := 0; i < n; i++ {
			rows = append(rows, &pendRow{args: []any{strings.Repeat("v", width), nil}})
		}
		return flushPoint{rows: rows}
	}
	fl := []flushPoint{chunk(4, 10), chunk(4, 10), chunk(4, 1000), chunk(4, 10)}
	if j := runEnd(fl, 0, 100, 1<<20); j != 4 {
		t.Errorf("rows and bytes both under budget: run to %d, want 4", j)
	}
	if j := runEnd(fl, 0, 100, 200); j != 2 {
		t.Errorf("byte budget 200: run to %d, want 2 (the third chunk is ~4 KB)", j)
	}
	if j := runEnd(fl, 0, 8, 1<<20); j != 2 {
		t.Errorf("row budget 8: run to %d, want 2", j)
	}
	if j := runEnd(fl, 2, 100, 200); j != 3 {
		t.Errorf("a chunk alone over budget still makes a run of one: run to %d, want 3", j)
	}
}

func TestOnlyAPlainTableWithNothingThatReadsItTakesTheBulkLoad(t *testing.T) {
	w := &writer{}
	base := func() *pgschema.Table {
		return &pgschema.Table{Schema: "plenum_cafm", Name: "x", Exists: true, Kind: "r"}
	}
	if !w.bulkReady(&tablePlan{dest: "x", tab: base()}) {
		t.Fatal("a plain table is refused")
	}
	for name, mod := range map[string]func(*pgschema.Table){
		"partitioned":         func(t *pgschema.Table) { t.Kind = "p" },
		"a partition":         func(t *pgschema.Table) { t.Partition = true },
		"inheritance":         func(t *pgschema.Table) { t.Inherits = true },
		"row security":        func(t *pgschema.Table) { t.RowSecurity = true },
		"a check calling SQL": func(t *pgschema.Table) { t.CallsUserCode = true },
		"a trigger":           func(t *pgschema.Table) { t.UserTriggers = true },
		"a rule":              func(t *pgschema.Table) { t.Rules = true },
	} {
		tab := base()
		mod(tab)
		if w.bulkReady(&tablePlan{dest: "x", tab: tab}) {
			t.Errorf("%s: taken in bulk", name)
		}
	}
}

func TestACheckThatCountsTheTableKeepsTheChunkedWrite(t *testing.T) {
	// "At most two vendors in Leeds": the check reads the table. Row by row it sees the rows before
	// it; inside one statement it would not, and every row would pass.
	seed := []string{
		`CREATE FUNCTION plenum_cafm.bk_room(c text) RETURNS boolean LANGUAGE sql STABLE AS
			$$ SELECT count(*) < 2 FROM plenum_cafm.vendors WHERE city = c $$`,
		`ALTER TABLE plenum_cafm.vendors ADD CONSTRAINT bk_quota CHECK (plenum_cafm.bk_room(city))`,
	}
	var rows [][]any
	for i := 0; i < 8; i++ {
		rows = append(rows, []any{fmt.Sprintf("V%d", i), fmt.Sprintf("Vendor %d", i), "Leeds"})
	}
	chunked, bulk := twin(t, seed, smallChunks(4, 0), map[string]string{"Vendors": "vendors"},
		tbl{"Vendors", []string{"vendor_code", "vendor_name", "city"}, rows})
	sameTwin(t, chunked, bulk)
	if bulk.res.BulkRows != 0 || len(bulk.dump["vendors"]) != 2 {
		t.Fatalf("bulk-loaded %d, vendors %d (want 0 and the quota's 2)", bulk.res.BulkRows, len(bulk.dump["vendors"]))
	}
}

func TestAnEventTriggerKeepsTheChunkedWrite(t *testing.T) {
	// A DDL audit would record the bulk load's temporary tables: rows the chunked write never writes.
	seed := []string{
		`CREATE TABLE plenum_cafm.bk_ddl_log (tag text)`,
		`CREATE FUNCTION plenum_cafm.bk_ddl_audit() RETURNS event_trigger LANGUAGE plpgsql AS
			$$ BEGIN INSERT INTO plenum_cafm.bk_ddl_log VALUES (tg_tag); END $$`,
		`CREATE EVENT TRIGGER bk_ddl_audit ON ddl_command_end EXECUTE FUNCTION plenum_cafm.bk_ddl_audit()`,
	}
	var rows [][]any
	for i := 0; i < 12; i++ {
		rows = append(rows, []any{fmt.Sprintf("V%02d", i), fmt.Sprintf("Vendor %02d", i)})
	}
	chunked, bulk := twin(t, seed, smallChunks(4, 0), map[string]string{"Vendors": "vendors"},
		tbl{"Vendors", []string{"vendor_code", "vendor_name"}, rows})
	sameTwin(t, chunked, bulk)
	if bulk.res.BulkRows != 0 {
		t.Fatalf("bulk-loaded %d with a DDL event trigger", bulk.res.BulkRows)
	}
}

func TestAUniqueExpressionIndexOverUserCodeKeepsTheChunkedWrite(t *testing.T) {
	// The index's function counts the table: row by row each row gets its own slot; in one
	// statement every row would get the first slot and ON CONFLICT would drop all but one.
	seed := []string{
		`CREATE TABLE plenum_cafm.bk_slots (id uuid PRIMARY KEY, org text)`,
		`CREATE FUNCTION plenum_cafm.bk_slot(o text) RETURNS text LANGUAGE sql IMMUTABLE AS
			$$ SELECT o || '#' || (SELECT count(*) FROM plenum_cafm.bk_slots WHERE org = o) $$`,
		`CREATE UNIQUE INDEX bk_slots_slot ON plenum_cafm.bk_slots (plenum_cafm.bk_slot(org))`,
	}
	rows := [][]any{{"o1"}, {"o1"}, {"o1"}, {"o1"}, {"o1"}, {"o1"}, {"o1"}, {"o1"}}
	chunked, bulk := twin(t, seed, smallChunks(4, 0), map[string]string{"Slots": "bk_slots"},
		tbl{"Slots", []string{"org"}, rows})
	sameTwin(t, chunked, bulk)
	if bulk.res.BulkRows != 0 || len(bulk.dump["bk_slots"]) != 8 {
		t.Fatalf("bulk-loaded %d, slots %d (want 0 and all 8)", bulk.res.BulkRows, len(bulk.dump["bk_slots"]))
	}
}
