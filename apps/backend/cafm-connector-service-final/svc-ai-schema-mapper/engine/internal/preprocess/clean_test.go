package preprocess

import (
	"reflect"
	"testing"

	"hoistra/engine/internal/cell"
)

func s(v string) cell.Cell { return cell.Of(v) }

var none = cell.None

func texts(rows [][]cell.Cell) [][]string {
	out := make([][]string, len(rows))
	for i, r := range rows {
		out[i] = make([]string, len(r))
		for j, c := range r {
			switch {
			case c.IsNone():
				out[i][j] = "<None>"
			case c.K == cell.Int:
				out[i][j] = "<0>"
			default:
				out[i][j] = c.S
			}
		}
	}
	return out
}

func TestATableIsCleanedInPreprocessOrder(t *testing.T) {
	cols := []string{"code", "gone", "qty", "note", "when_date", "tag"}
	rows := [][]cell.Cell{
		{s("A"), none, s("5"), none, s("2025-01-31"), s("t1")},
		{s("A"), none, s("5"), none, s("2025-01-31"), s("t1")}, // a duplicate, None equal to None
		{s("B"), none, none, s("x"), none, s("t2")},
		{s("C"), none, s("1.5"), none, s("2025-02-01"), s("t3")},
	}
	out := cleanTable("T", cols, rows, map[string]string{"code": "asset_code", "tag": "asset_code", "gone": "never"},
		map[string]bool{"note": true})
	rep := out.report
	if rep.RowsIn != 4 || rep.RowsOut != 3 || rep.DedupDropped != 1 || !reflect.DeepEqual(rep.NullColumnsDropped, []string{"gone"}) ||
		!reflect.DeepEqual(rep.DateColumns, []string{"when_date"}) || !reflect.DeepEqual(rep.Skipped, []string{"note"}) {
		t.Fatalf("%+v", rep)
	}
	// the rename collision keeps asset_code's first place and the later column's values; the
	// numeric fill is the int 0; the date is ISO, its None stays None
	if !reflect.DeepEqual(out.table.cols, []string{"asset_code", "qty", "when_date"}) {
		t.Fatalf("%q", out.table.cols)
	}
	want := [][]string{{"t1", "5", "2025-01-31T00:00:00"}, {"t2", "<0>", "<None>"}, {"t3", "1.5", "2025-02-01T00:00:00"}}
	if got := texts(out.table.rows); !reflect.DeepEqual(got, want) {
		t.Fatalf("%q", got)
	}
}

func TestASkipFieldIsMatchedAfterTheRename(t *testing.T) {
	out := cleanTable("T", []string{"a", "b"}, [][]cell.Cell{{s("1"), s("2")}}, map[string]string{"a": "x"},
		map[string]bool{"x": true, "a": true})
	if !reflect.DeepEqual(out.table.cols, []string{"b"}) || !reflect.DeepEqual(out.report.Skipped, []string{"x"}) {
		t.Fatalf("%q %+v", out.table.cols, out.report)
	}
}

func TestADateColumnPandasMustParseIsLeftToPython(t *testing.T) {
	cols := []string{"start_date", "end_date", "due_by"}
	rows := [][]cell.Cell{{s("10/07/26"), s("10/07/26"), s("yes")}, {s("13/07/26"), s("11/07/26"), s("no")}}
	out := cleanTable("T", cols, rows, map[string]string{"start_date": "end_date"}, nil)
	// two-digit years have no guess (dateutil parses each value); the first column is renamed
	// onto the second, which then wins — so only the second's result reaches the table
	want := []Deferred{{Table: "T", Column: "start_date", RenamedTo: ""}, {Table: "T", Column: "end_date", RenamedTo: "end_date"}}
	if !reflect.DeepEqual(out.deferred, want) {
		t.Fatalf("%+v", out.deferred)
	}
	if !reflect.DeepEqual(out.deferredValues.cols, []string{"start_date", "end_date"}) ||
		!reflect.DeepEqual(texts(out.deferredValues.rows), [][]string{{"10/07/26", "10/07/26"}, {"13/07/26", "11/07/26"}}) {
		t.Fatalf("%+v", out.deferredValues)
	}
	// "yes"/"no" carry no digit: dateutil cannot make a date of them, so nothing is left to Python
	if len(out.report.DateColumns) != 0 {
		t.Fatalf("%+v", out.report)
	}
}

func TestWhatTheDateCoercionLeavesToPython(t *testing.T) {
	str := func(v ...string) []dval {
		out := make([]dval, len(v))
		for i, x := range v {
			out[i] = dval{kind: vStr, s: x}
		}
		return out
	}
	cases := []struct {
		name     string
		vals     []dval
		coerced  bool
		deferred bool
	}{
		{"now", str("2025-01-01", "now"), false, true},
		{"a time zone on the ISO path", str("2025-01-01T10:00:00Z", "2025-01-02T10:00:00Z"), false, true},
		{"the 0 fill first", []dval{{kind: vInt0}, {kind: vStr, s: "5"}, {kind: vStr, s: "7"}}, false, true},
		{"names", str("ann", "bob", "cy"), false, false},
		{"ISO", str("2025-01-01 10:00", "2025-01-02 11:00", "2025-01-03"), true, false},
		{"80 % by the guess", str("13/01/2025 10:00", "14/01/2025 10:00", "15/01/2025 10:00", "16/01/2025 10:00", "x"), true, false},
		{"under 80 %", str("13/01/2025 10:00", "14/01/2025 10:00", "15/01/2025 10:00", "x", "y"), false, false},
	}
	for _, c := range cases {
		_, coerced, deferred := coerceDates(c.vals)
		if coerced != c.coerced || deferred != c.deferred {
			t.Errorf("%s: coerced %v deferred %v", c.name, coerced, deferred)
		}
	}
}
