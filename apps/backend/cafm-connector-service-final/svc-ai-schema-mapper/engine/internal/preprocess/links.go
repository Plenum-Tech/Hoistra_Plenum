package preprocess

import (
	"sort"
	"strings"

	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/pystr"
)

// Link is a measured relationship for the hierarchy model: how much of Table.Column's distinct
// values (lower-cased, stripped) the referenced column holds.
type Link struct {
	Table       string  `json:"table"`
	Column      string  `json:"column"`
	References  string  `json:"references"`
	Containment float64 `json:"containment"`
}

var keySuffixes = []string{"_id", "_code", "_ref", "_no"}

func hasKeySuffix(c string) bool {
	low := pystr.Lower(c)
	for _, s := range keySuffixes {
		if strings.HasSuffix(low, s) {
			return true
		}
	}
	return false
}

// singular is a table name's singular, as a column naming it would spell it (sites → site,
// properties → property, addresses → address).
func singular(name string) string {
	n := pystr.Lower(name)
	switch {
	case strings.HasSuffix(n, "ies") && len(n) > 3:
		return n[:len(n)-3] + "y"
	case strings.HasSuffix(n, "sses"), strings.HasSuffix(n, "xes"), strings.HasSuffix(n, "ches"), strings.HasSuffix(n, "shes"):
		return n[:len(n)-2]
	case strings.HasSuffix(n, "ss"), strings.HasSuffix(n, "us"):
		return n
	case strings.HasSuffix(n, "s") && len(n) > 1:
		return n[:len(n)-1]
	}
	return n
}

func distinct(t built, c int) map[string]struct{} {
	out := map[string]struct{}{}
	for _, r := range t.rows {
		v := r[c]
		if v.K != cell.Str { // None, and the numeric fill's 0, are not values
			continue
		}
		if k := pystr.Lower(pystr.Strip(v.S)); k != "" {
			out[k] = struct{}{}
		}
	}
	return out
}

func sortLinks(links []Link) {
	sort.SliceStable(links, func(i, j int) bool { return links[i].Containment > links[j].Containment })
}

// measureLinks pairs the columns whose names relate across tables — the same name, or a key-like
// name (…_id, …_code, …_ref, …_no) naming the other table, with that table's confirmed key (else
// its key-like columns) — and measures containment; the top maxPairs, highest first.
func measureLinks(tables []built, keys map[string][]string, maxPairs int) []Link {
	type pair struct{ a, ac, b, bc int }
	var pairs []pair
	seen := map[[4]int]bool{}
	add := func(p pair) {
		k := [4]int{p.a, p.ac, p.b, p.bc}
		if !seen[k] {
			seen[k] = true
			pairs = append(pairs, p)
		}
	}
	for ai, A := range tables {
		for ac, a := range A.cols {
			for bi, B := range tables {
				if bi == ai {
					continue
				}
				for bc, b := range B.cols {
					if b == a {
						add(pair{ai, ac, bi, bc})
					}
				}
				if !hasKeySuffix(a) || !strings.Contains(pystr.Lower(a), singular(B.name)) {
					continue
				}
				var targets []int
				for bc, b := range B.cols {
					for _, k := range keys[B.name] {
						if b == k {
							targets = append(targets, bc)
						}
					}
				}
				if len(targets) == 0 {
					for bc, b := range B.cols {
						if hasKeySuffix(b) || pystr.Lower(b) == "id" || pystr.Lower(b) == "code" {
							targets = append(targets, bc)
						}
					}
				}
				for _, bc := range targets {
					add(pair{ai, ac, bi, bc})
				}
			}
		}
	}
	cache := map[[2]int]map[string]struct{}{}
	values := func(t, c int) map[string]struct{} {
		k := [2]int{t, c}
		if d, ok := cache[k]; ok {
			return d
		}
		d := distinct(tables[t], c)
		cache[k] = d
		return d
	}
	links := []Link{}
	for _, p := range pairs {
		da, db := values(p.a, p.ac), values(p.b, p.bc)
		if len(da) == 0 {
			continue
		}
		in := 0
		for v := range da {
			if _, ok := db[v]; ok {
				in++
			}
		}
		if in == 0 {
			continue
		}
		links = append(links, Link{Table: tables[p.a].name, Column: tables[p.a].cols[p.ac],
			References: tables[p.b].name + "." + tables[p.b].cols[p.bc], Containment: float64(in) / float64(len(da))})
	}
	sortLinks(links)
	if maxPairs > 0 && len(links) > maxPairs {
		links = links[:maxPairs]
	}
	return links
}
