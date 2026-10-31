package pdframe

import (
	"reflect"
	"testing"
)

func TestSanitizeNamesAsIngestDoes(t *testing.T) {
	got := Sanitize([]string{" a ", "Unnamed: 1", "", "NaN", "unnamed: x", "b", "nan "})
	want := []string{"a", "col_2", "col_3", "col_4", "col_5", "b", "col_7"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %q", got)
	}
}

func TestCollapseKeepsFirstPositionAndLastValue(t *testing.T) {
	cols, target := Collapse([]string{"a", "b", "a", "c", "b"})
	if !reflect.DeepEqual(cols, []string{"a", "b", "c"}) || !reflect.DeepEqual(target, []int{0, 1, 0, 2, 1}) {
		t.Fatalf("cols %q target %v", cols, target)
	}
}

func TestDefaultNASet(t *testing.T) {
	for _, s := range []string{"", "NA", "N/A", "#N/A", "null", "NULL", "None", "nan", "NaN", "-nan", "<NA>"} {
		if !IsNA(s) {
			t.Errorf("%q should be NA", s)
		}
	}
	for _, s := range []string{" NA", "none", "Null", "0", "-"} {
		if IsNA(s) {
			t.Errorf("%q should not be NA", s)
		}
	}
}
