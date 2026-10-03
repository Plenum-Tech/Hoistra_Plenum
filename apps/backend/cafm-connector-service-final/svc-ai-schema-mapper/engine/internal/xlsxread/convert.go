package xlsxread

import (
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"

	"hoistra/engine/internal/pystr"
)

// rustFloat is Rust's f64::from_str, which calamine parses <v> with: Go's ParseFloat less the
// syntax only Go has (hex floats, digit underscores); an out-of-range value is ±inf, not an error.
func rustFloat(s string) (float64, bool) {
	if s == "" || strings.ContainsAny(s, "_xX") {
		return 0, false
	}
	f, err := strconv.ParseFloat(s, 64)
	if err != nil {
		if ne, ok := err.(*strconv.NumError); ok && ne.Err == strconv.ErrRange {
			return f, true
		}
		return 0, false
	}
	return f, true
}

// floatCell is pandas' _convert_cell for a float: one with no fraction becomes an int (of any
// size), NaN and infinity raise, as int() does.
func floatCell(f float64) pcell {
	switch {
	case math.IsNaN(f):
		return pcell{text: "cannot convert float NaN to integer", lvl: 2}
	case math.IsInf(f, 0):
		return pcell{text: "cannot convert float infinity to integer", lvl: 2}
	case f == math.Trunc(f):
		return pcell{text: intText(f), kind: pInt}
	}
	return pcell{text: pystr.FloatRepr(f), kind: pFloat}
}

func intText(f float64) string {
	if math.Abs(f) < 1<<62 {
		return strconv.FormatInt(int64(f), 10)
	}
	i, _ := new(big.Float).SetFloat64(f).Int(nil)
	return i.String()
}

const msPerDay = 86_400_000

// rustRound is Rust's `x.round() as i64`: half away from zero, saturating, NaN → 0.
func rustRound(x float64) int64 {
	r := math.Round(x)
	switch {
	case math.IsNaN(r):
		return 0
	case r >= math.MaxInt64:
		return math.MaxInt64
	case r <= math.MinInt64:
		return math.MinInt64 + 1 // chrono's smallest duration
	}
	return int64(r)
}

// civil is a proleptic Gregorian date (days since 1970-01-01 ↔ year, month, day).
func civil(days int64) (y int64, m, d int) {
	z := days + 719468
	era := z / 146097
	if z < 0 && z%146097 != 0 {
		era--
	}
	doe := z - era*146097
	yoe := (doe - doe/1460 + doe/36524 - doe/146096) / 365
	y = yoe + era*400
	doy := doe - (365*yoe + yoe/4 - yoe/100)
	mp := (5*doy + 2) / 153
	d = int(doy - (153*mp+2)/5 + 1)
	if mp < 10 {
		m = int(mp + 3)
	} else {
		m = int(mp - 9)
	}
	if m <= 2 {
		y++
	}
	return y, m, d
}

func daysFromCivil(y int64, m, d int) int64 {
	if m <= 2 {
		y--
	}
	era := y / 400
	if y < 0 && y%400 != 0 {
		era--
	}
	yoe := y - era*400
	mm := int64(m)
	if m > 2 {
		mm -= 3
	} else {
		mm += 9
	}
	doy := (153*mm+2)/5 + int64(d) - 1
	doe := yoe*365 + yoe/4 - yoe/100 + doy
	return era*146097 + doe - 719468
}

var excelEpoch = daysFromCivil(1899, 12, 30)

func floorDiv(a, b int64) int64 {
	q := a / b
	if (a%b != 0) && ((a < 0) != (b < 0)) {
		q--
	}
	return q
}

// moment is a date and a time of day to the millisecond.
type moment struct {
	y        int64
	mo, d    int
	msOfDay  int64
	micro    int // for an ISO value, which carries microseconds
	fromISO  bool
	hasMicro bool
}

// asDatetime is calamine's ExcelDateTime::as_datetime: 1899-12-30 plus the serial in days (1904
// serials shifted by 1462 days, the 1900 leap-year bug kept below day 60), rounded to the
// millisecond; false when chrono's dates cannot hold it.
func asDatetime(value float64, date1904 bool) (moment, bool) {
	f := value
	if date1904 {
		f += 1462.0
	}
	if !(f >= 60.0) {
		f += 1.0
	}
	ms := rustRound(f * msPerDay)
	days := floorDiv(ms, msPerDay)
	y, mo, d := civil(excelEpoch + days)
	if y < -262144 || y > 262143 {
		return moment{}, false
	}
	return moment{y: y, mo: mo, d: d, msOfDay: ms - days*msPerDay}, true
}

func (m moment) clock() (h, mi, s, us int) {
	t := m.msOfDay
	h, mi, s = int(t/3_600_000), int(t/60_000%60), int(t/1000%60)
	us = int(t%1000) * 1000
	if m.fromISO {
		us = m.micro
	}
	return
}

func (m moment) timeText() string {
	h, mi, s, us := m.clock()
	if us != 0 {
		return fmt.Sprintf("%02d:%02d:%02d.%06d", h, mi, s, us)
	}
	return fmt.Sprintf("%02d:%02d:%02d", h, mi, s)
}

// stampText is str(pd.Timestamp(v)) for calamine's date or datetime.
func (m moment) stampText() string {
	return fmt.Sprintf("%04d-%02d-%02d ", m.y, m.mo, m.d) + m.timeText()
}

// dateCell is python-calamine's reading of a date-formatted number, then pandas': below 1.0 a
// time, otherwise a Timestamp — or the float itself when Python's datetime cannot hold it
// (after year 9999).
func dateCell(f float64, date1904 bool) pcell {
	m, ok := asDatetime(f, date1904)
	if f < 1.0 {
		if !ok {
			return floatCell(f)
		}
		return pcell{text: m.timeText(), kind: pTime}
	}
	if !ok || m.y > 9999 {
		return floatCell(f)
	}
	return pcell{text: m.stampText(), kind: pStamp}
}

// durationCell is a duration-formatted number: calamine's milliseconds, then pyo3's timedelta
// (days beyond an int32 read as 2147483647; |days| over 999999999 raise), then pd.Timedelta
// (microseconds must fit an int64).
func durationCell(f float64) pcell {
	ms := rustRound(f * msPerDay)
	nd := ms / msPerDay
	rem := ms - nd*msPerDay
	secs := rem / 1000
	us := (rem - secs*1000) * 1000
	d := nd
	if d > math.MaxInt32 || d < math.MinInt32 {
		d = math.MaxInt32
	}
	if us < 0 || us >= 1_000_000 {
		q := floorDiv(us, 1_000_000)
		secs += q
		us -= q * 1_000_000
	}
	if secs < 0 || secs >= 86400 {
		q := floorDiv(secs, 86400)
		d += q
		secs -= q * 86400
	}
	if d < -999_999_999 || d > 999_999_999 {
		return pcell{text: fmt.Sprintf("days=%d; must have magnitude <= 999999999", d), lvl: 1}
	}
	total := new(big.Int).Mul(big.NewInt(d), big.NewInt(86_400_000_000))
	total.Add(total, big.NewInt(secs*1_000_000+us))
	if !total.IsInt64() {
		return pcell{text: "Python int too large to convert to C long", lvl: 2}
	}
	return pcell{text: deltaText(d*msPerDay + secs*1000 + us/1000), kind: pDelta}
}

// deltaText is str(pd.Timedelta): "D days HH:MM:SS[.ffffff]", a negative one as whole days below
// plus a positive time of day ("-1 days +12:00:00").
func deltaText(ms int64) string {
	days := floorDiv(ms, msPerDay)
	rem := ms - days*msPerDay
	sign := " "
	if ms < 0 {
		sign = " +"
	}
	s := fmt.Sprintf("%d days%s%02d:%02d:%02d", days, sign, rem/3_600_000, rem/60_000%60, rem/1000%60)
	if f := rem % 1000; f != 0 {
		s += fmt.Sprintf(".%03d000", f)
	}
	return s
}

// isoCell is a t="d" cell: python-calamine parses the text as a chrono datetime
// ("Y-M-DTH:M:S[.f]"), else a date, else a time ("H:M[:S[.f]]"), else keeps the text.
func isoCell(text string) pcell {
	s := strings.TrimSpace(text)
	if i := strings.IndexByte(s, 'T'); i > 0 {
		if y, mo, d, ok := isoDate(s[:i]); ok {
			if t, ok := isoTime(s[i+1:], true); ok {
				t.y, t.mo, t.d = y, mo, d
				return pcell{text: t.stampText(), kind: pStamp}
			}
		}
		return pcell{text: text}
	}
	if y, mo, d, ok := isoDate(s); ok {
		return pcell{text: moment{y: y, mo: mo, d: d}.stampText(), kind: pStamp}
	}
	if t, ok := isoTime(s, false); ok {
		return pcell{text: t.timeText(), kind: pTime}
	}
	return pcell{text: text}
}

// number reads 1..max digits (and spaces before them, as chrono does); rest is what follows.
func number(s string, max int) (n int, rest string, ok bool) {
	s = strings.TrimLeft(s, " \t\n\r")
	i := 0
	for i < len(s) && i < max && s[i] >= '0' && s[i] <= '9' {
		n = n*10 + int(s[i]-'0')
		i++
	}
	return n, s[i:], i > 0
}

func lit(s string, c byte) (string, bool) {
	s = strings.TrimLeft(s, " \t\n\r")
	if s == "" || s[0] != c {
		return s, false
	}
	return s[1:], true
}

func isoDate(s string) (int64, int, int, bool) {
	sign := int64(1)
	t := strings.TrimLeft(s, " \t\n\r")
	width := 4
	if t != "" && (t[0] == '+' || t[0] == '-') {
		if t[0] == '-' {
			sign = -1
		}
		t, width = t[1:], 9
	}
	y, t, ok := number(t, width)
	if !ok {
		return 0, 0, 0, false
	}
	var mo, d int
	if t, ok = lit(t, '-'); !ok {
		return 0, 0, 0, false
	}
	if mo, t, ok = number(t, 2); !ok {
		return 0, 0, 0, false
	}
	if t, ok = lit(t, '-'); !ok {
		return 0, 0, 0, false
	}
	if d, t, ok = number(t, 2); !ok || strings.TrimSpace(t) != "" {
		return 0, 0, 0, false
	}
	yy := sign * int64(y)
	if mo < 1 || mo > 12 || d < 1 || d > daysIn(yy, mo) {
		return 0, 0, 0, false
	}
	return yy, mo, d, true
}

func daysIn(y int64, m int) int {
	switch m {
	case 2:
		if y%4 == 0 && (y%100 != 0 || y%400 == 0) {
			return 29
		}
		return 28
	case 4, 6, 9, 11:
		return 30
	}
	return 31
}

// isoTime reads "H:M:S[.f]" (seconds optional unless needSecs); a leap second reads as :59, and a
// fraction is cut to microseconds.
func isoTime(s string, needSecs bool) (moment, bool) {
	h, t, ok := number(s, 2)
	if !ok {
		return moment{}, false
	}
	if t, ok = lit(t, ':'); !ok {
		return moment{}, false
	}
	mi, t, ok := number(t, 2)
	if !ok {
		return moment{}, false
	}
	sec, us := 0, 0
	if t2, ok := lit(t, ':'); ok {
		if sec, t, ok = number(t2, 2); !ok {
			return moment{}, false
		}
		if strings.HasPrefix(t, ".") {
			digits := 0
			t = t[1:]
			for digits < len(t) && t[digits] >= '0' && t[digits] <= '9' {
				digits++
			}
			if digits == 0 {
				return moment{}, false
			}
			frac := t[:digits] + "000000"
			us, _ = strconv.Atoi(frac[:6])
			t = t[digits:]
		}
	} else if needSecs {
		return moment{}, false
	}
	if strings.TrimSpace(t) != "" || h > 23 || mi > 59 || sec > 60 {
		return moment{}, false
	}
	if sec == 60 {
		sec = 59
	}
	return moment{msOfDay: int64(h)*3_600_000 + int64(mi)*60_000 + int64(sec)*1000, micro: us, fromISO: true}, true
}
