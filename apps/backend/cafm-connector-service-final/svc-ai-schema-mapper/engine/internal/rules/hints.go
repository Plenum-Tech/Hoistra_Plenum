package rules

import (
	"regexp"
	"strings"

	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/pystr"
)

const (
	Gas         = "gas"
	Electricity = "electricity"
)

var uuidRE = regexp.MustCompile(`^(?i)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

// LooksLikeUUID is building_link.looks_like_uuid: truthy and a UUID once stripped.
func LooksLikeUUID(v cell.Cell) bool {
	return v.Truthy() && uuidRE.MatchString(pystr.Strip(v.PyStr()))
}

func looksLikeUUIDStr(s string) bool { return s != "" && uuidRE.MatchString(pystr.Strip(s)) }

var nonAlnum = regexp.MustCompile(`[^a-z0-9]+`)

// normKey is the header normalisation of meter_link._keys / reference_link._keys.
func normKey(k string) string {
	return strings.Trim(nonAlnum.ReplaceAllString(pystr.Lower(pystr.Strip(k)), "_"), "_")
}

// keys is `_keys(row)`: the row under normalised headers, first spelling wins.
func keys(row *Row) map[string]cell.Cell {
	out := make(map[string]cell.Cell, row.Len())
	for i, k := range row.K {
		n := normKey(k)
		if n == "" {
			continue
		}
		if _, seen := out[n]; !seen {
			out[n] = row.V[i]
		}
	}
	return out
}

func get(m map[string]cell.Cell, k string) cell.Cell {
	if v, ok := m[k]; ok {
		return v
	}
	return cell.None
}

// clean is meter_link._clean: str(v).strip() unless v is None or "".
func clean(v cell.Cell) string {
	if v.IsNone() || (v.K == cell.Str && v.S == "") {
		return ""
	}
	return pystr.Strip(v.PyStr())
}

// BuildingHint is building_link.building_hint.
func BuildingHint(spec *Spec, row *Row) (string, bool) {
	if LooksLikeUUID(row.Get("building_id")) {
		return "", false
	}
	for _, key := range spec.BuildingHintKeys {
		v := row.Get(key)
		if v.IsNone() {
			continue
		}
		s := pystr.Strip(v.PyStr())
		if s != "" && !looksLikeUUIDStr(s) {
			return s, true
		}
	}
	return "", false
}

// MeterHint is meter_link.meter_hint.
func MeterHint(spec *Spec, row *Row) (string, bool) {
	if LooksLikeUUID(row.Get("meter_id")) {
		return "", false
	}
	lower := keys(row)
	for _, key := range spec.MeterHintKeys {
		s := clean(get(lower, key))
		if s != "" && !looksLikeUUIDStr(s) {
			return s, true
		}
	}
	return "", false
}

func firstClean(lower map[string]cell.Cell, ks []string) string {
	for _, k := range ks {
		if s := clean(get(lower, k)); s != "" {
			return s
		}
	}
	return ""
}

// SupplyNumbers is meter_link.supply_numbers: (mpan, mprn), "" meaning None.
func SupplyNumbers(spec *Spec, row *Row) (string, string) {
	lower := keys(row)
	mpan := firstClean(lower, spec.MPANKeys)
	mprn := firstClean(lower, spec.MPRNKeys)
	if mpan != "" || mprn != "" {
		return mpan, mprn
	}
	amb := firstClean(lower, spec.EitherKeys)
	if amb == "" || looksLikeUUIDStr(amb) {
		return "", ""
	}
	if fuelWord(spec, lower) == Gas {
		return "", amb
	}
	return amb, ""
}

func fuelWord(spec *Spec, lower map[string]cell.Cell) string {
	for _, key := range spec.FuelKeys {
		s := pystr.Lower(clean(get(lower, key)))
		if s == "" {
			continue
		}
		for _, w := range spec.GasWords {
			if strings.Contains(s, w) {
				return Gas
			}
		}
		for _, w := range spec.ElecWords {
			if strings.Contains(s, w) {
				return Electricity
			}
		}
	}
	return ""
}

// SectionHint is meter_link.section_hint.
func SectionHint(spec *Spec, row *Row) (string, bool) {
	lower := keys(row)
	for _, key := range spec.SectionKeys {
		v := clean(get(lower, key))
		if v != "" && !looksLikeUUIDStr(v) {
			return v, true
		}
	}
	return "", false
}

// IsSubMeterFor is meter_link.is_sub_meter_for.
func IsSubMeterFor(spec *Spec, row *Row) bool {
	lower := keys(row)
	for _, key := range spec.SubMeterKeys {
		v := pystr.Lower(clean(get(lower, key)))
		if v == "" {
			continue
		}
		switch v {
		case "true", "yes", "y", "1", "sub", "submeter", "sub_meter", "sub-meter", "tenant":
			return true
		case "false", "no", "n", "0", "main", "incoming", "primary", "site":
			return false
		}
	}
	_, ok := SectionHint(spec, row)
	return ok
}

// FloorHint is meter_link.floor_hint.
func FloorHint(spec *Spec, row *Row) (string, bool) {
	lower := keys(row)
	for _, key := range spec.FloorKeys {
		v := clean(get(lower, key))
		if v != "" && !looksLikeUUIDStr(v) {
			return v, true
		}
	}
	return "", false
}

// MeterTypeFor is meter_link.meter_type_for.
func MeterTypeFor(spec *Spec, row *Row) string {
	if stated := fuelWord(spec, keys(row)); stated != "" {
		return stated
	}
	mpan, mprn := SupplyNumbers(spec, row)
	if mprn != "" && mpan == "" {
		return Gas
	}
	return Electricity
}

// ReferenceHint is reference_link.hint_for.
func ReferenceHint(spec *Spec, column string, row *Row) (string, bool) {
	ref, ok := spec.Reference(column)
	if !ok {
		return "", false
	}
	lower := keys(row)
	if LooksLikeUUID(get(lower, column)) {
		return "", false
	}
	for _, key := range ref.HintKeys {
		v := get(lower, key)
		s := ""
		if !(v.IsNone() || (v.K == cell.Str && v.S == "")) {
			s = pystr.Strip(v.PyStr())
		}
		if s != "" && !looksLikeUUIDStr(s) {
			return s, true
		}
	}
	return "", false
}

// SiteNamesFromRun is building_link.site_names_from_run: reference (lower-cased) → name, in
// insertion order.
func SiteNamesFromRun(spec *Spec, names []string, tables map[string][]*Row, routing map[string]string) ([]string, map[string]string) {
	var order []string
	out := map[string]string{}
	add := func(k, v string) {
		if _, seen := out[k]; !seen {
			out[k] = v
			order = append(order, k)
		}
	}
	for _, source := range names {
		dest, routed := routing[source]
		if !routed {
			dest = source
		}
		if !contains(spec.SiteTables, pystr.Lower(dest)) && !contains(spec.SiteTables, pystr.Lower(source)) {
			continue
		}
		for _, r := range tables[source] {
			name := ""
			for _, k := range []string{"site_name", "building_name", "name"} {
				if v := r.Get(k); !v.Empty() {
					name = pystr.Strip(v.PyStr())
					break
				}
			}
			if name == "" {
				continue
			}
			for _, k := range []string{"site_id", "site_code", "building_code", "id", "code", "site_ref"} {
				if ref := r.Get(k); !ref.Empty() && !LooksLikeUUID(ref) {
					add(pystr.Lower(pystr.Strip(ref.PyStr())), name)
				}
			}
			add(pystr.Lower(name), name)
		}
	}
	return order, out
}
