// Package coerce ports write_node._coerce_value_for_db_type (and _infer_sql_type_for_value):
// what a cleaned value becomes for its destination column, decided by the column's
// information_schema data_type string in the same branch order as the Python function.
package coerce

import (
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"

	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/pystr"
)

type Kind uint8

const (
	Null Kind = iota
	Mismatch
	Text
	Interval
	Int
	Decimal
	Bool
	Timestamp
	TimestampTZ
	Date
	JSON
	UUID
	Raw
)

var kindNames = [...]string{"null", "mismatch", "text", "interval", "int", "decimal", "bool", "timestamp",
	"timestamptz", "date", "json", "uuid", "raw"}

func (k Kind) String() string { return kindNames[k] }

// Value is a coerced value: its kind and the canonical text Postgres parses back to it.
type Value struct {
	K Kind
	S string
}

func (v Value) IsNull() bool { return v.K == Null }

// Truthy is Python truthiness of the coerced value (0, False, Decimal 0 and "" are false).
func (v Value) Truthy() bool {
	switch v.K {
	case Null, Mismatch:
		return false
	case Int:
		return v.S != "0"
	case Decimal:
		d, ok := new(big.Rat).SetString(v.S)
		return !ok || d.Sign() != 0
	case Bool:
		return v.S == "true"
	}
	return v.S != ""
}

// PyStr is str() of the coerced Python value, which is what _already_written keys its cache on.
func (v Value) PyStr() string {
	switch v.K {
	case Bool:
		if v.S == "true" {
			return "True"
		}
		return "False"
	case Timestamp, TimestampTZ:
		return strings.Replace(v.S, "T", " ", 1)
	case Null:
		return "None"
	}
	return v.S
}

var mismatch = Value{K: Mismatch}

// ForType is _coerce_value_for_db_type(value, db_type).
func ForType(v cell.Cell, dbType string) Value {
	if v.K == cell.Null {
		return Value{K: Null}
	}
	t := strings.ToLower(dbType)
	isStr := v.K == cell.Str
	s := ""
	if isStr {
		s = pystr.Strip(v.S)
		if s == "" {
			return Value{K: Null}
		}
	}
	switch {
	case strings.Contains(t, "char") || strings.Contains(t, "text") || t == "name" || t == "citext":
		if isStr {
			return Value{K: Text, S: v.S}
		}
		if v.K == cell.Bool {
			return Value{K: Text, S: boolText(v)}
		}
		return Value{K: Text, S: v.PyStr()}
	case strings.HasPrefix(t, "interval"):
		if isStr {
			return Value{K: Interval, S: s}
		}
		return passThrough(v)
	case strings.Contains(t, "int") || strings.Contains(t, "serial"):
		if !isStr {
			return Value{K: Int, S: strconv.FormatInt(v.I, 10)} // int(True) == 1
		}
		if n, ok := pystr.ParseInt(s); ok {
			return Value{K: Int, S: n.String()}
		}
		if f, ok := pystr.ParseFloat(s); ok {
			if math.IsInf(f, 0) || math.IsNaN(f) {
				return mismatch
			}
			n, _ := new(big.Float).SetFloat64(f).Int(nil) // int() truncates toward zero
			return Value{K: Int, S: n.String()}
		}
		return mismatch
	case strings.Contains(t, "numeric") || strings.Contains(t, "decimal") || strings.Contains(t, "double") ||
		strings.Contains(t, "real"):
		if !isStr {
			return Value{K: Decimal, S: strconv.FormatInt(v.I, 10)} // Decimal(int(value))
		}
		if d, ok := pystr.ParseDecimal(s); ok {
			return Value{K: Decimal, S: d}
		}
		return mismatch
	case t == "boolean":
		if !isStr {
			return boolValue(v.I != 0)
		}
		switch pystr.Lower(s) {
		case "1", "true", "t", "yes", "y":
			return boolValue(true)
		case "0", "false", "f", "no", "n":
			return boolValue(false)
		}
		return mismatch
	case strings.Contains(t, "timestamp"):
		if !isStr {
			return mismatch
		}
		dt, ok := datetimeFromISO(strings.ReplaceAll(s, "Z", "+00:00"))
		if !ok {
			return mismatch
		}
		if dt.hasTZ {
			return Value{K: TimestampTZ, S: dt.isoformat()}
		}
		return Value{K: Timestamp, S: dt.isoformat()}
	case t == "date":
		if !isStr {
			return mismatch
		}
		head := s
		if r := []rune(s); len(r) > 10 {
			head = string(r[:10])
		}
		y, m, d, ok := dateFromISO(head)
		if !ok {
			return mismatch
		}
		return Value{K: Date, S: fmt.Sprintf("%04d-%02d-%02d", y, m, d)}
	case strings.Contains(t, "json"):
		if isStr {
			return Value{K: JSON, S: v.S}
		}
		if v.K == cell.Bool {
			return Value{K: JSON, S: boolText(v)} // json.dumps(True) == "true"
		}
		return Value{K: JSON, S: v.PyStr()} // json.dumps(0) == "0"
	case t == "uuid":
		if pythonUUIDValid(v.PyStr()) && isStr {
			return Value{K: UUID, S: v.S}
		}
		return mismatch
	}
	if isStr {
		return Value{K: Raw, S: v.S}
	}
	return passThrough(v)
}

// passThrough is a non-string value handed over untouched (Python returns `value`).
func passThrough(v cell.Cell) Value {
	if v.K == cell.Bool {
		return Value{K: Bool, S: boolText(v)}
	}
	return Value{K: Int, S: v.PyStr()}
}

func boolText(v cell.Cell) string {
	if v.I != 0 {
		return "true"
	}
	return "false"
}

func boolValue(b bool) Value {
	if b {
		return Value{K: Bool, S: "true"}
	}
	return Value{K: Bool, S: "false"}
}

// pythonUUIDValid is uuid.UUID(s) not raising: urn/uuid prefixes removed, braces stripped from
// both ends, dashes removed, exactly 32 characters left that int(x, 16) reads as a non-negative number.
func pythonUUIDValid(s string) bool {
	h := strings.ReplaceAll(strings.ReplaceAll(s, "urn:", ""), "uuid:", "")
	h = strings.Trim(h, "{}")
	h = strings.ReplaceAll(h, "-", "")
	if pystr.Len(h) != 32 {
		return false
	}
	return pyHexNonNegative(h)
}

// pyHexNonNegative is `int(h, 16) >= 0` succeeding: surrounding whitespace, an optional sign,
// underscores between digits, Unicode decimal digits for 0-9.
func pyHexNonNegative(h string) bool {
	body := pystr.Strip(h)
	if body == "" {
		return false
	}
	if body[0] == '+' {
		body = body[1:]
	} else if body[0] == '-' {
		return false
	}
	if body == "" {
		return false
	}
	prevUnderscore := true
	for _, r := range body {
		switch {
		case r == '_':
			if prevUnderscore {
				return false
			}
			prevUnderscore = true
			continue
		case (r >= 'a' && r <= 'f') || (r >= 'A' && r <= 'F'):
		default:
			if _, ok := pystr.DigitValue(r); !ok {
				return false
			}
		}
		prevUnderscore = false
	}
	return !prevUnderscore
}

// InferSQLType is _infer_sql_type_for_value for the values a cleaned row carries.
func InferSQLType(c cell.Cell) string {
	if c.K == cell.Int {
		return "BIGINT"
	}
	return "TEXT"
}
