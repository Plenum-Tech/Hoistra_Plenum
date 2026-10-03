package xlsxread

import (
	"fmt"
	"strconv"
)

// pkind is a cell's Python type once python-calamine and pandas' _convert_cell have read it.
type pkind uint8

const (
	pStr   pkind = iota // str: "" for an empty cell, an error, a formula with nothing cached
	pInt                // a float with no fraction, which _convert_cell makes an int
	pFloat              // any other float
	pBool
	pStamp // pd.Timestamp (calamine's date or datetime)
	pTime  // datetime.time
	pDelta // pd.Timedelta
)

// pcell is one cell as read_excel holds it before dtype=str: its type and f"{value}". A cell
// whose conversion raises carries the error instead (lvl 1: python-calamine's to_python, lvl 2:
// pandas' _convert_cell), and text is the message.
type pcell struct {
	text string
	kind pkind
	lvl  uint8
}

// grid is a sheet's range from A1 to the last cell calamine keeps, one []pcell per row (a row may
// be shorter than width; the rest of it is "").
type grid struct {
	rows  [][]pcell
	width int
	errs  [3]*cellErr // the first conversion error of each level, in row-major order
}

type cellErr struct {
	row, col int
	msg      string
}

func (g *grid) put(row, col int, v pcell) {
	if v.lvl > 0 {
		if e := g.errs[v.lvl]; e == nil || row < e.row || (row == e.row && col < e.col) {
			g.errs[v.lvl] = &cellErr{row, col, v.text}
		}
		v = pcell{}
	}
	for len(g.rows) <= row {
		g.rows = append(g.rows, nil)
	}
	r := g.rows[row]
	if len(r) <= col {
		if cap(r) > col {
			r = r[:col+1]
		} else {
			r = append(r, make([]pcell, col+1-len(r))...)
		}
	}
	r[col] = v
	g.rows[row] = r
	if col+1 > g.width {
		g.width = col + 1
	}
}

// err is the error reading the sheet's values raises: python-calamine's before pandas'.
func (g *grid) err() error {
	for _, e := range g.errs[1:] {
		if e != nil {
			return fmt.Errorf("%s", e.msg)
		}
	}
	return nil
}

// grid reads a worksheet as calamine's read_sheet_data does: a <row r> sets the row (one without r
// follows the last), a <c r> sets the cell's place and the column count the next cell without r
// follows (but not the row), and a cell is kept when it has a value — an empty string or an
// error included; an empty <v>, or none, is no value (except a str formula's empty result).
func (wb *Workbook) grid(sheet string) (*grid, error) {
	var ref *sheetRef
	for i := range wb.sheets {
		if wb.sheets[i].name == sheet {
			ref = &wb.sheets[i]
			break
		}
	}
	if ref == nil {
		return nil, fmt.Errorf("Worksheet '%s' not found", sheet)
	}
	data, err := wb.part(ref.path)
	if err != nil {
		return nil, err
	}
	if data == nil {
		return nil, fmt.Errorf("Worksheet '%s' not found", sheet)
	}
	g := &grid{}
	sc := scanner{b: data}
	for sc.nextTag() { // to <sheetData>
		if sc.is("sheetData") {
			if sc.kind != tagStart {
				return g, nil
			}
			break
		}
	}
	if sc.err != nil {
		return nil, sc.err
	}
	rowIdx, colIdx := 0, 0
	for sc.nextTag() {
		switch {
		case sc.is("sheetData") && sc.kind == tagEnd:
			return g, nil
		case sc.is("row"):
			if sc.kind != tagEnd {
				if r, ok := sc.raw("r"); ok {
					if n, ok := rowNumber(r); ok {
						rowIdx = n
					}
				}
			}
			if sc.kind != tagStart {
				rowIdx++
				colIdx = 0
			}
		case sc.is("c") && sc.kind != tagEnd:
			row, col := rowIdx, colIdx
			if r, ok := sc.raw("r"); ok {
				if rr, cc, ok := cellRef(r); ok {
					row, col = rr, cc
					colIdx = cc
				}
			}
			t, hasT := sc.raw("t")
			s, hasS := sc.raw("s")
			v, kept, err := wb.cellValue(&sc, string(t), hasT, styleIndex(s, hasS))
			if err != nil {
				return nil, err
			}
			if kept {
				g.put(row, col, v)
			}
			colIdx++
		}
	}
	// The input ended inside <sheetData>: calamine refuses the sheet (quick-xml's error, or the
	// element it was reading).
	return nil, closed(&sc, false, "sheetData")
}

// rowNumber is calamine's get_row: digits only, 1-based in, 0-based out.
func rowNumber(b []byte) (int, bool) {
	n := 0
	for _, c := range b {
		if c < '0' || c > '9' {
			return 0, false
		}
		n = n*10 + int(c-'0')
	}
	if n > 0 {
		n--
	}
	return n, len(b) > 0
}

// cellRef parses "B12" (or "b12") into (row 11, col 1).
func cellRef(b []byte) (int, int, bool) {
	col, i := 0, 0
	for i < len(b) && (b[i] >= 'A' && b[i] <= 'Z' || b[i] >= 'a' && b[i] <= 'z') {
		ch := b[i] | 0x20
		col = col*26 + int(ch-'a') + 1
		i++
	}
	if i == 0 || i == len(b) {
		return 0, 0, false
	}
	row, ok := rowNumber(b[i:])
	if !ok || row < 0 {
		return 0, 0, false
	}
	return row, col - 1, true
}

// styleIndex is the cell's s="…" as calamine reads it: no s means the default format (-1 here),
// one it cannot parse means xf 0.
func styleIndex(s []byte, has bool) int {
	if !has {
		return -1
	}
	n, err := strconv.Atoi(string(s))
	if err != nil || n < 0 {
		return 0
	}
	return n
}

// cellValue reads a <c>'s children up to </c>: an <is> inline string (whatever t says), or a <v>
// read by its t. Formulas and anything else are skipped.
func (wb *Workbook) cellValue(sc *scanner, t string, hasT bool, xf int) (pcell, bool, error) {
	if sc.kind == tagEmpty {
		return pcell{}, false, nil
	}
	var v pcell
	kept := false
	for sc.nextTag() {
		if sc.kind == tagEnd {
			if sc.is("c") {
				break
			}
			continue
		}
		switch {
		case sc.is("is"):
			if s, ok := sc.readString("is"); ok {
				v, kept = pcell{text: s}, true
			}
		case sc.is("v"):
			text := ""
			if sc.kind == tagStart {
				text = sc.content("v", false)
			}
			if sc.err != nil {
				return pcell{}, false, sc.err
			}
			var err error
			v, kept, err = wb.readValue(text, t, hasT, xf)
			if err != nil {
				return pcell{}, false, err
			}
		default:
			sc.skip()
		}
	}
	return v, kept, nil
}

// supportedErrors are the error values calamine reads (any other fails the read).
var supportedErrors = map[string]bool{"#DIV/0!": true, "#N/A": true, "#NAME?": true, "#NULL!": true,
	"#NUM!": true, "#REF!": true, "#VALUE!": true}

// readValue is calamine's read_value for a <v> under the cell's t.
func (wb *Workbook) readValue(text, t string, hasT bool, xf int) (pcell, bool, error) {
	if text == "" && t != "str" {
		return pcell{}, false, nil
	}
	switch {
	case !hasT || t == "n":
		f, ok := rustFloat(text)
		if !ok {
			if hasT {
				return pcell{}, false, fmt.Errorf("Parse float error: invalid float literal")
			}
			return pcell{text: text}, true, nil // a number calamine cannot read stays text
		}
		switch wb.format(xf) {
		case fmtDateTime:
			return dateCell(f, wb.date1904), true, nil
		case fmtTimeDelta:
			return durationCell(f), true, nil
		}
		return floatCell(f), true, nil
	case t == "s":
		i, err := strconv.Atoi(text)
		if err != nil || i < 0 {
			i = 0
		}
		if i >= len(wb.sst) {
			return pcell{}, false, fmt.Errorf("Cell string index not found in shared strings table")
		}
		return pcell{text: wb.sst[i]}, true, nil
	case t == "str" || t == "inlineStr":
		return pcell{text: text}, true, nil
	case t == "b":
		if text != "0" {
			return pcell{text: "True", kind: pBool}, true, nil
		}
		return pcell{text: "False", kind: pBool}, true, nil
	case t == "e":
		if !supportedErrors[text] {
			return pcell{}, false, fmt.Errorf("Unsupported cell error value '%s'", text)
		}
		return pcell{}, true, nil
	case t == "d":
		return isoCell(text), true, nil
	}
	return pcell{}, false, fmt.Errorf("Unknown cell 't' attribute: %q", t)
}

// format is the number format of a cell's xf: none (no s) is a plain number, as is an xf the
// styles do not have.
func (wb *Workbook) format(xf int) cellFormat {
	if xf < 0 {
		return fmtOther
	}
	return wb.styles.format(xf)
}
