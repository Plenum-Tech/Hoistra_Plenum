package preprocess

import (
	"regexp"
	"strconv"
	"strings"
)

// The day-first fallback of _coerce_dates: pd.to_datetime(values, dayfirst=True) guesses one
// format from the first value (guess_datetime_format: dateutil parses it, then each of its tokens
// is matched to a directive) and parses every value with it. The engine guesses for the value
// shapes below, exactly as pandas does; for any other first value the column is left to Python.

var (
	numericDMY = regexp.MustCompile(`^([0-9]{1,2})([/.-])([0-9]{1,2})([/.-])([0-9]{4})(?: ([0-9]{1,2}):([0-9]{1,2})(?::([0-9]{1,2})(?:\.([0-9]+))?)?)?$`)
	numericYMD = regexp.MustCompile(`^([0-9]{4})([/.-])([0-9]{1,2})([/.-])([0-9]{1,2})(?: ([0-9]{1,2}):([0-9]{1,2})(?::([0-9]{1,2})(?:\.([0-9]+))?)?)?$`)
	dayMonYear = regexp.MustCompile(`^([0-9]{1,2})( |-)([A-Za-z]+)( |-)([0-9]{4})$`)
	monDayYear = regexp.MustCompile(`^([A-Za-z]+) ([0-9]{1,2}), ([0-9]{4})$`)
)

// dateutil's month names, as its parser reads them (any case).
var dateutilMonths = map[string]int{"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
	"apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8,
	"sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11,
	"dec": 12, "december": 12}

var monthTitle = []string{"January", "February", "March", "April", "May", "June", "July", "August",
	"September", "October", "November", "December"}

// guessDayfirst is guess_datetime_format(s, dayfirst=True) for the shapes the engine reads.
func guessDayfirst(s string) (string, bool) {
	var tokens []string
	var t stamp
	var us int
	if m := numericDMY.FindStringSubmatch(s); m != nil && m[2] == m[4] {
		a, b, y := atoiASCII(m[1]), atoiASCII(m[3]), atoiASCII(m[5])
		var day, month int
		switch {
		case a > 31:
			return "", false // dateutil reads the first number as a year
		case a > 12 || b <= 12:
			day, month = a, b
		default:
			day, month = b, a
		}
		t = stamp{y: y, mo: month, d: day}
		tokens = []string{m[1], m[2], m[3], m[4], m[5]}
		if !withTime(&t, &us, &tokens, m[6], m[7], m[8], m[9]) {
			return "", false
		}
	} else if m := numericYMD.FindStringSubmatch(s); m != nil && m[2] == m[4] {
		y, a, b := atoiASCII(m[1]), atoiASCII(m[3]), atoiASCII(m[5])
		day, month := b, a
		if b <= 12 {
			day, month = a, b // year, day, month: dateutil's day-first reading of a year-first date
		}
		t = stamp{y: y, mo: month, d: day}
		tokens = []string{m[1], m[2], m[3], m[4], m[5]}
		if !withTime(&t, &us, &tokens, m[6], m[7], m[8], m[9]) {
			return "", false
		}
	} else if m := dayMonYear.FindStringSubmatch(s); m != nil && m[2] == m[4] {
		month, ok := dateutilMonths[strings.ToLower(m[3])]
		if !ok {
			return "", false
		}
		t = stamp{y: atoiASCII(m[5]), mo: month, d: atoiASCII(m[1])}
		tokens = []string{m[1], m[2], m[3], m[4], m[5]}
	} else if m := monDayYear.FindStringSubmatch(s); m != nil {
		month, ok := dateutilMonths[strings.ToLower(m[1])]
		if !ok {
			return "", false
		}
		t = stamp{y: atoiASCII(m[3]), mo: month, d: atoiASCII(m[2])}
		tokens = []string{m[1], " ", m[2], ",", " ", m[3]}
	} else {
		return "", false
	}
	if t.y < 1000 || t.mo < 1 || t.mo > 12 || t.d < 1 || t.d > daysIn(t.y, t.mo) {
		return "", false // dateutil raises (or strftime would not print the year as written)
	}
	return matchTokens(t, us, tokens, s)
}

// withTime adds " HH:MM[:SS[.f]]" to the parse and its tokens; false when dateutil would refuse it.
func withTime(t *stamp, us *int, tokens *[]string, h, mi, sec, frac string) bool {
	if h == "" {
		return true
	}
	t.h, t.mi = atoiASCII(h), atoiASCII(mi)
	*tokens = append(*tokens, " ", h, ":", mi)
	if sec != "" {
		t.s = atoiASCII(sec)
		tok := sec
		if frac != "" {
			tok += "." + frac
			f := (frac + "000000")[:6] // dateutil keeps microseconds, cut
			*us = atoiASCII(f)
		}
		*tokens = append(*tokens, ":", tok)
	}
	return t.h < 24 && t.mi < 60 && t.s < 60
}

func atoiASCII(s string) int {
	n, _ := strconv.Atoi(s)
	return n
}

type attr struct {
	names   []string
	format  string
	padding int
}

// the directives pandas tries, day first (dayfirst=True); %z/%Z are skipped for a naive value.
var guessAttrs = []attr{
	{[]string{"day"}, "%d", 2},
	{[]string{"year", "month", "day", "hour", "minute", "second"}, "%Y%m%d%H%M%S", 0},
	{[]string{"year", "month", "day", "hour", "minute"}, "%Y%m%d%H%M", 0},
	{[]string{"year", "month", "day", "hour"}, "%Y%m%d%H", 0},
	{[]string{"year", "month", "day"}, "%Y%m%d", 0},
	{[]string{"hour", "minute", "second"}, "%H%M%S", 0},
	{[]string{"hour", "minute"}, "%H%M", 0},
	{[]string{"year"}, "%Y", 0},
	{[]string{"month"}, "%B", 0},
	{[]string{"month"}, "%b", 0},
	{[]string{"month"}, "%m", 2},
	{[]string{"hour"}, "%H", 2},
	{[]string{"minute"}, "%M", 2},
	{[]string{"second"}, "%S", 2},
	{[]string{"second", "microsecond"}, "%S.%f", 0},
	{[]string{"day_of_week"}, "%a", 0},
	{[]string{"day_of_week"}, "%A", 0},
	{[]string{"meridiem"}, "%p", 0},
}

var decimalToken = regexp.MustCompile(`[0-9]+\.[0-9]+`)

// fillToken is pandas' _fill_token.
func fillToken(tok string, padding int) string {
	if decimalToken.FindStringIndex(tok) == nil {
		return zfill(tok, padding)
	}
	parts := strings.Split(tok, ".")
	sec := atoiASCII(parts[0])
	ns := (parts[1] + "000000000")[:6]
	return twoDigits(sec) + "." + ns
}

func zfill(s string, width int) string {
	if len(s) >= width {
		return s
	}
	return strings.Repeat("0", width-len(s)) + s
}

func twoDigits(n int) string {
	if n < 10 {
		return "0" + strconv.Itoa(n)
	}
	return strconv.Itoa(n)
}

var weekdays = []string{"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}

// weekday is Monday=0 … Sunday=6 (Zeller-free: days since 0001-01-01, a Monday).
func weekday(y, m, d int) int {
	days := 0
	for yy := 1; yy < y; yy++ {
		if leap(yy) {
			days += 366
		} else {
			days += 365
		}
	}
	for mm := 1; mm < m; mm++ {
		days += daysIn(y, mm)
	}
	days += d - 1
	return days % 7
}

// strftime prints the directives a guessed format can hold (C locale).
func strftime(t stamp, us int, format string) string {
	var sb strings.Builder
	for i := 0; i < len(format); i++ {
		c := format[i]
		if c != '%' || i+1 == len(format) {
			sb.WriteByte(c)
			continue
		}
		i++
		switch format[i] {
		case 'd':
			sb.WriteString(twoDigits(t.d))
		case 'm':
			sb.WriteString(twoDigits(t.mo))
		case 'Y':
			sb.WriteString(strconv.Itoa(t.y))
		case 'H':
			sb.WriteString(twoDigits(t.h))
		case 'M':
			sb.WriteString(twoDigits(t.mi))
		case 'S':
			sb.WriteString(twoDigits(t.s))
		case 'f':
			sb.WriteString(zfill(strconv.Itoa(us), 6))
		case 'B':
			sb.WriteString(monthTitle[t.mo-1])
		case 'b':
			sb.WriteString(monthTitle[t.mo-1][:3])
		case 'A':
			sb.WriteString(weekdays[weekday(t.y, t.mo, t.d)])
		case 'a':
			sb.WriteString(weekdays[weekday(t.y, t.mo, t.d)][:3])
		case 'p':
			if t.h < 12 {
				sb.WriteString("AM")
			} else {
				sb.WriteString("PM")
			}
		default:
			sb.WriteByte('%')
			sb.WriteByte(format[i])
		}
	}
	return sb.String()
}

// matchTokens is the rest of guess_datetime_format: give each token the first directive that
// prints it, require a year, a month and a day, refuse a number left over, and accept the format
// only when it parses the value and prints it back as written.
func matchTokens(t stamp, us int, tokens []string, original string) (string, bool) {
	guess := make([]string, len(tokens))
	found := map[string]bool{}
	for _, a := range guessAttrs {
		taken := false
		for _, n := range a.names {
			taken = taken || found[n]
		}
		if taken {
			continue
		}
		printed := strftime(t, us, a.format)
		for i := range tokens {
			filled := fillToken(tokens[i], a.padding)
			if guess[i] == "" && filled == printed {
				guess[i] = a.format
				tokens[i] = filled
				for _, n := range a.names {
					found[n] = true
				}
				break
			}
		}
	}
	if !(found["year"] && found["month"] && found["day"]) {
		return "", false
	}
	var out strings.Builder
	for i, g := range guess {
		if g != "" {
			out.WriteString(g)
			continue
		}
		if _, err := strconv.ParseFloat(strings.TrimSpace(tokens[i]), 64); err == nil && strings.TrimSpace(tokens[i]) != "" {
			return "", false // a number nothing claimed: the guess is wrong
		}
		out.WriteString(tokens[i])
	}
	format := out.String()
	if strings.Contains(format, "%p") && strings.Contains(format, "%H") {
		format = strings.Replace(format, "%H", "%I", 1)
	}
	if _, ok, _ := strptime(original, format); !ok {
		return "", false
	}
	if strftime(t, us, format) != strings.Join(tokens, "") {
		return "", false
	}
	return format, true
}
