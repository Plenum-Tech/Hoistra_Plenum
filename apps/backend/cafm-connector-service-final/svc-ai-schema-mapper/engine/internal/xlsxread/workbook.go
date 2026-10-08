// Package xlsxread reads .xlsx/.xlsm workbooks as ingest reads them: python-calamine's cell values
// (pandas' calamine engine, to_python(skip_empty_area=False)), pandas' read_excel(dtype=str) frame,
// and excel_parser's banner-row detection.
package xlsxread

import (
	"archive/zip"
	"fmt"
	"io"
	"strings"
)

// Workbook is one opened workbook: its sheets in workbook order, the date system, the shared
// strings and the cell formats. calamine reads the parts at fixed paths (case-insensitively).
type Workbook struct {
	zr       *zip.ReadCloser
	names    []string // zip entry names, in archive order
	files    map[string]*zip.File
	sheets   []sheetRef
	date1904 bool
	sst      []string
	styles   *styles
}

type sheetRef struct {
	name      string
	path      string
	worksheet bool // not a chart or dialog sheet
}

// Open reads the workbook's structure (not its sheets).
func Open(p string) (*Workbook, error) {
	zr, err := zip.OpenReader(p)
	if err != nil {
		return nil, fmt.Errorf("Cannot detect file format: %v", err)
	}
	wb := &Workbook{zr: zr, files: map[string]*zip.File{}}
	for _, f := range zr.File {
		if _, dup := wb.files[f.Name]; !dup {
			wb.names = append(wb.names, f.Name)
			wb.files[f.Name] = f
		}
	}
	if err := wb.load(); err != nil {
		zr.Close()
		return nil, err
	}
	return wb, nil
}

func (wb *Workbook) Close() error { return wb.zr.Close() }

// part is a zip entry by name, matched case-insensitively as calamine's xml_reader does (the
// first entry that matches); nil when there is none.
func (wb *Workbook) part(name string) ([]byte, error) {
	f := wb.files[name]
	if f == nil {
		for _, n := range wb.names {
			if strings.EqualFold(n, name) {
				f = wb.files[n]
				break
			}
		}
	}
	if f == nil {
		return nil, nil
	}
	rc, err := f.Open()
	if err != nil {
		return nil, err
	}
	defer rc.Close()
	b, err := io.ReadAll(rc)
	if err != nil {
		return nil, err
	}
	return decodePart(b)
}

// closed is the error for a part whose root element (or sheetData) never ends: calamine reads
// each part to that end tag and refuses one that stops before it.
func closed(sc *scanner, seen bool, name string) error {
	if sc.err != nil {
		return sc.err
	}
	if !seen {
		return fmt.Errorf("%s", name)
	}
	return nil
}

func (wb *Workbook) load() error {
	if b, err := wb.part("xl/sharedStrings.xml"); err != nil {
		return err
	} else if b != nil {
		if wb.sst, err = parseSST(b); err != nil {
			return err
		}
	}
	if b, err := wb.part("xl/styles.xml"); err != nil {
		return err
	} else if b != nil {
		if wb.styles, err = parseStyles(b); err != nil {
			return err
		}
	}
	rb, err := wb.part("xl/_rels/workbook.xml.rels")
	if err != nil {
		return err
	}
	if rb == nil {
		return fmt.Errorf("File not found 'xl/_rels/workbook.xml.rels'")
	}
	targets := map[string]string{}
	sc := scanner{b: rb}
	relsDone := false
	for sc.nextTag() {
		if sc.kind == tagEnd && sc.is("Relationships") {
			relsDone = true
			break
		}
		if sc.kind != tagEnd && sc.is("Relationship") {
			id, _ := sc.raw("Id")
			targets[string(id)] = sc.attr("Target")
		}
	}
	if err := closed(&sc, relsDone || sc.kind == tagEmpty && sc.is("Relationships"), "Relationships"); err != nil {
		return err
	}
	data, err := wb.part("xl/workbook.xml")
	if err != nil {
		return err
	}
	if data == nil {
		return fmt.Errorf("Cannot detect file format") // python-calamine will not open it either
	}
	sc = scanner{b: data}
	wbDone := false
	for sc.nextTag() {
		if sc.kind == tagEnd {
			if sc.is("workbook") {
				wbDone = true
				break
			}
			continue
		}
		switch {
		case sc.is("sheet"):
			id, _ := sc.raw("id")
			t, ok := targets[string(id)]
			if !ok {
				return fmt.Errorf("Relationship not found")
			}
			switch {
			case strings.HasPrefix(t, "/xl/"):
				t = t[1:]
			case strings.HasPrefix(t, "xl/"):
			default:
				t = "xl/" + t
			}
			kind := ""
			if parts := strings.Split(t, "/"); len(parts) > 1 {
				kind = parts[1]
			}
			wb.sheets = append(wb.sheets, sheetRef{name: sc.attr("name"), path: t, worksheet: kind == "worksheets"})
		case sc.is("workbookPr") && !strings.Contains(string(sc.fullName()), ":"):
			v := sc.attr("date1904")
			wb.date1904 = v == "1" || v == "true"
		}
	}
	return closed(&sc, wbDone, "workbook")
}

// SheetNames are the worksheets in workbook order, hidden ones included (pandas lists only
// worksheets: chart and dialog sheets have no cells).
func (wb *Workbook) SheetNames() []string {
	var out []string
	for _, s := range wb.sheets {
		if s.worksheet {
			out = append(out, s.name)
		}
	}
	return out
}

func (wb *Workbook) Date1904() bool { return wb.date1904 }

// parseSST reads the shared strings: each <si> as calamine's read_string reads it, up to </sst>.
func parseSST(b []byte) ([]string, error) {
	var out []string
	sc := scanner{b: b}
	done := false
	for sc.nextTag() {
		if sc.is("sst") && sc.kind != tagStart {
			done = true
			break
		}
		if sc.is("si") && sc.kind != tagEnd {
			s, _ := sc.readString("si")
			out = append(out, s)
		}
	}
	return out, closed(&sc, done, "sst")
}

// readString is calamine's read_string for an <si> or <is> (the scanner is just past its start
// tag): a plain <t> is the whole string (anything after it is skipped); rich <r> runs are
// concatenated; phonetic <rPh> runs are left out; _xHHHH_ escapes are decoded. ok is false when
// there was neither a <t> nor an <r>.
func (sc *scanner) readString(closing string) (string, bool) {
	if sc.kind == tagEmpty {
		return "", false
	}
	var rich *strings.Builder
	phonetic := false
	for sc.nextTag() {
		switch {
		case sc.kind == tagEnd && sc.is(closing):
			if rich == nil {
				return "", false
			}
			return rich.String(), true
		case sc.is("r") && sc.kind != tagEnd:
			if rich == nil {
				rich = &strings.Builder{}
			}
		case sc.is("rPh"):
			phonetic = sc.kind == tagStart
		case sc.is("t") && sc.kind != tagEnd && !phonetic:
			val := ""
			if sc.kind == tagStart {
				val = sc.content("t", true)
			}
			val = unescapeOOXML(val)
			if rich == nil {
				for sc.nextTag() {
					if sc.kind == tagEnd && sc.is(closing) {
						break
					}
				}
				return val, true
			}
			rich.WriteString(val)
		}
	}
	if rich == nil {
		return "", false
	}
	return rich.String(), true
}

// unescapeOOXML decodes the _xHHHH_ escapes OOXML uses for characters XML cannot carry, left to
// right in one pass (so _x005F_x0041_ is "_x0041_").
func unescapeOOXML(s string) string {
	if !strings.Contains(s, "_x") {
		return s
	}
	var sb strings.Builder
	for i := 0; i < len(s); i++ {
		if s[i] == '_' && i+6 < len(s) && s[i+1] == 'x' && s[i+6] == '_' && isHex(s[i+2:i+6]) {
			var n rune
			for _, c := range []byte(s[i+2 : i+6]) {
				n = n*16 + hexVal(c)
			}
			sb.WriteRune(n)
			i += 6
			continue
		}
		sb.WriteByte(s[i])
	}
	return sb.String()
}

func isHex(s string) bool {
	for i := 0; i < len(s); i++ {
		if hexVal(s[i]) < 0 {
			return false
		}
	}
	return true
}

func hexVal(c byte) rune {
	switch {
	case c >= '0' && c <= '9':
		return rune(c - '0')
	case c >= 'a' && c <= 'f':
		return rune(c-'a') + 10
	case c >= 'A' && c <= 'F':
		return rune(c-'A') + 10
	}
	return -1
}
