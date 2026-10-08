package arrowtab

import (
	"path/filepath"
	"testing"

	"hoistra/engine/internal/cell"
)

func build(t *testing.T, name string, cols []string, rows ...[]cell.Cell) *Table {
	t.Helper()
	b := NewBuilder(name, cols)
	for _, r := range rows {
		b.Append(r)
	}
	return b.Build()
}

func same(t *testing.T, want, got []*Table) {
	t.Helper()
	if len(want) != len(got) {
		t.Fatalf("tables: want %d got %d", len(want), len(got))
	}
	for i := range want {
		w, g := want[i], got[i]
		if w.Name != g.Name || w.Rows != g.Rows || len(w.Columns) != len(g.Columns) {
			t.Fatalf("table %d: want %s/%d/%v got %s/%d/%v", i, w.Name, w.Rows, w.Columns, g.Name, g.Rows, g.Columns)
		}
		for c := range w.Columns {
			if w.Columns[c] != g.Columns[c] {
				t.Fatalf("%s column %d: %q vs %q", w.Name, c, w.Columns[c], g.Columns[c])
			}
			for r := 0; r < w.Rows; r++ {
				if w.Cell(c, r) != g.Cell(c, r) {
					t.Fatalf("%s[%d].%s: want %+v got %+v", w.Name, r, w.Columns[c], w.Cell(c, r), g.Cell(c, r))
				}
			}
		}
	}
}

func TestRoundTripKeepsCellsNamesAndOrder(t *testing.T) {
	want := []*Table{
		build(t, "Work Orders", []string{"wo", "qty", "note"},
			[]cell.Cell{cell.Of("W1"), cell.OfInt(0), cell.None},
			[]cell.Cell{cell.Of("W2"), cell.Of("5"), cell.Of("")}),
		build(t, "Empty", nil),
		build(t, "Ünïcode ✓", []string{"naïve"}, []cell.Cell{cell.Of("é")}),
	}
	dir := t.TempDir()
	if err := WriteDir(dir, want); err != nil {
		t.Fatal(err)
	}
	got, err := ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	same(t, want, got)
}

func TestManyBatchesReadBackInOrder(t *testing.T) {
	b := NewBuilder("big", []string{"n"})
	for i := 0; i < 200_000; i++ {
		b.Append([]cell.Cell{cell.Of(string(rune('a' + i%26)))})
	}
	tab := b.Build()
	dir := t.TempDir()
	if err := WriteDir(dir, []*Table{tab}); err != nil {
		t.Fatal(err)
	}
	got, err := ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	same(t, []*Table{tab}, got)
}

func TestAColumnCannotMixTheFilledZeroWithANull(t *testing.T) {
	tab := build(t, "T", []string{"a"}, []cell.Cell{cell.OfInt(0)}, []cell.Cell{cell.None})
	if err := WriteDir(t.TempDir(), []*Table{tab}); err == nil {
		t.Fatal("want an error")
	}
	tab = build(t, "T", []string{"a"}, []cell.Cell{cell.OfInt(5)})
	if err := WriteDir(t.TempDir(), []*Table{tab}); err == nil {
		t.Fatal("an int other than the filled 0 has no encoding: want an error")
	}
}

func TestReadsAPythonWrittenDir(t *testing.T) {
	got, err := ReadDir(filepath.Join("testdata", "py_bridge"))
	if err != nil {
		t.Fatal(err)
	}
	want := []*Table{
		build(t, "Work Orders", []string{"wo", "qty", "note"},
			[]cell.Cell{cell.Of("W1"), cell.OfInt(0), cell.None},
			[]cell.Cell{cell.Of("W2"), cell.Of("5"), cell.Of("")}),
		build(t, "Empty", nil),
		build(t, "Ünïcode ✓", []string{"naïve"}, []cell.Cell{cell.Of("é")}),
	}
	same(t, want, got)
}
