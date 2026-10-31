package csvread

import (
	"errors"
	"fmt"
	"strings"

	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/pdframe"
)

// IsNA is a field pandas reads as NaN.
func IsNA(s string) bool { return pdframe.IsNA(s) }

// ErrNoColumns is pandas' EmptyDataError.
var ErrNoColumns = errors.New("No columns to parse from file")

// headerNames is TextReader._get_header for one header row: empty names become "Unnamed: i",
// then duplicates are renamed — real names first, unnamed ones last — to name.1, name.2, …,
// skipping any name already in the header.
func headerNames(fields []string) []string {
	names := append([]string(nil), fields...)
	var unnamed []int
	for i, n := range names {
		if n == "" {
			names[i] = fmt.Sprintf("Unnamed: %d", i)
			unnamed = append(unnamed, i)
		}
	}
	isUnnamed := map[int]bool{}
	for _, i := range unnamed {
		isUnnamed[i] = true
	}
	order := make([]int, 0, len(names))
	for i := range names {
		if !isUnnamed[i] {
			order = append(order, i)
		}
	}
	order = append(order, unnamed...)
	in := func(s string) bool {
		for _, n := range names {
			if n == s {
				return true
			}
		}
		return false
	}
	counts := map[string]int{}
	for _, i := range order {
		col := names[i]
		old := col
		cur := counts[col]
		if cur > 0 {
			for cur > 0 {
				counts[old] = cur + 1
				col = fmt.Sprintf("%s.%d", old, cur)
				if in(col) {
					cur++
				} else {
					cur = counts[col]
				}
			}
		}
		names[i] = col
		counts[col] = cur + 1
	}
	return names
}

// Read is pd.read_csv(io.StringIO(text), delimiter=delim, dtype=str) followed by
// _sanitize_column_names and to_dict(orient="records"): the column names, and every row as
// strings or None (NaN). Columns that sanitize to one name keep the first position and the
// last value, as a dict built from duplicate keys does.
func Read(text string, delim byte) ([]string, [][]cell.Cell, error) {
	text = strings.TrimPrefix(text, "\ufeff")
	recs, quoteRow, terr := tokenize(text, delim)
	if len(recs) == 0 {
		if terr != nil {
			return nil, nil, tokenizingError("EOF inside string starting at row %d", quoteRow)
		}
		return nil, nil, ErrNoColumns
	}
	header := headerNames(recs[0].fields)
	leading := 0
	if len(recs) > 1 && len(recs[1].fields) > len(header) {
		leading = len(recs[1].fields) - len(header)
	}
	expected := len(header) + leading
	for i := 2; i < len(recs); i++ {
		if r := recs[i]; len(r.fields) > expected {
			return nil, nil, tokenizingError("Expected %d fields in line %d, saw %d", expected, r.line, len(r.fields))
		}
	}
	if terr != nil {
		return nil, nil, tokenizingError("EOF inside string starting at row %d", quoteRow)
	}
	data := recs[1:]
	if len(data) == 0 {
		return header, [][]cell.Cell{}, nil // an empty frame is not sanitised
	}
	cols, target := pdframe.Collapse(pdframe.Sanitize(header))
	rows := make([][]cell.Cell, len(data))
	for ri, r := range data {
		row := make([]cell.Cell, len(cols))
		for i := range header {
			v := cell.None
			if j := leading + i; j < len(r.fields) && !pdframe.IsNA(r.fields[j]) {
				v = cell.Of(r.fields[j])
			}
			row[target[i]] = v // a later duplicate overwrites the earlier one
		}
		rows[ri] = row
	}
	return cols, rows, nil
}
