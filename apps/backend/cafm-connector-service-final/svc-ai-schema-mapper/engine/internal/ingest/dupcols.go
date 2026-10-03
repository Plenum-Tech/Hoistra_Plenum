package ingest

import (
	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/protocol"
	"hoistra/engine/internal/pystr"
)

// minRedundantRows is primitives._MIN_REDUNDANT_ROWS: rows of real evidence a merge needs.
const minRedundantRows = 3

// norm is primitives._norm(v).casefold().
func norm(c cell.Cell) string {
	if c.IsNone() {
		return ""
	}
	return pystr.Casefold(pystr.Strip(c.PyStr()))
}

func isASCII(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] >= 0x80 {
			return false
		}
	}
	return true
}

// same compares two cells as norm() would, without building the strings when it can.
func same(a, b cell.Cell) (blank, equal bool) {
	if a.K == cell.Str && b.K == cell.Str && isASCII(a.S) && isASCII(b.S) {
		x, y := pystr.Strip(a.S), pystr.Strip(b.S)
		if x == "" && y == "" {
			return true, true
		}
		if len(x) != len(y) {
			return false, false
		}
		for i := 0; i < len(x); i++ {
			cx, cy := x[i], y[i]
			if 'A' <= cx && cx <= 'Z' {
				cx += 'a' - 'A'
			}
			if 'A' <= cy && cy <= 'Z' {
				cy += 'a' - 'A'
			}
			if cx != cy {
				return false, false
			}
		}
		return false, true
	}
	x, y := norm(a), norm(b)
	return x == "" && y == "", x == y
}

// redundantColumnGroups is primitives.redundant_column_groups: columns whose values agree on
// every row (case- and whitespace-insensitively, rows blank on both sides ignored) with at least
// minRedundantRows rows of evidence; multi-member groups in source order.
func redundantColumnGroups(t *Table) [][]int {
	var cols []int
	seen := map[string]bool{}
	for i, c := range t.Columns {
		if c != "" && !seen[c] {
			seen[c] = true
			cols = append(cols, i)
		}
	}
	if len(cols) < 2 {
		return nil
	}
	parent := map[int]int{}
	for _, c := range cols {
		parent[c] = c
	}
	var find func(int) int
	find = func(x int) int {
		for parent[x] != x {
			parent[x] = parent[parent[x]]
			x = parent[x]
		}
		return x
	}
	identical := func(a, b int) bool {
		if len(t.Rows) == 0 {
			return false
		}
		evidence := 0
		for _, r := range t.Rows {
			blank, eq := same(r[a], r[b])
			if blank {
				continue
			}
			if !eq {
				return false
			}
			evidence++
		}
		return evidence >= minRedundantRows
	}
	for i, a := range cols {
		for _, b := range cols[i+1:] {
			if find(a) != find(b) && identical(a, b) {
				parent[find(a)] = find(b)
			}
		}
	}
	var roots []int
	buckets := map[int][]int{}
	for _, c := range cols {
		r := find(c)
		if _, ok := buckets[r]; !ok {
			roots = append(roots, r)
		}
		buckets[r] = append(buckets[r], c)
	}
	var out [][]int
	for _, r := range roots {
		if len(buckets[r]) > 1 {
			out = append(out, buckets[r]) // already in source order
		}
	}
	return out
}

// mergeDuplicateColumns is column_merge.merge_duplicate_columns: for each group of identical
// columns, keep the one destination column among them (or the longest name, earliest on a tie)
// and drop the rest; two or more destination columns are distinct facts, kept and reported as
// declined. It sets each table's Kept columns and returns the report.
func mergeDuplicateColumns(tables []*Table, known map[string]bool) ([]*Table, *protocol.Object) {
	reports := &protocol.Object{}
	merges, dropped := 0, 0
	for _, t := range tables {
		t.Kept = append([]string{}, t.Columns...)
		if len(t.Rows) == 0 {
			continue
		}
		groups := redundantColumnGroups(t)
		if len(groups) == 0 {
			continue
		}
		drop := map[string]bool{}
		entries := []*protocol.Object{}
		for _, g := range groups {
			names := make([]string, len(g))
			var facts []string
			for i, c := range g {
				names[i] = t.Columns[c]
				if known[pystr.Lower(pystr.Strip(names[i]))] {
					facts = append(facts, names[i])
				}
			}
			e := &protocol.Object{}
			if len(facts) >= 2 {
				e.Set("kept", names).Set("dropped", []string{}).Set("members", names).Set("match_pct", 100).
					Set("row_count", len(t.Rows)).Set("declined", "distinct destination columns")
				entries = append(entries, e)
				continue
			}
			kept := ""
			if len(facts) == 1 {
				kept = facts[0]
			} else {
				kept = names[0]
				for _, n := range names[1:] {
					if pystr.Len(n) > pystr.Len(kept) {
						kept = n
					}
				}
			}
			gone := []string{}
			for _, n := range names {
				if n != kept {
					gone = append(gone, n)
					drop[n] = true
				}
			}
			e.Set("kept", kept).Set("dropped", gone).Set("members", names).Set("match_pct", 100).Set("row_count", len(t.Rows))
			entries = append(entries, e)
		}
		t.Kept = t.Kept[:0]
		for _, c := range t.Columns {
			if !drop[c] {
				t.Kept = append(t.Kept, c)
			}
		}
		reports.Set(t.Name, entries)
		merges += len(entries)
		dropped += len(drop)
	}
	out := &protocol.Object{}
	out.Set("total_merges", merges).Set("total_columns_dropped", dropped).Set("tables", reports)
	return tables, out
}
