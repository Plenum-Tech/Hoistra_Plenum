package coerce

import (
	"fmt"
	"time"
)

// isoDT is a datetime as CPython's C datetime.fromisoformat builds it.
type isoDT struct {
	y, mo, d, h, mi, s, us int
	hasTZ                  bool
	tzSec, tzUS            int // the offset: tzSec seconds plus tzUS microseconds (both signed)
}

func at(b []byte, i int) byte {
	if i < 0 || i >= len(b) {
		return 0
	}
	return b[i]
}

func isDigit(c byte) bool { return c >= '0' && c <= '9' }

func parseDigits(b []byte, p, n int) (int, bool) {
	v := 0
	for i := 0; i < n; i++ {
		c := at(b, p+i)
		if !isDigit(c) {
			return 0, false
		}
		v = v*10 + int(c-'0')
	}
	return v, true
}

// findSeparator is _find_isoformat_datetime_separator (Modules/_datetimemodule.c, 3.12).
func findSeparator(b []byte) int {
	n := len(b)
	if n == 7 {
		return 7
	}
	if at(b, 4) == '-' {
		if at(b, 5) == 'W' {
			if n < 8 {
				return -1
			}
			if n > 8 && at(b, 8) == '-' {
				if n == 9 {
					return -1
				}
				if n > 10 && isDigit(at(b, 10)) {
					return 8
				}
				return 10
			}
			return 8
		}
		return 10
	}
	if at(b, 4) == 'W' {
		idx := 7
		for ; idx < n; idx++ {
			if !isDigit(at(b, idx)) {
				break
			}
		}
		if idx < 9 {
			return idx
		}
		if idx%2 == 0 {
			return 7
		}
		return 8
	}
	return 8
}

// parseISODate is parse_isoformat_date: reads from the start of b; length bounds the ISO-week day.
func parseISODate(b []byte, length int) (y, m, d int, ok bool) {
	p := 0
	y, ok = parseDigits(b, p, 4)
	if !ok {
		return
	}
	p += 4
	usesSep := at(b, p) == '-'
	if usesSep {
		p++
	}
	if at(b, p) == 'W' {
		p++
		week, good := parseDigits(b, p, 2)
		if !good {
			return 0, 0, 0, false
		}
		p += 2
		day := 1
		if p < length {
			if usesSep {
				if at(b, p) != '-' {
					return 0, 0, 0, false
				}
				p++
			}
			dd, good := parseDigits(b, p, 1)
			if !good {
				return 0, 0, 0, false
			}
			day = dd
		}
		return isoToYMD(y, week, day)
	}
	m, ok = parseDigits(b, p, 2)
	if !ok {
		return
	}
	p += 2
	if usesSep {
		if at(b, p) != '-' {
			return 0, 0, 0, false
		}
		p++
	}
	d, ok = parseDigits(b, p, 2)
	return
}

func isLeap(y int) bool { return y%4 == 0 && (y%100 != 0 || y%400 == 0) }

func isoToYMD(year, week, day int) (int, int, int, bool) {
	if year < 1 || year > 9999 {
		return 0, 0, 0, false
	}
	if week <= 0 || week >= 53 {
		out := true
		if week == 53 {
			first := int(time.Date(year, 1, 1, 0, 0, 0, 0, time.UTC).Weekday()) // Sunday=0
			// C: first_weekday = (ordinal + 6) % 7 with Monday=0 → Thursday=3, Wednesday=2
			mondayBased := (first + 6) % 7
			if mondayBased == 3 || (mondayBased == 2 && isLeap(year)) {
				out = false
			}
		}
		if out {
			return 0, 0, 0, false
		}
	}
	if day <= 0 || day >= 8 {
		return 0, 0, 0, false
	}
	jan4 := time.Date(year, 1, 4, 0, 0, 0, 0, time.UTC)
	offset := (int(jan4.Weekday()) + 6) % 7 // days since Monday
	monday := jan4.AddDate(0, 0, -offset)
	t := monday.AddDate(0, 0, (week-1)*7+day-1)
	return t.Year(), int(t.Month()), t.Day(), true
}

var fracCorrection = [5]int{100000, 10000, 1000, 100, 10}

// parseHHMMSSFF is parse_hh_mm_ss_ff. It returns rv: 0 at the end of the string, 1 when more
// follows, negative on failure.
func parseHHMMSSFF(b []byte, p, pEnd int) (h, mi, s, us, rv int) {
	vals := [3]*int{&h, &mi, &s}
	hasSep := true
	i := 0
	for ; i < 3; i++ {
		v, ok := parseDigits(b, p, 2)
		if !ok {
			return 0, 0, 0, 0, -3
		}
		*vals[i] = v
		p += 2
		c := at(b, p)
		p++
		if i == 0 {
			hasSep = c == ':'
		}
		if p >= pEnd {
			if c != 0 {
				return h, mi, s, us, 1
			}
			return h, mi, s, us, 0
		}
		if hasSep && c == ':' {
			continue
		}
		if c == '.' || c == ',' {
			break
		}
		if !hasSep {
			p--
			continue
		}
		return 0, 0, 0, 0, -4
	}
	toParse := pEnd - p
	if toParse >= 6 {
		toParse = 6
	}
	if toParse <= 0 {
		return 0, 0, 0, 0, -3
	}
	v, ok := parseDigits(b, p, toParse)
	if !ok {
		return 0, 0, 0, 0, -3
	}
	us = v
	p += toParse
	if toParse < 6 {
		us *= fracCorrection[toParse-1]
	}
	for isDigit(at(b, p)) {
		p++
	}
	if at(b, p) != 0 {
		return h, mi, s, us, 1
	}
	return h, mi, s, us, 0
}

// parseISOTime is parse_isoformat_time over b (the text after the separator).
func parseISOTime(b []byte) (h, mi, s, us int, hasTZ bool, tzSec, tzUS int, ok bool) {
	pEnd := len(b)
	tz := 0
	for {
		c := at(b, tz)
		if c == 'Z' || c == '+' || c == '-' {
			break
		}
		tz++
		if tz >= pEnd {
			break
		}
	}
	h, mi, s, us, rv := parseHHMMSSFF(b, 0, tz)
	if rv < 0 {
		return
	}
	if tz == pEnd {
		if rv == 1 {
			return
		}
		return h, mi, s, us, false, 0, 0, true
	}
	if at(b, tz) == 'Z' {
		if at(b, tz+1) != 0 {
			return
		}
		return h, mi, s, us, true, 0, 0, true
	}
	sign := 1
	if at(b, tz) == '-' {
		sign = -1
	}
	th, tm, ts, tus, rv2 := parseHHMMSSFF(b, tz+1, pEnd)
	if rv2 != 0 {
		return
	}
	return h, mi, s, us, true, sign * (th*3600 + tm*60 + ts), sign * tus, true
}

func validDate(y, m, d int) bool {
	if y < 1 || y > 9999 || m < 1 || m > 12 || d < 1 {
		return false
	}
	return time.Date(y, time.Month(m), d, 0, 0, 0, 0, time.UTC).Day() == d
}

// datetimeFromISO is datetime.fromisoformat (C implementation, CPython 3.12).
func datetimeFromISO(str string) (isoDT, bool) {
	var out isoDT
	if len([]rune(str)) < 7 {
		return out, false
	}
	b := []byte(str)
	sep := findSeparator(b)
	if sep < 0 {
		return out, false
	}
	y, m, d, ok := parseISODate(b, sep)
	if !ok {
		return out, false
	}
	out.y, out.mo, out.d = y, m, d
	if len(b) > sep {
		h, mi, s, us, hasTZ, tzSec, tzUS, ok := parseISOTime(b[sep+1:])
		if !ok {
			return out, false
		}
		out.h, out.mi, out.s, out.us, out.hasTZ, out.tzSec, out.tzUS = h, mi, s, us, hasTZ, tzSec, tzUS
	}
	if !validDate(out.y, out.mo, out.d) || out.h > 23 || out.mi > 59 || out.s > 59 || out.us > 999999 {
		return out, false
	}
	if out.hasTZ {
		total := int64(out.tzSec)*1_000_000 + int64(out.tzUS)
		if total <= -86_400_000_000 || total >= 86_400_000_000 {
			return out, false
		}
	}
	return out, true
}

// dateFromISO is date.fromisoformat (C implementation, CPython 3.12).
func dateFromISO(str string) (int, int, int, bool) {
	b := []byte(str)
	if n := len(b); n != 7 && n != 8 && n != 10 {
		return 0, 0, 0, false
	}
	y, m, d, ok := parseISODate(b, len(b))
	if !ok || !validDate(y, m, d) {
		return 0, 0, 0, false
	}
	return y, m, d, true
}

// isoformat is datetime.isoformat().
func (t isoDT) isoformat() string {
	out := fmt.Sprintf("%04d-%02d-%02dT%02d:%02d:%02d", t.y, t.mo, t.d, t.h, t.mi, t.s)
	if t.us != 0 {
		out += fmt.Sprintf(".%06d", t.us)
	}
	if t.hasTZ {
		off := int64(t.tzSec)*1_000_000 + int64(t.tzUS)
		sign := "+"
		if off < 0 {
			sign, off = "-", -off
		}
		hh := off / 3_600_000_000
		rem := off % 3_600_000_000
		mm := rem / 60_000_000
		rem %= 60_000_000
		out += fmt.Sprintf("%s%02d:%02d", sign, hh, mm)
		if rem != 0 {
			out += fmt.Sprintf(":%02d", rem/1_000_000)
			if us := rem % 1_000_000; us != 0 {
				out += fmt.Sprintf(".%06d", us)
			}
		}
	}
	return out
}
