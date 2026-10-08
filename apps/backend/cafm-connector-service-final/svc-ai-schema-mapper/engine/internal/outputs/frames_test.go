package outputs

import (
	"bytes"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/xlsxread"
)

func TestAnIllegalValueIsTheFirstPandasWrites(t *testing.T) {
	// pandas' ExcelFormatter writes the header, then the body column by column (probed: openpyxl
	// raises on "a\x07" in column a's second row before "b\x07" in column b's first).
	sheets := []Sheet{{Title: "S", Columns: []string{"a", "b"},
		Rows: [][]cell.Cell{{cell.Of("ok"), cell.Of("b\x07")}, {cell.Of("a\x07"), cell.Of("ok")}}}}
	why, err := WriteFrames(&bytes.Buffer{}, sheets, "2026-10-01T00:00:00Z")
	if err != nil || why != "a\x07 cannot be used in worksheets." {
		t.Fatalf("%q %v", why, err)
	}
	sheets = []Sheet{{Title: "A", Columns: []string{"a"}, Rows: [][]cell.Cell{{cell.Of("v\x07")}}},
		{Title: "B", Columns: []string{"h\x07"}, Rows: [][]cell.Cell{{cell.Of("x")}}}}
	if why, _ := WriteFrames(&bytes.Buffer{}, sheets, ""); why != "v\x07 cannot be used in worksheets." {
		t.Fatalf("%q", why)
	}
	sheets = []Sheet{{Title: "S", Columns: []string{"a"}, Rows: [][]cell.Cell{{cell.Of("x\ufffe")}}}}
	if why, _ := WriteFrames(&bytes.Buffer{}, sheets, ""); why != xmlIncompatible {
		t.Fatalf("%q", why)
	}
}

func TestFramesReadBackAsOpenpyxlWritesThem(t *testing.T) {
	long := strings.Repeat("x", 32770)
	sheets := []Sheet{
		{Title: "Data", Columns: []string{"a", "a", "=f"}, Rows: [][]cell.Cell{
			{cell.Of("1"), cell.Of("=SUM(1,2)"), cell.Of("#REF!")},
			{cell.Of(""), cell.None, cell.Of(" pad ")},
			{cell.Of(long), cell.Of("True"), cell.Of("2024-01-15 00:00:00")}}},
		{Title: "Empty", Columns: nil, Rows: nil},
		{Title: "HeaderOnly", Columns: []string{"Unnamed: 0", "b"}, Rows: nil},
	}
	p := filepath.Join(t.TempDir(), "c.xlsx")
	var buf bytes.Buffer
	if why, err := WriteFrames(&buf, sheets, "2026-10-01T00:00:00Z"); why != "" || err != nil {
		t.Fatalf("%q %v", why, err)
	}
	if err := os.WriteFile(p, buf.Bytes(), 0o644); err != nil {
		t.Fatal(err)
	}
	wb, err := xlsxread.Open(p)
	if err != nil {
		t.Fatal(err)
	}
	defer wb.Close()
	if got := wb.SheetNames(); !reflect.DeepEqual(got, []string{"Data", "Empty", "HeaderOnly"}) {
		t.Fatalf("%q", got)
	}
	got := map[string][][]string{}
	for _, name := range wb.SheetNames() {
		var rows [][]string
		if err := wb.Rows(name, func(_ int, cells []cell.Cell) error {
			r := make([]string, len(cells))
			for i, c := range cells {
				r[i] = c.PyStr()
			}
			rows = append(rows, r)
			return nil
		}); err != nil {
			t.Fatal(err)
		}
		got[name] = rows
	}
	want := map[string][][]string{
		// a formula, an error code, "" and None all read back as nothing; text over 32,767
		// characters is cut there, as openpyxl's check_string cuts it
		"Data": {{"a", "a", "None"}, {"1", "None", "None"}, {"None", "None", " pad "},
			{long[:32767], "True", "2024-01-15 00:00:00"}},
		"Empty":      nil,
		"HeaderOnly": {{"Unnamed: 0", "b"}},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %q", got)
	}
}
