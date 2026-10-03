// Package pdframe holds what ingest does to a parsed pandas frame whatever its source: pandas'
// default NA set, _sanitize_column_names, and to_dict(orient="records") over duplicate names.
package pdframe

import (
	"fmt"
	"strings"

	"hoistra/engine/internal/pystr"
)

// naValues is pandas' default NA set (keep_default_na=True).
var naValues = map[string]bool{
	"": true, "#N/A": true, "#N/A N/A": true, "#NA": true, "-1.#IND": true, "-1.#QNAN": true, "-NaN": true,
	"-nan": true, "1.#IND": true, "1.#QNAN": true, "<NA>": true, "N/A": true, "NA": true, "NULL": true, "NaN": true,
	"None": true, "n/a": true, "nan": true, "null": true,
}

// IsNA is a string pandas reads as NaN.
func IsNA(s string) bool { return naValues[s] }

// Sanitize is ingest_node._sanitize_column_names: an "Unnamed: …", empty or "nan" name becomes
// col_{position}; any other name is stripped.
func Sanitize(names []string) []string {
	out := make([]string, len(names))
	for i, raw := range names {
		s := pystr.Strip(raw)
		low := pystr.Lower(s)
		if s == "" || strings.HasPrefix(low, "unnamed:") || low == "nan" {
			out[i] = fmt.Sprintf("col_%d", i+1)
		} else {
			out[i] = s
		}
	}
	return out
}

// Collapse is what to_dict(orient="records") makes of duplicate names: each name keeps its first
// position, and a later column with the same name overwrites the value. cols are the record keys
// in order; target[i] is the key column i writes to.
func Collapse(names []string) (cols []string, target []int) {
	pos := make(map[string]int, len(names))
	target = make([]int, len(names))
	for i, n := range names {
		p, ok := pos[n]
		if !ok {
			p = len(cols)
			pos[n] = p
			cols = append(cols, n)
		}
		target[i] = p
	}
	return cols, target
}
