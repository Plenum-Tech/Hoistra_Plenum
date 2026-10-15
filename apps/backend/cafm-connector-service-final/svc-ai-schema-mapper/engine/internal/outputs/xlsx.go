package outputs

import (
	"archive/zip"
	"bufio"
	"fmt"
	"io"
	"regexp"
	"strconv"
	"strings"
	"unicode"

	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/pystr"
)

// The workbook output_generator_node builds with openpyxl 3.1 (lxml writer): one sheet per full
// table, or per records table when no full table has rows; a value or title openpyxl refuses
// means no workbook, with openpyxl's message.

var (
	invalidTitle  = regexp.MustCompile(`[\\*?:/\[\]]`)
	illegalString = regexp.MustCompile("[\x00-\x08\x0b\x0c\x0e-\x1f]")
	errorCodes    = map[string]bool{"#NULL!": true, "#DIV/0!": true, "#VALUE!": true, "#REF!": true, "#NAME?": true,
		"#NUM!": true, "#N/A": true}
)

const xmlIncompatible = "All strings must be XML compatible: Unicode or ASCII, no NULL bytes or control characters"

// sheetData is what a sheet is written from: a table of the run, or a frame (Sheet).
type sheetData interface {
	cols() []string
	rows() int
	at(c, r int) cell.Cell
}

type xlsxSheet struct {
	title string
	t     sheetData
}

// refusal is the first value openpyxl refuses in a sheet, as pandas' to_excel meets them: the
// header, then the body column by column (ExcelFormatter writes a frame series by series).
func refusal(t sheetData) string {
	for _, c := range t.cols() {
		if _, why := checkString(c); why != "" {
			return why
		}
	}
	for c := range t.cols() {
		for r := 0; r < t.rows(); r++ {
			if v := t.at(c, r); v.K == cell.Str {
				if _, why := checkString(v.S); why != "" {
					return why
				}
			}
		}
	}
	return ""
}

// xmlRefusal is lxml's refusal, while saving, of a character XML 1.0 cannot carry.
func xmlRefusal(sheets []xlsxSheet) string {
	bad := func(s string) bool { return strings.ContainsAny(s, "\ufffe\uffff") }
	for _, s := range sheets {
		if bad(s.title) {
			return xmlIncompatible
		}
		for _, c := range s.t.cols() {
			if bad(c) {
				return xmlIncompatible
			}
		}
		for r := 0; r < s.t.rows(); r++ {
			for c := range s.t.cols() {
				if v := s.t.at(c, r); v.K == cell.Str && bad(firstRunes(v.S, 32767)) {
					return xmlIncompatible
				}
			}
		}
	}
	return ""
}

func firstRunes(s string, n int) string {
	if rs := []rune(s); len(rs) > n {
		return string(rs[:n])
	}
	return s
}

// checkString is openpyxl's Cell.check_string: at most 32,767 characters, none of the C0
// controls Excel cannot store.
func checkString(s string) (string, string) {
	s = firstRunes(s, 32767)
	if illegalString.MatchString(s) {
		return "", s + " cannot be used in worksheets."
	}
	return s, ""
}

func foldEq(a, b rune) bool {
	if a == b {
		return true
	}
	for f := unicode.SimpleFold(a); f != a; f = unicode.SimpleFold(f) {
		if f == b {
			return true
		}
	}
	return false
}

// avoidDuplicateName is openpyxl.workbook.child.avoid_duplicate_name.
func avoidDuplicateName(names []string, value string) string {
	match := false
	for _, n := range names {
		if pystr.Lower(n) == pystr.Lower(value) {
			match = true
			break
		}
	}
	if !match {
		return value
	}
	hay := []rune(strings.Join(names, ","))
	pat := []rune(value)
	highest := 0
	for i := 0; i+len(pat) <= len(hay); {
		ok := true
		for k := range pat {
			if !foldEq(hay[i+k], pat[k]) {
				ok = false
				break
			}
		}
		if !ok {
			i++
			continue
		}
		j := i + len(pat)
		n, digits := 0, 0
		for j < len(hay) && unicode.IsDigit(hay[j]) {
			d, _ := pystr.DigitValue(hay[j])
			n = n*10 + d
			digits++
			j++
		}
		if digits > 0 && n > highest {
			highest = n
		}
		if j < len(hay) && hay[j] == ',' {
			j++
		}
		i = j
	}
	return value + strconv.Itoa(highest+1)
}

// planWorkbook replays excel_bytes_for up to wb.save(): the sheets, or the message of the
// exception that stops it.
func planWorkbook(full, records []*table) ([]xlsxSheet, string) {
	var sheets []xlsxSheet
	var names []string
	add := func(t *table) string {
		title := firstRunes(t.name, 31)
		if title == "" {
			title = "Sheet"
		}
		if m := invalidTitle.FindString(title); m != "" {
			return "Invalid character " + m + " found in sheet title"
		}
		title = avoidDuplicateName(names, title)
		names = append(names, title)
		sheets = append(sheets, xlsxSheet{title: title, t: t})
		return refusal(t)
	}
	for _, t := range full {
		if t.rows() == 0 || len(t.cols()) == 0 { // df.empty
			continue
		}
		if why := add(t); why != "" {
			return nil, why
		}
	}
	if len(sheets) == 0 {
		for _, t := range records {
			if t.rows() == 0 {
				continue
			}
			if why := add(t); why != "" {
				return nil, why
			}
		}
	}
	if len(sheets) == 0 {
		return nil, "At least one sheet must be visible"
	}
	// lxml, while saving, refuses characters XML 1.0 cannot carry.
	if why := xmlRefusal(sheets); why != "" {
		return nil, why
	}
	return sheets, ""
}

// ── writing ──────────────────────────────────────────────────────────────────────────────

var textEscaper = strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", "\r", "&#13;")
var attrEscaper = strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;", "\t", "&#9;",
	"\n", "&#10;", "\r", "&#13;")

func colLetter(n int) string {
	s := ""
	for n > 0 {
		n--
		s = string(rune('A'+n%26)) + s
		n /= 26
	}
	return s
}

// writeCell is openpyxl's _bind_value + etree_write_cell for the values a table carries.
func writeCell(w *bufio.Writer, ref string, c cell.Cell) {
	switch c.K {
	case cell.Null:
		return // a cell holding None is not written
	case cell.Int, cell.Bool:
		fmt.Fprintf(w, `<c r="%s" t="n"><v>%d</v></c>`, ref, c.I)
		return
	}
	s, _ := checkString(c.S)
	switch {
	case len([]rune(s)) > 1 && strings.HasPrefix(s, "="):
		fmt.Fprintf(w, `<c r="%s"><f>%s</f><v></v></c>`, ref, textEscaper.Replace(s[1:]))
	case errorCodes[s]:
		fmt.Fprintf(w, `<c r="%s" t="e"><v>%s</v></c>`, ref, textEscaper.Replace(s))
	case s == "":
		fmt.Fprintf(w, `<c r="%s" t="inlineStr"></c>`, ref)
	default:
		space := ""
		if st := pystr.Strip(s); st != "" && st != s {
			space = ` xml:space="preserve"`
		}
		fmt.Fprintf(w, `<c r="%s" t="inlineStr"><is><t%s>%s</t></is></c>`, ref, space, textEscaper.Replace(s))
	}
}

func writeSheetXML(w *bufio.Writer, t sheetData) {
	cols := t.cols()
	dim := "A1:A1"
	if len(cols) > 0 {
		dim = "A1:" + colLetter(len(cols)) + strconv.Itoa(t.rows()+1)
	}
	letters := make([]string, len(cols))
	for i := range cols {
		letters[i] = colLetter(i + 1)
	}
	w.WriteString(`<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetPr>` +
		`<outlinePr summaryBelow="1" summaryRight="1"/><pageSetUpPr/></sheetPr><dimension ref="` + dim + `"/>` +
		`<sheetViews><sheetView workbookViewId="0"><selection activeCell="A1" sqref="A1"/></sheetView></sheetViews>` +
		`<sheetFormatPr baseColWidth="8" defaultRowHeight="15"/><sheetData>`)
	if len(cols) > 0 {
		w.WriteString(`<row r="1">`)
		for i, c := range cols {
			writeCell(w, letters[i]+"1", cell.Of(c))
		}
		w.WriteString(`</row>`)
		for r := 0; r < t.rows(); r++ {
			rn := strconv.Itoa(r + 2)
			w.WriteString(`<row r="` + rn + `">`)
			for c := range cols {
				writeCell(w, letters[c]+rn, t.at(c, r))
			}
			w.WriteString(`</row>`)
		}
	}
	w.WriteString(`</sheetData><pageMargins left="0.75" right="0.75" top="1" bottom="1" header="0.5" footer="0.5"/></worksheet>`)
}

const stylesXML = `<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="0"/>` +
	`<fonts count="1"><font><name val="Calibri"/><family val="2"/><sz val="11"/><scheme val="minor"/></font></fonts>` +
	`<fills count="2"><fill><patternFill/></fill><fill><patternFill patternType="gray125"/></fill></fills>` +
	`<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>` +
	`<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>` +
	`<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" pivotButton="0" quotePrefix="0" xfId="0"/></cellXfs>` +
	`<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0" hidden="0"/></cellStyles>` +
	`<tableStyles count="0" defaultTableStyle="TableStyleMedium9" defaultPivotStyle="PivotStyleLight16"/></styleSheet>`

// writeWorkbook writes the .xlsx, one sheet at a time.
func writeWorkbook(out io.Writer, sheets []xlsxSheet, created string) error {
	zw := zip.NewWriter(out)
	part := func(name string, fill func(*bufio.Writer)) error {
		f, err := zw.Create(name)
		if err != nil {
			return err
		}
		bw := bufio.NewWriterSize(f, 1<<20)
		fill(bw)
		return bw.Flush()
	}
	str := func(s string) func(*bufio.Writer) { return func(w *bufio.Writer) { w.WriteString(s) } }
	if err := part("docProps/app.xml", str(`<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>Microsoft Excel Compatible / Hoistra hoist-engine</Application><AppVersion>3.1</AppVersion></Properties>`)); err != nil {
		return err
	}
	if err := part("docProps/core.xml", str(`<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"><dc:creator xmlns:dc="http://purl.org/dc/elements/1.1/">openpyxl</dc:creator><dcterms:created xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="dcterms:W3CDTF">`+created+`</dcterms:created><dcterms:modified xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="dcterms:W3CDTF">`+created+`</dcterms:modified></cp:coreProperties>`)); err != nil {
		return err
	}
	for i, s := range sheets {
		t := s.t
		if err := part(fmt.Sprintf("xl/worksheets/sheet%d.xml", i+1), func(w *bufio.Writer) { writeSheetXML(w, t) }); err != nil {
			return err
		}
	}
	if err := part("xl/styles.xml", str(stylesXML)); err != nil {
		return err
	}
	if err := part("_rels/.rels", str(`<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml" Id="rId1"/><Relationship Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml" Id="rId2"/><Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml" Id="rId3"/></Relationships>`)); err != nil {
		return err
	}
	if err := part("xl/workbook.xml", func(w *bufio.Writer) {
		w.WriteString(`<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><workbookPr/><workbookProtection/><bookViews><workbookView visibility="visible" minimized="0" showHorizontalScroll="1" showVerticalScroll="1" showSheetTabs="1" tabRatio="600" firstSheet="0" activeTab="0" autoFilterDateGrouping="1"/></bookViews><sheets>`)
		for i, s := range sheets {
			fmt.Fprintf(w, `<sheet xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" name="%s" sheetId="%d" state="visible" r:id="rId%d"/>`,
				attrEscaper.Replace(s.title), i+1, i+1)
		}
		w.WriteString(`</sheets><definedNames/><calcPr calcId="124519" fullCalcOnLoad="1"/></workbook>`)
	}); err != nil {
		return err
	}
	if err := part("xl/_rels/workbook.xml.rels", func(w *bufio.Writer) {
		w.WriteString(`<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">`)
		for i := range sheets {
			fmt.Fprintf(w, `<Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="/xl/worksheets/sheet%d.xml" Id="rId%d"/>`, i+1, i+1)
		}
		fmt.Fprintf(w, `<Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml" Id="rId%d"/></Relationships>`, len(sheets)+1)
	}); err != nil {
		return err
	}
	if err := part("[Content_Types].xml", func(w *bufio.Writer) {
		w.WriteString(`<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>`)
		for i := range sheets {
			fmt.Fprintf(w, `<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`, i+1)
		}
		w.WriteString(`<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>`)
	}); err != nil {
		return err
	}
	return zw.Close()
}
