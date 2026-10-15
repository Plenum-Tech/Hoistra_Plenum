package write

import (
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"
	"unicode"

	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/coerce"
	"hoistra/engine/internal/pgschema"
	"hoistra/engine/internal/pystr"
)

type pyKind uint8

const (
	pyNone pyKind = iota
	pyStr
	pyInt
	pyBool
	pyDecimal
	pyDatetime
	pyDate
)

// pyVal is the Python object write_node hands asyncpg for one parameter, reduced to what decides
// how asyncpg encodes it (or refuses it) and how Python prints it in an error. s is the value as
// text: a str itself, an int's digits, "true"/"false", str(Decimal), or isoformat().
type pyVal struct {
	k     pyKind
	s     string
	aware bool // a datetime with a tzinfo
}

// pyOf is the object _coerce_value_for_db_type returned for a coerced value.
func pyOf(v coerce.Value) pyVal {
	switch v.K {
	case coerce.Text, coerce.JSON, coerce.UUID, coerce.Raw, coerce.Interval:
		return pyVal{k: pyStr, s: v.S}
	case coerce.Int:
		return pyVal{k: pyInt, s: v.S}
	case coerce.Bool:
		return pyVal{k: pyBool, s: v.S}
	case coerce.Decimal:
		return pyVal{k: pyDecimal, s: v.S}
	case coerce.Timestamp:
		return pyVal{k: pyDatetime, s: v.S}
	case coerce.TimestampTZ:
		return pyVal{k: pyDatetime, s: v.S, aware: true}
	case coerce.Date:
		return pyVal{k: pyDate, s: v.S}
	}
	return pyVal{}
}

// pyOfCell is a raw value the writer binds without coercing it: a system default.
func pyOfCell(c cell.Cell) pyVal {
	switch c.K {
	case cell.Str:
		return pyVal{k: pyStr, s: c.S}
	case cell.Int:
		return pyVal{k: pyInt, s: strconv.FormatInt(c.I, 10)}
	case cell.Bool:
		if c.I != 0 {
			return pyVal{k: pyBool, s: "true"}
		}
		return pyVal{k: pyBool, s: "false"}
	}
	return pyVal{}
}

// typeName is type(obj).__name__.
func (p pyVal) typeName() string {
	switch p.k {
	case pyStr:
		return "str"
	case pyInt:
		return "int"
	case pyBool:
		return "bool"
	case pyDecimal:
		return "Decimal"
	case pyDatetime:
		return "datetime"
	case pyDate:
		return "date"
	}
	return "NoneType"
}

// qualName is the C type's tp_name, which AttributeError and a few TypeErrors print.
func (p pyVal) qualName() string {
	switch p.k {
	case pyDecimal:
		return "decimal.Decimal"
	case pyDatetime:
		return "datetime.datetime"
	case pyDate:
		return "datetime.date"
	}
	return p.typeName()
}

// repr is repr(obj).
func (p pyVal) repr() string {
	switch p.k {
	case pyStr:
		return pyRepr(p.s)
	case pyInt:
		return p.s
	case pyBool:
		if p.s == "true" {
			return "True"
		}
		return "False"
	case pyDecimal:
		return "Decimal('" + p.s + "')"
	case pyDatetime:
		return reprDatetime(p.s)
	case pyDate:
		y, m, d := atoi(p.s[0:4]), atoi(p.s[5:7]), atoi(p.s[8:10])
		return fmt.Sprintf("datetime.date(%d, %d, %d)", y, m, d)
	}
	return "None"
}

func atoi(s string) int {
	n, _ := strconv.Atoi(s)
	return n
}

// pyRepr is repr(str) (CPython's unicode_repr).
func pyRepr(s string) string {
	quote := '\''
	if strings.ContainsRune(s, '\'') && !strings.ContainsRune(s, '"') {
		quote = '"'
	}
	var b strings.Builder
	b.WriteRune(quote)
	for _, r := range s {
		switch {
		case r == quote || r == '\\':
			b.WriteByte('\\')
			b.WriteRune(r)
		case r == '\t':
			b.WriteString(`\t`)
		case r == '\n':
			b.WriteString(`\n`)
		case r == '\r':
			b.WriteString(`\r`)
		case r < ' ' || r == 0x7f:
			fmt.Fprintf(&b, `\x%02x`, r)
		case r < 0x7f:
			b.WriteRune(r)
		case unicode.IsPrint(r):
			b.WriteRune(r)
		case r <= 0xff:
			fmt.Fprintf(&b, `\x%02x`, r)
		case r <= 0xffff:
			fmt.Fprintf(&b, `\u%04x`, r)
		default:
			fmt.Fprintf(&b, `\U%08x`, r)
		}
	}
	b.WriteRune(quote)
	return b.String()
}

// reprDatetime is repr(datetime) for datetime.isoformat() text.
func reprDatetime(iso string) string {
	y, mo, d := atoi(iso[0:4]), atoi(iso[5:7]), atoi(iso[8:10])
	h, mi, s := atoi(iso[11:13]), atoi(iso[14:16]), atoi(iso[17:19])
	rest := iso[19:]
	us := 0
	if strings.HasPrefix(rest, ".") {
		us, rest = atoi(rest[1:7]), rest[7:]
	}
	var out string
	switch {
	case us != 0:
		out = fmt.Sprintf("datetime.datetime(%d, %d, %d, %d, %d, %d, %d", y, mo, d, h, mi, s, us)
	case s != 0:
		out = fmt.Sprintf("datetime.datetime(%d, %d, %d, %d, %d, %d", y, mo, d, h, mi, s)
	default:
		out = fmt.Sprintf("datetime.datetime(%d, %d, %d, %d, %d", y, mo, d, h, mi)
	}
	if rest != "" {
		out += ", tzinfo=" + reprOffset(rest)
	}
	return out + ")"
}

// reprOffset is repr() of the tzinfo fromisoformat attaches: timezone.utc for a zero offset,
// otherwise timezone(timedelta(...)).
func reprOffset(off string) string {
	sign := int64(1)
	if off[0] == '-' {
		sign = -1
	}
	parts := strings.SplitN(off[1:], ":", 3)
	total := int64(atoi(parts[0]))*3_600_000_000 + int64(atoi(parts[1]))*60_000_000
	if len(parts) == 3 {
		sec := parts[2]
		frac := ""
		if i := strings.IndexByte(sec, '.'); i >= 0 {
			sec, frac = sec[:i], sec[i+1:]
		}
		total += int64(atoi(sec)) * 1_000_000
		if frac != "" {
			total += int64(atoi(frac))
		}
	}
	total *= sign
	if total == 0 {
		return "datetime.timezone.utc"
	}
	days := total / 86_400_000_000
	rem := total % 86_400_000_000
	if rem < 0 {
		days--
		rem += 86_400_000_000
	}
	var args []string
	if days != 0 {
		args = append(args, fmt.Sprintf("days=%d", days))
	}
	if secs := rem / 1_000_000; secs != 0 {
		args = append(args, fmt.Sprintf("seconds=%d", secs))
	}
	if usec := rem % 1_000_000; usec != 0 {
		args = append(args, fmt.Sprintf("microseconds=%d", usec))
	}
	return "datetime.timezone(datetime.timedelta(" + strings.Join(args, ", ") + "))"
}

func isNaNText(s string) bool { return strings.Contains(s, "NaN") }

// encode is asyncpg 0.31 binding this object to a parameter of the column's type. It returns the
// text the engine sends in its place — Postgres parses it back to the value asyncpg would have
// sent — or, when asyncpg refuses the object, its reason. A refused parameter fails the row on the
// client, before anything reaches the server.
func (p pyVal) encode(col *pgschema.Column) (any, string) {
	if p.k == pyNone {
		return nil, ""
	}
	dt := strings.ToLower(col.DataType)
	switch {
	case col.IntBits > 0:
		return p.encodeInt(col.IntBits)
	case dt == "real" || dt == "double precision":
		return p.encodeFloat(dt == "real")
	case dt == "numeric":
		return p.encodeNumeric()
	case dt == "boolean":
		if p.k == pyBool {
			return p.s, ""
		}
		return nil, fmt.Sprintf("a boolean is required (got type %s)", p.typeName())
	case dt == "uuid":
		if p.k != pyStr {
			return nil, fmt.Sprintf("'%s' object has no attribute 'bytes'", p.qualName())
		}
		return asyncpgUUID(p.s)
	case dt == "date":
		switch p.k {
		case pyDate:
			return p.s, ""
		case pyDatetime:
			return p.s[:10], ""
		}
		return nil, fmt.Sprintf("'%s' object has no attribute 'toordinal'", p.qualName())
	case dt == "timestamp without time zone":
		switch p.k {
		case pyDatetime:
			if p.aware {
				return nil, "can't subtract offset-naive and offset-aware datetimes"
			}
			return p.s, ""
		case pyDate:
			return p.s + "T00:00:00", ""
		}
		return nil, fmt.Sprintf("expected a datetime.date or datetime.datetime instance, got '%s'", p.typeName())
	case dt == "timestamp with time zone":
		switch p.k {
		case pyDatetime:
			return p.s, ""
		case pyDate:
			return p.s + "T00:00:00+00:00", ""
		}
		return nil, fmt.Sprintf("expected a datetime.date or datetime.datetime instance, got '%s'", p.typeName())
	case dt == "interval":
		return nil, fmt.Sprintf("'%s' object has no attribute 'days'", p.qualName())
	case dt == "time without time zone":
		return nil, fmt.Sprintf("'%s' object has no attribute 'hour'", p.qualName())
	case dt == "time with time zone":
		return nil, fmt.Sprintf("'%s' object has no attribute 'tzinfo'", p.qualName())
	case dt == "bytea" || dt == "bit" || dt == "bit varying":
		return nil, fmt.Sprintf("a bytes-like object is required, not '%s'", p.qualName())
	case dt == "point" || dt == "line" || dt == "lseg" || dt == "box" || dt == "path" || dt == "polygon" ||
		dt == "circle":
		if p.k == pyStr {
			return nil, "must be real number, not str"
		}
		return nil, fmt.Sprintf("'%s' object is not subscriptable", p.qualName())
	case dt == "array":
		return nil, fmt.Sprintf("a sized iterable container expected (got type '%s')", p.typeName())
	case dt == "json":
		if p.k == pyStr {
			return p.s, ""
		}
		return nil, fmt.Sprintf("descriptor 'encode' for 'str' objects doesn't apply to a '%s' object", p.qualName())
	case dt == "jsonb":
		if p.k == pyStr {
			return p.s, ""
		}
		return nil, fmt.Sprintf("'%s' object has no attribute 'encode'", p.qualName())
	}
	// Every other type takes text: character types, enums, money, xml, tsvector, network types.
	if p.k == pyStr {
		return p.s, ""
	}
	return nil, fmt.Sprintf("expected str, got %s", p.typeName())
}

func (p pyVal) encodeInt(bits int) (any, string) {
	switch p.k {
	case pyInt:
		n, ok := new(big.Int).SetString(p.s, 10)
		if !ok {
			return nil, fmt.Sprintf("invalid literal for int() with base 10: %s", pyRepr(p.s))
		}
		limit := new(big.Int).Lsh(big.NewInt(1), uint(bits-1))
		if n.Cmp(new(big.Int).Neg(limit)) < 0 || n.Cmp(limit) >= 0 {
			return nil, fmt.Sprintf("value out of int%d range", bits)
		}
		return p.s, ""
	case pyBool:
		if p.s == "true" {
			return "1", ""
		}
		return "0", ""
	}
	return nil, fmt.Sprintf("'%s' object cannot be interpreted as an integer", p.qualName())
}

func (p pyVal) encodeFloat(single bool) (any, string) {
	var f float64
	switch p.k {
	case pyInt:
		bf, _, err := big.ParseFloat(p.s, 10, 53, big.ToNearestEven)
		if err != nil {
			return nil, err.Error()
		}
		f, _ = bf.Float64()
	case pyBool:
		if p.s == "true" {
			f = 1
		}
	case pyDecimal:
		switch {
		case strings.Contains(p.s, "sNaN"):
			return nil, "cannot convert signaling NaN to float"
		case isNaNText(p.s):
			f = math.NaN()
		case p.s == "Infinity":
			f = math.Inf(1)
		case p.s == "-Infinity":
			f = math.Inf(-1)
		default:
			v, err := strconv.ParseFloat(p.s, 64) // float(Decimal): correctly rounded, ±inf / ±0 past the range
			if err != nil {
				if ne, ok := err.(*strconv.NumError); !ok || ne.Err != strconv.ErrRange {
					return nil, err.Error()
				}
			}
			f = v
		}
	default:
		return nil, fmt.Sprintf("must be real number, not %s", p.qualName())
	}
	if single {
		f32 := float32(f)
		if math.IsInf(float64(f32), 0) && !math.IsInf(f, 0) {
			return nil, "value out of float32 range"
		}
		return floatText(float64(f32), 32), ""
	}
	return floatText(f, 64), ""
}

func floatText(f float64, bits int) string {
	switch {
	case math.IsNaN(f):
		return "NaN"
	case math.IsInf(f, 1):
		return "Infinity"
	case math.IsInf(f, -1):
		return "-Infinity"
	}
	return strconv.FormatFloat(f, 'g', -1, bits)
}

func (p pyVal) encodeNumeric() (any, string) {
	switch p.k {
	case pyDecimal:
		if isNaNText(p.s) {
			return "NaN", ""
		}
		return p.s, ""
	case pyInt:
		return p.s, ""
	case pyBool:
		if p.s == "true" {
			return "1", ""
		}
		return "0", ""
	case pyStr:
		d, ok := pystr.ParseDecimal(p.s)
		if !ok {
			return nil, "[<class 'decimal.ConversionSyntax'>]"
		}
		if isNaNText(d) {
			return "NaN", ""
		}
		return d, ""
	}
	return nil, fmt.Sprintf("conversion from %s to Decimal is not supported", p.qualName())
}

// asyncpgUUID is asyncpg's pg_uuid_bytes_from_str over the str's UTF-8 bytes; the engine sends
// the canonical form of the 16 bytes it decodes (asyncpg accepts dashes anywhere, Postgres does not).
func asyncpgUUID(s string) (any, string) {
	if n := len(s); n < 32 || n > 36 {
		return nil, fmt.Sprintf("invalid UUID %s: length must be between 32..36 characters, got %d", pyRepr(s), n)
	}
	hex := make([]byte, 0, 32)
	for i := 0; i < len(s); i++ {
		c := s[i]
		if c == '-' {
			continue
		}
		if c >= 0x80 {
			return nil, "invalid UUID {u!r}: unexpected character" // asyncpg's own unformatted message
		}
		if !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')) {
			return nil, fmt.Sprintf("invalid UUID %s: unexpected character %s", pyRepr(s), pyRepr(string(c)))
		}
		if len(hex) == 32 {
			return nil, fmt.Sprintf("invalid UUID %s: decodes to more than 16 bytes", pyRepr(s))
		}
		hex = append(hex, c|0x20)
	}
	if len(hex) < 32 {
		return nil, fmt.Sprintf("invalid UUID %s: decodes to less than 16 bytes", pyRepr(s))
	}
	h := string(hex)
	return h[0:8] + "-" + h[8:12] + "-" + h[12:16] + "-" + h[16:20] + "-" + h[20:32], ""
}
