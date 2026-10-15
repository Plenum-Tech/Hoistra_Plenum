package resolve

import (
	"context"
	"testing"

	"github.com/jackc/pgx/v5"

	"hoistra/engine/internal/rules"
	"hoistra/engine/internal/testdb"
)

const org = "11111111-1111-4111-8111-111111111111"

var spec = &rules.Spec{
	References: []rules.Reference{
		{Column: "asset_id", Table: "assets", MatchColumns: []string{"asset_code", "serial_number", "inventory_code"}},
		{Column: "vendor_id", Table: "vendors", MatchColumns: []string{"vendor_code", "vendor_name"}},
	},
}

func setup(t *testing.T, sql ...string) (context.Context, pgx.Tx) {
	t.Helper()
	conn := testdb.Fresh(t)
	ctx := context.Background()
	for _, q := range sql {
		if _, err := conn.Exec(ctx, q); err != nil {
			t.Fatalf("%s: %v", q, err)
		}
	}
	tx, err := conn.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = tx.Rollback(context.Background()) })
	return ctx, tx
}

func building(id, name, code string) string {
	return "INSERT INTO plenum_cafm.buildings (building_id, organization_id, name, building_code) VALUES ('" +
		id + "', '" + org + "', '" + name + "', '" + code + "')"
}

const (
	b301 = "00000000-0000-4000-8000-0000000b0301"
	b103 = "00000000-0000-4000-8000-0000000b0103"
)

func TestBuildingByCodeWinsOverAmbiguousName(t *testing.T) {
	ctx, tx := setup(t, building(b301, "Bishopsgate Tower", "B-301"), building(b103, "Bishopsgate Tower", "B-103"))
	s := New(tx, spec, org, "plenum_cafm", map[string]string{"b-301": "Bishopsgate Tower"}, nil)
	if id, ok := s.Building(ctx, "B-301"); !ok || id != b301 {
		t.Fatalf("got %q %v", id, ok)
	}
}

func TestBuildingAmbiguousResolvesToNothingAndIsReported(t *testing.T) {
	ctx, tx := setup(t, building(b301, "Harbour Point", "HP-1"), building(b103, "Harbour Point", "HP-2"))
	s := New(tx, spec, org, "plenum_cafm", nil, nil)
	if id, ok := s.Building(ctx, " harbour point "); ok {
		t.Fatalf("ambiguous name resolved to %s", id)
	}
	if len(s.BuildingAmbiguous) != 1 || s.BuildingAmbiguous[0] != "harbour point" {
		t.Fatalf("ambiguous %v", s.BuildingAmbiguous)
	}
}

func TestBuildingViaTheSitesTable(t *testing.T) {
	// plenum_cafm.sites keys organisations by integer in this schema (as on hoistra_test), so the
	// via-sites path is exercised in a schema of its own with the columns the lookup reads.
	ctx, tx := setup(t,
		"CREATE SCHEMA rt",
		"CREATE TABLE rt.buildings (building_id uuid, organization_id uuid, name text, building_code text, site_id text)",
		"CREATE TABLE rt.sites (site_id text, organization_id uuid, site_name text, site_code text, building_name text, name text)",
		"INSERT INTO rt.buildings VALUES ('"+b301+"', '"+org+"', 'Riverside House', 'RH-1', NULL)",
		"INSERT INTO rt.sites VALUES ('S-9', '"+org+"', 'Riverside House', 'RIV', NULL, NULL)")
	s := New(tx, spec, org, "rt", nil, nil)
	if id, ok := s.Building(ctx, "riv"); !ok || id != b301 {
		t.Fatalf("got %q %v", id, ok)
	}
}

func TestSectionPrefersTheExactName(t *testing.T) {
	floor := "00000000-0000-4000-8000-00000000f002"
	ctx, tx := setup(t, building(b301, "Tower", "T-1"),
		"INSERT INTO plenum_cafm.floors (floor_id, building_id, level, name) VALUES ('"+floor+"', '"+b301+"', 2, 'Level 2')",
		"INSERT INTO plenum_cafm.building_sections (section_id, building_id, floor_id, name, section_type) VALUES ('00000000-0000-4000-8000-00000000a001', '"+b301+"', '"+floor+"', 'Level 2', 'floor')",
		"INSERT INTO plenum_cafm.building_sections (section_id, building_id, floor_id, name, section_type) VALUES ('00000000-0000-4000-8000-00000000a002', '"+b301+"', '"+floor+"', 'Plant Room', 'plant')")
	s := New(tx, spec, org, "plenum_cafm", nil, nil)
	if id, ok := s.Section(ctx, b301, "Level 2"); !ok || id != "00000000-0000-4000-8000-00000000a001" {
		t.Fatalf("got %q %v", id, ok)
	}
	if id, ok := s.Floor(ctx, b301, "2"); !ok || id != floor {
		t.Fatalf("floor by level: %q %v", id, ok)
	}
}

func TestMeterFindCachesOnlyAHit(t *testing.T) {
	ctx, tx := setup(t, building(b301, "Tower", "T-1"))
	s := New(tx, spec, org, "plenum_cafm", nil, nil)
	if _, ok := s.MeterFind(ctx, "MPAN1"); ok {
		t.Fatal("no meter yet")
	}
	if _, err := tx.Exec(ctx, "INSERT INTO plenum_cafm.energy_meters (id, organization_id, building_id, meter_type, mpan) VALUES ('00000000-0000-4000-8000-0000000e0001', '"+org+"', '"+b301+"', 'electricity', 'MPAN1')"); err != nil {
		t.Fatal(err)
	}
	if id, ok := s.MeterFind(ctx, "mpan1"); !ok || id != "00000000-0000-4000-8000-0000000e0001" {
		t.Fatalf("got %q %v", id, ok)
	}
}

func TestAMeterIsCreatedOnlyWithABuilding(t *testing.T) {
	ctx, tx := setup(t, building(b301, "Tower", "T-1"))
	s := New(tx, spec, org, "plenum_cafm", nil, nil)
	if _, ok := s.MeterResolve(ctx, "NEW1", MeterSpec{MeterType: "electricity", MPAN: "NEW1"}); ok {
		t.Fatal("created a meter with no building")
	}
	id, ok := s.MeterResolve(ctx, "NEW2", MeterSpec{BuildingID: b301, MeterType: "gas", MPRN: "NEW2", IsSubMeter: true})
	if !ok || id == "" || s.MetersCreated != 1 || len(s.MetersUnlinked) != 1 || s.MetersUnlinked[0] != "NEW1" {
		t.Fatalf("id %q ok %v created %d unlinked %v", id, ok, s.MetersCreated, s.MetersUnlinked)
	}
	var mprn string
	var sub bool
	if err := tx.QueryRow(ctx, "SELECT mprn, is_sub_meter FROM plenum_cafm.energy_meters WHERE id::text = $1", id).Scan(&mprn, &sub); err != nil || mprn != "NEW2" || !sub {
		t.Fatalf("created meter %q %v %v", mprn, sub, err)
	}
	if again, _ := s.MeterResolve(ctx, "new2", MeterSpec{BuildingID: b301}); again != id || s.MetersCreated != 1 {
		t.Fatalf("second resolve created again: %s", again)
	}
}

func TestAnAmbiguousMeterIsNeverCreated(t *testing.T) {
	ctx, tx := setup(t, building(b301, "Tower", "T-1"),
		"INSERT INTO plenum_cafm.energy_meters (id, organization_id, building_id, meter_type, mpan) VALUES ('00000000-0000-4000-8000-0000000e0001', '"+org+"', '"+b301+"', 'electricity', 'DUP')",
		"INSERT INTO plenum_cafm.energy_meters (id, organization_id, building_id, meter_type, mpan) VALUES ('00000000-0000-4000-8000-0000000e0002', '"+org+"', '"+b301+"', 'electricity', 'DUP')")
	s := New(tx, spec, org, "plenum_cafm", nil, nil)
	if _, ok := s.MeterResolve(ctx, "DUP", MeterSpec{BuildingID: b301}); ok || s.MetersCreated != 0 || len(s.MeterAmbiguous) != 1 {
		t.Fatalf("ok %v created %d ambiguous %v", ok, s.MetersCreated, s.MeterAmbiguous)
	}
}

func TestAReferenceMissIsAskedAgainLater(t *testing.T) {
	ctx, tx := setup(t)
	s := New(tx, spec, org, "plenum_cafm", nil, nil)
	if _, ok := s.Reference(ctx, "vendor_id", "Acme Ltd"); ok {
		t.Fatal("no vendor yet")
	}
	if _, err := tx.Exec(ctx, "INSERT INTO plenum_cafm.vendors (id, organization_id, vendor_name) VALUES ('00000000-0000-4000-8000-0000000c0001', '"+org+"', 'Acme Ltd')"); err != nil {
		t.Fatal(err)
	}
	if id, ok := s.Reference(ctx, "vendor_id", "acme ltd"); !ok || id != "00000000-0000-4000-8000-0000000c0001" {
		t.Fatalf("got %q %v", id, ok)
	}
	rep := s.ReferenceReport()
	if rep["resolved"] != 1 || rep["reads"] != 2 {
		t.Fatalf("report %v", rep)
	}
}

func TestALookupErrorIsNoMatchAndTheTransactionSurvives(t *testing.T) {
	ctx, tx := setup(t, "ALTER TABLE plenum_cafm.assets RENAME COLUMN inventory_code TO inventory_code_gone")
	s := New(tx, spec, org, "plenum_cafm", nil, nil)
	if _, ok := s.Reference(ctx, "asset_id", "A-1"); ok {
		t.Fatal("a failing lookup must read as no match")
	}
	var n int
	if err := tx.QueryRow(ctx, "SELECT count(*) FROM plenum_cafm.assets").Scan(&n); err != nil {
		t.Fatalf("transaction poisoned: %v", err)
	}
	if len(s.Warnings) == 0 {
		t.Fatal("the failed lookup must be reported")
	}
}

func TestAssetLookupsCacheHitsAndMisses(t *testing.T) {
	ctx, tx := setup(t, building(b301, "Tower", "T-1"),
		"INSERT INTO plenum_cafm.assets (id, organization_id, asset_name, asset_code, building_id) VALUES ('00000000-0000-4000-8000-0000000a0001', '"+org+"', 'Pump', 'P-1', '"+b301+"')")
	s := New(tx, spec, org, "plenum_cafm", nil, nil)
	if id, ok := s.ExistingAsset(ctx, "P-1"); !ok || id != "00000000-0000-4000-8000-0000000a0001" {
		t.Fatalf("existing asset %q %v", id, ok)
	}
	if b, ok := s.AssetBuilding(ctx, "P-1"); !ok || b != b301 {
		t.Fatalf("asset building %q %v", b, ok)
	}
	if _, ok := s.AssetBuilding(ctx, ""); ok {
		t.Fatal("empty ref")
	}
}
