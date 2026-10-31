package rules

import (
	"strings"
	"testing"
)

// A name the engine makes is one PostgreSQL takes bare, as write_node._to_safe_identifier makes
// it: a sheet's "end" column stopped a whole write (29 Oct 2026).
func TestSafeIdentRepairsWhatPostgresWouldRefuse(t *testing.T) {
	cases := map[string]string{
		"end": "end_value", "End": "end_value", "ORDER": "order_value", "user": "user_value",
		"2024 Cost": "col_2024_cost", "1col": "col_1col",
		"time": "time", "timestamp": "timestamp", "row": "row", "name": "name", "Asset Ref": "asset_ref",
		strings.Repeat("a", 70): strings.Repeat("a", 54) + "_ed6c69d9",
	}
	for in, want := range cases {
		if got, ok := SafeIdent(in); !ok || got != want {
			t.Errorf("SafeIdent(%q) = %q, %v; want %q", in, got, ok, want)
		}
	}
	for _, in := range []string{"", "   ", "!!!"} {
		if got, ok := SafeIdent(in); ok {
			t.Errorf("SafeIdent(%q) = %q, want refused", in, got)
		}
	}
}
