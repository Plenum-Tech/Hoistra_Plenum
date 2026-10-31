package ingest

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/outputs"
	"hoistra/engine/internal/xlsxread"
)

func TestSheetNamesAreSafeSheetNames(t *testing.T) {
	name := newSheetNamer()
	long := strings.Repeat("L", 40)
	got := []string{name("a:b/c\\d?e*f[g]h"), name("   "), name(long), name(long), name("Data"), name("data"),
		name("DATA"), name(" spaced "), name("Café")}
	want := []string{"a_b_c_d_e_f_g_h", "sheet", strings.Repeat("L", 31), strings.Repeat("L", 29) + "_2",
		"Data", "data_2", "DATA_3", "spaced", "Café"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got  %q\nwant %q", got, want)
	}
}

func TestAFileStemIsPathlibs(t *testing.T) {
	for in, want := range map[string]string{"x.csv": "x", "a.b.csv": "a.b", ".csv": ".csv", "dir/y.tsv": "y",
		"a.": "a.", "noext": "noext", "": ""} {
		if got := pathStem(in); got != want {
			t.Errorf("%q: %q, want %q", in, got, want)
		}
	}
}

func TestALoneGenericSheetTakesTheFileName(t *testing.T) {
	for label, want := range map[string]bool{"Sheet1": true, "sheet": true, "SHEET 2": true, "Sheet \t12": true,
		"Sheet1a": false, "Data": false, "Sheets": false, "": false, "Sheet٣": true} {
		if got := isGenericSheet(label); got != want {
			t.Errorf("%q: %v", label, got)
		}
	}
}

// frameFile writes a CSV frame the way the schema-mapper hands one over (JSON lines: the column
// names, then each row).
func frameFile(t *testing.T, cols []string, rows [][]string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "f.jsonl")
	var b strings.Builder
	enc := json.NewEncoder(&b)
	enc.Encode(cols)
	for _, r := range rows {
		enc.Encode(r)
	}
	if err := os.WriteFile(p, []byte(b.String()), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

func workbookFile(t *testing.T, sheets []outputs.Sheet) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "w.xlsx")
	f, err := os.Create(p)
	if err != nil {
		t.Fatal(err)
	}
	if why, err := outputs.WriteFrames(f, sheets, ""); why != "" || err != nil {
		t.Fatalf("%q %v", why, err)
	}
	f.Close()
	return p
}

func TestCombineWritesOneWorkbookOfEverySheet(t *testing.T) {
	book := workbookFile(t, []outputs.Sheet{
		{Title: "Sites", Columns: []string{"code", " name "}, Rows: [][]cell.Cell{{cell.Of("S1"), cell.Of("Harbour")}}},
		{Title: "Banner", Columns: []string{"Report"}, Rows: [][]cell.Cell{{cell.None}, {cell.Of("a")}}},
	})
	lone := workbookFile(t, []outputs.Sheet{{Title: "Sheet1", Columns: []string{"x"}, Rows: [][]cell.Cell{{cell.Of("1")}}}})
	frame := frameFile(t, []string{"id", "id", "note"}, [][]string{{"1", "1", ""}, {"2", "2", "n"}})
	out := filepath.Join(t.TempDir(), "combined.xlsx")
	res, err := Combine(context.Background(), CombineJob{OutPath: out, Files: []CombineFile{
		{Path: book, Name: "b101.xlsx", Kind: "workbook"},
		{Path: frame, Name: "sites.csv", Kind: "frame"},
		{Path: lone, Name: "meters.xlsx", Kind: "workbook"},
	}}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(res.Sheets, []string{"Sites", "Banner", "sites_2", "meters"}) || res.Bytes <= 0 {
		t.Fatalf("%+v", res)
	}
	wb, err := xlsxread.Open(out)
	if err != nil {
		t.Fatal(err)
	}
	defer wb.Close()
	read := func(name string) ([]string, [][]string) {
		cols, rows, err := xlsxread.Sheet(wb, name)
		if err != nil {
			t.Fatal(err)
		}
		var out [][]string
		for _, r := range rows {
			var o []string
			for _, c := range r {
				o = append(o, c.PyStr())
			}
			out = append(out, o)
		}
		return cols, out
	}
	// the duplicate names the CSV's frame carries are written as they are; Node 1 reads them back
	// as pandas would (id, id.1)
	if cols, rows := read("sites_2"); !reflect.DeepEqual(cols, []string{"id", "id.1", "note"}) ||
		!reflect.DeepEqual(rows, [][]string{{"1", "1", "None"}, {"2", "2", "n"}}) {
		t.Fatalf("%q %q", cols, rows)
	}
	if cols, rows := read("Sites"); !reflect.DeepEqual(cols, []string{"code", "name"}) ||
		!reflect.DeepEqual(rows, [][]string{{"S1", "Harbour"}}) {
		t.Fatalf("%q %q", cols, rows)
	}
	if cols, rows := read("meters"); !reflect.DeepEqual(cols, []string{"x"}) || !reflect.DeepEqual(rows, [][]string{{"1"}}) {
		t.Fatalf("%q %q", cols, rows)
	}
}

func TestCombineFailsOnTheFirstFileThatFails(t *testing.T) {
	bad := filepath.Join(t.TempDir(), "bad.xlsx")
	os.WriteFile(bad, []byte("PK\x03\x04 not really"), 0o644)
	frame := frameFile(t, []string{"a"}, [][]string{{"1"}})
	_, err := Combine(context.Background(), CombineJob{OutPath: filepath.Join(t.TempDir(), "c.xlsx"), Files: []CombineFile{
		{Path: frame, Name: "ok.csv", Kind: "frame"},
		{Name: "broken.csv", Kind: "error", Error: "Error tokenizing data. C error: Expected 1 fields in line 3, saw 2"},
		{Path: bad, Name: "bad.xlsx", Kind: "workbook"},
	}}, nil)
	if err == nil || err.Error() != "parse_error: Could not parse 'broken.csv': Error tokenizing data. C error: Expected 1 fields in line 3, saw 2" {
		t.Fatalf("%v", err)
	}
	_, err = Combine(context.Background(), CombineJob{OutPath: filepath.Join(t.TempDir(), "c.xlsx"),
		Files: []CombineFile{{Path: bad, Name: "bad.xlsx", Kind: "workbook"}}}, nil)
	if err == nil || err.Error() != "parse_error: Could not parse 'bad.xlsx': Could not read the Excel file: Cannot detect file format" {
		t.Fatalf("%v", err)
	}
	_, err = Combine(context.Background(), CombineJob{OutPath: filepath.Join(t.TempDir(), "c.xlsx")}, nil)
	if err == nil || err.Error() != "parse_error: No parseable structured data found in uploads" {
		t.Fatalf("%v", err)
	}
}
