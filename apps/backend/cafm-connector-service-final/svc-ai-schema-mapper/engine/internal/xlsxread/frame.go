package xlsxread

import (
	"fmt"

	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/pdframe"
)

// Rows calls fn for every row of the sheet's range from A1 (pandas passes skip_empty_area=False),
// each as wide as the widest, a cell as its dtype=str text and None where pandas reads NaN.
func (wb *Workbook) Rows(sheet string, fn func(rowIdx int, cells []cell.Cell) error) error {
	g, err := wb.grid(sheet)
	if err != nil {
		return err
	}
	if err := g.err(); err != nil {
		return err
	}
	for i, r := range g.rows {
		out := make([]cell.Cell, g.width)
		for j := range out {
			var pc pcell
			if j < len(r) {
				pc = r[j]
			}
			if pc.kind == pStr && pdframe.IsNA(pc.text) {
				out[j] = cell.None
			} else {
				out[j] = cell.Of(pc.text)
			}
		}
		if err := fn(i, out); err != nil {
			return err
		}
	}
	return nil
}

// Sheet is what ingest's ExcelWorkbook makes of a sheet: header_row (the first of the top 10 rows
// with at least max(2, widest/2) values, else row 0), then read(sheet, header=h, dtype=str) and
// _sanitize_column_names — the column names (any duplicates the sanitiser leaves stay) and every
// row after the header, None for NaN. Blank rows stay, as read_excel keeps them; a sheet with a
// header and no rows keeps pandas' own names (the sanitiser leaves an empty frame alone).
func Sheet(wb *Workbook, sheet string) ([]string, [][]cell.Cell, error) {
	g, err := wb.grid(sheet)
	if err != nil {
		return nil, nil, err
	}
	if err := g.err(); err != nil {
		return nil, nil, err
	}
	if len(g.rows) == 0 {
		return []string{}, [][]cell.Cell{}, nil
	}
	h := g.headerRow()
	names := g.columnNames(h)
	body := g.rows[h+1:]
	if len(body) == 0 {
		return names, [][]cell.Cell{}, nil
	}
	memo := make([]memo, g.width)
	rows := make([][]cell.Cell, len(body))
	for i, r := range body {
		out := make([]cell.Cell, g.width)
		for j := range out {
			var pc pcell
			if j < len(r) {
				pc = r[j]
			}
			out[j] = memo[j].value(pc)
		}
		rows[i] = out
		body[i] = nil // the frame row replaces the sheet row
	}
	return pdframe.Sanitize(names), rows, nil
}

// Frame is Sheet then to_dict(orient="records"): the record keys, and every row with columns that
// share a name collapsed (first place, last value).
func Frame(wb *Workbook, sheet string) ([]string, [][]cell.Cell, error) {
	names, rows, err := Sheet(wb, sheet)
	if err != nil || len(rows) == 0 {
		return names, rows, err
	}
	cols, target := pdframe.Collapse(names)
	if len(cols) == len(names) {
		return cols, rows, nil
	}
	for i, r := range rows {
		out := make([]cell.Cell, len(cols))
		for j, v := range r {
			out[target[j]] = v
		}
		rows[i] = out
	}
	return cols, rows, nil
}

func isNA(pc pcell) bool { return pc.kind == pStr && pdframe.IsNA(pc.text) }

// headerRow is excel_parser._detect_header_row over header=None, nrows=10.
func (g *grid) headerRow() int {
	n := min(len(g.rows), 10)
	counts := make([]int, n)
	widest := 0
	for i := 0; i < n; i++ {
		for _, pc := range g.rows[i] {
			if !isNA(pc) {
				counts[i]++
			}
		}
		widest = max(widest, counts[i])
	}
	if widest <= 1 {
		return 0
	}
	threshold := max(2, widest/2)
	for i, c := range counts {
		if c >= threshold {
			return i
		}
	}
	return 0
}

// key is a header value as a dict key: Python's equality (True == 1, False == 0; a str is never
// equal to a number).
func key(pc pcell) string {
	switch pc.kind {
	case pBool:
		if pc.text == "True" {
			return "i:1"
		}
		return "i:0"
	case pInt:
		return "i:" + pc.text
	case pStr:
		return "s:" + pc.text
	}
	return fmt.Sprintf("%d:%s", pc.kind, pc.text)
}

// columnNames is the python parser's _infer_columns for header row h: an empty cell is
// "Unnamed: i"; duplicates (by Python equality) become name.1, name.2, … — real names first,
// unnamed ones last — skipping any name a string column already has.
func (g *grid) columnNames(h int) []string {
	type label struct {
		key, text string
		str       bool
	}
	cur := make([]label, g.width)
	var unnamed []int
	r := g.rows[h]
	for i := range cur {
		var pc pcell
		if i < len(r) {
			pc = r[i]
		}
		switch {
		case pc.kind == pStr && pc.text == "":
			n := fmt.Sprintf("Unnamed: %d", i)
			cur[i] = label{"s:" + n, n, true}
			unnamed = append(unnamed, i)
		default:
			cur[i] = label{key(pc), pc.text, pc.kind == pStr}
		}
	}
	isUnnamed := make(map[int]bool, len(unnamed))
	for _, i := range unnamed {
		isUnnamed[i] = true
	}
	order := make([]int, 0, len(cur))
	for i := range cur {
		if !isUnnamed[i] {
			order = append(order, i)
		}
	}
	order = append(order, unnamed...)
	inColumns := func(s string) bool {
		for _, l := range cur {
			if l.str && l.text == s {
				return true
			}
		}
		return false
	}
	counts := map[string]int{}
	for _, i := range order {
		col := cur[i]
		old := col
		n := counts[col.key]
		for n > 0 {
			counts[old.key] = n + 1
			t := fmt.Sprintf("%s.%d", old.text, n)
			col = label{"s:" + t, t, true}
			if inColumns(t) {
				n++
			} else {
				n = counts[col.key]
			}
		}
		cur[i] = col
		counts[col.key] = n + 1
	}
	names := make([]string, len(cur))
	for i, l := range cur {
		names[i] = l.text
	}
	return names
}

// memo is pandas' sanitize_objects memo for one column: a value equal to one seen before is
// replaced by that first one — so a 1 after a True reads "True", a False after a 0 reads "0".
type memo struct{ one, zero string }

func (m *memo) value(pc pcell) cell.Cell {
	switch pc.kind {
	case pStr:
		if pdframe.IsNA(pc.text) {
			return cell.None
		}
	case pInt, pBool:
		switch pc.text {
		case "1", "True":
			if m.one == "" {
				m.one = pc.text
			}
			return cell.Of(m.one)
		case "0", "False":
			if m.zero == "" {
				m.zero = pc.text
			}
			return cell.Of(m.zero)
		}
	}
	return cell.Of(pc.text)
}
