// Package arrowtab reads and writes the engine's data sets: a manifest.json plus one Arrow IPC
// file per table, the exact format src/engine/store.py writes and reads (the "bridge"). Every
// column is nullable utf8; a column whose field metadata says hoist.null_fill=int0 stores the
// integer 0 that preprocess's numeric null fill leaves as a null.
package arrowtab

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"

	"github.com/apache/arrow-go/v18/arrow"
	"github.com/apache/arrow-go/v18/arrow/array"
	"github.com/apache/arrow-go/v18/arrow/ipc"
	"github.com/apache/arrow-go/v18/arrow/memory"

	"hoistra/engine/internal/cell"
)

const (
	batchRows   = 65536
	nullFillKey = "hoist.null_fill"
	nullFillInt = "int0"
)

// Table is one table of a data set, held column by column.
type Table struct {
	Name    string
	Columns []string
	Rows    int

	chunks [][]*array.String // per column, the record-batch chunks in order
	starts []int             // first row of each chunk (shared by every column)
	int0   []bool            // per column: a null means the filled integer 0
	byName map[string]int

	pending    [][]cell.Cell // rows a Builder could not encode, kept so WriteDir can say why
	pendingErr error

	manifestCols []string // when set, the columns the manifest lists (the file keeps them all)
}

// ListOnly makes the manifest list only these columns: a reader then never sees the others,
// which stay in the file.
func (t *Table) ListOnly(cols []string) { t.manifestCols = append([]string{}, cols...) }

// FromRows encodes rows (not copied) into a table.
func FromRows(name string, columns []string, rows [][]cell.Cell) (*Table, error) {
	return encode(name, append([]string(nil), columns...), rows)
}

// ColumnIndex is the position of a column, or -1.
func (t *Table) ColumnIndex(name string) int {
	if i, ok := t.byName[name]; ok {
		return i
	}
	return -1
}

// Cell is the value at (column, row).
func (t *Table) Cell(col, row int) cell.Cell {
	k := sort.Search(len(t.starts), func(i int) bool { return t.starts[i] > row }) - 1
	arr := t.chunks[col][k]
	i := row - t.starts[k]
	if arr.IsNull(i) {
		if t.int0[col] {
			return cell.OfInt(0)
		}
		return cell.None
	}
	return cell.Of(arr.Value(i))
}

func newTable(name string, cols []string) *Table {
	t := &Table{Name: name, Columns: cols, byName: make(map[string]int, len(cols))}
	for i, c := range cols {
		if _, dup := t.byName[c]; !dup {
			t.byName[c] = i
		}
	}
	t.chunks = make([][]*array.String, len(cols))
	t.int0 = make([]bool, len(cols))
	return t
}

// Builder accumulates a table row by row.
type Builder struct {
	name string
	cols []string
	rows [][]cell.Cell
}

func NewBuilder(name string, columns []string) *Builder {
	return &Builder{name: name, cols: append([]string(nil), columns...)}
}

// Append adds one row; the slice is copied.
func (b *Builder) Append(row []cell.Cell) { b.rows = append(b.rows, append([]cell.Cell(nil), row...)) }

// Build encodes the rows into Arrow arrays in batches.
func (b *Builder) Build() *Table {
	t, err := encode(b.name, b.cols, b.rows)
	if err != nil {
		// Builders made by the engine only hold encodable cells; WriteDir reports errors for others.
		t = newTable(b.name, b.cols)
		t.Rows = len(b.rows)
		t.pending = b.rows
		t.pendingErr = err
	}
	return t
}

func encode(name string, cols []string, rows [][]cell.Cell) (*Table, error) {
	t := newTable(name, cols)
	t.Rows = len(rows)
	for c := range cols {
		hasInt0, hasNull := false, false
		for _, r := range rows {
			v := r[c]
			switch v.K {
			case cell.Null:
				hasNull = true
			case cell.Int:
				if v.I != 0 {
					return nil, fmt.Errorf("table %q column %q: the integer %d has no encoding (only the filled 0)", name, cols[c], v.I)
				}
				hasInt0 = true
			}
		}
		if hasInt0 && hasNull {
			return nil, fmt.Errorf("table %q column %q: both a null and a filled 0", name, cols[c])
		}
		t.int0[c] = hasInt0
	}
	mem := memory.DefaultAllocator
	for start := 0; start < len(rows); start += batchRows {
		end := start + batchRows
		if end > len(rows) {
			end = len(rows)
		}
		t.starts = append(t.starts, start)
		for c := range cols {
			sb := array.NewStringBuilder(mem)
			sb.Reserve(end - start)
			for _, r := range rows[start:end] {
				if r[c].K == cell.Str {
					sb.Append(r[c].S)
				} else {
					sb.AppendNull()
				}
			}
			t.chunks[c] = append(t.chunks[c], sb.NewStringArray())
			sb.Release()
		}
	}
	return t, nil
}

type manifestEntry struct {
	Name    string   `json:"name"`
	File    *string  `json:"file"`
	Rows    int      `json:"rows"`
	Columns []string `json:"columns"`
}

type manifest struct {
	Version int             `json:"version"`
	Tables  []manifestEntry `json:"tables"`
}

func schemaOf(t *Table) *arrow.Schema {
	fields := make([]arrow.Field, len(t.Columns))
	for i, c := range t.Columns {
		f := arrow.Field{Name: c, Type: arrow.BinaryTypes.String, Nullable: true}
		if t.int0[i] {
			f.Metadata = arrow.NewMetadata([]string{nullFillKey}, []string{nullFillInt})
		}
		fields[i] = f
	}
	return arrow.NewSchema(fields, nil)
}

// WriteDir writes tables as a data set (manifest.json + t0001.arrow …), replacing any earlier one.
func WriteDir(dir string, tables []*Table) error {
	for _, t := range tables {
		if t.pendingErr != nil {
			return t.pendingErr
		}
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	old, _ := filepath.Glob(filepath.Join(dir, "t*.arrow"))
	for _, f := range old {
		_ = os.Remove(f)
	}
	m := manifest{Version: 1, Tables: make([]manifestEntry, 0, len(tables))}
	for i, t := range tables {
		e := manifestEntry{Name: t.Name, Rows: t.Rows, Columns: append([]string{}, t.Columns...)}
		if t.manifestCols != nil {
			e.Columns = append([]string{}, t.manifestCols...)
		}
		if t.Rows > 0 {
			fname := fmt.Sprintf("t%04d.arrow", i+1)
			if err := writeFile(filepath.Join(dir, fname), t); err != nil {
				return fmt.Errorf("table %q: %w", t.Name, err)
			}
			e.File = &fname
		} else {
			e.Columns = []string{}
		}
		m.Tables = append(m.Tables, e)
	}
	b, err := json.Marshal(m)
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(dir, "manifest.json"), b, 0o644)
}

func writeFile(path string, t *Table) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	schema := schemaOf(t)
	w, err := ipc.NewFileWriter(f, ipc.WithSchema(schema), ipc.WithAllocator(memory.DefaultAllocator), ipc.WithZstd())
	if err != nil {
		return err
	}
	for k, start := range t.starts {
		end := t.Rows
		if k+1 < len(t.starts) {
			end = t.starts[k+1]
		}
		cols := make([]arrow.Array, len(t.Columns))
		for c := range t.Columns {
			cols[c] = t.chunks[c][k]
		}
		rec := array.NewRecord(schema, cols, int64(end-start))
		err := w.Write(rec)
		rec.Release()
		if err != nil {
			return err
		}
	}
	return w.Close()
}

// ReadDir reads a data set written by WriteDir or by src/engine/store.py.
func ReadDir(dir string) ([]*Table, error) {
	raw, err := os.ReadFile(filepath.Join(dir, "manifest.json"))
	if err != nil {
		return nil, err
	}
	var m manifest
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, fmt.Errorf("manifest: %w", err)
	}
	out := make([]*Table, 0, len(m.Tables))
	for _, e := range m.Tables {
		if e.File == nil || e.Rows == 0 {
			t := newTable(e.Name, append([]string{}, e.Columns...))
			out = append(out, t)
			continue
		}
		t, err := readFile(filepath.Join(dir, *e.File), e.Name, e.Columns)
		if err != nil {
			return nil, fmt.Errorf("table %q: %w", e.Name, err)
		}
		out = append(out, t)
	}
	return out, nil
}

func readFile(path, name string, keep []string) (*Table, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	r, err := ipc.NewFileReader(f, ipc.WithAllocator(memory.DefaultAllocator))
	if err != nil {
		return nil, err
	}
	defer r.Close()
	schema := r.Schema()
	idx := make([]int, len(keep))
	for i, c := range keep {
		found := schema.FieldIndices(c)
		if len(found) == 0 {
			return nil, fmt.Errorf("column %q is not in the file", c)
		}
		idx[i] = found[0]
	}
	t := newTable(name, append([]string{}, keep...))
	for i, fi := range idx {
		md := schema.Field(fi).Metadata
		if k := md.FindKey(nullFillKey); k >= 0 && md.Values()[k] == nullFillInt {
			t.int0[i] = true
		}
	}
	rows := 0
	for k := 0; k < r.NumRecords(); k++ {
		rec, err := r.Record(k)
		if err != nil {
			return nil, err
		}
		t.starts = append(t.starts, rows)
		for i, fi := range idx {
			col, ok := rec.Column(fi).(*array.String)
			if !ok {
				return nil, fmt.Errorf("column %q is %s, not utf8", keep[i], rec.Column(fi).DataType())
			}
			col.Retain()
			t.chunks[i] = append(t.chunks[i], col)
		}
		rows += int(rec.NumRows())
	}
	t.Rows = rows
	return t, nil
}
