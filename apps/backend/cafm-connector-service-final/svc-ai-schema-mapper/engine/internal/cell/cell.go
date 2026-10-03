// Package cell is one value of a migrated table as the Python pipeline holds it: a string,
// None, the integer 0 that preprocess's numeric null fill leaves, or (inside the writer only)
// the True/False it computes for is_sub_meter.
package cell

import "strconv"

type Kind uint8

const (
	Null Kind = iota
	Str
	Int
	Bool
)

type Cell struct {
	K Kind
	S string
	I int64
}

// None is Python's None.
var None = Cell{}

func Of(s string) Cell   { return Cell{K: Str, S: s} }
func OfInt(i int64) Cell { return Cell{K: Int, I: i} }

func OfBool(b bool) Cell {
	if b {
		return Cell{K: Bool, I: 1}
	}
	return Cell{K: Bool}
}

func (c Cell) IsNone() bool { return c.K == Null }

// Truthy is Python's bool(v).
func (c Cell) Truthy() bool {
	switch c.K {
	case Str:
		return c.S != ""
	case Int, Bool:
		return c.I != 0
	}
	return false
}

// PyStr is Python's str(v).
func (c Cell) PyStr() string {
	switch c.K {
	case Str:
		return c.S
	case Int:
		return strconv.FormatInt(c.I, 10)
	case Bool:
		if c.I != 0 {
			return "True"
		}
		return "False"
	}
	return "None"
}

// Empty is the writer's `v is None or str(v) == ""`.
func (c Cell) Empty() bool { return c.K == Null || (c.K == Str && c.S == "") }
