package rules

import (
	"reflect"
	"testing"

	"hoistra/engine/internal/cell"
)

// Row behaves like a Python dict: assignment keeps an existing key's place, a new key and a
// pop-then-assign go to the end, setdefault never overwrites.
func TestRowFollowsPythonDictOrdering(t *testing.T) {
	r := NewRow(4)
	r.Set("a", cell.Of("1"))
	r.Set("b", cell.Of("2"))
	r.Set("c", cell.Of("3"))
	r.Set("a", cell.Of("9"))
	r.SetDefault("b", cell.Of("x"))
	r.SetDefault("d", cell.Of("4"))
	v := r.Pop("b")
	r.Set("b", v)
	if !reflect.DeepEqual(r.K, []string{"a", "c", "d", "b"}) || r.Get("a").S != "9" || r.Get("b").S != "2" {
		t.Fatalf("got %v %v", r.K, r.V)
	}
	if r.Pop("missing") != cell.None || r.Get("missing") != cell.None || r.Has("missing") {
		t.Fatal("absent keys")
	}
}

func TestOrReturnsTheFirstTruthyElseTheLast(t *testing.T) {
	if got := or(cell.None, cell.Of(""), cell.Of("x")); got.S != "x" {
		t.Fatal(got)
	}
	if got := or(cell.None, cell.OfInt(0)); got != cell.OfInt(0) {
		t.Fatal(got)
	}
}
