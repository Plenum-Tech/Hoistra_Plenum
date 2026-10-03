package write

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"

	"hoistra/engine/internal/arrowtab"
	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/protocol"
	"hoistra/engine/internal/rules"
	"hoistra/engine/internal/testdb"
)

const org = "11111111-1111-4111-8111-111111111111"

type tbl struct {
	name string
	cols []string
	rows [][]any // string, nil, or int (the filled 0)
}

func loadSpec(t *testing.T) rules.Spec {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("testdata", "rules_spec.json"))
	if err != nil {
		t.Fatal(err)
	}
	var s rules.Spec
	if err := json.Unmarshal(b, &s); err != nil {
		t.Fatal(err)
	}
	return s
}

func writeCleaned(t *testing.T, tables ...tbl) string {
	t.Helper()
	dir := t.TempDir()
	var out []*arrowtab.Table
	for _, tb := range tables {
		b := arrowtab.NewBuilder(tb.name, tb.cols)
		for _, r := range tb.rows {
			cells := make([]cell.Cell, len(r))
			for i, v := range r {
				switch x := v.(type) {
				case nil:
					cells[i] = cell.None
				case string:
					cells[i] = cell.Of(x)
				case int:
					cells[i] = cell.OfInt(int64(x))
				}
			}
			b.Append(cells)
		}
		out = append(out, b.Build())
	}
	if err := arrowtab.WriteDir(dir, out); err != nil {
		t.Fatal(err)
	}
	return dir
}

type env struct {
	t    *testing.T
	ctx  context.Context
	conn *pgx.Conn
	spec rules.Spec
}

func newEnv(t *testing.T, seed ...string) *env {
	t.Helper()
	conn := testdb.Fresh(t)
	ctx := context.Background()
	for _, q := range seed {
		if _, err := conn.Exec(ctx, q); err != nil {
			t.Fatalf("seed %s: %v", q, err)
		}
	}
	return &env{t: t, ctx: ctx, conn: conn, spec: loadSpec(t)}
}

func (e *env) job(mode string, routing map[string]string, tables ...tbl) Job {
	return Job{
		Mode: mode, Schema: "plenum_cafm", OrganizationID: org, CleanedDir: writeCleaned(e.t, tables...),
		OutDir: e.t.TempDir(), Routing: routing, ApprovedNewColumns: map[string][]string{}, Rules: e.spec,
		DeterministicIDs: true,
	}
}

func (e *env) run(job Job) (*Result, error) {
	var out bytes.Buffer
	res, err := Run(e.ctx, job, testdb.DSN(), protocol.NewEmitter(&out))
	e.t.Logf("engine events:\n%s", out.String()) // shown only when the test fails
	return res, err
}

func (e *env) mustRun(job Job) *Result {
	e.t.Helper()
	res, err := e.run(job)
	if err != nil {
		e.t.Fatalf("run: %v", err)
	}
	return res
}

func (e *env) count(sql string, args ...any) int {
	e.t.Helper()
	var n int
	if err := e.conn.QueryRow(e.ctx, sql, args...).Scan(&n); err != nil {
		e.t.Fatalf("%s: %v", sql, err)
	}
	return n
}

func (e *env) str(sql string, args ...any) string {
	e.t.Helper()
	var s *string
	if err := e.conn.QueryRow(e.ctx, sql, args...).Scan(&s); err != nil {
		e.t.Fatalf("%s: %v", sql, err)
	}
	if s == nil {
		return "<nil>"
	}
	return *s
}

const b101 = "00000000-0000-4000-8000-0000000b0101"

func seedBuilding() string {
	return "INSERT INTO plenum_cafm.buildings (building_id, organization_id, name, building_code) VALUES ('" + b101 + "', '" + org + "', 'Harbour Point', 'B-101')"
}

func TestAssetsLinkToBuildingsAndMergeByCode(t *testing.T) {
	e := newEnv(t, seedBuilding(),
		"INSERT INTO plenum_cafm.assets (id, organization_id, asset_name, asset_code) VALUES ('00000000-0000-4000-8000-0000000a0001', '"+org+"', 'Old pump', 'P-1')")
	res := e.mustRun(e.job("apply", map[string]string{"Assets": "assets"}, tbl{"Assets",
		[]string{"asset_code", "asset_name", "site"},
		[][]any{{"P-1", "Pump 1", "Harbour Point"}, {"P-2", "Pump 2", "B-101"}}}))
	if res.RowsMerged != 1 || res.BuildingsLinked != 2 {
		t.Fatalf("merged %d linked %d", res.RowsMerged, res.BuildingsLinked)
	}
	if got := e.str("SELECT building_id::text || '|' || asset_name FROM plenum_cafm.assets WHERE asset_code = 'P-1'"); got != b101+"|Pump 1" {
		t.Fatalf("merged asset: %s", got)
	}
	if got := e.str("SELECT building_id::text FROM plenum_cafm.assets WHERE asset_code = 'P-2'"); got != b101 {
		t.Fatalf("inserted asset building: %s", got)
	}
}

func TestWorkOrdersInheritTheirAssetsBuildingAndResolveTheirAsset(t *testing.T) {
	e := newEnv(t, seedBuilding(),
		"INSERT INTO plenum_cafm.assets (id, organization_id, asset_name, asset_code, building_id) VALUES ('00000000-0000-4000-8000-0000000a0001', '"+org+"', 'Pump', 'A-1', '"+b101+"')")
	e.mustRun(e.job("apply", map[string]string{"WOs": "work_orders"}, tbl{"WOs",
		[]string{"wo_code", "asset_id", "description"}, [][]any{{"W-1", "A-1", "Fix pump"}}}))
	got := e.str("SELECT coalesce(building_id::text, '-') || '|' || coalesce(asset_id::text, '-') || '|' || title FROM plenum_cafm.work_orders WHERE wo_code = 'W-1'")
	if got != b101+"|00000000-0000-4000-8000-0000000a0001|Fix pump" {
		t.Fatalf("work order: %s", got)
	}
}

func TestReadingsCreateMetersOnlyWithABuilding(t *testing.T) {
	e := newEnv(t, seedBuilding())
	job := e.job("apply", map[string]string{"Readings": "meter_readings"}, tbl{"Readings",
		[]string{"mpan", "timestamp", "kwh", "site"},
		[][]any{{"M1", "2026-01-01T00:00:00", "1.5", "B-101"}, {"M1", "2026-01-01T00:30:00", "2.5", "B-101"}}})
	res := e.mustRun(job)
	if res.MetersCreated != 1 || res.MetersLinked != 2 {
		t.Fatalf("created %d linked %d", res.MetersCreated, res.MetersLinked)
	}
	if n := e.count("SELECT count(*) FROM plenum_cafm.meter_readings r JOIN plenum_cafm.energy_meters m ON m.id = r.meter_id WHERE m.mpan = 'M1' AND m.building_id = $1", b101); n != 2 {
		t.Fatalf("readings on the meter: %d", n)
	}
}

func TestReadingsThatNameNoBuildingStopAtTheHundredthIdenticalFailure(t *testing.T) {
	// 600 readings name a meter with no building to create it against, then 10 name one that can
	// be made. The first chunk of 500 stops at its 100th identical failure and the table stops
	// with it: the good readings in the next chunk are never sent, as in Python.
	e := newEnv(t, seedBuilding())
	rows := make([][]any, 0, 610)
	for i := 0; i < 600; i++ {
		rows = append(rows, []any{"M9", "2026-01-01T00:00:00", "1", nil})
	}
	for i := 0; i < 10; i++ {
		rows = append(rows, []any{"M1", fmt.Sprintf("2026-01-02T%02d:00:00", i), "1", "B-101"})
	}
	res := e.mustRun(e.job("apply", map[string]string{"Readings": "meter_readings"},
		tbl{"Readings", []string{"mpan", "timestamp", "kwh", "site"}, rows}))
	if n := e.count("SELECT count(*) FROM plenum_cafm.meter_readings"); n != 0 {
		t.Fatalf("readings written after the table stopped: %d", n)
	}
	if res.RowsSkipped != 500 {
		t.Fatalf("skipped %d, want the abandoned chunk's 500 (Python does not count the rows it never reached)", res.RowsSkipped)
	}
	if len(res.RowErrors) == 0 || !strings.Contains(res.RowErrors[0],
		"meter_readings: stopped after 100 consecutive identical failures — every row is failing the same way, so the remaining 400 were not attempted.") {
		t.Fatalf("no abandonment message first in %v", res.RowErrors)
	}
	if len(res.MetersUnlinked) != 1 || res.MetersUnlinked[0] != "M9" {
		t.Fatalf("unlinked %v", res.MetersUnlinked)
	}
	if res.MetersCreated != 1 { // every row is resolved before any is written, so M1 is still made
		t.Fatalf("meters created %d", res.MetersCreated)
	}
}

func TestATypeMismatchDropsTheFieldAndKeepsTheRow(t *testing.T) {
	e := newEnv(t)
	res := e.mustRun(e.job("apply", map[string]string{"Parts": "spare_parts"}, tbl{"Parts",
		[]string{"part_code", "part_name", "reorder_level"}, [][]any{{"SP-1", "Belt", "lots"}}}))
	if n := e.count("SELECT count(*) FROM plenum_cafm.spare_parts WHERE part_code = 'SP-1' AND reorder_level = 0"); n != 1 {
		t.Fatalf("row not kept with its default: %d", n)
	}
	if !strings.Contains(strings.Join(res.RowErrors, "\n"), "spare_parts.reorder_level: 1 value(s) did not fit column type 'integer' (e.g. 'lots')") {
		t.Fatalf("mismatch not reported: %v", res.RowErrors)
	}
}

func TestANumericColumnIsWidenedToTextForCodes(t *testing.T) {
	// Every numeric column of the platform's own tables is read by a view, which stops the
	// ALTER (Python's too), so the widening is shown on a table nothing else reads.
	e := newEnv(t, "CREATE TABLE plenum_cafm.custom_gauges (id uuid PRIMARY KEY, organization_id uuid, label text, reading integer)")
	e.mustRun(e.job("apply", map[string]string{"Gauges": "custom_gauges"}, tbl{"Gauges",
		[]string{"label", "reading"}, [][]any{{"G1", "T001"}, {"G2", "17"}}}))
	if got := e.str("SELECT data_type FROM information_schema.columns WHERE table_schema = 'plenum_cafm' AND table_name = 'custom_gauges' AND column_name = 'reading'"); got != "text" {
		t.Fatalf("column type %s", got)
	}
	if got := e.str("SELECT string_agg(reading, ',' ORDER BY label) FROM plenum_cafm.custom_gauges"); got != "T001,17" {
		t.Fatalf("values %s", got)
	}
}

func TestAWideningAViewBlocksKeepsTheTypeAndDropsTheValue(t *testing.T) {
	e := newEnv(t)
	res := e.mustRun(e.job("apply", map[string]string{"Parts": "spare_parts"}, tbl{"Parts",
		[]string{"part_code", "part_name", "max_quantity"}, [][]any{{"SP-1", "Belt", "T001"}}}))
	if got := e.str("SELECT data_type FROM information_schema.columns WHERE table_schema = 'plenum_cafm' AND table_name = 'spare_parts' AND column_name = 'max_quantity'"); got != "integer" {
		t.Fatalf("column type %s", got)
	}
	if n := e.count("SELECT count(*) FROM plenum_cafm.spare_parts WHERE part_code = 'SP-1' AND max_quantity IS NULL"); n != 1 {
		t.Fatalf("row not kept without the value: %d", n)
	}
	if !strings.Contains(strings.Join(res.RowErrors, "\n"), "spare_parts.max_quantity: 1 value(s) did not fit column type 'integer' (e.g. 'T001')") {
		t.Fatalf("mismatch not reported: %v", res.RowErrors)
	}
}

func TestNewColumnsOnACoreTableNeedApproval(t *testing.T) {
	e := newEnv(t)
	job := e.job("apply", map[string]string{"Assets": "assets"}, tbl{"Assets",
		[]string{"asset_code", "asset_name", "colour", "flavour"}, [][]any{{"A-9", "Fan", "red", "mint"}}})
	job.ApprovedNewColumns = map[string][]string{"assets": {"colour"}}
	e.mustRun(job)
	if got := e.str("SELECT colour FROM plenum_cafm.assets WHERE asset_code = 'A-9'"); got != "red" {
		t.Fatalf("approved column: %s", got)
	}
	if n := e.count("SELECT count(*) FROM information_schema.columns WHERE table_schema = 'plenum_cafm' AND table_name = 'assets' AND column_name = 'flavour'"); n != 0 {
		t.Fatal("an unapproved column was added to a core table")
	}
}

func TestAMissingTableIsCreatedFromItsFirstRow(t *testing.T) {
	e := newEnv(t)
	e.mustRun(e.job("apply", map[string]string{"Widgets": "custom_widgets"}, tbl{"Widgets",
		[]string{"Widget Name", "qty"}, [][]any{{"Sprocket", 0}, {"Cog", "5"}}}))
	if n := e.count("SELECT count(*) FROM plenum_cafm.custom_widgets"); n != 2 {
		t.Fatalf("rows %d", n)
	}
	if got := e.str("SELECT data_type FROM information_schema.columns WHERE table_schema = 'plenum_cafm' AND table_name = 'custom_widgets' AND column_name = 'qty'"); got != "bigint" {
		t.Fatalf("qty type %s (the first row's filled 0 makes it BIGINT)", got)
	}
}

func TestAnOrphanForeignKeyIsNulledWhenTheColumnIsNullable(t *testing.T) {
	e := newEnv(t)
	res := e.mustRun(e.job("apply", map[string]string{"Assets": "assets"}, tbl{"Assets",
		[]string{"asset_code", "asset_name", "location_id"},
		[][]any{{"A-1", "Fan", "00000000-0000-4000-8000-00000000dead"}}}))
	if got := e.str("SELECT coalesce(location_id::text, 'null') FROM plenum_cafm.assets WHERE asset_code = 'A-1'"); got != "null" {
		t.Fatalf("location_id %s", got)
	}
	if !strings.Contains(strings.Join(res.RowErrors, "\n"), "assets.location_id: 1 row(s) referenced a parent not present in the target") {
		t.Fatalf("orphan not reported: %v", res.RowErrors)
	}
}

func TestTwoViolatedForeignKeysFailTheRow(t *testing.T) {
	// Python nulls the key the first violation names and retries once; the second key fails the retry.
	e := newEnv(t)
	res := e.mustRun(e.job("apply", map[string]string{"Assets": "assets"}, tbl{"Assets",
		[]string{"asset_code", "asset_name", "location_id", "parent_asset_id"},
		[][]any{{"A-1", "Fan", "00000000-0000-4000-8000-00000000dead", "00000000-0000-4000-8000-00000000beef"},
			{"A-2", "Pump", nil, nil}}}))
	if n := e.count("SELECT count(*) FROM plenum_cafm.assets"); n != 1 || res.RowsSkipped != 1 {
		t.Fatalf("assets %d skipped %d", n, res.RowsSkipped)
	}
	if !strings.Contains(strings.Join(res.RowErrors, "\n"), "ForeignKeyViolationError") {
		t.Fatalf("no foreign-key error reported: %v", res.RowErrors)
	}
}

func TestANotNullForeignKeyOrphanFailsTheRow(t *testing.T) {
	e := newEnv(t)
	res := e.mustRun(e.job("apply", map[string]string{"Readings": "meter_readings"}, tbl{"Readings",
		[]string{"meter_id", "reading_at", "consumption_kwh"},
		[][]any{{"00000000-0000-4000-8000-00000000dead", "2026-01-01T00:00:00", "1"}}}))
	if n := e.count("SELECT count(*) FROM plenum_cafm.meter_readings"); n != 0 || res.RowsSkipped != 1 {
		t.Fatalf("readings %d skipped %d", n, res.RowsSkipped)
	}
	// The row error is Python's first 220 characters, which end before the DETAIL line.
	if !strings.Contains(strings.Join(res.RowErrors, "\n"), `ForeignKeyViolationError'>: insert or update on table "meter_readings" violates foreign key constraint "meter_readings_meter_id_fkey"`) {
		t.Fatalf("orphan not reported: %v", res.RowErrors)
	}
}

func TestAnInvalidEnumLabelFailsOnlyItsRow(t *testing.T) {
	e := newEnv(t, "CREATE TYPE plenum_cafm.t_mood AS ENUM ('happy', 'ok')",
		"CREATE TABLE plenum_cafm.custom_moods (id uuid PRIMARY KEY, organization_id uuid, who text, mood plenum_cafm.t_mood)")
	res := e.mustRun(e.job("apply", map[string]string{"Moods": "custom_moods"}, tbl{"Moods",
		[]string{"who", "mood"}, [][]any{{"a", "happy"}, {"b", "sad"}, {"c", "ok"}}}))
	if n := e.count("SELECT count(*) FROM plenum_cafm.custom_moods"); n != 2 || res.RowsSkipped != 1 {
		t.Fatalf("rows %d skipped %d", n, res.RowsSkipped)
	}
	if !strings.Contains(strings.Join(res.RowErrors, "\n"), `invalid input value for enum plenum_cafm.t_mood: "sad"`) &&
		!strings.Contains(strings.Join(res.RowErrors, "\n"), `invalid input value for enum t_mood: "sad"`) {
		t.Fatalf("enum failure not reported: %v", res.RowErrors)
	}
}

func TestAnOverlongValueFailsOnlyItsRow(t *testing.T) {
	e := newEnv(t)
	long := strings.Repeat("x", 400)
	res := e.mustRun(e.job("apply", map[string]string{"Vendors": "vendors"}, tbl{"Vendors",
		[]string{"vendor_code", "vendor_name"}, [][]any{{"V1", "Acme"}, {"V2", long}, {"V3", "Bolt"}}}))
	if n := e.count("SELECT count(*) FROM plenum_cafm.vendors"); n != 2 || res.RowsSkipped != 1 {
		t.Fatalf("vendors %d skipped %d", n, res.RowsSkipped)
	}
}

func TestADuplicateInsideTheFileFollowsPythonsInsertOrder(t *testing.T) {
	// r1 and r3 share a column set, r2 has an extra column: within a chunk Python runs r1+r3's
	// statement before r2's, so r3 takes vendor_code V2 and r2 is the duplicate.
	e := newEnv(t)
	e.mustRun(e.job("apply", map[string]string{"Vendors": "vendors"}, tbl{"Vendors",
		[]string{"vendor_code", "vendor_name", "city"},
		[][]any{{"V1", "First", nil}, {"V2", "Second", "Leeds"}, {"V2", "Third", nil}}}))
	if got := e.str("SELECT vendor_name FROM plenum_cafm.vendors WHERE vendor_code = 'V2'"); got != "Third" {
		t.Fatalf("V2 is %s", got)
	}
}

func TestTheAssetsUpsertFoldsLaterRowsOntoTheFirst(t *testing.T) {
	e := newEnv(t)
	e.mustRun(e.job("apply", map[string]string{"Assets": "assets"}, tbl{"Assets",
		[]string{"asset_code", "serial_number", "asset_name", "model"},
		[][]any{{"X1", "S1", "First", nil}, {"X2", "S1", "Second", "M2"}}}))
	got := e.str("SELECT asset_code || '|' || asset_name || '|' || coalesce(model, '-') FROM plenum_cafm.assets WHERE serial_number = 'S1'")
	if got != "X1|Second|M2" {
		t.Fatalf("upserted asset %s", got)
	}
}

func TestARerunWritesNothingTwice(t *testing.T) {
	e := newEnv(t, seedBuilding())
	tables := []tbl{
		{"Vendors", []string{"vendor_code", "vendor_name"}, [][]any{{"V1", "Acme"}}},
		{"Assets", []string{"asset_code", "asset_name", "site"}, [][]any{{"A-1", "Fan", "B-101"}}},
		{"WOs", []string{"wo_code", "asset_id", "vendor_name", "description"}, [][]any{{"W-1", "A-1", "Acme", "Fix"}}},
	}
	routing := map[string]string{"Vendors": "vendors", "Assets": "assets", "WOs": "work_orders"}
	e.mustRun(e.job("apply", routing, tables...))
	// The second run draws its ids from a block of its own: a row it wrote again would take a new
	// id and be counted, instead of colliding on the first run's id and vanishing.
	again := e.job("apply", routing, tables...)
	again.DeterministicIDsFrom = 500000
	second := e.mustRun(again)
	if n := e.count("SELECT (SELECT count(*) FROM plenum_cafm.vendors) + (SELECT count(*) FROM plenum_cafm.assets) + (SELECT count(*) FROM plenum_cafm.work_orders)"); n != 3 {
		t.Fatalf("rows after a re-run: %d", n)
	}
	if second.RowsMerged != 1 {
		t.Fatalf("the asset should merge on the re-run: %+v", second)
	}
}

func TestThePlanChangesNothing(t *testing.T) {
	e := newEnv(t, seedBuilding(), "INSERT INTO plenum_cafm.vendors (id, organization_id, vendor_name, vendor_code) VALUES ('00000000-0000-4000-8000-0000000c0001', '"+org+"', 'Acme', 'V1')")
	job := e.job("plan", map[string]string{"Vendors": "vendors", "Parts": "spare_parts"},
		tbl{"Vendors", []string{"vendor_code", "vendor_name"}, [][]any{{"V1", "Acme"}, {"V2", "Bolt"}}},
		tbl{"Parts", []string{"part_code", "part_name", "reorder_level", "flavour"}, [][]any{{"SP-1", "Belt", "lots", "x"}}})
	res := e.mustRun(job)
	if n := e.count("SELECT count(*) FROM plenum_cafm.vendors"); n != 1 {
		t.Fatalf("the plan wrote vendors: %d", n)
	}
	if n := e.count("SELECT count(*) FROM plenum_cafm.spare_parts"); n != 0 {
		t.Fatalf("the plan wrote parts: %d", n)
	}
	if res.Plan == nil || len(res.Plan.Tables) != 2 {
		t.Fatalf("plan %+v", res.Plan)
	}
	v := res.Plan.Tables[0]
	if v.Dest != "vendors" || v.Rows != 2 || v.AlreadyPresent != 1 {
		t.Fatalf("vendors plan %+v", v)
	}
	p := res.Plan.Tables[1]
	if len(p.InvalidValues) != 1 || p.InvalidValues[0].Column != "reorder_level" || len(p.DroppedColumns) != 1 {
		t.Fatalf("parts plan %+v", p)
	}
	if _, err := os.Stat(filepath.Join(job.OutDir, "plan.json")); err != nil {
		t.Fatal("plan.json not written")
	}
}

func TestAFailingDDLStatementIsDdlFailed(t *testing.T) {
	e := newEnv(t)
	job := e.job("apply", map[string]string{"Vendors": "vendors"}, tbl{"Vendors", []string{"vendor_name"}, [][]any{{"Acme"}}})
	job.DDL = []DDLStatement{{SQL: "ALTER TABLE plenum_cafm.vendors ADD COLUMN ok_col text", Description: "first"},
		{SQL: "ALTER TABLE plenum_cafm.no_such_table ADD COLUMN x text", Description: "second"}}
	_, err := e.run(job)
	var pe *protocol.Error
	if !errorsAs(err, &pe) || pe.Code != protocol.CodeDDL || !strings.Contains(pe.Message, "DDL execution failed at statement 2/2: 'second'") {
		t.Fatalf("got %v", err)
	}
	if n := e.count("SELECT count(*) FROM information_schema.columns WHERE table_name = 'vendors' AND column_name = 'ok_col'"); n != 0 {
		t.Fatal("the first DDL statement was not rolled back")
	}
}

func TestAGateDDLStringCarryingASecondStatementIsRefusedWhole(t *testing.T) {
	// The gates' DDL strings hold gate answers (column names, types) as typed. Python sent each one
	// as a prepared statement (SQLAlchemy + asyncpg), which the server refuses when it holds more
	// than one command; sent as a simple query, the second statement would run too.
	e := newEnv(t)
	job := e.job("apply", map[string]string{"Vendors": "vendors"}, tbl{"Vendors", []string{"vendor_name"}, [][]any{{"Acme"}}})
	job.DDL = []DDLStatement{{SQL: "ALTER TABLE plenum_cafm.vendors ADD COLUMN IF NOT EXISTS x TEXT; " +
		"CREATE TABLE plenum_cafm.smuggled (i int)", Description: "custom column x"}}
	_, err := e.run(job)
	var pe *protocol.Error
	if !errorsAs(err, &pe) || pe.Code != protocol.CodeDDL ||
		!strings.Contains(pe.Message, "cannot insert multiple commands into a prepared statement") {
		t.Fatalf("want ddl_failed refusing the second command, got %v", err)
	}
	if n := e.count("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'plenum_cafm' AND table_name = 'smuggled'"); n != 0 {
		t.Fatal("the second statement ran")
	}
	if n := e.count("SELECT count(*) FROM information_schema.columns WHERE table_name = 'vendors' AND column_name = 'x'"); n != 0 {
		t.Fatal("the first statement ran")
	}
	if n := e.count("SELECT count(*) FROM plenum_cafm.vendors"); n != 0 {
		t.Fatal("rows were written after the DDL failed")
	}
}

func TestEachTablesDDLIsCommittedOnItsOwnAndABlockedALTERGivesUp(t *testing.T) {
	// Python commits each table's DDL before it loads rows and caps every statement at 45 s
	// (asyncpg command_timeout). Here another session holds meter_readings open: the engine's
	// ALTER on it must give up at the cap, and while it waits, buildings — altered before it —
	// must not stay locked (every page in the product reads buildings).
	e := newEnv(t, seedBuilding())
	defer func(d time.Duration) { ddlStatementTimeout = d }(ddlStatementTimeout)
	ddlStatementTimeout = 3 * time.Second

	blocker, err := pgx.Connect(e.ctx, testdb.DSN())
	if err != nil {
		t.Fatal(err)
	}
	defer blocker.Close(context.Background())
	btx, err := blocker.Begin(e.ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = btx.Rollback(context.Background()) }()
	if _, err := btx.Exec(e.ctx, "SELECT count(*) FROM plenum_cafm.meter_readings"); err != nil {
		t.Fatal(err)
	}

	job := e.job("apply", map[string]string{"Buildings": "buildings", "Readings": "meter_readings"},
		tbl{"Buildings", []string{"building_code", "name", "added_b"}, [][]any{{"B-102", "Second", "x"}}},
		tbl{"Readings", []string{"mpan", "timestamp", "kwh", "site", "added_r"},
			[][]any{{"M1", "2026-01-01T00:00:00", "1", "B-101", "y"}}})
	done := make(chan error, 1)
	go func() {
		var out bytes.Buffer
		_, err := Run(e.ctx, job, testdb.DSN(), protocol.NewEmitter(&out))
		done <- err
	}()

	// Wait until the engine is queued behind the blocker, then read buildings from a third session.
	probe, err := pgx.Connect(e.ctx, testdb.DSN())
	if err != nil {
		t.Fatal(err)
	}
	defer probe.Close(context.Background())
	for i := 0; i < 200; i++ {
		var waiting int
		_ = probe.QueryRow(e.ctx, "SELECT count(*) FROM pg_stat_activity WHERE application_name = 'hoist-engine' "+
			"AND wait_event_type = 'Lock'").Scan(&waiting)
		if waiting > 0 {
			break
		}
		time.Sleep(25 * time.Millisecond)
	}
	if _, err := probe.Exec(e.ctx, "SET lock_timeout = '1s'"); err != nil {
		t.Fatal(err)
	}
	var n int
	readErr := probe.QueryRow(e.ctx, "SELECT count(*) FROM plenum_cafm.buildings").Scan(&n)

	var runErr error
	select {
	case runErr = <-done:
	case <-time.After(30 * time.Second):
		_ = btx.Rollback(context.Background()) // let the engine finish before the test ends
		<-done
		t.Fatal("the blocked ALTER on meter_readings did not give up")
	}
	if readErr != nil {
		t.Fatalf("buildings stayed locked while a later table's DDL waited: %v", readErr)
	}
	if runErr == nil {
		t.Fatal("the run succeeded although its ALTER on meter_readings could not get its lock")
	}
	if c := e.count("SELECT count(*) FROM information_schema.columns WHERE table_schema = 'plenum_cafm' " +
		"AND table_name = 'buildings' AND column_name = 'added_b'"); c != 1 {
		t.Fatal("buildings' DDL was not committed on its own")
	}
	if c := e.count("SELECT count(*) FROM plenum_cafm.buildings WHERE building_code = 'B-102'"); c != 0 {
		t.Fatal("rows were written although the DDL failed")
	}
}

func TestALostConnectionRollsTheWholeWriteBack(t *testing.T) {
	e := newEnv(t, seedBuilding())
	rows := make([][]any, 60000)
	for i := range rows {
		rows[i] = []any{"M1", time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC).Add(time.Duration(i) * 30 * time.Minute).Format("2006-01-02T15:04:05"), "1"}
	}
	job := e.job("apply", map[string]string{"Readings": "meter_readings"},
		tbl{"Vendors", []string{"vendor_code", "vendor_name"}, [][]any{{"V1", "Acme"}}},
		tbl{"Readings", []string{"mpan", "timestamp", "kwh", "site"}, withSite(rows)})
	job.Routing["Vendors"] = "vendors"
	// The killer has its own connection (a pgx connection serves one goroutine), and the test
	// waits for it to finish before it counts rows.
	killer, err := pgx.Connect(e.ctx, testdb.DSN())
	if err != nil {
		t.Fatal(err)
	}
	defer killer.Close(context.Background())
	killed := make(chan struct{})
	go func() { // kill the writer's backend once it is loading readings
		defer close(killed)
		for i := 0; i < 400; i++ {
			time.Sleep(25 * time.Millisecond)
			var n int
			_ = killer.QueryRow(e.ctx, "SELECT count(*) FROM pg_stat_activity WHERE application_name = 'hoist-engine' AND query ILIKE '%meter_readings%'").Scan(&n)
			if n > 0 {
				_, _ = killer.Exec(e.ctx, "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name = 'hoist-engine'")
				return
			}
		}
	}()
	_, err = e.run(job)
	<-killed
	var pe *protocol.Error
	if !errorsAs(err, &pe) || pe.Code != protocol.CodeConnLost {
		t.Fatalf("want connection_lost, got %v", err)
	}
	if n := e.count("SELECT (SELECT count(*) FROM plenum_cafm.vendors) + (SELECT count(*) FROM plenum_cafm.meter_readings) + (SELECT count(*) FROM plenum_cafm.energy_meters)"); n != 0 {
		t.Fatalf("partial write kept: %d rows", n)
	}
}

func withSite(rows [][]any) [][]any {
	out := make([][]any, len(rows))
	for i, r := range rows {
		out[i] = append(append([]any{}, r...), "B-101")
	}
	return out
}
