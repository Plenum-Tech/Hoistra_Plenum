package csvread

import (
	"reflect"
	"strings"
	"testing"

	"hoistra/engine/internal/cell"
)

// Every expectation below is what pandas 2.3.3's read_csv(dtype=str) followed by ingest's
// _sanitize_column_names produced for the same text (rows: None for NaN).

const N = "\x00none" // stands for None in the expected rows

func rowsOf(rs [][]cell.Cell) [][]string {
	out := make([][]string, len(rs))
	for i, r := range rs {
		out[i] = make([]string, len(r))
		for j, c := range r {
			if c.IsNone() {
				out[i][j] = N
			} else {
				out[i][j] = c.S
			}
		}
	}
	return out
}

func TestReadFollowsPandas(t *testing.T) {
	cases := []struct {
		name, text string
		delim      byte
		cols       []string
		rows       [][]string
	}{
		{"plain", "code,name,qty\nA1,Pump,3\nA2,Fan,\n", ',', []string{"code", "name", "qty"},
			[][]string{{"A1", "Pump", "3"}, {"A2", "Fan", N}}},
		{"implicit index", "a,b\n1,2,\n3,4,\n", ',', []string{"a", "b"}, [][]string{{"2", N}, {"4", N}}},
		{"implicit then short", "a,b\n1,2,\n3,4\n5\n", ',', []string{"a", "b"},
			[][]string{{"2", N}, {"4", N}, {N, N}}},
		{"two leading index columns", "a\n1,2,3\n", ',', []string{"a"}, [][]string{{"3"}}},
		{"short rows padded", "a,b,c\n1,2,3\n4,5\n6\n", ',', []string{"a", "b", "c"},
			[][]string{{"1", "2", "3"}, {"4", "5", N}, {"6", N, N}}},
		{"short then normal", "a,b,c\n1\n2,3,4\n", ',', []string{"a", "b", "c"}, [][]string{{"1", N, N}, {"2", "3", "4"}}},
		{"header longer", "a,b,c,d\n1,2\n", ',', []string{"a", "b", "c", "d"}, [][]string{{"1", "2", N, N}}},
		{"lone cr", "a,b\r1,2\r3,4\r", ',', []string{"a", "b"}, [][]string{{"1", "2"}, {"3", "4"}}},
		{"crlf", "\ufeffcode,name\r\nA1,Pump\r\n", ',', []string{"code", "name"}, [][]string{{"A1", "Pump"}}},
		{"crlf in quotes", "a,b\r\n\"x\r\ny\",2\r\n", ',', []string{"a", "b"}, [][]string{{"x\r\ny", "2"}}},
		{"quote mid field", "a,b\nx\"y,2\n\"q\"r,3\n", ',', []string{"a", "b"}, [][]string{{"x\"y", "2"}, {"qr", "3"}}},
		{"doubled quotes", "a,b\n\"x\"\"\",1\n\"\"\"\",2\n", ',', []string{"a", "b"}, [][]string{{"x\"", "1"}, {"\"", "2"}}},
		{"blank and whitespace lines", "code,name\nA1,Pump\n\n   \nA2,Fan\n \t \n  A3, Spaced \n", ',',
			[]string{"code", "name"}, [][]string{{"A1", "Pump"}, {"A2", "Fan"}, {"  A3", " Spaced "}}},
		{"whitespace line first", "   \na,b\n1,2\n", ',', []string{"a", "b"}, [][]string{{"1", "2"}}},
		{"whitespace with delimiter is data", "a,b\n , \n1,2\n", ',', []string{"a", "b"},
			[][]string{{" ", " "}, {"1", "2"}}},
		{"leading tab kept", "a,b\n\t1,2\n", ',', []string{"a", "b"}, [][]string{{"\t1", "2"}}},
		{"space before quote", "a,b\n \"x\",2\n", ',', []string{"a", "b"}, [][]string{{" \"x\"", "2"}}},
		{"space after quote", "a,b\n\"x\" ,2\n", ',', []string{"a", "b"}, [][]string{{"x ", "2"}}},
		{"nul cuts the field", "a,b\n1\x002,3\n", ',', []string{"a", "b"}, [][]string{{"1", "3"}}},
		{"na tokens", "a,b,c,d,e,f,g\nNA,N/A,null,None,#N/A,,nan\n-,NULL,<NA>,n/a,-NaN,  ,x\n", ',',
			[]string{"a", "b", "c", "d", "e", "f", "g"},
			[][]string{{N, N, N, N, N, N, N}, {"-", N, N, N, N, "  ", "x"}}},
		{"quoted na", "a,b\n\"NA\",\"\"\n", ',', []string{"a", "b"}, [][]string{{N, N}}},
		{"names stripped and unnamed", " a ,,b\n1,2,3\n", ',', []string{"a", "col_2", "b"}, [][]string{{"1", "2", "3"}}},
		{"na words in header", "NA,nan,,None\n1,2,3,4\n", ',', []string{"NA", "col_2", "col_3", "None"},
			[][]string{{"1", "2", "3", "4"}}},
		{"dedup avoids later names", "a,a,a.1,a\n1,2,3,4\n", ',', []string{"a", "a.2", "a.1", "a.3"},
			[][]string{{"1", "2", "3", "4"}}},
		{"dedup a a.1 a", "a,a.1,a\n1,2,3\n", ',', []string{"a", "a.1", "a.2"}, [][]string{{"1", "2", "3"}}},
		{"unnamed named last", "id,,id,name,,Unnamed: 1\n1,a,2,x,b,c\n", ',',
			[]string{"id", "col_2", "id.1", "name", "col_5", "col_6"}, [][]string{{"1", "a", "2", "x", "b", "c"}}},
		{"trailing delimiter in header", "a,b,\n1,2,\n", ',', []string{"a", "b", "col_3"}, [][]string{{"1", "2", N}}},
		{"no final newline", "a,b\n1,2", ',', []string{"a", "b"}, [][]string{{"1", "2"}}},
		{"semicolon keeps commas", "a;b\n1,5;2\n", ';', []string{"a", "b"}, [][]string{{"1,5", "2"}}},
		{"collision after strip keeps the last value", "a, a\n1,2\n", ',', []string{"a"}, [][]string{{"2"}}},
		{"header only is not sanitised", "a, b ,\n", ',', []string{"a", " b ", "Unnamed: 2"}, [][]string{}},
	}
	for _, c := range cases {
		cols, rows, err := Read(c.text, c.delim)
		if err != nil {
			t.Errorf("%s: %v", c.name, err)
			continue
		}
		if !reflect.DeepEqual(cols, c.cols) {
			t.Errorf("%s: columns %q, want %q", c.name, cols, c.cols)
		}
		if got := rowsOf(rows); !reflect.DeepEqual(got, c.rows) {
			t.Errorf("%s: rows %q, want %q", c.name, got, c.rows)
		}
	}
}

func TestReadFailsWherePandasFails(t *testing.T) {
	cases := []struct{ name, text, want string }{
		{"too many fields", "a,b\n1,2\n3,4,5\n", "Error tokenizing data. C error: Expected 2 fields in line 3, saw 3"},
		{"implicit then more", "a,b\n1,2,3\n5,6,7,8\n", "Error tokenizing data. C error: Expected 3 fields in line 3, saw 4"},
		{"after blank lines", "a,b\n\n1,2\n\n3,4,5\n", "Error tokenizing data. C error: Expected 2 fields in line 5, saw 3"},
		{"after a quoted newline", "a,b\n\"x\ny\",2\n3,4,5\n", "Error tokenizing data. C error: Expected 2 fields in line 3, saw 3"},
		{"short then long", "a,b\n1\n2,3,4\n", "Error tokenizing data. C error: Expected 2 fields in line 3, saw 3"},
		{"normal short long", "a,b,c\n1,2,3\n4\n5,6,7,8\n", "Error tokenizing data. C error: Expected 3 fields in line 4, saw 4"},
		{"eof in quotes", "a,b\n\"x,1\n", "Error tokenizing data. C error: EOF inside string starting at row 1"},
		{"empty", "", "No columns to parse from file"},
		{"blank lines only", "\n\n\n", "No columns to parse from file"},
	}
	for _, c := range cases {
		_, _, err := Read(c.text, ',')
		if err == nil || err.Error() != c.want {
			t.Errorf("%s: got %v, want %q", c.name, err, c.want)
		}
	}
}

func TestDelimiterIsIngestsFirstMatch(t *testing.T) {
	cases := map[string]byte{
		"a,b\n1,2":                      ',',
		"a\tb\n1\t2, with comma":        ',', // any comma in the first five lines wins
		"a\tb\n1\t2":                    '\t',
		"a;b\n1;2":                      ';',
		"a|b\n1|2":                      '|',
		"plain":                         ',',
		"1\n2\n3\n4\n5\n6,7":            ',', // the sixth line is not looked at — and nothing else matched
		"1\n2\n3\n4\n5\t\n6;7":          '\t',
		strings.Repeat("x", 5000) + ",": ',',
	}
	for text, want := range cases {
		if got := Delimiter(text); got != want {
			t.Errorf("Delimiter(%.20q) = %q, want %q", text, got, want)
		}
	}
	// Only the first 4,096 characters count: a semicolon past them is not seen.
	if got := Delimiter(strings.Repeat("é", 4096) + ";"); got != ',' {
		t.Errorf("past 4096 characters: %q", got)
	}
}
