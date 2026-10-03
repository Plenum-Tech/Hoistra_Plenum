package preprocess

import (
	"strings"
	"testing"
)

// Every expectation is what pandas 2.3.3 answered (scratchpad probe of _infer_column_type and
// _contains_dates, 1 Oct 2026).

func TestNumericIsWhatPdToNumericAccepts(t *testing.T) {
	cases := map[string]bool{
		"5":                               true,
		" 5 ":                             true,
		"\x095":                           true,
		"5\x0a":                           true,
		"\x0b5\x0c":                       true,
		"+5":                              true,
		"-5":                              true,
		"--5":                             false,
		".5":                              true,
		"5.":                              true,
		".":                               false,
		"-.5":                             true,
		"1e3":                             true,
		"1E3":                             true,
		"1e":                              false,
		"1e+":                             false,
		"1e-3":                            true,
		"1.5e+300":                        true,
		"1e308":                           true,
		"1e309":                           false,
		"10e308":                          false,
		"1e-400":                          true,
		"1e400":                           false,
		"inf":                             true,
		"INF":                             true,
		"-inf":                            true,
		"+inf":                            true,
		"Infinity":                        true,
		"-Infinity":                       true,
		"+infinity":                       true,
		" inf":                            false,
		"inf ":                            false,
		"nan":                             false,
		"NaN":                             false,
		"1,000":                           false,
		"1_000":                           false,
		"0x10":                            false,
		"\u0663":                          false,
		"\uff15":                          false,
		"007":                             true,
		"-0":                              true,
		"00":                              true,
		"123456789012345678901234567890":  false,
		"-123456789012345678901234567890": false,
		"18446744073709551615":            true,
		"18446744073709551616":            false,
		"-9223372036854775808":            true,
		"-9223372036854775809":            false,
		"1234567890123456789012345678.0":  true,
		"1e5x":                            false,
		"5 6":                             false,
		"":                                true,
		"  ":                              false,
		"\u00a05":                         false,
		"5\u00a0":                         false,
		"True":                            false,
		"1.2.3":                           false,
		"+":                               false,
		"-":                               false,
		"e5":                              false,
		"5e5e5":                           false,
	}
	for v, want := range cases {
		if got := IsPandasNumeric([]string{v}); got != want {
			t.Errorf("%q: %v, want %v", v, got, want)
		}
	}
	long := strings.Repeat("1", 400)
	for v, want := range map[string]bool{long: false, long + ".0": false, "0." + strings.Repeat("0", 400) + "1": true} {
		if got := IsPandasNumeric([]string{v}); got != want {
			t.Errorf("%d chars: %v, want %v", len(v), got, want)
		}
	}
	if !IsPandasNumeric([]string{"1", "", "2.5"}) || IsPandasNumeric([]string{"1", "x"}) {
		t.Error("a column is numeric only when every value is")
	}
}

func TestADateShapeIsWhatContainsDatesMatches(t *testing.T) {
	cases := map[string]bool{
		"2025-01-01":  true,
		" 2025-01-01": true,
		"2025-01-01 ": true,
		"2025-1-1":    true,
		"1-2-3":       true,
		"12345-1-1":   false,
		"2025-001-01": false,
		"1/2/3":       true,
		"2025/01/01":  true,
		"2025.01.01":  false,
		"\u0662\u0660\u0662\u0665-\u0660\u0661-\u0660\u0661": true,
		"2025-01-01\x0a":   true,
		"2025-01-01T00:00": false,
		"x":                false,
		"2025-01-01-01":    false,
		"1-1-12345":        false,
	}
	for v, want := range cases {
		vals := make([]string, 10)
		for i := range vals {
			vals[i] = v
		}
		if got := containsDates(vals); got != want {
			t.Errorf("%q: %v, want %v", v, got, want)
		}
	}
	// at least half of the first ten
	five := []string{"2025-01-01", "x", "2025-01-02", "y", "2025-01-03", "z", "2025-01-04", "w", "2025-01-05", "v", "u", "t"}
	four := []string{"2025-01-01", "x", "y", "z", "2025-01-02", "w", "v", "2025-01-03", "s", "2025-01-04", "2025-01-05"}
	if !containsDates(five) || containsDates(four) || !containsDates([]string{"2025-01-01"}) {
		t.Error("the 50 % bar")
	}
}
