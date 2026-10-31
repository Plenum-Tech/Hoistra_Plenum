package pystr

import (
	"math"
	"testing"
)

// Every expected value below is Python 3.12's own answer (worker image, 1 Oct 2026).

func TestFloatReprMatchesPython(t *testing.T) {
	cases := []struct {
		f    float64
		want string
	}{
		{0, "0.0"}, {1, "1.0"}, {-1.5, "-1.5"}, {0.1, "0.1"}, {1e16, "1e+16"}, {1e15, "1000000000000000.0"},
		{0.0001, "0.0001"}, {0.00001, "1e-05"}, {123456789.123, "123456789.123"},
		{1.7976931348623157e308, "1.7976931348623157e+308"}, {5e-324, "5e-324"}, {2.5e-7, "2.5e-07"},
		{1234567890123456789.0, "1.2345678901234568e+18"}, {0.30000000000000004, "0.30000000000000004"},
		{100, "100.0"}, {1e22, "1e+22"}, {9.999999999999999e15, "1e+16"},
	}
	for _, c := range cases {
		if got := FloatRepr(c.f); got != c.want {
			t.Errorf("FloatRepr(%v) = %q, want %q", c.f, got, c.want)
		}
	}
	if FloatRepr(math.Copysign(0, -1)) != "-0.0" || FloatRepr(math.NaN()) != "nan" ||
		FloatRepr(math.Inf(-1)) != "-inf" || FloatRepr(math.Inf(1)) != "inf" {
		t.Error("specials")
	}
}

func TestStripUsesPythonsWhitespace(t *testing.T) {
	cases := map[string]string{
		"\x1c　 a b \x1f": "a b",
		"​ a":            "​ a", // ZWSP is not whitespace to Python
		"\u0085x\u00a0":  "x",
		" \t5\n":         "5",
	}
	for in, want := range cases {
		if got := Strip(in); got != want {
			t.Errorf("Strip(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestLowerAndCasefoldMatchPython(t *testing.T) {
	if got := Lower("İSTANBUL ß Σ"); got != "i̇stanbul ß σ" {
		t.Errorf("Lower: %q", got)
	}
	if got := Lower("ΣΑΣ"); got != "σας" {
		t.Errorf("final sigma: %q", got)
	}
	if got := Lower("ǅ"); got != "ǆ" {
		t.Errorf("titlecase digraph: %q", got)
	}
	if got := Casefold("Straße ΣΑΣ İ"); got != "strasse σασ i̇" {
		t.Errorf("Casefold: %q", got)
	}
	if got := Casefold("ﬃ"); got != "ffi" {
		t.Errorf("Casefold ligature: %q", got)
	}
}

func TestParseIntFollowsPythonsInt(t *testing.T) {
	cases := map[string]bool{"5": true, "+5": true, "-0": true, "1_000": true, "1__0": false, "_1": false,
		"٣": true, "1.0": false, "0x10": false, "": false, " 5": true, "5 ": true, "1_": false,
		"٣٣": true, "５": true}
	for s, ok := range cases {
		if _, got := ParseInt(s); got != ok {
			t.Errorf("ParseInt(%q) ok=%v want %v", s, got, ok)
		}
	}
	if v, _ := ParseInt("1_000"); v.String() != "1000" {
		t.Errorf("1_000 -> %s", v)
	}
	if v, _ := ParseInt("٣"); v.String() != "3" {
		t.Errorf("Arabic-Indic three -> %s", v)
	}
	if v, _ := ParseInt("-99999999999999999999"); v.String() != "-99999999999999999999" {
		t.Errorf("big -> %s", v)
	}
}

func TestParseFloatFollowsPythonsFloat(t *testing.T) {
	cases := map[string]bool{"1e5": true, ".5": true, "5.": true, "inf": true, "-Infinity": true, "nan": true,
		"1_0.5": true, "1e": false, "e5": false, "1,0": false, "0x1p3": false, "+.5e-3": true,
		"1_000.000_1": true, "1._5": false, "infinity": true, "NaN": true, "-nan": true,
		"٣.5": true, "1e1_0": true, "1_e10": false, "  1.5 ": true}
	for s, ok := range cases {
		if _, got := ParseFloat(s); got != ok {
			t.Errorf("ParseFloat(%q) ok=%v want %v", s, got, ok)
		}
	}
	if v, _ := ParseFloat("1e400"); !math.IsInf(v, 1) {
		t.Errorf("1e400 -> %v (Python gives inf)", v)
	}
	if v, _ := ParseFloat("٣.5"); v != 3.5 {
		t.Errorf("Arabic-Indic 3.5 -> %v", v)
	}
}

func TestParseDecimalGivesPythonsCanonicalString(t *testing.T) {
	cases := map[string]string{
		"1e5": "1E+5", "inf": "Infinity", "-Infinity": "-Infinity", "nan": "NaN", "sNaN": "sNaN",
		"1_0": "10", "1.50": "1.50", "-0": "-0", "٣": "3", "+.5": "0.5", " 1": "1",
		"1.5e-7": "1.5E-7", "0.0000001": "1E-7", "100": "100", "1E2": "1E+2", "-0.00": "-0.00",
		"123456789012345678901234567890": "123456789012345678901234567890", "1e1000000": "1E+1000000",
		"007": "7", "0.000001": "0.000001",
	}
	for in, want := range cases {
		got, ok := ParseDecimal(in)
		if !ok || got != want {
			t.Errorf("ParseDecimal(%q) = %q,%v want %q", in, got, ok, want)
		}
	}
	for _, bad := range []string{"1e", "abc", "1,0", "", "."} {
		if _, ok := ParseDecimal(bad); ok {
			t.Errorf("ParseDecimal(%q) should fail", bad)
		}
	}
}

func TestLenCountsCodePoints(t *testing.T) {
	if Len("naïve ✓") != 7 {
		t.Fatal(Len("naïve ✓"))
	}
}
