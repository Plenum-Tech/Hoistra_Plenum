package write

import (
	"bytes"
	"context"
	"reflect"
	"sort"
	"strings"
	"testing"

	"hoistra/engine/internal/coerce"
	"hoistra/engine/internal/protocol"
	"hoistra/engine/internal/resolve"
	"hoistra/engine/internal/rules"
	"hoistra/engine/internal/testdb"
)

// The retired-vendor SQL on a real Postgres: the parity database (scripts/parity_db.sh up, then
// scripts/dev.sh test-db), skipped without it like every test that calls newEnv. The fakes in
// retired_vendor_test.go and resolve/retired_test.go hold the logic; these hold the statements:
// to_jsonb on alias t, lower(t.id::text) = $1 under NOT (live), and the live predicate inside the
// prefetch's EXISTS. Each of those lookups reads a failed statement as "no match", so every test
// here also needs a live hit and no "lookup failed" line, or a broken statement would pass as a refusal.

const (
	dbRetired = "00000000-0000-4000-8000-0000000d0001" // status 'Deleted', name never marked
	dbLive    = "00000000-0000-4000-8000-0000000d0002"
	dbMarked  = "00000000-0000-4000-8000-0000000d0003" // status 'deleted', name marked
	dbRetaken = "00000000-0000-4000-8000-0000000d0004"
)

func retiredVendorSeed() []string {
	ins := "INSERT INTO plenum_cafm.vendors (id, organization_id, vendor_code, vendor_name, status) VALUES "
	return []string{
		ins + "('" + dbRetired + "', '" + org + "', 'APX-01', 'Apex Mechanical Ltd', 'Deleted')",
		ins + "('" + dbLive + "', '" + org + "', 'BRK-01', 'Brook Lifts', 'active')",
		ins + "('" + dbMarked + "', '" + org + "', NULL, 'Cole Electrical (deleted)', 'deleted')",
	}
}

func codeKey(code string) rules.KeyMatch {
	return rules.KeyMatch{Cols: []string{"vendor_code"}, Vals: []coerce.Value{{K: coerce.Text, S: code}}}
}

func noFailedLookup(t *testing.T, logs string, warnings []string) {
	t.Helper()
	if strings.Contains(logs, "lookup failed") || len(warnings) > 0 {
		t.Fatalf("a lookup failed on Postgres:\n%s\n%v", logs, warnings)
	}
}

func TestRetiredVendorLookupsRunOnPostgres(t *testing.T) {
	e := newEnv(t, retiredVendorSeed()...)
	tx, err := e.conn.Begin(e.ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = tx.Rollback(context.Background()) }()
	var logs bytes.Buffer
	w, p := vendorKeyWriter()
	w.org, w.em = org, protocol.NewEmitter(&logs)

	for _, c := range []struct {
		km   rules.KeyMatch
		want bool
	}{{nameKey("Apex Mechanical Ltd"), false}, {codeKey("APX-01"), false}, {nameKey("Brook Lifts"), true}, {codeKey("BRK-01"), true}} {
		if dup, err := w.keyLookup(e.ctx, tx, p, c.km); err != nil || dup != c.want {
			t.Fatalf("keyLookup %v: got %v %v, want %v", c.km.Vals, dup, err, c.want)
		}
	}

	keys := []rules.KeyMatch{nameKey("Apex Mechanical Ltd"), nameKey("Brook Lifts"), nameKey("Cole Electrical (deleted)")}
	items := make([]*built, len(keys))
	for i, km := range keys {
		items[i] = &built{keys: []rules.KeyMatch{km}}
	}
	known, err := w.prefetchKeys(e.ctx, tx, p, items)
	if err != nil {
		t.Fatal(err)
	}
	for i, want := range []bool{false, true, false} {
		// a key missing from the answer means the one statement failed and Python's path was taken
		if got, answered := known[nkCacheKey("vendors", keys[i])]; !answered || got != want {
			t.Fatalf("prefetch %v: got %v answered %v, want %v", keys[i].Vals, got, answered, want)
		}
	}

	spec := loadSpec(t)
	s := resolve.New(tx, &spec, org, "plenum_cafm", nil, nil)
	for _, hint := range []string{"Apex Mechanical Ltd", "APX-01", "Cole Electrical (deleted)", dbRetired} {
		if id, ok := s.Reference(e.ctx, "vendor_id", hint); ok {
			t.Fatalf("%q linked to the retired vendor %s", hint, id)
		}
	}
	for _, hint := range []string{"brook lifts", "BRK-01", strings.ToUpper(dbLive)} {
		if id, ok := s.Reference(e.ctx, "vendor_id", hint); !ok || id != dbLive {
			t.Fatalf("%q: got %q %v, want the live vendor", hint, id, ok)
		}
	}
	for id, want := range map[string]bool{dbRetired: false, dbMarked: false, dbLive: true,
		"00000000-0000-4000-8000-0000000d00ff": true} { // naming no vendor yet: may be one this file writes
		if s.Linkable(e.ctx, "vendor_id", id) != want {
			t.Fatalf("Linkable %s: want %v", id, want)
		}
	}

	// A supplier taken on again: the live twin is found, never ambiguous with the retired row.
	if _, err := tx.Exec(e.ctx, "INSERT INTO plenum_cafm.vendors (id, organization_id, vendor_name) VALUES ('"+
		dbRetaken+"', '"+org+"', 'Apex Mechanical Ltd')"); err != nil {
		t.Fatal(err)
	}
	if id, ok := s.Reference(e.ctx, "vendor_id", "Apex Mechanical Ltd"); !ok || id != dbRetaken || len(s.RefAmbiguous) != 0 {
		t.Fatalf("got %q %v ambiguous %v, want the vendor taken on again", id, ok, s.RefAmbiguous)
	}
	if dup, err := w.keyLookup(e.ctx, tx, p, nameKey("Apex Mechanical Ltd")); err != nil || !dup {
		t.Fatalf("the vendor taken on again is not already written: %v %v", dup, err)
	}
	noFailedLookup(t, logs.String(), s.Warnings)
}

// to_jsonb(t)->>'status' rather than t.status: a vendors table without the column still answers,
// every row live.
func TestRetiredVendorSQLRunsWithoutAStatusColumn(t *testing.T) {
	e := newEnv(t, append(retiredVendorSeed(), "ALTER TABLE plenum_cafm.vendors DROP COLUMN status CASCADE")...)
	tx, err := e.conn.Begin(e.ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = tx.Rollback(context.Background()) }()
	var logs bytes.Buffer
	w, p := vendorKeyWriter()
	w.org, w.em = org, protocol.NewEmitter(&logs)
	if dup, err := w.keyLookup(e.ctx, tx, p, nameKey("Apex Mechanical Ltd")); err != nil || !dup {
		t.Fatalf("keyLookup: %v %v", dup, err)
	}
	known, err := w.prefetchKeys(e.ctx, tx, p, []*built{{keys: []rules.KeyMatch{codeKey("APX-01")}}})
	if err != nil || !known[nkCacheKey("vendors", codeKey("APX-01"))] {
		t.Fatalf("prefetch %v %v", known, err)
	}
	spec := loadSpec(t)
	s := resolve.New(tx, &spec, org, "plenum_cafm", nil, nil)
	if id, ok := s.Reference(e.ctx, "vendor_id", "APX-01"); !ok || id != dbRetired {
		t.Fatalf("Reference: %q %v", id, ok)
	}
	if !s.Linkable(e.ctx, "vendor_id", dbRetired) {
		t.Fatal("Linkable refused with no status column")
	}
	noFailedLookup(t, logs.String(), s.Warnings)
}

// The whole writer: a vendors sheet re-registers the retired supplier and skips the live one; work
// orders carrying the retired id or the marked name are written unlinked and reported.
func TestARunNeverLinksNewWorkToARetiredVendor(t *testing.T) {
	e := newEnv(t, append([]string{seedBuilding()}, retiredVendorSeed()...)...)
	job := e.job("apply", map[string]string{"Vendors": "vendors", "Assets": "assets", "WOs": "work_orders"},
		tbl{"Vendors", []string{"vendor_code", "vendor_name"}, [][]any{{"APX-02", "Apex Mechanical Ltd"}, {"BRK-01", "Brook Lifts"}}},
		tbl{"Assets", []string{"asset_code", "asset_name", "site"}, [][]any{{"A-1", "Fan", "B-101"}}},
		tbl{"WOs", []string{"wo_code", "asset_id", "vendor_id", "vendor_name", "description"}, [][]any{
			{"W-1", "A-1", dbRetired, nil, "Fix"},
			{"W-2", "A-1", dbLive, nil, "Fix"},
			{"W-3", "A-1", nil, "Cole Electrical (deleted)", "Fix"},
			{"W-4", "A-1", nil, "Apex Mechanical Ltd", "Fix"},
		}})
	var out bytes.Buffer
	res, err := Run(e.ctx, job, testdb.DSN(), protocol.NewEmitter(&out))
	if err != nil {
		t.Fatalf("run: %v\n%s", err, out.String())
	}
	noFailedLookup(t, out.String(), nil)
	if n := e.count("SELECT count(*) FROM plenum_cafm.vendors"); n != 4 {
		t.Fatalf("vendors %d, want 3 seeded + Apex taken on again", n)
	}
	apex := e.str("SELECT id::text FROM plenum_cafm.vendors WHERE vendor_name = 'Apex Mechanical Ltd' AND status = 'active'")
	for wo, want := range map[string]string{"W-1": "<nil>", "W-2": dbLive, "W-3": "<nil>", "W-4": apex} {
		if got := e.str("SELECT vendor_id::text FROM plenum_cafm.work_orders WHERE wo_code = $1", wo); got != want {
			t.Fatalf("%s vendor_id %s, want %s\n%s", wo, got, want, out.String())
		}
	}
	unres := res.References["unresolved"].(map[string][]string)["vendor_id"]
	sort.Strings(unres)
	if want := []string{dbRetired, "Cole Electrical (deleted)"}; !reflect.DeepEqual(unres, want) {
		t.Fatalf("unresolved %v, want %v", unres, want)
	}
}
