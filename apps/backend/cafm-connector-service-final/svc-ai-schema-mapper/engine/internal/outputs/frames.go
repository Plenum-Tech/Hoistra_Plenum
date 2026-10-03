package outputs

import (
	"io"

	"hoistra/engine/internal/cell"
)

// Sheet is one frame written as pandas' df.to_excel(writer, sheet_name=Title, index=False) writes
// it through openpyxl: a header row of Columns (duplicates as they are), then Rows.
type Sheet struct {
	Title   string
	Columns []string
	Rows    [][]cell.Cell
}

func (s *Sheet) cols() []string { return s.Columns }
func (s *Sheet) rows() int      { return len(s.Rows) }
func (s *Sheet) at(c, r int) cell.Cell {
	if row := s.Rows[r]; c < len(row) {
		return row[c]
	}
	return cell.None
}

// WriteFrames writes the sheets as one workbook, in order, with openpyxl's cell typing (a string
// starting "=" is a formula, an error code an error, text cut at 32,767 characters). A value or
// title openpyxl or lxml refuses comes back as their message and nothing is written; titles are
// taken as given (avoid_duplicate_name still applies).
func WriteFrames(out io.Writer, sheets []Sheet, created string) (string, error) {
	var plan []xlsxSheet
	var names []string
	for i := range sheets {
		s := &sheets[i]
		title := firstRunes(s.Title, 31)
		if title == "" {
			title = "Sheet"
		}
		if m := invalidTitle.FindString(title); m != "" {
			return "Invalid character " + m + " found in sheet title", nil
		}
		title = avoidDuplicateName(names, title)
		names = append(names, title)
		plan = append(plan, xlsxSheet{title: title, t: s})
		if why := refusal(s); why != "" {
			return why, nil
		}
	}
	if len(plan) == 0 {
		return "At least one sheet must be visible", nil
	}
	if why := xmlRefusal(plan); why != "" {
		return why, nil
	}
	if created == "" {
		created = "2026-01-01T00:00:00Z"
	}
	return "", writeWorkbook(out, plan, created)
}
