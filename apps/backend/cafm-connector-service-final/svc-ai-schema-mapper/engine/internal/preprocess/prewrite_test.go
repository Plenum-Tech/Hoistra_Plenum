package preprocess

import (
	"context"
	"reflect"
	"strings"
	"testing"

	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/testdb"
)

func TestPrewriteNamesWhatTheWriteWouldDropOrRefuse(t *testing.T) {
	testdb.Fresh(t)
	long := strings.Repeat("x", 151)
	parts := built{name: "Parts", cols: []string{"part_code", "unit_price", "warranty_months", "not_a_column"},
		rows: [][]cell.Cell{
			{s("P1"), s("12.5"), s("12"), s("z")},
			{s(long), s("abc"), s("99999999999"), s("z")},
			{s("P3"), s("abc"), cell.OfInt(0), s("z")},
		}}
	wos := built{name: "WOs", cols: []string{"title"}, rows: [][]cell.Cell{{s("Fix")}}}
	got, err := prewrite(context.Background(), testdb.DSN(), "plenum_cafm", []built{parts, wos},
		map[string]string{"Parts": "spare_parts", "WOs": "work_orders"}, []string{"organization_id", "org_id"})
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 || got[0].Dest != "spare_parts" || got[1].Dest != "work_orders" {
		t.Fatalf("%+v", got)
	}
	sp := got[0]
	if !reflect.DeepEqual(sp.Sources, []string{"Parts"}) || sp.Rows != 3 || !sp.Exists {
		t.Fatalf("%+v", sp)
	}
	byCol := map[string]PrewriteIssue{}
	for _, iv := range sp.InvalidValues {
		byCol[iv.Column] = iv
	}
	if iv := byCol["unit_price"]; iv.Count != 2 || iv.Reason != "not a numeric" || !reflect.DeepEqual(iv.Samples, []string{"abc"}) {
		t.Errorf("unit_price %+v", iv)
	}
	if iv := byCol["part_code"]; iv.Count != 1 || !strings.Contains(iv.Reason, "too long") {
		t.Errorf("part_code %+v", iv)
	}
	if iv := byCol["warranty_months"]; iv.Count != 1 || !strings.Contains(iv.Reason, "int32") {
		t.Errorf("warranty_months %+v", iv)
	}
	if _, ok := byCol["not_a_column"]; ok {
		t.Error("a column the table does not have is not checked")
	}
	if len(sp.RequiredMissing) != 0 { // part_name is NOT NULL, but the writer fills text with ""
		t.Errorf("%q", sp.RequiredMissing)
	}
	for _, c := range got[1].RequiredMissing {
		if c == "id" || c == "organization_id" || c == "title" {
			t.Errorf("work_orders %q is filled by the writer or the source", c)
		}
	}
}

func TestPrewriteWithoutADatabaseIsEmpty(t *testing.T) {
	got, err := prewrite(context.Background(), "", "plenum_cafm", []built{{name: "T", cols: []string{"a"}}}, nil, nil)
	if err != nil || len(got) != 0 {
		t.Fatalf("%+v %v", got, err)
	}
}
