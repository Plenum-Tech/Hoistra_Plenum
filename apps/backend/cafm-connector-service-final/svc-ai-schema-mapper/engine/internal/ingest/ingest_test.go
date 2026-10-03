package ingest

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"hoistra/engine/internal/arrowtab"
	"hoistra/engine/internal/cell"
)

func s(v string) cell.Cell { return cell.Of(v) }

var none = cell.None

// asJSON round-trips a report the way the Python side receives it.
func asJSON(t *testing.T, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func TestNaNScanReportsAsScanNanValuesDoes(t *testing.T) {
	tables := []*Table{
		{Name: "t1", Columns: []string{"a", "b"}, Rows: [][]cell.Cell{{s("x"), none}, {s(" \t"), s("y")}, {none, none}}},
		{Name: "empty", Columns: []string{}, Rows: nil},
		{Name: "t3", Columns: []string{"c"}, Rows: [][]cell.Cell{{s("1")}}},
	}
	got := asJSON(t, scanNaN(tables, 2))
	want := `{"total_nan_cells":4,"total_rows_with_nan":3,"total_rows":4,"columns_with_nan":2,"tables":{` +
		`"t1":{"row_count":3,"rows_with_nan":3,"nan_cells":4,"columns":{"b":2,"a":2},"sample_rows":[` +
		`{"row_index":0,"nan_columns":["b"],"values":{"a":"x","b":null}},` +
		`{"row_index":1,"nan_columns":["a"],"values":{"a":" \t","b":"y"}}]},` +
		`"empty":{"row_count":0,"rows_with_nan":0,"nan_cells":0,"columns":{},"sample_rows":[]},` +
		`"t3":{"row_count":1,"rows_with_nan":0,"nan_cells":0,"columns":{},"sample_rows":[]}}}`
	if got != want {
		t.Fatalf("got  %s\nwant %s", got, want)
	}
}

func TestNaNColumnsLeadByCountThenFirstSeen(t *testing.T) {
	tables := []*Table{{Name: "t", Columns: []string{"a", "b", "c"}, Rows: [][]cell.Cell{
		{none, s("1"), none}, {s("1"), none, none}, {s("1"), none, s("1")}, {s("1"), s(""), none}}}}
	got := asJSON(t, scanNaN(tables, 20))
	if want := `"columns":{"c":3,"b":3,"a":1}`; !contains(got, want) {
		t.Fatalf("%s does not contain %s", got, want)
	}
}

func contains(s, sub string) bool { return len(s) >= len(sub) && (s == sub || indexOf(s, sub) >= 0) }

func indexOf(s, sub string) int {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return i
		}
	}
	return -1
}

func col(vals ...string) []cell.Cell {
	out := make([]cell.Cell, len(vals))
	for i, v := range vals {
		if v == "<None>" {
			out[i] = none
		} else {
			out[i] = s(v)
		}
	}
	return out
}

// table builds a table from columns of values.
func table(name string, names []string, cols ...[]cell.Cell) *Table {
	t := &Table{Name: name, Columns: names}
	for r := range cols[0] {
		row := make([]cell.Cell, len(cols))
		for c := range cols {
			row[c] = cols[c][r]
		}
		t.Rows = append(t.Rows, row)
	}
	return t
}

func TestDuplicateColumnsMergeAsMergeDuplicateColumnsDoes(t *testing.T) {
	known := map[string]bool{"asset_code": true, "expiry_date": true, "next_due_date": true}
	tables := []*Table{
		table("works", []string{"id", "tagnum", "name"},
			col("A-1", "a-2 ", "A-3", "<None>"), col("a-1", "A-2", " A-3", ""), col("x", "y", "z", "w")),
		table("certs", []string{"expiry_date", "next_due_date", "ref"},
			col("2024", "2025", "2026"), col("2024", "2025", "2026"), col("r1", "r2", "r3")),
		table("assets", []string{"Asset Code", " asset_code ", "label"},
			col("A", "B", "C"), col("a", "b", "c"), col("1", "2", "3")),
		table("thin", []string{"p", "q"}, col("1", "2", "<None>"), col("1", "2", "")),
		table("mismatch", []string{"p", "q"}, col("1", "2", "3", "4"), col("1", "2", "3", "<None>")),
		table("ties", []string{"ab", "cd", "e"}, col("1", "2", "3"), col("1", "2", "3"), col("1", "2", "3")),
		{Name: "none", Columns: []string{}},
	}
	merged, report := mergeDuplicateColumns(tables, known)
	want := `{"total_merges":4,"total_columns_dropped":4,"tables":{` +
		`"works":[{"kept":"tagnum","dropped":["id"],"members":["id","tagnum"],"match_pct":100,"row_count":4}],` +
		`"certs":[{"kept":["expiry_date","next_due_date"],"dropped":[],"members":["expiry_date","next_due_date"],"match_pct":100,"row_count":3,"declined":"distinct destination columns"}],` +
		`"assets":[{"kept":" asset_code ","dropped":["Asset Code"],"members":["Asset Code"," asset_code "],"match_pct":100,"row_count":3}],` +
		`"ties":[{"kept":"ab","dropped":["cd","e"],"members":["ab","cd","e"],"match_pct":100,"row_count":3}]}}`
	if got := asJSON(t, report); got != want {
		t.Fatalf("got  %s\nwant %s", got, want)
	}
	kept := map[string][]string{}
	for _, k := range merged {
		kept[k.Name] = k.Kept
	}
	wantKept := map[string][]string{"works": {"tagnum", "name"}, "certs": {"expiry_date", "next_due_date", "ref"},
		"assets": {" asset_code ", "label"}, "thin": {"p", "q"}, "mismatch": {"p", "q"}, "ties": {"ab"}, "none": {}}
	if !reflect.DeepEqual(kept, wantKept) {
		t.Fatalf("kept %v", kept)
	}
}

func writeFile(t *testing.T, name, body string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), name)
	if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestACSVParsesToOneDataTable(t *testing.T) {
	src := writeFile(t, "a.csv", "\ufeffid;tag;note\n1;1;x\n2;2;\n3;3;NA\n4;4;z\n")
	out := filepath.Join(t.TempDir(), "full")
	res, err := Run(context.Background(), Job{Source: src, OutDir: out, PreviewRows: 2, NanSampleRows: 20}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if res.DetectedFileFormat != "csv" || res.SourceDelimiter != ";" || res.SourceEncoding != "utf-8" || len(res.Tables) != 1 {
		t.Fatalf("%+v", res)
	}
	tb := res.Tables[0]
	if tb.Name != "data" || tb.Rows != 4 || !reflect.DeepEqual(tb.Columns, []string{"id", "tag", "note"}) ||
		!reflect.DeepEqual(tb.KeptColumns, []string{"tag", "note"}) {
		t.Fatalf("%+v", tb)
	}
	// the preview is the parsed rows before the merge: the node measures table health on it,
	// then applies the merge report
	if got := asJSON(t, tb.Preview); got != `[{"id":"1","tag":"1","note":"x"},{"id":"2","tag":"2","note":null}]` {
		t.Fatalf("preview %s", got)
	}
	ts, err := arrowtab.ReadDir(out)
	if err != nil || len(ts) != 1 || !reflect.DeepEqual(ts[0].Columns, []string{"tag", "note"}) || ts[0].Rows != 4 {
		t.Fatalf("%v %+v", err, ts)
	}
}

func TestATextFileThatIsNotACSVFailsAsTheExcelReaderDoes(t *testing.T) {
	src := writeFile(t, "a.csv", "a,b\n\"x,1\n")
	_, err := Run(context.Background(), Job{Source: src, OutDir: t.TempDir(), PreviewRows: 10}, nil)
	if err == nil || err.Error() != "parse_error: Could not read the Excel file: Cannot detect file format" {
		t.Fatalf("%v", err)
	}
}

func TestPostWriteSheetNamesMatchOnLettersAndDigits(t *testing.T) {
	set := map[string]bool{"contractterms": true, "invoicelines": true}
	for name, want := range map[string]bool{"Contract Terms": true, "invoice_lines": true, "ContractTerms2": false, "Invoices": false} {
		if got := isPostWriteSheet(name, set); got != want {
			t.Errorf("%q: %v", name, got)
		}
	}
}
