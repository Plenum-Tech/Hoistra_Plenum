package outputs

import (
	"bufio"
	"strconv"
	"strings"

	"hoistra/engine/internal/cell"
)

// colKind is the dtype pandas infers for a column of pd.DataFrame(records).
type colKind uint8

const (
	kObject  colKind = iota // any string, or nothing but None/missing
	kInt64                  // every row an int
	kFloat64                // ints and None/missing: pandas makes them floats
)

type csvCol struct {
	name string
	kind colKind
	idx  []int // per part: the column's index there, -1 when the part lacks it (NaN)
}

func csvColumns(r *routed) []csvCol {
	var cols []csvCol
	pos := map[string]int{}
	for _, p := range r.parts {
		if p.rows() == 0 {
			continue // an empty table adds no record, so no key
		}
		for _, c := range p.cols() {
			if _, ok := pos[c]; !ok {
				pos[c] = len(cols)
				cols = append(cols, csvCol{name: c})
			}
		}
	}
	for i := range cols {
		c := &cols[i]
		c.idx = make([]int, len(r.parts))
		hasStr, hasInt, hasNull := false, false, false
		for pi, p := range r.parts {
			c.idx[pi] = p.t.ColumnIndex(c.name)
			if p.rows() == 0 {
				continue
			}
			if c.idx[pi] < 0 {
				hasNull = true
				continue
			}
			for row := 0; row < p.rows(); row++ {
				switch p.at(c.idx[pi], row).K {
				case cell.Str:
					hasStr = true
				case cell.Int, cell.Bool:
					hasInt = true
				default:
					hasNull = true
				}
			}
		}
		switch {
		case hasStr || !hasInt:
			c.kind = kObject
		case hasNull:
			c.kind = kFloat64
		default:
			c.kind = kInt64
		}
	}
	return cols
}

// csvField is one field as Python's csv writer (QUOTE_MINIMAL, '"', lineterminator "\n") writes it.
func csvField(w *bufio.Writer, s string, only bool) {
	if s == "" {
		if only {
			w.WriteString(`""`)
		}
		return
	}
	if !strings.ContainsAny(s, ",\"\n\r") {
		w.WriteString(s)
		return
	}
	w.WriteByte('"')
	w.WriteString(strings.ReplaceAll(s, `"`, `""`))
	w.WriteByte('"')
}

func formatCSV(c cell.Cell, kind colKind) string {
	switch c.K {
	case cell.Null:
		return ""
	case cell.Int, cell.Bool:
		if kind == kFloat64 {
			return strconv.FormatInt(c.I, 10) + ".0"
		}
		return strconv.FormatInt(c.I, 10)
	}
	return c.S
}

// writeCSV is pd.DataFrame(records).to_csv(index=False) for one destination's records.
func writeCSV(w *bufio.Writer, r *routed) {
	cols := csvColumns(r)
	only := len(cols) == 1
	for i, c := range cols {
		if i > 0 {
			w.WriteByte(',')
		}
		csvField(w, c.name, only)
	}
	w.WriteByte('\n')
	for pi, p := range r.parts {
		for row := 0; row < p.rows(); row++ {
			for i, c := range cols {
				if i > 0 {
					w.WriteByte(',')
				}
				v := ""
				if c.idx[pi] >= 0 {
					v = formatCSV(p.at(c.idx[pi], row), c.kind)
				}
				csvField(w, v, only)
			}
			w.WriteByte('\n')
		}
	}
}
