// Package preprocess is preprocess_node's cleaning (preprocess_tables) for the engine: dedupe,
// drop fully-null columns, fill nulls by inferred type, coerce dates, rename, drop skip fields.
package preprocess

import (
	"math"
	"math/big"
	"strconv"
	"strings"
)

// IsPandasNumeric is `pd.to_numeric(non_null)` not raising — lib.maybe_convert_numeric with
// errors="raise" — for a column's non-null strings.
func IsPandasNumeric(values []string) bool {
	for _, v := range values {
		if !pandasNumeric(v) {
			return false
		}
	}
	return true
}

// pandasNumeric: an empty string is a NaN; anything else must floatify, and a value floatify
// reads as an integer must also fit int(value) in [-2^63, 2^64-1] ("Integer out of range.").
func pandasNumeric(s string) bool {
	if s == "" {
		return true
	}
	c := s
	if i := strings.IndexByte(c, 0); i >= 0 {
		c = c[:i] // floatify reads the UTF-8 bytes as a C string
	}
	ok, maybeInt := floatify(c)
	if !ok {
		return false
	}
	if !maybeInt {
		return true
	}
	if c != s {
		return false // int() sees the NUL and raises
	}
	n, ok := new(big.Int).SetString(strings.Trim(s, " \t\n\v\f\r"), 10)
	if !ok {
		return false
	}
	return n.Cmp(minInt64) >= 0 && n.Cmp(maxUint64) <= 0
}

var (
	minInt64  = big.NewInt(math.MinInt64)
	maxUint64 = new(big.Int).SetUint64(math.MaxUint64)
)

// floatify is pandas' parse_helper floatify: precise_xstrtod over the whole string (ASCII
// whitespace around it allowed), else exactly inf / -inf / +inf / infinity / ±infinity in any
// case.
func floatify(s string) (ok, maybeInt bool) {
	end, mi, rangeErr := preciseXstrtod(s)
	if !rangeErr && end == len(s) {
		return true, mi
	}
	l := strings.ToLower(s) // strcasecmp is ASCII; a non-ASCII letter never matches anyway
	switch len(s) {
	case 3:
		return l == "inf", false
	case 4:
		return l == "-inf" || l == "+inf", false
	case 8:
		return l == "infinity", false
	case 9:
		return l == "-infinity" || l == "+infinity", false
	}
	return false, false
}

func isSpaceASCII(c byte) bool {
	return c == ' ' || c == '\t' || c == '\n' || c == '\v' || c == '\f' || c == '\r'
}

func isDigitASCII(c byte) bool { return c >= '0' && c <= '9' }

// pow10 is the e[] table precise_xstrtod scales by (the correctly rounded doubles 1e0..1e308).
var pow10 = func() []float64 {
	out := make([]float64, 309)
	for i := range out {
		out[i], _ = strconv.ParseFloat("1e"+strconv.Itoa(i), 64)
	}
	return out
}()

// preciseXstrtod is pandas' precise_xstrtod(str, &end, '.', 'e', '\0', skip_trailing=1): where the
// number ends, whether it could be an integer, and whether it overflowed (ERANGE, or no digits).
func preciseXstrtod(s string) (end int, maybeInt, rangeErr bool) {
	const maxDigits = 17
	maybeInt = true
	p := 0
	at := func(i int) byte {
		if i < len(s) {
			return s[i]
		}
		return 0
	}
	for isSpaceASCII(at(p)) {
		p++
	}
	negative := false
	switch at(p) {
	case '-':
		negative = true
		p++
	case '+':
		p++
	}
	number := 0.0
	exponent, numDigits, numDecimals := 0, 0, 0
	for isDigitASCII(at(p)) {
		if numDigits < maxDigits {
			number = number*10 + float64(at(p)-'0')
			numDigits++
		} else {
			exponent++
		}
		p++
	}
	if at(p) == '.' {
		maybeInt = false
		p++
		for numDigits < maxDigits && isDigitASCII(at(p)) {
			number = number*10 + float64(at(p)-'0')
			p++
			numDigits++
			numDecimals++
		}
		if numDigits >= maxDigits {
			for isDigitASCII(at(p)) {
				p++
			}
		}
		exponent -= numDecimals
	}
	if numDigits == 0 {
		return p, maybeInt, true
	}
	if negative {
		number = -number
	}
	if c := at(p); c == 'e' || c == 'E' {
		maybeInt = false
		p++
		neg := false
		switch at(p) {
		case '-':
			neg = true
			p++
		case '+':
			p++
		}
		digits, n := 0, 0
		for digits < maxDigits && isDigitASCII(at(p)) {
			n = n*10 + int(at(p)-'0')
			digits++
			p++
		}
		if neg {
			exponent -= n
		} else {
			exponent += n
		}
		if digits == 0 {
			p-- // un-consume the e (or the sign after it: the C code steps back one)
		}
	}
	switch {
	case exponent > 308:
		return p, maybeInt, true
	case exponent > 0:
		number *= pow10[exponent]
	case exponent < -308:
		if exponent < -616 {
			number = 0
		} else {
			number /= pow10[-308-exponent]
			number /= pow10[308]
		}
	default:
		number /= pow10[-exponent]
	}
	if math.IsInf(number, 0) {
		rangeErr = true
	}
	for isSpaceASCII(at(p)) {
		p++
	}
	return p, maybeInt, rangeErr
}
