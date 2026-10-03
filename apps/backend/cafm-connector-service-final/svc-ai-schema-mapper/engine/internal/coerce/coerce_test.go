package coerce

import (
	"testing"

	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/pgschema"
)

func s(v string) cell.Cell { return cell.Of(v) }

// Expected values are what write_node._coerce_value_for_db_type returns (Python 3.12); the
// exhaustive cross-check is tests/test_engine_coerce_oracle.py.
func TestForTypeFollowsThePythonBranches(t *testing.T) {
	cases := []struct {
		in   cell.Cell
		db   string
		want Value
	}{
		{cell.None, "text", Value{K: Null}},
		{s("   "), "integer", Value{K: Null}},
		{s(" 5 "), "text", Value{K: Text, S: " 5 "}},
		{cell.OfInt(0), "character varying", Value{K: Text, S: "0"}},
		{s(" 1 day "), "interval", Value{K: Interval, S: "1 day"}},
		{s("102.0"), "integer", Value{K: Int, S: "102"}},
		{s("1.9"), "bigint", Value{K: Int, S: "1"}},
		{s("-1.9"), "bigint", Value{K: Int, S: "-1"}},
		{s("1_000"), "integer", Value{K: Int, S: "1000"}},
		{s("Quarterly"), "integer", Value{K: Mismatch}},
		{s("inf"), "integer", Value{K: Mismatch}},
		{s("1e3"), "numeric", Value{K: Decimal, S: "1E+3"}},
		{s("12.50"), "double precision", Value{K: Decimal, S: "12.50"}},
		{cell.OfInt(0), "numeric", Value{K: Decimal, S: "0"}},
		{s("1,234"), "numeric", Value{K: Mismatch}},
		{s("T"), "boolean", Value{K: Bool, S: "true"}},
		{s("No"), "boolean", Value{K: Bool, S: "false"}},
		{s("2"), "boolean", Value{K: Mismatch}},
		{cell.OfInt(0), "boolean", Value{K: Bool, S: "false"}},
		{s("2025-12-31"), "timestamp without time zone", Value{K: Timestamp, S: "2025-12-31T00:00:00"}},
		{s("2025-12-31 10:30"), "timestamp without time zone", Value{K: Timestamp, S: "2025-12-31T10:30:00"}},
		{s("2025-12-31T10:30:00Z"), "timestamp with time zone", Value{K: TimestampTZ, S: "2025-12-31T10:30:00+00:00"}},
		{s("2025-12-31T10:30:00.5+05:30"), "timestamp with time zone", Value{K: TimestampTZ, S: "2025-12-31T10:30:00.500000+05:30"}},
		{s("20251231"), "timestamp without time zone", Value{K: Timestamp, S: "2025-12-31T00:00:00"}},
		{s("2025-W01-1"), "timestamp without time zone", Value{K: Timestamp, S: "2024-12-30T00:00:00"}},
		{s("31/12/2025"), "timestamp without time zone", Value{K: Mismatch}},
		{cell.OfInt(0), "timestamp without time zone", Value{K: Mismatch}},
		{s("2025-12-31T10:30:00"), "date", Value{K: Date, S: "2025-12-31"}},
		{s("2025-02-30"), "date", Value{K: Mismatch}},
		{s("hello"), "jsonb", Value{K: JSON, S: "hello"}},
		{cell.OfInt(0), "json", Value{K: JSON, S: "0"}},
		{s("0e9a1c1e-6b6f-4f0a-9d0e-8f3b2a1c4d5e"), "uuid", Value{K: UUID, S: "0e9a1c1e-6b6f-4f0a-9d0e-8f3b2a1c4d5e"}},
		{s("{0E9A1C1E-6B6F-4F0A-9D0E-8F3B2A1C4D5E}"), "uuid", Value{K: UUID, S: "{0E9A1C1E-6B6F-4F0A-9D0E-8F3B2A1C4D5E}"}},
		{s("A-001"), "uuid", Value{K: Mismatch}},
		{s("x"), "USER-DEFINED", Value{K: Raw, S: "x"}},
		{cell.OfInt(0), "USER-DEFINED", Value{K: Int, S: "0"}},
	}
	for _, c := range cases {
		if got := ForType(c.in, c.db); got != c.want {
			t.Errorf("ForType(%+v, %q) = %+v, want %+v", c.in, c.db, got, c.want)
		}
	}
}

func col(dataType string, mut ...func(*pgschema.Column)) *pgschema.Column {
	c := &pgschema.Column{Name: "c", DataType: dataType}
	switch dataType {
	case "smallint":
		c.IntBits = 16
	case "integer":
		c.IntBits = 32
	case "bigint":
		c.IntBits = 64
	}
	for _, m := range mut {
		m(c)
	}
	return c
}

func TestBindCheckRefusesWhatAsyncpgRefuses(t *testing.T) {
	n8 := 8
	p6, s2 := 6, 2
	bad := []struct {
		v Value
		c *pgschema.Column
	}{
		{Value{K: Int, S: "40000"}, col("smallint")},
		{Value{K: Int, S: "99999999999999999999"}, col("bigint")},
		{Value{K: TimestampTZ, S: "2025-12-31T10:30:00+00:00"}, col("timestamp without time zone")},
		{Value{K: UUID, S: "{0E9A1C1E-6B6F-4F0A-9D0E-8F3B2A1C4D5E}"}, col("uuid")},
		{Value{K: JSON, S: "hello"}, col("jsonb")},
		{Value{K: Interval, S: "1 day"}, col("interval")},
		{Value{K: Raw, S: "c"}, col("USER-DEFINED", func(c *pgschema.Column) { c.EnumLabels = []string{"a", "b"} })},
		{Value{K: Text, S: "123456789"}, col("character varying", func(c *pgschema.Column) { c.MaxChars = &n8 })},
		{Value{K: Decimal, S: "12345.6"}, col("numeric", func(c *pgschema.Column) { c.NumPrecision, c.NumScale = &p6, &s2 })},
		{Value{K: Decimal, S: "1E+39"}, col("real")},
		{Value{K: Decimal, S: "sNaN"}, col("double precision")},
	}
	for _, b := range bad {
		if err := BindCheck(b.v, b.c); err == nil {
			t.Errorf("BindCheck(%+v, %s) = nil, want an error", b.v, b.c.DataType)
		}
	}
	good := []struct {
		v Value
		c *pgschema.Column
	}{
		{Value{K: Int, S: "32767"}, col("smallint")},
		{Value{K: Timestamp, S: "2025-12-31T10:30:00"}, col("timestamp with time zone")},
		{Value{K: UUID, S: "0e9a1c1e-6b6f-4f0a-9d0e-8f3b2a1c4d5e"}, col("uuid")},
		{Value{K: JSON, S: `{"a": 1}`}, col("jsonb")},
		{Value{K: Raw, S: "a"}, col("USER-DEFINED", func(c *pgschema.Column) { c.EnumLabels = []string{"a", "b"} })},
		{Value{K: Text, S: "12345678   "}, col("character varying", func(c *pgschema.Column) { c.MaxChars = &n8 })},
		{Value{K: Decimal, S: "1234.567"}, col("numeric", func(c *pgschema.Column) { c.NumPrecision, c.NumScale = &p6, &s2 })},
		{Value{K: Decimal, S: "NaN"}, col("numeric")},
		{Value{K: Decimal, S: "1E+400"}, col("double precision")},
	}
	for _, g := range good {
		if err := BindCheck(g.v, g.c); err != nil {
			t.Errorf("BindCheck(%+v, %s) = %v, want nil", g.v, g.c.DataType, err)
		}
	}
}

func TestInferSQLTypeMatchesTheWriter(t *testing.T) {
	if InferSQLType(s("x")) != "TEXT" || InferSQLType(cell.OfInt(0)) != "BIGINT" || InferSQLType(cell.None) != "TEXT" {
		t.Fatal("InferSQLType")
	}
}
