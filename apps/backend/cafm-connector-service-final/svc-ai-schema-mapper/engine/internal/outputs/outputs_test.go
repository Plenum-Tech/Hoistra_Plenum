package outputs

import (
	"bufio"
	"bytes"
	"strings"
	"testing"
)

// The titles openpyxl 3.1.5 gave these create_sheet calls, in order.
func TestDuplicateSheetTitlesFollowOpenpyxl(t *testing.T) {
	in := []string{"abc", "abc", "ABC", "abc1", "abc", strings.Repeat("x", 31), strings.Repeat("x", 31)}
	want := []string{"abc", "abc1", "ABC2", "abc11", "abc12", strings.Repeat("x", 31), strings.Repeat("x", 31) + "1"}
	var names []string
	for i, v := range in {
		got := avoidDuplicateName(names, v)
		if got != want[i] {
			t.Fatalf("title %d: %q, want %q", i, got, want[i])
		}
		names = append(names, got)
	}
}

func TestQuoteIdentFollowsTheExportersRegex(t *testing.T) {
	cases := map[string]string{
		"abc": "abc", "Abc_1": "Abc_1", "_x": "_x", "1abc": `"1abc"`, "a b": `"a b"`, `a"b`: `"a""b"`,
		"abc\n":  "abc\n", // re.match's $ also matches before a final newline
		"ſtatus": "ſtatus", "Kelvin_K": "Kelvin_K", "é": `"é"`, "": `""`,
	}
	for in, want := range cases {
		if got := quoteIdent(in); got != want {
			t.Errorf("quoteIdent(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestColumnLetters(t *testing.T) {
	for n, want := range map[int]string{1: "A", 26: "Z", 27: "AA", 52: "AZ", 702: "ZZ", 703: "AAA"} {
		if got := colLetter(n); got != want {
			t.Errorf("colLetter(%d) = %s, want %s", n, got, want)
		}
	}
}

func TestJSONStringsAreEscapedAsPythonEscapesThem(t *testing.T) {
	var buf bytes.Buffer
	w := bufio.NewWriter(&buf)
	writeJSONString(w, "\u00e9\U0001D518\x7f\x1f\"\\\n\r\t\b\f/")
	w.Flush()
	// json.dumps: lowercase hex, a surrogate pair above U+FFFF, DEL left as it is.
	want := `"\u00e9\ud835\udd18` + "\x7f" + `\u001f\"\\\n\r\t\b\f/"`
	if got := buf.String(); got != want {
		t.Fatalf("got %q want %q", got, want)
	}
}
