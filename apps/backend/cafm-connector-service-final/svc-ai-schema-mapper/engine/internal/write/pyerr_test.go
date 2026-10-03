package write

import (
	"strings"
	"testing"

	"hoistra/engine/internal/pgschema"
)

// The expected strings are what SQLAlchemy 2.0.54 + asyncpg 0.31 printed for the same value and
// column on the parity database (str(exc) up to "\n[SQL", single-parameter INSERTs).
func TestBindErrorsReadAsPythonPrintsThem(t *testing.T) {
	col := func(dt string, bits int) *pgschema.Column { return &pgschema.Column{DataType: dt, IntBits: bits} }
	const pre = "(sqlalchemy.dialects.postgresql.asyncpg.Error) <class 'asyncpg.exceptions.DataError'>: invalid input for query argument $1: "
	cases := []struct {
		col  *pgschema.Column
		v    pyVal
		want string
	}{
		{col("smallint", 16), pyVal{k: pyInt, s: "40000"}, "40000 (value out of int16 range)"},
		{col("integer", 32), pyVal{k: pyInt, s: "99999999999"}, "99999999999 (value out of int32 range)"},
		{col("bigint", 64), pyVal{k: pyInt, s: "-9223372036854775809"}, "-9223372036854775809 (value out of int64 range)"},
		{col("real", 0), pyVal{k: pyDecimal, s: "1E+39"}, "Decimal('1E+39') (value out of float32 range)"},
		{col("double precision", 0), pyVal{k: pyDecimal, s: "sNaN"}, "Decimal('sNaN') (cannot convert signaling NaN to float)"},
		{col("timestamp without time zone", 0), pyVal{k: pyDatetime, s: "2026-01-01T00:00:00+00:00", aware: true},
			"datetime.datetime(2026, 1, 1, 0, 0, tzin... (can't subtract offset-naive and offset-aware datetimes)"},
		{col("uuid", 0), pyVal{k: pyStr, s: "{12345678-1234-5678-1234-567812345678}"},
			"'{12345678-1234-5678-1234-567812345678}' (invalid UUID '{12345678-1234-5678-1234-567812345678}': length must be between 32..36 characters, got 38)"},
		{col("uuid", 0), pyVal{k: pyStr, s: "urn:uuid:12345678-1234-5678-1234-567812345678"},
			"'urn:uuid:12345678-1234-5678-1234-567812... (invalid UUID 'urn:uuid:12345678-1234-5678-1234-567812345678': length must be between 32..36 characters, got 45)"},
		{col("uuid", 0), pyVal{k: pyStr, s: "12345678-1234-5678-1234-56781234567g"},
			"'12345678-1234-5678-1234-56781234567g' (invalid UUID '12345678-1234-5678-1234-56781234567g': unexpected character 'g')"},
		{col("uuid", 0), pyVal{k: pyStr, s: "123456781234567812345678123456789"},
			"'123456781234567812345678123456789' (invalid UUID '123456781234567812345678123456789': decodes to more than 16 bytes)"},
		{col("uuid", 0), pyVal{k: pyStr, s: "migration"},
			"'migration' (invalid UUID 'migration': length must be between 32..36 characters, got 9)"},
		{col("uuid", 0), pyVal{k: pyStr, s: strings.Repeat("\u0661", 32)},
			"'" + strings.Repeat("\u0661", 32) + "' (invalid UUID '" + strings.Repeat("\u0661", 32) + "': length must be between 32..36 characters, got 64)"},
		{col("interval", 0), pyVal{k: pyStr, s: "1 day"}, "'1 day' ('str' object has no attribute 'days')"},
		{col("interval", 0), pyVal{k: pyInt, s: "0"}, "0 ('int' object has no attribute 'days')"},
		{col("time without time zone", 0), pyVal{k: pyStr, s: "10:00"}, "'10:00' ('str' object has no attribute 'hour')"},
		{col("time with time zone", 0), pyVal{k: pyStr, s: "10:00+01"}, "'10:00+01' ('str' object has no attribute 'tzinfo')"},
		{col("bytea", 0), pyVal{k: pyStr, s: "abc"}, "'abc' (a bytes-like object is required, not 'str')"},
		{col("bit", 0), pyVal{k: pyStr, s: "101"}, "'101' (a bytes-like object is required, not 'str')"},
		{col("point", 0), pyVal{k: pyStr, s: "(1,2)"}, "'(1,2)' (must be real number, not str)"},
		{col("point", 0), pyVal{k: pyInt, s: "0"}, "0 ('int' object is not subscriptable)"},
		{col("ARRAY", 0), pyVal{k: pyStr, s: "{1,2}"}, "'{1,2}' (a sized iterable container expected (got type 'str'))"},
		{col("USER-DEFINED", 0), pyVal{k: pyInt, s: "0"}, "0 (expected str, got int)"},
		{col("text", 0), pyVal{k: pyBool, s: "true"}, "True (expected str, got bool)"},
		{col("boolean", 0), pyVal{k: pyStr, s: "migration"}, "'migration' (a boolean is required (got type str))"},
		{col("date", 0), pyVal{k: pyStr, s: "2026-01-01"}, "'2026-01-01' ('str' object has no attribute 'toordinal')"},
		{col("integer", 32), pyVal{k: pyStr, s: "migration"}, "'migration' ('str' object cannot be interpreted as an integer)"},
		{col("numeric", 0), pyVal{k: pyStr, s: "migration"}, "'migration' ([<class 'decimal.ConversionSyntax'>])"},
		{col("double precision", 0), pyVal{k: pyStr, s: "5"}, "'5' (must be real number, not str)"},
		{col("timestamp without time zone", 0), pyVal{k: pyStr, s: "migration"},
			"'migration' (expected a datetime.date or datetime.datetime instance, got 'str')"},
		{col("json", 0), pyVal{k: pyInt, s: "0"}, "0 (descriptor 'encode' for 'str' objects doesn't apply to a 'int' object)"},
		{col("jsonb", 0), pyVal{k: pyInt, s: "0"}, "0 ('int' object has no attribute 'encode')"},
		{col("uuid", 0), pyVal{k: pyInt, s: "0"}, "0 ('int' object has no attribute 'bytes')"},
	}
	for _, c := range cases {
		_, why := c.v.encode(c.col)
		if why == "" {
			t.Errorf("%s %+v: asyncpg refuses it, the engine did not", c.col.DataType, c.v)
			continue
		}
		e := bindPyErr(1, c.v, why, "INSERT INTO b (x) VALUES ($1)", []pyVal{c.v})
		got := e.str[:strings.Index(e.str, "\n[SQL")]
		if got != pre+c.want {
			t.Errorf("%s %+v:\n got %s\nwant %s", c.col.DataType, c.v, got, pre+c.want)
		}
		if e.typ != "DBAPIError" {
			t.Errorf("type %s", e.typ)
		}
	}
	// What asyncpg accepts the engine sends, in the form Postgres reads back to the same value.
	ok := []struct {
		col  *pgschema.Column
		v    pyVal
		want string
	}{
		{col("double precision", 0), pyVal{k: pyDecimal, s: "1E+400"}, "Infinity"},
		{col("real", 0), pyVal{k: pyDecimal, s: "NaN"}, "NaN"},
		{col("numeric", 0), pyVal{k: pyDecimal, s: "sNaN"}, "NaN"},
		{col("numeric", 0), pyVal{k: pyDecimal, s: "-Infinity"}, "-Infinity"},
		{col("numeric", 0), pyVal{k: pyStr, s: " 5 "}, "5"},
		{col("integer", 32), pyVal{k: pyBool, s: "true"}, "1"},
		{col("timestamp with time zone", 0), pyVal{k: pyDatetime, s: "2026-01-01T00:00:00"}, "2026-01-01T00:00:00"},
		{col("uuid", 0), pyVal{k: pyStr, s: "1234-5678123456781234567812345678"}, "12345678-1234-5678-1234-567812345678"},
		{col("uuid", 0), pyVal{k: pyStr, s: "ABCDEF00-0000-4000-8000-000000000001"}, "abcdef00-0000-4000-8000-000000000001"},
	}
	for _, c := range ok {
		arg, why := c.v.encode(c.col)
		if why != "" || arg != c.want {
			t.Errorf("%s %+v: sent %v (%s), want %s", c.col.DataType, c.v, arg, why, c.want)
		}
	}
}

func TestServerErrorsReadAsPythonPrintsThem(t *testing.T) {
	e := newPyErr("23502", `null value in column "k" of relation "t" violates not-null constraint`,
		"Failing row contains (836cfbd3-bb44-4ec8-b9da-01987c1bd6e6, null, 1, null, null).", "",
		"INSERT INTO t (id, n) VALUES ($1, $2) ON CONFLICT DO NOTHING",
		[]pyVal{{k: pyStr, s: "836cfbd3-bb44-4ec8-b9da-01987c1bd6e6"}, {k: pyInt, s: "1"}})
	want := "(sqlalchemy.dialects.postgresql.asyncpg.IntegrityError) <class 'asyncpg.exceptions.NotNullViolationError'>: null value in column \"k\" of relation \"t\" violates not-null constraint\nDETAIL:  Failing row contains (836cfbd3-bb44-4ec8-b9da-01987c1bd6e6, null, 1, null, null).\n[SQL: INSERT INTO t (id, n) VALUES ($1, $2) ON CONFLICT DO NOTHING]\n[parameters: ('836cfbd3-bb44-4ec8-b9da-01987c1bd6e6', 1)]\n(Background on this error at: https://sqlalche.me/e/20/gkpj)"
	if e.str != want || e.typ != "IntegrityError" {
		t.Fatalf("got %q (%s)", e.str, e.typ)
	}
	p := newPyErr("42P10", "there is no unique or exclusion constraint matching the ON CONFLICT specification", "", "",
		"INSERT INTO t (id, k) VALUES ($1, $2) ON CONFLICT (k) DO NOTHING", []pyVal{{k: pyStr, s: "x"}, {k: pyStr, s: "a"}})
	if p.typ != "ProgrammingError" || !strings.HasPrefix(p.str, "(sqlalchemy.dialects.postgresql.asyncpg.ProgrammingError) <class 'asyncpg.exceptions.InvalidColumnReferenceError'>: there is no unique") ||
		!strings.HasSuffix(p.str, "https://sqlalche.me/e/20/f405)") {
		t.Fatalf("got %q (%s)", p.str, p.typ)
	}
	fk := newPyErr("23503", `insert or update on table "t" violates foreign key constraint "t_p_fkey"`,
		`Key (p)=(00000000-0000-4000-8000-000000000001) is not present in table "p".`, "", "INSERT …", nil)
	if cols := fk.fkColumns(); len(cols) != 1 || cols[0] != "p" {
		t.Fatalf("fk columns %v", cols)
	}
	uq := newPyErr("23505", `duplicate key value violates unique constraint "t_k_key"`, `Key (k)=(a) already exists.`, "", "INSERT …", nil)
	if cols := uq.fkColumns(); cols != nil {
		t.Fatalf("a unique violation named fk columns %v", cols)
	}
	if !strings.HasPrefix(e.sig(), "IntegrityError:(sqlalchemy.dialects.postgresql.asyncpg.IntegrityError) <class 'asyncpg.exceptions.NotNullViolationError'>: null value ") ||
		len([]rune(strings.TrimPrefix(e.sig(), "IntegrityError:"))) != 120 {
		t.Fatalf("sig %q", e.sig())
	}
}
