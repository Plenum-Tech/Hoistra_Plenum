package rules

import "sort"

// Edge is one confirmed hierarchy: source_table references target_table.
type Edge struct {
	SourceTable string `json:"source_table"`
	TargetTable string `json:"target_table"`
}

// WriteOrder is write_node._ordered_source_tables over the source tables that have rows, in
// their original order.
func WriteOrder(spec *Spec, sources []string, routing map[string]string, hierarchies []Edge) []string {
	destOf := func(s string) string {
		if d, ok := routing[s]; ok {
			return d
		}
		return s
	}
	parents := map[string]map[string]bool{}
	addParent := func(child, parent string) {
		if parents[child] == nil {
			parents[child] = map[string]bool{}
		}
		parents[child][parent] = true
	}
	for _, h := range hierarchies {
		child, parent := destOf(h.SourceTable), destOf(h.TargetTable)
		if child != "" && parent != "" && child != parent {
			addParent(child, parent)
		}
	}
	for c, ps := range spec.CoreParents {
		if parents[c] == nil {
			parents[c] = map[string]bool{}
		}
		for _, p := range ps {
			parents[c][p] = true
		}
	}
	idx := map[string]int{}
	for i, s := range sources {
		idx[s] = i
	}
	seen := map[string]bool{}
	var out []string
	var visit func(s string, stack map[string]bool)
	visit = func(s string, stack map[string]bool) {
		if seen[s] || stack[s] {
			return
		}
		stack[s] = true
		var ps []string
		for _, x := range sources {
			if parents[destOf(s)][destOf(x)] {
				ps = append(ps, x)
			}
		}
		sort.SliceStable(ps, func(i, j int) bool { return idx[ps[i]] < idx[ps[j]] })
		for _, p := range ps {
			visit(p, stack)
		}
		delete(stack, s)
		if !seen[s] {
			seen[s] = true
			out = append(out, s)
		}
	}
	for _, s := range sources {
		visit(s, map[string]bool{})
	}
	return out
}
