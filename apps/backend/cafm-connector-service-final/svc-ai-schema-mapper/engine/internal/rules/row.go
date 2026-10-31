// Package rules ports the per-row write rules of write_node.py, building_link.py, meter_link.py
// and reference_link.py. The tables those rules read come from the Python modules on every run
// (src/engine/rules_spec.py → Spec); the procedures are ported line for line and
// tests/test_engine_rules_oracle.py holds the two together.
package rules

import "hoistra/engine/internal/cell"

// Row is a Python dict of cells: insertion-ordered, assignment to an existing key keeps its
// place, a new key goes at the end, pop removes it.
type Row struct {
	K []string
	V []cell.Cell
}

func NewRow(capacity int) *Row {
	return &Row{K: make([]string, 0, capacity), V: make([]cell.Cell, 0, capacity)}
}

func (r *Row) idx(k string) int {
	for i, x := range r.K {
		if x == k {
			return i
		}
	}
	return -1
}

func (r *Row) Len() int { return len(r.K) }

// Has is `k in row`.
func (r *Row) Has(k string) bool { return r.idx(k) >= 0 }

// Get is `row.get(k)` (None when absent).
func (r *Row) Get(k string) cell.Cell {
	if i := r.idx(k); i >= 0 {
		return r.V[i]
	}
	return cell.None
}

// Set is `row[k] = v`.
func (r *Row) Set(k string, v cell.Cell) {
	if i := r.idx(k); i >= 0 {
		r.V[i] = v
		return
	}
	r.K = append(r.K, k)
	r.V = append(r.V, v)
}

// SetDefault is `row.setdefault(k, v)`.
func (r *Row) SetDefault(k string, v cell.Cell) {
	if !r.Has(k) {
		r.K = append(r.K, k)
		r.V = append(r.V, v)
	}
}

// Pop is `row.pop(k, None)`.
func (r *Row) Pop(k string) cell.Cell {
	i := r.idx(k)
	if i < 0 {
		return cell.None
	}
	v := r.V[i]
	r.K = append(r.K[:i], r.K[i+1:]...)
	r.V = append(r.V[:i], r.V[i+1:]...)
	return v
}

func (r *Row) Copy() *Row {
	return &Row{K: append([]string(nil), r.K...), V: append([]cell.Cell(nil), r.V...)}
}

// or is Python's `a or b or ...`: the first truthy operand, else the last one.
func or(vals ...cell.Cell) cell.Cell {
	for _, v := range vals {
		if v.Truthy() {
			return v
		}
	}
	return vals[len(vals)-1]
}
