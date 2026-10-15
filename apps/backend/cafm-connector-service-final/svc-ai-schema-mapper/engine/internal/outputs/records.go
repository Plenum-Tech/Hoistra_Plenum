package outputs

import (
	"os"

	"hoistra/engine/internal/arrowtab"
	"hoistra/engine/internal/cell"
)

// table is one table of records: every row carries every column (preprocess and the bridge
// guarantee it), so a row is a position and a value is t.Cell(col, row).
type table struct {
	name string
	t    *arrowtab.Table
}

func (t *table) rows() int             { return t.t.Rows }
func (t *table) cols() []string        { return t.t.Columns }
func (t *table) at(c, r int) cell.Cell { return t.t.Cell(c, r) }

// routed is one destination: its source tables, rows in order (`extend`).
type routed struct {
	dest  string
	parts []*table
}

func (r *routed) rows() int {
	n := 0
	for _, p := range r.parts {
		n += p.rows()
	}
	return n
}

func readDir(dir string) ([]*table, error) {
	if dir == "" {
		return nil, nil
	}
	if _, err := os.Stat(dir); os.IsNotExist(err) {
		return nil, nil
	}
	ts, err := arrowtab.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	out := make([]*table, len(ts))
	for i, t := range ts {
		out[i] = &table{name: t.Name, t: t}
	}
	return out, nil
}

// recordsTables is `dict(cleaned_tables)` with each full table assigned by name: an existing
// name keeps its place, a new one goes at the end.
func recordsTables(cleaned, full []*table) []*table {
	out := append([]*table(nil), cleaned...)
	idx := map[string]int{}
	for i, t := range out {
		idx[t.name] = i
	}
	for _, t := range full {
		if i, ok := idx[t.name]; ok {
			out[i] = t
			continue
		}
		idx[t.name] = len(out)
		out = append(out, t)
	}
	return out
}

// routeTables is output_generator_node.routed_records.
func routeTables(records []*table, routing map[string]string) []*routed {
	var out []*routed
	idx := map[string]int{}
	for _, t := range records {
		dest := routing[t.name]
		if dest == "" {
			dest = t.name
		}
		if i, ok := idx[dest]; ok {
			out[i].parts = append(out[i].parts, t)
			continue
		}
		idx[dest] = len(out)
		out = append(out, &routed{dest: dest, parts: []*table{t}})
	}
	return out
}
