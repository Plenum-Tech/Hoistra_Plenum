package rules

import (
	"regexp"
	"strings"

	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/coerce"
	"hoistra/engine/internal/pystr"
)

var (
	notIdentChar = regexp.MustCompile(`[^a-z0-9_]`)
	safeIdent    = regexp.MustCompile(`^[a-z_][a-z0-9_]{0,62}$`)
)

// SafeIdent is write_node._to_safe_identifier.
func SafeIdent(raw string) (string, bool) {
	n := pystr.Lower(pystr.Strip(raw))
	n = strings.ReplaceAll(strings.ReplaceAll(n, " ", "_"), "-", "_")
	n = notIdentChar.ReplaceAllString(n, "")
	if !safeIdent.MatchString(n) {
		return "", false
	}
	return n, true
}

// Filtered is the writer's `filtered` dict: column → coerced value, in insertion order.
type Filtered struct {
	K []string
	V []coerce.Value
}

func (f *Filtered) idx(k string) int {
	for i, x := range f.K {
		if x == k {
			return i
		}
	}
	return -1
}

func (f *Filtered) Get(k string) (coerce.Value, bool) {
	if i := f.idx(k); i >= 0 {
		return f.V[i], true
	}
	return coerce.Value{}, false
}

func (f *Filtered) Set(k string, v coerce.Value) {
	if i := f.idx(k); i >= 0 {
		f.V[i] = v
		return
	}
	f.K = append(f.K, k)
	f.V = append(f.V, v)
}

func (f *Filtered) Delete(k string) {
	if i := f.idx(k); i >= 0 {
		f.K = append(f.K[:i], f.K[i+1:]...)
		f.V = append(f.V[:i], f.V[i+1:]...)
	}
}

func (f *Filtered) Len() int { return len(f.K) }

// present is write_node._key_value_present: not missing, not blank. 0 and False say something.
func present(f *Filtered, c string) bool {
	v, ok := f.Get(c)
	return ok && !v.IsNull() && pystr.Strip(v.PyStr()) != ""
}

// KeyCol splits a natural-key column as the spec names it: "?unit" is the column unit, which may
// be empty and then matches only an empty one.
func KeyCol(c string) (string, bool) {
	if strings.HasPrefix(c, "?") {
		return strings.TrimLeft(c, "?"), true
	}
	return c, false
}

// KeyMatch is one natural key of a row: its columns and the row's values for them.
type KeyMatch struct {
	Cols []string
	Vals []coerce.Value
}

// NaturalKeys is write_node._natural_keys_for.
func NaturalKeys(spec *Spec, table string, f *Filtered, dbCols map[string]bool) []KeyMatch {
	var out []KeyMatch
	for _, group := range spec.NaturalKeys[table] {
		ok := true
		cols := make([]string, len(group))
		for i, g := range group {
			c, optional := KeyCol(g)
			cols[i] = c
			if !dbCols[c] || (!optional && !present(f, c)) {
				ok = false
				break
			}
		}
		if !ok {
			continue
		}
		vals := make([]coerce.Value, len(group))
		for i, c := range cols {
			vals[i], _ = f.Get(c) // a column the row lacks is None, as row.get(c) is
		}
		out = append(out, KeyMatch{Cols: cols, Vals: vals})
	}
	return out
}

// AssetMatchCode is building_link.asset_match_code over a filtered row.
func AssetMatchCode(f *Filtered) (string, bool) {
	if v, ok := f.Get("asset_code"); ok && v.Truthy() {
		if code := pystr.Strip(v.PyStr()); code != "" {
			return code, true
		}
	}
	if v, ok := f.Get("id"); ok && v.Truthy() {
		if ident := pystr.Strip(v.PyStr()); ident != "" && !looksLikeUUIDStr(ident) {
			return ident, true
		}
	}
	return "", false
}

// SystemDefault is write_node._system_default_for_db_type: a raw Python value, not coerced.
func SystemDefault(col, dbType, orgID string) (cell.Cell, bool) {
	t := pystr.Lower(dbType)
	c := pystr.Lower(col)
	switch {
	case (c == "org_id" || c == "organization_id") && orgID != "":
		return cell.Of(orgID), true
	case strings.Contains(t, "bool"):
		return cell.OfBool(false), true
	case strings.Contains(t, "json"):
		return cell.Of("{}"), true
	case c == "source" || c == "origin" || c == "created_by" || c == "source_system":
		return cell.Of("migration"), true
	case strings.Contains(t, "int") || strings.Contains(t, "numeric") || strings.Contains(t, "double") ||
		strings.Contains(t, "real"):
		return cell.OfInt(0), true
	case strings.Contains(t, "char") || strings.Contains(t, "text"):
		return cell.Of(""), true
	}
	return cell.None, false
}
