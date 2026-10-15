package ingest

import (
	"sort"

	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/protocol"
	"hoistra/engine/internal/pystr"
)

// nullOrNaN is nan_scan._is_null_or_nan: None, or a string that strips to nothing.
func nullOrNaN(c cell.Cell) bool {
	return c.IsNone() || (c.K == cell.Str && pystr.Strip(c.S) == "")
}

// scanNaN is nan_scan.scan_nan_values over the parsed tables (before the duplicate merge).
func scanNaN(tables []*Table, sampleLimit int) *protocol.Object {
	report := &protocol.Object{}
	totalCells, totalRowsWith, totalRows, colsWith := 0, 0, 0, 0
	for _, t := range tables {
		var order []string
		counts := map[string]int{}
		rowsWith, cells := 0, 0
		samples := []*protocol.Object{}
		for ri, row := range t.Rows {
			var nanCols []string
			for ci, c := range row {
				if nullOrNaN(c) {
					nanCols = append(nanCols, t.Columns[ci])
				}
			}
			if len(nanCols) == 0 {
				continue
			}
			rowsWith++
			cells += len(nanCols)
			for _, c := range nanCols {
				if _, seen := counts[c]; !seen {
					order = append(order, c)
				}
				counts[c]++
			}
			if len(samples) < sampleLimit {
				s := &protocol.Object{}
				s.Set("row_index", ri).Set("nan_columns", nanCols).Set("values", record(t.Columns, row, nil))
				samples = append(samples, s)
			}
		}
		totalRows += len(t.Rows)
		entry := &protocol.Object{}
		entry.Set("row_count", len(t.Rows)).Set("rows_with_nan", rowsWith).Set("nan_cells", cells)
		columns := &protocol.Object{}
		if cells > 0 {
			totalCells += cells
			totalRowsWith += rowsWith
			colsWith += len(counts)
			sort.SliceStable(order, func(i, j int) bool { return counts[order[i]] > counts[order[j]] })
			for _, c := range order {
				columns.Set(c, counts[c])
			}
		} else {
			samples = []*protocol.Object{}
		}
		entry.Set("columns", columns).Set("sample_rows", samples)
		report.Set(t.Name, entry)
	}
	out := &protocol.Object{}
	out.Set("total_nan_cells", totalCells).Set("total_rows_with_nan", totalRowsWith).Set("total_rows", totalRows).
		Set("columns_with_nan", colsWith).Set("tables", report)
	return out
}
