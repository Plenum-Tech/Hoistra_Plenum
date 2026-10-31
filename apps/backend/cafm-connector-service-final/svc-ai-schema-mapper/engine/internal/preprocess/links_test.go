package preprocess

import (
	"reflect"
	"testing"

	"hoistra/engine/internal/cell"
)

func TestLinksMeasureHowMuchOfAColumnTheOtherTableHolds(t *testing.T) {
	sites := built{name: "Sites", cols: []string{"site_code", "name"},
		rows: [][]cell.Cell{{s("S1"), s("Harbour")}, {s("S2"), s("Quay")}, {s("s3 "), s("Dock")}}}
	assets := built{name: "Assets", cols: []string{"asset_code", "site_code", "site_ref", "name"},
		rows: [][]cell.Cell{{s("A1"), s(" s1"), s("S1"), s("Pump")}, {s("A2"), s("S2"), s("X9"), s("Fan")},
			{s("A3"), s("S9"), none, s("Quay")}, {s("A4"), cell.OfInt(0), s(""), s("Dock")}}}
	wos := built{name: "Work_Orders", cols: []string{"wo", "asset_id"},
		rows: [][]cell.Cell{{s("W1"), s("a1")}, {s("W2"), s("A2")}, {s("W3"), s("A7")}}}
	got := measureLinks([]built{sites, assets, wos}, map[string][]string{"Assets": {"asset_code"}}, 200)
	want := []Link{
		{Table: "Sites", Column: "site_code", References: "Assets.site_code", Containment: 2.0 / 3},
		{Table: "Sites", Column: "name", References: "Assets.name", Containment: 2.0 / 3},
		{Table: "Assets", Column: "site_code", References: "Sites.site_code", Containment: 2.0 / 3}, // the 0 fill is not a value
		{Table: "Assets", Column: "site_ref", References: "Sites.site_code", Containment: 1.0 / 2},
		{Table: "Assets", Column: "name", References: "Sites.name", Containment: 2.0 / 4},
		{Table: "Work_Orders", Column: "asset_id", References: "Assets.asset_code", Containment: 2.0 / 3},
	}
	sortLinks(want)
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got  %+v\nwant %+v", got, want)
	}
	if got := measureLinks([]built{sites, assets, wos}, nil, 2); len(got) != 2 || got[0].Containment < got[1].Containment {
		t.Fatalf("the top pairs: %+v", got)
	}
}

func TestSingularTableNames(t *testing.T) {
	for in, want := range map[string]string{"Sites": "site", "work_orders": "work_order", "Properties": "property",
		"Addresses": "address", "Staff": "staff", "Bus": "bus"} {
		if got := singular(in); got != want {
			t.Errorf("%q: %q, want %q", in, got, want)
		}
	}
}
