package preprocess

import (
	"regexp"
	"strconv"
	"strings"
	"sync"
	"unicode"

	"hoistra/engine/internal/pystr"
)

// containsDatesRE is _contains_dates' pattern; Python's \d is any Unicode decimal digit.
var containsDatesRE = regexp.MustCompile(`^\p{Nd}{1,4}[-/]\p{Nd}{1,2}[-/]\p{Nd}{1,4}$`)

// containsDates is preprocess_node._contains_dates over a column's non-null values: at least half
// of the first ten, stripped, look like d-m-y / y-m-d with - or /.
func containsDates(nonNull []string) bool {
	n := min(len(nonNull), 10)
	matches := 0
	for _, v := range nonNull[:n] {
		if containsDatesRE.MatchString(pystr.Strip(v)) {
			matches++
		}
	}
	return float64(matches) >= float64(n)*0.5
}

// ── values and stamps ─────────────────────────────────────────────────────────────────────────

const (
	vNone uint8 = iota
	vStr
	vInt0 // the numeric fill's 0
)

// dval is a cell as _coerce_dates sees it after the null fill.
type dval struct {
	kind uint8
	s    string
}

// stamp is a datetime64[ns] value.
type stamp struct {
	y, mo, d, h, mi, s int
	ns                 int64 // within the second
}

// iso is Timestamp.isoformat(): microseconds when there are any, nanoseconds after them when
// there are any.
func (t stamp) iso() string {
	b := make([]byte, 0, 32)
	b = appendInt(b, t.y, 4)
	b = append(b, '-')
	b = appendInt(b, t.mo, 2)
	b = append(b, '-')
	b = appendInt(b, t.d, 2)
	b = append(b, 'T')
	b = appendInt(b, t.h, 2)
	b = append(b, ':')
	b = appendInt(b, t.mi, 2)
	b = append(b, ':')
	b = appendInt(b, t.s, 2)
	us, ns := int(t.ns/1000), int(t.ns%1000)
	if us != 0 {
		b = append(b, '.')
		b = appendInt(b, us, 6)
	}
	if ns != 0 {
		if us == 0 {
			b = append(b, ".000000"...)
		}
		b = appendInt(b, ns, 3)
	}
	return string(b)
}

func appendInt(b []byte, n, width int) []byte {
	s := []byte{}
	for n > 0 || len(s) < width {
		s = append(s, byte('0'+n%10))
		n /= 10
	}
	for i := len(s) - 1; i >= 0; i-- {
		b = append(b, s[i])
	}
	return b
}

func leap(y int) bool { return y%4 == 0 && (y%100 != 0 || y%400 == 0) }

func daysIn(y, m int) int {
	switch m {
	case 2:
		if leap(y) {
			return 29
		}
		return 28
	case 4, 6, 9, 11:
		return 30
	}
	return 31
}

// inNsBounds is check_dts_bounds for datetime64[ns]: 1677-09-21 00:12:43.145224192 to
// 2262-04-11 23:47:16.854775807.
func (t stamp) inNsBounds() bool {
	lo := stamp{1677, 9, 21, 0, 12, 43, 145224192}
	hi := stamp{2262, 4, 11, 23, 47, 16, 854775807}
	return !t.less(lo) && !hi.less(t)
}

func (t stamp) less(o stamp) bool {
	a := [7]int64{int64(t.y), int64(t.mo), int64(t.d), int64(t.h), int64(t.mi), int64(t.s), t.ns}
	b := [7]int64{int64(o.y), int64(o.mo), int64(o.d), int64(o.h), int64(o.mi), int64(o.s), o.ns}
	for i := range a {
		if a[i] != b[i] {
			return a[i] < b[i]
		}
	}
	return false
}

// ── strptime: CPython's _strptime regexes, as pandas' array_strptime uses them ──────────────────

// pySpace is Python's \s for str patterns (str.isspace).
const pySpace = `[\t\n\v\f\r \x{1c}-\x{1f}\x{85}\x{a0}\x{1680}\x{2000}-\x{200a}\x{2028}\x{2029}\x{202f}\x{205f}\x{3000}]`

var directiveRE = map[byte]string{
	'd': `(?P<d>3[0-1]|[1-2]\p{Nd}|0[1-9]|[1-9]| [1-9])`,
	'm': `(?P<m>1[0-2]|0[1-9]|[1-9])`,
	'Y': `(?P<Y>\p{Nd}\p{Nd}\p{Nd}\p{Nd})`,
	'y': `(?P<y>\p{Nd}\p{Nd})`,
	'H': `(?P<H>2[0-3]|[0-1]\p{Nd}|\p{Nd})`,
	'I': `(?P<I>1[0-2]|0[1-9]|[1-9])`,
	'M': `(?P<M>[0-5]\p{Nd}|\p{Nd})`,
	'S': `(?P<S>6[0-1]|[0-5]\p{Nd}|\p{Nd})`,
	'f': `(?P<f>[0-9]{1,9})`,
	'p': `(?P<p>am|pm)`,
	'b': `(?P<b>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)`,
	'B': `(?P<B>september|february|november|december|january|october|august|march|april|june|july|may)`,
}

var (
	shortMonths = []string{"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"}
	longMonths  = []string{"january", "february", "march", "april", "may", "june", "july", "august",
		"september", "october", "november", "december"}
	strptimeCache sync.Map // format → *regexp.Regexp (nil: a directive the engine does not read)
)

// strptimeRegex is TimeRE().compile(format): whitespace in the format matches any run of
// whitespace, other characters themselves, directives their patterns; case-insensitive.
func strptimeRegex(format string) *regexp.Regexp {
	if re, ok := strptimeCache.Load(format); ok {
		return re.(*regexp.Regexp)
	}
	var sb strings.Builder
	sb.WriteString(`(?i)^`)
	rs := []rune(format)
	ok := true
	for i := 0; i < len(rs); i++ {
		r := rs[i]
		switch {
		case r == '%' && i+1 < len(rs):
			i++
			if rs[i] == '%' {
				sb.WriteString(`%`)
				continue
			}
			p, known := directiveRE[byte(rs[i])]
			if !known || rs[i] > 0x7f {
				ok = false
			}
			sb.WriteString(p)
		case pystr.IsSpace(r):
			for i+1 < len(rs) && pystr.IsSpace(rs[i+1]) {
				i++
			}
			sb.WriteString(pySpace + `+`)
		default:
			sb.WriteString(regexp.QuoteMeta(string(r)))
		}
	}
	var re *regexp.Regexp
	if ok {
		re = regexp.MustCompile(sb.String())
	}
	strptimeCache.Store(format, re)
	return re
}

func pyInt(s string) int {
	n := 0
	for _, r := range strings.TrimSpace(s) {
		d, _ := pystr.DigitValue(r)
		n = n*10 + d
	}
	return n
}

// strptime is one value through array_strptime(format, exact=True): the stamp, or ok=false (NaT).
// leap is set for a seconds field of 60 or 61, which the engine leaves to Python.
func strptime(s, format string) (t stamp, ok, leapSec bool) {
	re := strptimeRegex(format)
	if re == nil {
		return stamp{}, false, true
	}
	m := re.FindStringSubmatchIndex(s)
	if m == nil || m[1] != len(s) {
		return stamp{}, false, false
	}
	year, month, day := 1900, 1, 1
	hour, minute, second := 0, 0, 0
	var ns int64
	ampm := ""
	if i := re.SubexpIndex("p"); i >= 0 && m[2*i] >= 0 {
		ampm = strings.ToLower(s[m[2*i]:m[2*i+1]])
	}
	for i, name := range re.SubexpNames() {
		if name == "" || m[2*i] < 0 {
			continue
		}
		g := s[m[2*i]:m[2*i+1]]
		switch name {
		case "y":
			year = pyInt(g)
			if year <= 68 {
				year += 2000
			} else {
				year += 1900
			}
		case "Y":
			year = pyInt(g)
		case "m":
			month = pyInt(g)
		case "b":
			month = index(shortMonths, strings.ToLower(g)) + 1
		case "B":
			month = index(longMonths, strings.ToLower(g)) + 1
		case "d":
			day = pyInt(g)
		case "H":
			hour = pyInt(g)
		case "I":
			hour = pyInt(g)
			if ampm == "" || ampm == "am" {
				if hour == 12 {
					hour = 0
				}
			} else if hour != 12 {
				hour += 12
			}
		case "M":
			minute = pyInt(g)
		case "S":
			second = pyInt(g)
		case "f":
			f := g + strings.Repeat("0", 9-len(g))
			v, _ := strconv.ParseInt(f, 10, 64)
			ns = v
		}
	}
	if second >= 60 {
		return stamp{}, false, true
	}
	if year < 1 || year > 9999 || month < 1 || month > 12 || day < 1 || day > daysIn(year, month) {
		return stamp{}, false, false // date(year, month, day) raises
	}
	t = stamp{year, month, day, hour, minute, second, ns}
	if !t.inNsBounds() {
		return stamp{}, false, false
	}
	return t, true, false
}

func index(list []string, s string) int {
	for i, x := range list {
		if x == s {
			return i
		}
	}
	return -1
}

// ── the ISO 8601 parser (string_to_dts with no format) ─────────────────────────────────────────

func cSpace(c byte) bool {
	return c == ' ' || c == '\t' || c == '\n' || c == '\v' || c == '\f' || c == '\r'
}

// parseISO is pandas' parse_iso_8601_datetime with no format to follow: a date (YYYY, YYYY-MM,
// YYYY-MM-DD with - . / \ or space between, or YYYYMMDD), then optionally T or space and a time
// (HH[:MM[:SS[.fraction]]] or HHMMSS), whitespace around. tz is set for a value with a time zone,
// which the engine leaves to Python.
func parseISO(s string) (t stamp, ok, tz bool) {
	i, n := 0, len(s)
	at := func(k int) byte {
		if k < n {
			return s[k]
		}
		return 0
	}
	digit := func(k int) bool { return isDigitASCII(at(k)) }
	for i < n && cSpace(s[i]) {
		i++
	}
	neg := false
	if at(i) == '-' {
		neg = true
		i++
	}
	if i >= n {
		return stamp{}, false, false
	}
	if !(digit(i) && digit(i+1) && digit(i+2) && digit(i+3)) {
		return stamp{}, false, false
	}
	t.y = int(s[i]-'0')*1000 + int(s[i+1]-'0')*100 + int(s[i+2]-'0')*10 + int(s[i+3]-'0')
	i += 4
	if neg {
		t.y = -t.y
	}
	t.mo, t.d = 1, 1
	if i == n {
		return t.checked()
	}
	hasSep := false
	var sep byte
	if !digit(i) {
		if !strings.ContainsRune(`-./\ `, rune(s[i])) {
			return stamp{}, false, false
		}
		hasSep, sep = true, s[i]
		i++
		if i == n || !digit(i) {
			return stamp{}, false, false
		}
	}
	if !digit(i) {
		return stamp{}, false, false
	}
	t.mo = int(s[i] - '0')
	i++
	if digit(i) {
		t.mo = t.mo*10 + int(s[i]-'0')
		i++
	} else if !hasSep {
		return stamp{}, false, false
	}
	if t.mo < 1 || t.mo > 12 {
		return stamp{}, false, false
	}
	if i == n {
		if !hasSep {
			return stamp{}, false, false // YYYYMM is not this format
		}
		return t.checked()
	}
	if hasSep {
		if s[i] != sep || i == n-1 {
			return stamp{}, false, false
		}
		i++
	}
	if !digit(i) {
		return stamp{}, false, false
	}
	t.d = int(s[i] - '0')
	i++
	if digit(i) {
		t.d = t.d*10 + int(s[i]-'0')
		i++
	} else if !hasSep {
		return stamp{}, false, false
	}
	if t.d < 1 || t.d > daysIn(t.y, t.mo) {
		return stamp{}, false, false
	}
	if i == n {
		return t.checked()
	}
	if (s[i] != 'T' && s[i] != ' ') || i == n-1 {
		return stamp{}, false, false
	}
	i++
	// hours
	if !digit(i) {
		return stamp{}, false, false
	}
	t.h = int(s[i] - '0')
	i++
	hour2 := false
	if digit(i) {
		hour2 = true
		t.h = t.h*10 + int(s[i]-'0')
		i++
		if t.h >= 24 {
			return stamp{}, false, false
		}
	}
	if i == n {
		if !hour2 {
			return stamp{}, false, false
		}
		return t.checked()
	}
	hmsSep := false
	switch {
	case s[i] == ':':
		hmsSep = true
		i++
		if i == n || !digit(i) {
			return stamp{}, false, false
		}
	case !digit(i):
		if !hour2 {
			return stamp{}, false, false
		}
		return t.zone(s, i)
	}
	// minutes
	t.mi = int(s[i] - '0')
	i++
	if digit(i) {
		t.mi = t.mi*10 + int(s[i]-'0')
		i++
		if t.mi >= 60 {
			return stamp{}, false, false
		}
	} else if !hmsSep {
		return stamp{}, false, false
	}
	if i == n {
		return t.checked()
	}
	switch {
	case hmsSep && s[i] == ':':
		i++
		if i == n || !digit(i) {
			return stamp{}, false, false
		}
	case !hmsSep && digit(i):
	default:
		return t.zone(s, i)
	}
	// seconds
	t.s = int(s[i] - '0')
	i++
	if digit(i) {
		t.s = t.s*10 + int(s[i]-'0')
		i++
		if t.s >= 60 {
			return stamp{}, false, false
		}
	} else if !hmsSep {
		return stamp{}, false, false
	}
	if i < n && s[i] == '.' {
		i++
		// up to 6 digits of microseconds, 6 of picoseconds, 6 of attoseconds
		var us, ps int64
		for k := 0; k < 6; k++ {
			us *= 10
			if digit(i) {
				us += int64(s[i] - '0')
				i++
			}
		}
		if digit(i) {
			for k := 0; k < 6; k++ {
				ps *= 10
				if digit(i) {
					ps += int64(s[i] - '0')
					i++
				}
			}
			if digit(i) {
				for k := 0; k < 6 && digit(i); k++ {
					i++
				}
			}
		}
		t.ns = us*1000 + ps/1000
	}
	return t.zone(s, i)
}

// zone is parse_iso_8601_datetime's parse_timezone: whitespace, then the end — or a zone.
func (t stamp) zone(s string, i int) (stamp, bool, bool) {
	for i < len(s) && cSpace(s[i]) {
		i++
	}
	if i == len(s) {
		return t.checked()
	}
	if s[i] == 'Z' || s[i] == '+' || s[i] == '-' {
		return stamp{}, false, true
	}
	return stamp{}, false, false
}

func (t stamp) checked() (stamp, bool, bool) {
	if !t.inNsBounds() {
		return stamp{}, false, false
	}
	return t, true, false
}

var (
	letterRun          = regexp.MustCompile(`\pL+`)
	negOffsetAfterTime = regexp.MustCompile(`\d:\d\d(?::\d\d(?:[.,]\d+)?)?\s*-\s*\d\d`)
)

// carriesZone: a value dateutil may read with a time zone — a Z, UTC or GMT token, or a numeric
// offset (any "+", or a "-" right after a time).
func carriesZone(s string) bool {
	if strings.ContainsRune(s, '+') {
		return true
	}
	for _, w := range letterRun.FindAllString(s, -1) {
		switch strings.ToLower(w) {
		case "z", "utc", "gmt":
			return true
		}
	}
	return negOffsetAfterTime.MatchString(s)
}

// hasNumber: a character dateutil's lexer could read as a digit (str.isdigit is within these).
func hasNumber(s string) bool {
	for _, r := range s {
		if unicode.IsNumber(r) {
			return true
		}
	}
	return false
}

// ── _coerce_dates ───────────────────────────────────────────────────────────────────────────

var dateFormats = []string{"%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y"}

// isoPrefixRE is the fallback's ISO test (Python \s and \d, and $ before a final newline).
var isoPrefixRE = regexp.MustCompile(`^` + pySpace + `*\p{Nd}{4}-\p{Nd}{1,2}-\p{Nd}{1,2}(?:[ T]|\n?$)`)

var natStrings = map[string]bool{"NaT": true, "nat": true, "NAT": true, "nan": true, "NaN": true, "NAN": true}

// dateHints are the name fragments that send a column to the date coercion.
var dateHints = []string{"date", "time", "created", "due", "completed"}

func dateHinted(name string) bool {
	low := pystr.Lower(name)
	for _, h := range dateHints {
		if strings.Contains(low, h) {
			return true
		}
	}
	return false
}

type parsedColumn struct {
	stamps []stamp
	ok     []bool
	count  int
}

// text is how array_strptime reads a value: a string as it is, the numeric fill's 0 as "0".
func (v dval) text() string {
	if v.kind == vInt0 {
		return "0"
	}
	return v.s
}

func parseColumn(vals []dval, one func(string) (stamp, bool, bool)) (parsedColumn, bool) {
	p := parsedColumn{stamps: make([]stamp, len(vals)), ok: make([]bool, len(vals))}
	for i, v := range vals {
		if v.kind == vNone {
			continue
		}
		s := v.text()
		if v.kind == vStr && (s == "" || natStrings[s]) {
			continue
		}
		t, ok, deferIt := one(s)
		if deferIt {
			return p, true
		}
		if ok {
			p.stamps[i], p.ok[i] = t, true
			p.count++
		}
	}
	return p, false
}

// coerceDates is preprocess_node._coerce_dates on one column after the null fill: the five
// formats, the first that parses every non-null value committed; else the ISO 8601 parser (when
// 80 % look ISO) or the day-first guess; the best of them committed when it keeps 80 % of the
// non-null values ("" and the 0 fill count as non-null, as notna() counts them). deferred: the
// column needs what only pandas does (dateutil, "now"/"today", time zones, leap seconds) and is
// Python's to coerce.
func coerceDates(vals []dval) (out []dval, coerced, deferred bool) {
	nonNull := 0
	for _, v := range vals {
		if v.kind != vNone {
			nonNull++
		}
		if v.kind == vStr && (v.s == "now" || v.s == "today") {
			return nil, false, true
		}
	}
	if nonNull == 0 {
		return nil, false, false
	}
	var best *parsedColumn
	commit := func(p parsedColumn) []dval {
		out := make([]dval, len(vals))
		for i := range vals {
			if p.ok[i] {
				out[i] = dval{kind: vStr, s: p.stamps[i].iso()}
			}
		}
		return out
	}
	for _, f := range dateFormats {
		p, def := parseColumn(vals, func(s string) (stamp, bool, bool) { return strptime(s, f) })
		if def {
			return nil, false, true
		}
		if p.count >= nonNull {
			return commit(p), true, false
		}
		if best == nil || p.count > best.count {
			if p.count > 0 {
				best = &p
			}
		}
	}
	iso := 0
	for _, v := range vals {
		if v.kind != vNone && isoPrefixRE.MatchString(v.text()) {
			iso++
		}
	}
	var p parsedColumn
	if float64(iso)/float64(nonNull) >= 0.8 {
		var def bool
		p, def = parseColumn(vals, parseISO)
		if def {
			return nil, false, true
		}
	} else {
		first := -1
		for i, v := range vals {
			if v.kind == vNone || (v.kind == vStr && (v.s == "" || natStrings[v.s])) {
				continue
			}
			first = i
			break
		}
		if first >= 0 {
			format, ok := "", false
			if vals[first].kind == vStr {
				format, ok = guessDayfirst(vals[first].s)
			}
			if !ok {
				// pandas parses each value with dateutil. A value with no digit cannot come out a
				// date in range (no year, no time: dateutil's default year 1 is out of bounds), so
				// when even every value that has one could not reach 80 %, nothing is committed.
				possible := 0
				for _, v := range vals {
					if v.kind == vInt0 || (v.kind == vStr && hasNumber(v.s)) {
						possible++
					}
				}
				bestCount := 0
				if best != nil {
					bestCount = best.count
				}
				// Unless a value carries a time zone: then pandas may hand back an array of
				// objects, in which a blank is the text "NaT" and counts toward the 80 % — left
				// to Python to decide.
				zoned := false
				for _, v := range vals {
					if v.kind == vStr && carriesZone(v.s) {
						zoned = true
						break
					}
				}
				if !zoned && float64(max(bestCount, possible)) < float64(nonNull)*0.8 {
					return nil, false, false
				}
				return nil, false, true
			}
			var def bool
			p, def = parseColumn(vals, func(s string) (stamp, bool, bool) { return strptime(s, format) })
			if def {
				return nil, false, true
			}
		}
	}
	if p.count > 0 && (best == nil || p.count > best.count) {
		best = &p
	}
	if best != nil && float64(best.count) >= float64(nonNull)*0.8 {
		return commit(*best), true, false
	}
	return nil, false, false
}
