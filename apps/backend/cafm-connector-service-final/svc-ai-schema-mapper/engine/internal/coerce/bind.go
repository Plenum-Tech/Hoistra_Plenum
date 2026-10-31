package coerce

import (
	"encoding/json"
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"

	"hoistra/engine/internal/pgschema"
	"hoistra/engine/internal/pystr"
)

// BindCheck reports what would make the Python writer's INSERT of this value fail: asyncpg 0.31
// refusing to encode it (an int out of range, an aware datetime for a naive column, a UUID string
// it cannot parse, a value for a type it has no str/int encoder for) or Postgres rejecting it on
// input (invalid JSON, an unknown enum label, a string longer than the column, a numeric that
// overflows its precision). In the Python writer each of these fails the row and the rest of the
// batch is retried row by row; the engine fails the same rows before it COPYs the rest.
func BindCheck(v Value, col *pgschema.Column) error {
	if v.K == Null {
		return nil
	}
	dt := strings.ToLower(col.DataType)
	switch v.K {
	case Int:
		if col.IntBits > 0 {
			n, ok := new(big.Int).SetString(v.S, 10)
			if !ok {
				return fmt.Errorf("invalid integer %q", v.S)
			}
			min := new(big.Int).Neg(new(big.Int).Lsh(big.NewInt(1), uint(col.IntBits-1)))
			max := new(big.Int).Sub(new(big.Int).Lsh(big.NewInt(1), uint(col.IntBits-1)), big.NewInt(1))
			if n.Cmp(min) < 0 || n.Cmp(max) > 0 {
				return fmt.Errorf("value out of int%d range", col.IntBits)
			}
			return nil
		}
		if isNumericType(dt) || dt == "boolean" || isTextType(dt) || strings.Contains(dt, "json") {
			return nil
		}
		return fmt.Errorf("invalid input for query argument: %s (expected a %s)", v.S, col.DataType)
	case Decimal:
		return checkDecimal(v.S, col, dt)
	case Bool, Date, Timestamp:
		return nil
	case TimestampTZ:
		if dt == "timestamp without time zone" {
			return fmt.Errorf("can't subtract offset-naive and offset-aware datetimes")
		}
		return nil
	case Text:
		return checkLength(v.S, col)
	case JSON:
		if !json.Valid([]byte(v.S)) {
			return fmt.Errorf("invalid input syntax for type json")
		}
		if dt == "jsonb" && strings.Contains(v.S, `\u0000`) {
			return fmt.Errorf("unsupported Unicode escape sequence")
		}
		return nil
	case UUID:
		return asyncpgUUID(v.S)
	case Interval:
		return fmt.Errorf("invalid input for query argument: %q (expected a datetime.timedelta instance)", v.S)
	case Raw:
		if col.EnumLabels != nil {
			for _, l := range col.EnumLabels {
				if l == v.S {
					return nil
				}
			}
			return fmt.Errorf("invalid input value for enum %s: %q", col.UDT, v.S)
		}
		switch dt {
		case "time without time zone", "time with time zone", "array", "bytea", "point", "line", "lseg",
			"box", "path", "polygon", "circle":
			return fmt.Errorf("invalid input for query argument: %q (no %s encoder for str)", v.S, col.DataType)
		}
		return nil
	}
	return nil
}

func isNumericType(dt string) bool {
	return strings.Contains(dt, "numeric") || strings.Contains(dt, "decimal") ||
		strings.Contains(dt, "double") || strings.Contains(dt, "real")
}

func isTextType(dt string) bool {
	return strings.Contains(dt, "char") || strings.Contains(dt, "text") || dt == "name" || dt == "citext"
}

// checkLength is Postgres's rule for character(n)/character varying(n): longer is an error unless
// every excess character is a space (then it is truncated).
func checkLength(s string, col *pgschema.Column) error {
	if col.MaxChars == nil {
		return nil
	}
	n := *col.MaxChars
	r := []rune(s)
	if len(r) <= n {
		return nil
	}
	for _, c := range r[n:] {
		if c != ' ' {
			return fmt.Errorf("value too long for type %s", col.FormatType)
		}
	}
	return nil
}

func checkDecimal(s string, col *pgschema.Column, dt string) error {
	switch {
	case dt == "real" || dt == "double precision":
		f, err := strconv.ParseFloat(s, 64)
		if err != nil {
			if ne, ok := err.(*strconv.NumError); !ok || ne.Err != strconv.ErrRange {
				return fmt.Errorf("cannot convert %s to float", s) // sNaN
			}
		}
		if dt == "real" && math.IsInf(float64(float32(f)), 0) && !math.IsInf(f, 0) {
			return fmt.Errorf("value out of float32 range")
		}
		return nil
	case col.NumPrecision != nil && col.NumScale != nil && strings.Contains(dt, "numeric"):
		if strings.HasSuffix(s, "NaN") {
			return nil
		}
		if strings.HasSuffix(s, "Infinity") {
			return fmt.Errorf("numeric field overflow")
		}
		r, ok := new(big.Rat).SetString(s)
		if !ok {
			return fmt.Errorf("invalid numeric %q", s)
		}
		p, sc := *col.NumPrecision, *col.NumScale
		scale := new(big.Rat).SetInt(new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(sc)), nil))
		scaled := new(big.Rat).Mul(new(big.Rat).Abs(r), scale)
		// Postgres rounds half away from zero to the column's scale.
		q, rem := new(big.Int).QuoRem(scaled.Num(), scaled.Denom(), new(big.Int))
		if new(big.Int).Mul(rem, big.NewInt(2)).Cmp(scaled.Denom()) >= 0 {
			q.Add(q, big.NewInt(1))
		}
		limit := new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(p)), nil) // |rounded| * 10^s < 10^p
		if q.Cmp(limit) >= 0 {
			return fmt.Errorf("numeric field overflow")
		}
	}
	return nil
}

// asyncpgUUID is asyncpg's pg_uuid_bytes_from_str: 32..36 UTF-8 bytes, dashes anywhere, every other
// byte a hex digit, exactly 16 bytes decoded.
func asyncpgUUID(s string) error {
	if n := len(s); n < 32 || n > 36 {
		return fmt.Errorf("invalid UUID %q: length must be between 32..36 characters, got %d", s, n)
	}
	digits := 0
	for i := 0; i < len(s); i++ {
		c := s[i]
		if c == '-' {
			continue
		}
		if !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')) {
			return fmt.Errorf("invalid UUID %q: unexpected character", s)
		}
		digits++
	}
	if digits != 32 {
		return fmt.Errorf("invalid UUID %q: decodes to %d hex digits", s, digits)
	}
	_ = pystr.Len // keep the import honest for future rules
	return nil
}
