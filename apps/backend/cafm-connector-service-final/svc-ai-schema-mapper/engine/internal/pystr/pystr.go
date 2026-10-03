// Package pystr reproduces the Python str/int/float/Decimal behaviour the migration pipeline
// relies on, so the engine reads and writes values exactly as the Python code it replaces.
package pystr

import (
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"
	"sync"
	"unicode"
	"unicode/utf8"

	"golang.org/x/text/cases"
	"golang.org/x/text/language"
)

// IsSpace is Python's str.isspace() for one character (it differs from unicode.IsSpace on
// U+001C..U+001F, which Python counts as whitespace).
func IsSpace(r rune) bool {
	switch r {
	case '\t', '\n', '\v', '\f', '\r', 0x1c, 0x1d, 0x1e, 0x1f, ' ', 0x85, 0xa0, 0x1680,
		0x2028, 0x2029, 0x202f, 0x205f, 0x3000:
		return true
	}
	return r >= 0x2000 && r <= 0x200a
}

// Strip is Python's str.strip() with no argument.
func Strip(s string) string { return strings.TrimFunc(s, IsSpace) }

// Len is Python's len(str): code points, not bytes.
func Len(s string) int { return utf8.RuneCountInString(s) }

var lowerPool = sync.Pool{New: func() any { c := cases.Lower(language.Und); return &c }}

func isASCII(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] >= utf8.RuneSelf {
			return false
		}
	}
	return true
}

// Lower is Python's str.lower(): full Unicode lowercase mapping, final sigma included.
func Lower(s string) string {
	if isASCII(s) {
		return strings.ToLower(s)
	}
	c := lowerPool.Get().(*cases.Caser)
	defer lowerPool.Put(c)
	return c.String(s)
}

var folder = cases.Fold()

// Casefold is Python's str.casefold(): full Unicode case folding (ß → ss).
func Casefold(s string) string {
	if isASCII(s) {
		return strings.ToLower(s)
	}
	return folder.String(s)
}

// digitValue is the value of a Unicode decimal digit (category Nd), as Python's int() reads it.
func digitValue(r rune) (int, bool) {
	if r >= '0' && r <= '9' {
		return int(r - '0'), true
	}
	if r < utf8.RuneSelf || !unicode.IsDigit(r) {
		return 0, false
	}
	start := r
	for unicode.IsDigit(start - 1) {
		start--
	}
	return int(r-start) % 10, true
}

// DigitValue is the value of a Unicode decimal digit, or false.
func DigitValue(r rune) (int, bool) { return digitValue(r) }

// digitPart reads Python's `digitpart ::= digit (["_"] digit)*` into ASCII digits.
func digitPart(s string) (string, bool) {
	if s == "" {
		return "", false
	}
	var b strings.Builder
	prevUnderscore := true
	for _, r := range s {
		if r == '_' {
			if prevUnderscore {
				return "", false
			}
			prevUnderscore = true
			continue
		}
		d, ok := digitValue(r)
		if !ok {
			return "", false
		}
		b.WriteByte(byte('0' + d))
		prevUnderscore = false
	}
	if prevUnderscore {
		return "", false
	}
	return b.String(), true
}

func splitSign(s string) (string, bool) {
	if s != "" && (s[0] == '+' || s[0] == '-') {
		return s[1:], s[0] == '-'
	}
	return s, false
}

// ParseInt is Python's int(s) for a base-10 string.
func ParseInt(s string) (*big.Int, bool) {
	body, neg := splitSign(Strip(s))
	digits, ok := digitPart(body)
	if !ok {
		return nil, false
	}
	v, ok := new(big.Int).SetString(digits, 10)
	if !ok {
		return nil, false
	}
	if neg {
		v.Neg(v)
	}
	return v, true
}

// asciiNumber reads Python's float/Decimal number body: [digitpart] "." digitpart | digitpart ["."],
// then an optional exponent. It returns the ASCII digits before and after the point, the exponent
// text (signed, ASCII digits) and whether the body was well formed.
func asciiNumber(s string) (intPart, fracPart, exp string, ok bool) {
	mant := s
	if i := strings.IndexAny(s, "eE"); i >= 0 {
		mant = s[:i]
		e := s[i+1:]
		sign := ""
		if e != "" && (e[0] == '+' || e[0] == '-') {
			sign, e = e[:1], e[1:]
		}
		d, good := digitPart(e)
		if !good {
			return "", "", "", false
		}
		exp = sign + d
	}
	whole, frac, hasDot := strings.Cut(mant, ".")
	if whole != "" {
		d, good := digitPart(whole)
		if !good {
			return "", "", "", false
		}
		intPart = d
	}
	if hasDot && frac != "" {
		d, good := digitPart(frac)
		if !good {
			return "", "", "", false
		}
		fracPart = d
	}
	if intPart == "" && fracPart == "" {
		return "", "", "", false
	}
	return intPart, fracPart, exp, true
}

// ParseFloat is Python's float(s).
func ParseFloat(s string) (float64, bool) {
	body, neg := splitSign(Strip(s))
	sign := 1.0
	if neg {
		sign = -1
	}
	switch strings.ToLower(body) {
	case "inf", "infinity":
		return math.Inf(int(sign)), true
	case "nan":
		return math.NaN(), true
	}
	ip, fp, exp, ok := asciiNumber(body)
	if !ok {
		return 0, false
	}
	txt := ip
	if txt == "" {
		txt = "0"
	}
	if fp != "" {
		txt += "." + fp
	}
	if exp != "" {
		txt += "e" + exp
	}
	f, err := strconv.ParseFloat(txt, 64)
	if err != nil {
		if ne, isNum := err.(*strconv.NumError); !isNum || ne.Err != strconv.ErrRange {
			return 0, false
		}
	}
	return sign * f, true
}

// ParseDecimal is Python's Decimal(s), returning str(Decimal(s)).
func ParseDecimal(s string) (string, bool) {
	body, neg := splitSign(Strip(s))
	sign := ""
	if neg {
		sign = "-"
	}
	low := strings.ToLower(body)
	switch {
	case low == "inf" || low == "infinity":
		return sign + "Infinity", true
	case strings.HasPrefix(low, "snan") || strings.HasPrefix(low, "nan"):
		head, rest := "NaN", body[3:]
		if strings.HasPrefix(low, "snan") {
			head, rest = "sNaN", body[4:]
		}
		payload := ""
		if rest != "" {
			d, ok := digitPart(rest)
			if !ok {
				return "", false
			}
			payload = strings.TrimLeft(d, "0")
		}
		return sign + head + payload, true
	}
	ip, fp, exp, ok := asciiNumber(body)
	if !ok {
		return "", false
	}
	e := 0
	if exp != "" {
		v, ok := new(big.Int).SetString(exp, 10)
		if !ok || !v.IsInt64() {
			return "", false
		}
		e = int(v.Int64())
	}
	coef := strings.TrimLeft(ip+fp, "0")
	if coef == "" {
		coef = "0"
	}
	e -= len(fp)
	return sign + sciString(coef, e), true
}

// sciString is Decimal.__str__ (to-scientific-string) for a finite coefficient and exponent.
func sciString(coef string, exp int) string {
	leftdigits := exp + len(coef)
	dotplace := 1
	if exp <= 0 && leftdigits > -6 {
		dotplace = leftdigits
	}
	var intpart, fracpart string
	switch {
	case dotplace <= 0:
		intpart, fracpart = "0", "."+strings.Repeat("0", -dotplace)+coef
	case dotplace >= len(coef):
		intpart = coef + strings.Repeat("0", dotplace-len(coef))
	default:
		intpart, fracpart = coef[:dotplace], "."+coef[dotplace:]
	}
	out := intpart + fracpart
	if leftdigits != dotplace {
		out += fmt.Sprintf("E%+d", leftdigits-dotplace)
	}
	return out
}

// FloatRepr is Python's repr(float): shortest round-trip digits, fixed notation for decimal
// exponents -4..15, scientific with a signed, at-least-two-digit exponent otherwise.
func FloatRepr(f float64) string {
	switch {
	case math.IsNaN(f):
		return "nan"
	case math.IsInf(f, 1):
		return "inf"
	case math.IsInf(f, -1):
		return "-inf"
	case f == 0:
		if math.Signbit(f) {
			return "-0.0"
		}
		return "0.0"
	}
	s := strconv.FormatFloat(f, 'e', -1, 64)
	mant, expStr, _ := strings.Cut(s, "e")
	exp, _ := strconv.Atoi(expStr)
	neg := strings.HasPrefix(mant, "-")
	mant = strings.TrimPrefix(mant, "-")
	digits := strings.Replace(mant, ".", "", 1)
	var out string
	switch {
	case exp < -4 || exp >= 16:
		out = digits[:1]
		if len(digits) > 1 {
			out += "." + digits[1:]
		}
		sign := "+"
		if exp < 0 {
			sign, exp = "-", -exp
		}
		out += fmt.Sprintf("e%s%02d", sign, exp)
	case exp >= 0:
		if len(digits) <= exp+1 {
			out = digits + strings.Repeat("0", exp+1-len(digits)) + ".0"
		} else {
			out = digits[:exp+1] + "." + digits[exp+1:]
		}
	default:
		out = "0." + strings.Repeat("0", -exp-1) + digits
	}
	if neg {
		out = "-" + out
	}
	return out
}
