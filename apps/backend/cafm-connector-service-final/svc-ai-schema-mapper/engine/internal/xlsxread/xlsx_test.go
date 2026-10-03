package xlsxread

import (
	"archive/zip"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"hoistra/engine/internal/cell"
)

// Every expectation below is what python-calamine 0.8.2 + pandas 2.3.3 (the worker image) gave
// for the same XML, probed one case at a time.

type book struct {
	rows     string   // <sheetData> content
	strings  []string // shared strings (raw <si> XML when it starts with "<")
	fmts     []any    // cellXfs 1.. : an int is a built-in numFmtId, a string a custom format code
	date1904 bool
}

func (b book) write(t *testing.T) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "b.xlsx")
	f, err := os.Create(p)
	if err != nil {
		t.Fatal(err)
	}
	z := zip.NewWriter(f)
	put := func(name, body string) {
		w, err := z.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		w.Write([]byte(body))
	}
	const ns = `xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"`
	put("[Content_Types].xml", `<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>`)
	put("_rels/.rels", `<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`)
	pr := ""
	if b.date1904 {
		pr = `<workbookPr date1904="1"/>`
	}
	put("xl/workbook.xml", `<?xml version="1.0"?><workbook `+ns+` xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">`+pr+`<sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>`)
	put("xl/_rels/workbook.xml.rels", `<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/></Relationships>`)
	var custom, xfs strings.Builder
	xfs.WriteString(`<xf numFmtId="0"/>`)
	for i, f := range b.fmts {
		switch f := f.(type) {
		case int:
			fmt.Fprintf(&xfs, `<xf numFmtId="%d"/>`, f)
		case string:
			code := strings.NewReplacer("&", "&amp;", `"`, "&quot;", "<", "&lt;").Replace(f)
			fmt.Fprintf(&custom, `<numFmt numFmtId="%d" formatCode="%s"/>`, 164+i, code)
			fmt.Fprintf(&xfs, `<xf numFmtId="%d"/>`, 164+i)
		}
	}
	put("xl/styles.xml", `<?xml version="1.0"?><styleSheet `+ns+`><numFmts>`+custom.String()+`</numFmts><cellXfs>`+xfs.String()+`</cellXfs></styleSheet>`)
	var sst strings.Builder
	for _, s := range b.strings {
		if strings.HasPrefix(s, "<") {
			sst.WriteString(s)
		} else {
			sst.WriteString(`<si><t xml:space="preserve">` + strings.NewReplacer("&", "&amp;", "<", "&lt;").Replace(s) + `</t></si>`)
		}
	}
	put("xl/sharedStrings.xml", `<?xml version="1.0"?><sst `+ns+`>`+sst.String()+`</sst>`)
	put("xl/worksheets/sheet1.xml", `<?xml version="1.0"?><worksheet `+ns+`><sheetData>`+b.rows+`</sheetData></worksheet>`)
	if err := z.Close(); err != nil {
		t.Fatal(err)
	}
	f.Close()
	return p
}

func open(t *testing.T, b book) *Workbook {
	t.Helper()
	wb, err := Open(b.write(t))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { wb.Close() })
	return wb
}

// texts is the sheet as Rows yields it: each cell's dtype=str text, "<NA>" for None.
func texts(t *testing.T, b book) [][]string {
	t.Helper()
	var out [][]string
	err := open(t, b).Rows("S", func(_ int, cells []cell.Cell) error {
		r := make([]string, len(cells))
		for i, c := range cells {
			if c.IsNone() {
				r[i] = "<NA>"
			} else {
				r[i] = c.S
			}
		}
		out = append(out, r)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func rowsErr(t *testing.T, b book) error {
	t.Helper()
	return open(t, b).Rows("S", func(int, []cell.Cell) error { return nil })
}

func TestBuiltinAndCustomNumberFormats(t *testing.T) {
	for id, want := range map[string]cellFormat{"14": fmtDateTime, "22": fmtDateTime, "45": fmtDateTime,
		"47": fmtDateTime, "46": fmtTimeDelta, "2": fmtOther, "23": fmtOther, "014": fmtOther, "": fmtOther} {
		if got := builtinFormat(id); got != want {
			t.Errorf("builtin %q = %v, want %v", id, got, want)
		}
	}
	for code, want := range map[string]cellFormat{
		"yyyy-mm-dd": fmtDateTime, "dddd": fmtDateTime, "ss.000": fmtDateTime, "mm:ss.0": fmtDateTime,
		"h:mm AM/PM": fmtDateTime, "[$-409]mmm": fmtDateTime, "AM/PM": fmtDateTime,
		"[h]:mm:ss": fmtTimeDelta, "[mm]:ss": fmtTimeDelta, "[s]": fmtTimeDelta, "[hh]": fmtTimeDelta,
		"0.00": fmtOther, "General": fmtOther, "#,##0": fmtOther, "0.0E+00": fmtOther, `"d"0`: fmtOther,
		`\d0`: fmtOther, "_d0": fmtOther, "[Red]0.0;[Blue]-0.0": fmtOther, "0;yyyy": fmtOther, "[Red]0": fmtOther,
	} {
		if got := customFormat(code); got != want {
			t.Errorf("custom %q = %v, want %v", code, got, want)
		}
	}
}

func TestAFormatCodeIsReadWithItsEntitiesDecoded(t *testing.T) {
	st, err := parseStyles([]byte(`<styleSheet><numFmts><numFmt numFmtId="164" formatCode="&quot;Date: &quot;dd/mm"/>` +
		`<numFmt numFmtId="165" formatCode="&quot;x&quot;0"/><numFmt numFmtId="14" formatCode=""/></numFmts>` +
		`<cellXfs><xf numFmtId="164"/><xf numFmtId="165"/><xf numFmtId="14"/><xf/></cellXfs></styleSheet>`))
	if err != nil {
		t.Fatal(err)
	}
	if want := []cellFormat{fmtDateTime, fmtOther, fmtDateTime, fmtOther}; !reflect.DeepEqual(st.xf, want) {
		t.Fatalf("got %v, want %v", st.xf, want)
	}
}

func TestXMLTextDecoding(t *testing.T) {
	cases := map[string]string{
		"a&amp;b&lt;&gt;&quot;&apos;": `a&b<>"'`, "&#65;&#x42;&#X43;": "ABC", "raw\r\nline": "raw\nline",
		"x\ry": "x\ny", "cr&#13;kept": "cr\rkept", "&bogus; &": "&bogus; &",
	}
	for in, want := range cases {
		if got := unescape([]byte(in)); got != want {
			t.Errorf("unescape(%q) = %q, want %q", in, got, want)
		}
	}
	for in, want := range map[string]string{"a_x000D_b": "a\rb", "_x005F_x0041_": "_x0041_", "_x00zz_": "_x00zz_", "_x0041": "_x0041"} {
		if got := unescapeOOXML(in); got != want {
			t.Errorf("unescapeOOXML(%q) = %q, want %q", in, got, want)
		}
	}
}

func c(ref, attrs, inner string) string { return `<c r="` + ref + `" ` + attrs + `>` + inner + `</c>` }
func v(x string) string                 { return "<v>" + x + "</v>" }

func TestTheRangeRunsFromA1ToTheLastCellCalamineKeeps(t *testing.T) {
	cases := []struct {
		name string
		b    book
		want [][]string
	}{
		{"an empty number does not extend", book{rows: `<row r="1">` + c("A1", "", v("1")) + c("D1", "", v("")) + `</row>`}, [][]string{{"1"}}},
		{"an empty inline string does", book{rows: `<row r="1">` + c("A1", "", v("1")) + c("C1", `t="inlineStr"`, "<is><t></t></is>") + `</row>`}, [][]string{{"1", "<NA>", "<NA>"}}},
		{"an empty shared string does", book{rows: `<row r="1">` + c("A1", "", v("1")) + c("C1", `t="s"`, v("0")) + `</row>`, strings: []string{""}}, [][]string{{"1", "<NA>", "<NA>"}}},
		{"an error does", book{rows: `<row r="1">` + c("A1", "", v("1")) + c("C1", `t="e"`, v("#N/A")) + `</row>`}, [][]string{{"1", "<NA>", "<NA>"}}},
		{"an empty str result does", book{rows: `<row r="1">` + c("A1", "", v("1")) + c("C1", `t="str"`, "<f>x</f><v></v>") + `</row>`}, [][]string{{"1", "<NA>", "<NA>"}}},
		{"a formula with no value does not", book{rows: `<row r="1">` + c("A1", "", v("1")) + c("C1", `t="str"`, "<f>x</f>") + `</row>`}, [][]string{{"1"}}},
		{"an empty boolean does not", book{rows: `<row r="1">` + c("A1", "", v("1")) + c("C1", `t="b"`, v("")) + `</row>`}, [][]string{{"1"}}},
		{"an empty error does not", book{rows: `<row r="1">` + c("A1", "", v("1")) + c("C1", `t="e"`, v("")) + `</row>`}, [][]string{{"1"}}},
		{"a styled empty cell does not", book{rows: `<row r="1">` + c("A1", "", v("1")) + `<c r="E1" s="0"/></row>`}, [][]string{{"1"}}},
		{"cells without r follow on", book{rows: `<row><c><v>1</v></c><c><v>2</v></c></row><row><c><v>3</v></c></row>`}, [][]string{{"1", "2"}, {"3", "<NA>"}}},
		{"a cell's r does not move the row counter", book{rows: `<row r="1"><c r="B5"><v>1</v></c><c><v>2</v></c></row><row><c><v>3</v></c></row>`},
			[][]string{{"<NA>", "<NA>", "2"}, {"3", "<NA>", "<NA>"}, {"<NA>", "<NA>", "<NA>"}, {"<NA>", "<NA>", "<NA>"}, {"<NA>", "1", "<NA>"}}},
		{"rows out of order land where they say", book{rows: `<row r="3"><c r="A3"><v>3</v></c></row><row r="1"><c r="B1"><v>1</v></c></row><row r="2"><c r="C2"><v>2</v></c></row>`},
			[][]string{{"<NA>", "1", "<NA>"}, {"<NA>", "<NA>", "2"}, {"3", "<NA>", "<NA>"}}},
		{"a lower-case ref", book{rows: `<row r="1"><c r="b1"><v>1</v></c></row>`}, [][]string{{"<NA>", "1"}}},
		{"the later of two cells at one place wins", book{rows: `<row r="1"><c r="A1"><v>1</v></c><c r="A1"><v>2</v></c></row>`}, [][]string{{"2"}}},
		{"rows before the first value are empty", book{rows: `<row r="3"><c r="B3"><v>1</v></c></row><row r="5"><c r="A5"><v>2</v></c></row>`},
			[][]string{{"<NA>", "<NA>"}, {"<NA>", "<NA>"}, {"<NA>", "1"}, {"<NA>", "<NA>"}, {"2", "<NA>"}}},
		{"no cells, no rows", book{rows: `<row r="1"><c r="A1"/></row>`}, nil},
	}
	for _, tc := range cases {
		if got := texts(t, tc.b); !reflect.DeepEqual(got, tc.want) {
			t.Errorf("%s: got %q, want %q", tc.name, got, tc.want)
		}
	}
}

func TestCellValuesAsCalamineReadsThem(t *testing.T) {
	cases := []struct {
		name string
		b    book
		want []string
	}{
		{"booleans: 0 is False, anything else True", book{rows: `<row r="1">` + c("A1", `t="b"`, v("0")) + c("B1", `t="b"`, v("true")) + c("C1", `t="b"`, v("2")) + `</row>`}, []string{"False", "True", "True"}},
		{"number text Rust parses, or keeps", book{rows: `<row r="1">` + c("A1", "", v(" 5")) + c("B1", "", v("5 ")) + c("C1", "", v("+5")) + c("D1", "", v(".5")) + c("E1", "", v("5.")) + c("F1", "", v("1_0")) + c("G1", "", v("0x10")) + `</row>`},
			[]string{" 5", "5 ", "5", "0.5", "5", "1_0", "0x10"}},
		{"integral floats read as ints, any size", book{rows: `<row r="1">` + c("A1", "", v("1e300")) + c("B1", "", v("-0")) + c("C1", "", v("1e-5")) + c("D1", "", v("123456789012345678")) + c("E1", "", v("3.14159")) + `</row>`},
			[]string{"1000000000000000052504760255204420248704468581108159154915854115511802457988908195786371375080447864043704443832883878176942523235360430575644792184786706982848387200926575803737830233794788090059368953234970799945081119038967640880074652742780142494579258788820056842838115669472196386865459400540160", "0", "1e-05", "123456789012345680", "3.14159"}},
		{"an inline string needs no t", book{rows: `<row r="1"><c r="A1"><is><t>x</t></is></c></row>`}, []string{"x"}},
		{"_xHHHH_ is decoded in strings, not in a str result", book{rows: `<row r="1">` + c("A1", `t="str"`, v("a_x000D_b")) + c("B1", `t="inlineStr"`, "<is><t>c_x000D_d</t></is>") + c("C1", `t="s"`, v("0")) + `</row>`, strings: []string{"a_x000D_b_x005F_x0041_"}},
			[]string{"a_x000D_b", "c\rd", "a\rb_x0041_"}},
		{"shared strings: CDATA, rich runs, phonetics, empty entries", book{
			rows:    `<row r="1">` + c("A1", `t="s"`, v("0")) + c("B1", `t="s"`, v("1")) + c("C1", `t="s"`, v("2")) + c("D1", `t="s"`, v("3")) + c("E1", `t="s"`, v("4")) + c("F1", `t="s"`, v("x")) + `</row>`,
			strings: []string{`<si><t><![CDATA[c<d]]></t></si>`, `<si><r><t>ri</t></r><r><t>ch</t></r><rPh><t>no</t></rPh></si>`, `<si/>`, `<si><t>first</t><r><t>ignored</t></r></si>`, `<si><t>e</t></si>`}},
			[]string{"c<d", "rich", "<NA>", "first", "e", "c<d"}},
		{"raw line ends read as newlines", book{rows: `<row r="1"><c r="A1" t="inlineStr"><is><t>a` + "\r\n" + `b` + "\r" + `c</t></is></c></row>`}, []string{"a\nb\nc"}},
		{"an ISO date cell", book{rows: `<row r="1">` + c("A1", `t="d"`, v("2024-01-15T10:30:00")) + c("B1", `t="d"`, v("2024-01-15")) + c("C1", `t="d"`, v("10:30")) + c("D1", `t="d"`, v("2024-01-15T10:30:00.123456789")) +
			c("E1", `t="d"`, v("2024-01-15 10:30:00")) + c("F1", `t="d"`, v("2024-01-15T10:30")) + c("G1", `t="d"`, v(" 2024-1-5 ")) + c("H1", `t="d"`, v("23:59:60")) + c("I1", `t="d"`, v("24:00:00")) +
			c("J1", `t="d"`, v("2024-02-30")) + c("K1", `t="d"`, v("+2024-01-01")) + c("L1", `t="d"`, v("12345-01-01")) + c("M1", `t="d"`, v("2024-01-15t10:30:00")) + c("N1", `t="d"`, v("1:2:3")) + `</row>`},
			[]string{"2024-01-15 10:30:00", "2024-01-15 00:00:00", "10:30:00", "2024-01-15 10:30:00.123456", "2024-01-15 10:30:00", "2024-01-15T10:30",
				"2024-01-05 00:00:00", "23:59:59", "24:00:00", "2024-02-30", "2024-01-01 00:00:00", "12345-01-01", "2024-01-15t10:30:00", "01:02:03"}},
	}
	for _, tc := range cases {
		got := texts(t, tc.b)
		if len(got) != 1 || !reflect.DeepEqual(got[0], tc.want) {
			t.Errorf("%s: got %q, want %q", tc.name, got, [][]string{tc.want})
		}
	}
}

func serials(t *testing.T, format any, date1904 bool, values []string) []string {
	t.Helper()
	var rows strings.Builder
	for i, x := range values {
		fmt.Fprintf(&rows, `<row r="%d"><c r="A%d" s="1"><v>%s</v></c></row>`, i+1, i+1, x)
	}
	var out []string
	for _, r := range texts(t, book{rows: rows.String(), fmts: []any{format}, date1904: date1904}) {
		out = append(out, r[0])
	}
	return out
}

func TestDateFormattedNumbers(t *testing.T) {
	in := []string{"0", "0.5", "0.999999999", "1", "1.5", "59", "60", "61", "45000.25", "45000.0000000001", "-1", "-0.25", "2958465", "2958465.9999", "2958466", "1e10", "45000.123456789"}
	want := []string{"00:00:00", "12:00:00", "00:00:00", "1900-01-01 00:00:00", "1900-01-01 12:00:00", "1900-02-28 00:00:00", "1900-02-28 00:00:00", "1900-03-01 00:00:00",
		"2023-03-15 06:00:00", "2023-03-15 00:00:00", "00:00:00", "18:00:00", "9999-12-31 00:00:00", "9999-12-31 23:59:51.360000", "2958466", "10000000000", "2023-03-15 02:57:46.667000"}
	for _, f := range []any{14, "mm:ss.0"} {
		if got := serials(t, f, false, in); !reflect.DeepEqual(got, want) {
			t.Errorf("format %v:\n got %q\nwant %q", f, got, want)
		}
	}
	want1904 := []string{"00:00:00", "12:00:00", "00:00:00", "1904-01-02 00:00:00", "1904-01-02 12:00:00", "1904-02-29 00:00:00", "1904-03-01 00:00:00", "1904-03-02 00:00:00",
		"2027-03-16 06:00:00", "2027-03-16 00:00:00", "00:00:00", "18:00:00", "2958465", "2958465.9999", "2958466", "10000000000", "2027-03-16 02:57:46.667000"}
	if got := serials(t, 14, true, in); !reflect.DeepEqual(got, want1904) {
		t.Errorf("1904:\n got %q\nwant %q", got, want1904)
	}
}

func TestDurationFormattedNumbers(t *testing.T) {
	in := []string{"0", "0.5", "1", "1.5", "-0.5", "-1.25", "0.0000057870370", "45351.5729166667", "200000", "0.123456789", "-0.0000000001", "10.99999999999", "106751.9919", "-106751.99", "-106752.5"}
	want := []string{"0 days 00:00:00", "0 days 12:00:00", "1 days 00:00:00", "1 days 12:00:00", "-1 days +12:00:00", "-2 days +18:00:00", "0 days 00:00:00.500000",
		"45351 days 13:45:00", "200000 days 00:00:00", "0 days 02:57:46.667000", "0 days 00:00:00", "11 days 00:00:00", "106751 days 23:48:20.160000", "-106752 days +00:14:24", "-106753 days +12:00:00"}
	for _, f := range []any{46, "[h]:mm:ss"} {
		if got := serials(t, f, false, in); !reflect.DeepEqual(got, want) {
			t.Errorf("format %v:\n got %q\nwant %q", f, got, want)
		}
	}
}

func TestValuesThatFailTheWholeRead(t *testing.T) {
	dur := func(x string) book {
		return book{rows: `<row r="1"><c r="A1" s="1"><v>` + x + `</v></c></row>`, fmts: []any{"[h]:mm:ss"}}
	}
	cases := map[string]book{
		"days=1000000000; must have magnitude <= 999999999":  dur("1000000000"),
		"days=-1000000000; must have magnitude <= 999999999": dur("-999999999.9"),
		"days=2147483647; must have magnitude <= 999999999":  dur("-3000000000"),
		"Python int too large to convert to C long":          dur("999999999"),
		"cannot convert float NaN to integer":                {rows: `<row r="1">` + c("A1", "", v("NaN")) + `</row>`},
		"cannot convert float infinity to integer":           {rows: `<row r="1">` + c("A1", "", v("1e400")) + `</row>`},
		"Unsupported cell error value '#SPILL!'":             {rows: `<row r="1">` + c("A1", `t="e"`, v("#SPILL!")) + `</row>`},
		"Cell string index not found in shared strings table": {rows: `<row r="1">` + c("A1", `t="s"`, v("9")) + `</row>`, strings: []string{"a"}},
		"Parse float error: invalid float literal":           {rows: `<row r="1">` + c("A1", `t="n"`, v("abc")) + `</row>`},
		`Unknown cell 't' attribute: "zz"`:                    {rows: `<row r="1">` + c("A1", `t="zz"`, v("x")) + `</row>`},
	}
	for want, b := range cases {
		if err := rowsErr(t, b); err == nil || err.Error() != want {
			t.Errorf("want %q, got %v", want, err)
		}
	}
	// calamine fails before pandas looks at a value: the duration error wins over an earlier NaN.
	b := book{rows: `<row r="1">` + c("A1", "", v("NaN")) + `</row><row r="2"><c r="A2" s="1"><v>1000000000</v></c></row>`, fmts: []any{46}}
	if err := rowsErr(t, b); err == nil || !strings.HasPrefix(err.Error(), "days=") {
		t.Errorf("got %v", err)
	}
}

type frame struct {
	cols []string
	rows [][]string
}

func readFrame(t *testing.T, b book) frame {
	t.Helper()
	cols, rows, err := Frame(open(t, b), "S")
	if err != nil {
		t.Fatal(err)
	}
	f := frame{cols: cols}
	for _, r := range rows {
		out := make([]string, len(r))
		for i, x := range r {
			if x.IsNone() {
				out[i] = "<None>"
			} else {
				out[i] = x.S
			}
		}
		f.rows = append(f.rows, out)
	}
	return f
}

func TestTheFrameIngestBuildsFromASheet(t *testing.T) {
	s := func(ref string, i int) string { return c(ref, `t="s"`, v(fmt.Sprint(i))) }
	b := func(ref, x string) string { return c(ref, `t="b"`, v(x)) }
	n := func(ref, x string) string { return c(ref, "", v(x)) }
	row := func(r int, cells ...string) string { return fmt.Sprintf(`<row r="%d">`, r) + strings.Join(cells, "") + "</row>" }
	cases := []struct {
		name string
		b    book
		want frame
	}{
		{"a 1 and a True in one column read as whichever came first", book{
			rows:    row(1, s("A1", 0), s("B1", 1)) + row(2, n("A2", "1"), b("B2", "1")) + row(3, b("A3", "1"), n("B3", "1")) + row(4, b("A4", "0"), n("B4", "0")) + row(5, n("A5", "0"), b("B5", "0")),
			strings: []string{"a", "b"}},
			frame{[]string{"a", "b"}, [][]string{{"1", "True"}, {"1", "True"}, {"False", "0"}, {"False", "0"}}}},
		{"an int header and a string header are different names", book{
			rows: row(1, n("A1", "5"), s("B1", 0), s("C1", 0)) + row(2, n("A2", "1"), n("B2", "2"), n("C2", "3")), strings: []string{"5"}},
			frame{[]string{"5", "5.1"}, [][]string{{"2", "3"}}}},
		{"a True header and a 1 header are one name", book{
			rows: row(1, b("A1", "1"), n("B1", "1"), s("C1", 0)) + row(2, n("A2", "1"), n("B2", "2"), n("C2", "3")), strings: []string{"x"}},
			frame{[]string{"True", "1.1", "x"}, [][]string{{"1", "2", "3"}}}},
		{"blank rows stay", book{rows: row(1, s("A1", 0), s("B1", 1)) + row(3, n("A3", "1")) + row(5, n("B5", "2")), strings: []string{"a", "b"}},
			frame{[]string{"a", "b"}, [][]string{{"<None>", "<None>"}, {"1", "<None>"}, {"<None>", "<None>"}, {"<None>", "2"}}}},
		{"a header with no rows keeps pandas' names", book{rows: row(1, s("A1", 0), s("C1", 1)), strings: []string{" a ", "x"}},
			frame{[]string{" a ", "Unnamed: 1", "x"}, nil}},
		{"NA tokens", book{rows: row(1, s("A1", 0), s("B1", 1)) + row(2, s("A2", 2), s("B2", 3)) + row(3, s("A3", 4), s("B3", 5)),
			strings: []string{"nan", "NA", "None", "null", " NA", "#N/A"}},
			frame{[]string{"col_1", "NA"}, [][]string{{"<None>", "<None>"}, {" NA", "<None>"}}}},
		{"a banner row before the header", book{rows: row(1, s("A1", 0)) + row(2, s("A2", 1), s("B2", 2), s("C2", 3)) + row(3, n("A3", "1"), n("B3", "2"), n("C3", "3")),
			strings: []string{"Report", "a", "b", "c"}},
			frame{[]string{"a", "b", "c"}, [][]string{{"1", "2", "3"}}}},
		{"the first row at half the widest is the header", book{rows: row(1, s("A1", 0)) + row(2, s("A2", 1), s("B2", 2)) + row(3, s("A3", 1), s("B3", 2), s("C3", 3), s("D3", 0)),
			strings: []string{"t", "a", "b", "c"}},
			frame{[]string{"a", "b", "col_3", "col_4"}, [][]string{{"a", "b", "c", "t"}}}},
		{"no row wider than one: row 0", book{rows: row(1, s("A1", 0)) + row(2, s("B2", 1)), strings: []string{"t", "x"}},
			frame{[]string{"t", "col_2"}, [][]string{{"<None>", "x"}}}},
		{"duplicate names after stripping keep the first place, last value", book{rows: row(1, s("A1", 0), s("B1", 1), s("C1", 2)) + row(2, n("A2", "1"), n("B2", "2"), n("C2", "3")),
			strings: []string{"a", " a", "b"}},
			frame{[]string{"a", "b"}, [][]string{{"2", "3"}}}},
		{"an empty sheet", book{rows: ""}, frame{nil, nil}},
	}
	for _, tc := range cases {
		got := readFrame(t, tc.b)
		if len(got.cols) == 0 {
			got.cols = nil
		}
		if !reflect.DeepEqual(got, tc.want) {
			t.Errorf("%s:\n got %q %q\nwant %q %q", tc.name, got.cols, got.rows, tc.want.cols, tc.want.rows)
		}
	}
}

func TestSheetNamesAndTheDateSystem(t *testing.T) {
	wb := open(t, book{rows: "", date1904: true})
	if !reflect.DeepEqual(wb.SheetNames(), []string{"S"}) || !wb.Date1904() {
		t.Fatalf("%q %v", wb.SheetNames(), wb.Date1904())
	}
	if _, err := Open(filepath.Join(t.TempDir(), "missing.xlsx")); err == nil {
		t.Fatal("want an error")
	}
}
