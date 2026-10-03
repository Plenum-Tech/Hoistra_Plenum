package write

import (
	"context"
	"fmt"

	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/pystr"
	"hoistra/engine/internal/resolve"
	"hoistra/engine/internal/rules"
)

// truthyStr is `str(v or "")`: "" for any falsy value.
func truthyStr(v cell.Cell) string {
	if !v.Truthy() {
		return ""
	}
	return v.PyStr()
}

// tableMemo remembers, for one source table, the lookups that found nothing. write_node resolves
// every row of a table before it inserts any of them, so within that pass nothing it looks up can
// appear: a miss stays a miss. Python repeats those queries row by row; the answers are the same.
type tableMemo struct {
	section map[[2]string]bool
	meter   map[string]bool
	ref     map[[2]string]bool // value: the miss was two matches, not none
}

func newTableMemo() *tableMemo {
	return &tableMemo{section: map[[2]string]bool{}, meter: map[string]bool{}, ref: map[[2]string]bool{}}
}

func memoKey(s string) string { return pystr.Lower(pystr.Strip(s)) }

// section is write_node._section_for.
func (w *writer) section(ctx context.Context, m *tableMemo, building cell.Cell, hint string) string {
	b := truthyStr(building)
	if b == "" || hint == "" {
		return ""
	}
	k := [2]string{b, memoKey(hint)}
	if m.section[k] {
		return ""
	}
	id, ok := w.res.Section(ctx, b, hint)
	if !ok {
		m.section[k] = true
	}
	return id
}

// meterFind is MeterResolver.find.
func (w *writer) meterFind(ctx context.Context, m *tableMemo, hint string) string {
	k := memoKey(hint)
	if k == "" || m.meter[k] {
		return ""
	}
	id, ok := w.res.MeterFind(ctx, hint)
	if !ok {
		m.meter[k] = true
	}
	return id
}

// reference is ReferenceResolver.resolve. A remembered miss still counts as a read and records the
// hint, as the query Python repeats would.
func (w *writer) reference(ctx context.Context, m *tableMemo, column, hint string) string {
	k := [2]string{column, memoKey(hint)}
	if k[1] == "" {
		return ""
	}
	if ambiguous, missed := m.ref[k]; missed {
		w.res.RefReads++
		if ambiguous {
			w.res.RefAmbiguous = append(w.res.RefAmbiguous, column+"="+hint)
		} else {
			if w.res.RefUnresolved[column] == nil {
				w.res.RefUnresolved[column] = map[string]bool{}
			}
			w.res.RefUnresolved[column][hint] = true
		}
		return ""
	}
	before := len(w.res.RefAmbiguous)
	id, ok := w.res.Reference(ctx, column, hint)
	if !ok {
		m.ref[k] = len(w.res.RefAmbiguous) > before
	}
	return id
}

// resolveRows is the second half of write_node's per-row pass: building, section, floor, meter and
// reference links, for every row of the table, before any of it is inserted.
func (w *writer) resolveRows(ctx context.Context, p *tablePlan) []*rules.Row {
	spec := &w.spec
	t := p.dest
	memo := newTableMemo()
	out := make([]*rules.Row, len(p.norm))
	for i, raw := range p.src.rows {
		safe := p.norm[i].Copy()
		hint := ""
		if spec.IsBuildingHintTable(t) {
			hint, _ = rules.BuildingHint(spec, raw)
		}
		if spec.IsBuildingLinked(t) && p.resolveCols["building_id"] && !rules.LooksLikeUUID(safe.Get("building_id")) {
			bid := ""
			if hint != "" {
				bid, _ = w.res.Building(ctx, hint)
			}
			if bid == "" && spec.IsBuildingViaAsset(t) {
				bid, _ = w.res.AssetBuilding(ctx, pystr.Strip(truthyStr(safe.Get("asset_id"))))
			}
			if bid == "" {
				bid = w.defaultBuilding
			}
			if bid != "" {
				safe.Set("building_id", cell.Of(bid))
				w.buildingsLinked++
			} else {
				safe.Pop("building_id")
			}
		}
		if t != "building_sections" && p.resolveCols["section_id"] && !rules.LooksLikeUUID(safe.Get("section_id")) {
			sh, _ := rules.SectionHint(spec, raw)
			if sid := w.section(ctx, memo, safe.Get("building_id"), sh); sid != "" {
				safe.Set("section_id", cell.Of(sid))
			} else {
				safe.Pop("section_id")
			}
		}
		if t == "building_sections" && p.resolveCols["floor_id"] && !rules.LooksLikeUUID(safe.Get("floor_id")) {
			fh, _ := rules.FloorHint(spec, raw)
			if fid, ok := w.res.Floor(ctx, truthyStr(safe.Get("building_id")), fh); ok {
				safe.Set("floor_id", cell.Of(fid))
			} else {
				safe.Pop("floor_id")
			}
		}
		if t == "energy_meters" {
			if !rules.LooksLikeUUID(safe.Get("id")) {
				if mh, _ := rules.MeterHint(spec, raw); mh != "" {
					if known := w.meterFind(ctx, memo, mh); known != "" {
						safe.Set("id", cell.Of(known))
						w.metersMatched++
					}
				}
			}
			if v := safe.Get("is_sub_meter"); v.IsNone() || (v.K == cell.Str && v.S == "") {
				safe.Set("is_sub_meter", cell.OfBool(rules.IsSubMeterFor(spec, raw)))
			}
			if !safe.Get("meter_type").Truthy() {
				safe.Set("meter_type", cell.Of(rules.MeterTypeFor(spec, raw)))
			}
		}
		for _, ref := range spec.References {
			col := ref.Column
			if !p.resolveCols[col] || rules.LooksLikeUUID(safe.Get(col)) {
				continue
			}
			rid := ""
			if rh, _ := rules.ReferenceHint(spec, col, raw); rh != "" {
				rid = w.reference(ctx, memo, col, rh)
			}
			if rid != "" {
				safe.Set(col, cell.Of(rid))
			} else {
				safe.Pop(col)
			}
		}
		if t == "meter_readings" && !rules.LooksLikeUUID(safe.Get("meter_id")) {
			mh, _ := rules.MeterHint(spec, raw)
			mid, mbid := "", ""
			if mh != "" {
				mpan, mprn := rules.SupplyNumbers(spec, raw)
				if hint != "" {
					mbid, _ = w.res.Building(ctx, hint)
				}
				if mbid == "" {
					mbid = w.defaultBuilding
				}
				sh, _ := rules.SectionHint(spec, raw)
				sid := w.section(ctx, memo, cell.Of(mbid), sh)
				mid, _ = w.res.MeterResolve(ctx, mh, resolve.MeterSpec{BuildingID: mbid,
					MeterType: rules.MeterTypeFor(spec, raw), MPAN: mpan, MPRN: mprn, SectionID: sid,
					IsSubMeter: rules.IsSubMeterFor(spec, raw)})
			}
			if mid != "" {
				safe.Set("meter_id", cell.Of(mid))
				w.metersLinked++
			} else {
				if !w.unlinkedReported {
					w.unlinkedReported = true
					w.logf("warning", unlinkedMessage(t, mh, hint, mbid))
				}
				safe.Pop("meter_id")
			}
		}
		out[i] = safe
		w.progress("resolve", t, i+1, len(p.norm))
	}
	return out
}

// unlinkedMessage is the one warning write_node logs for a reading it cannot place.
func unlinkedMessage(table, mh, hint, mbid string) string {
	r := func(s string) string {
		if s == "" {
			return "None"
		}
		return pyRepr(s)
	}
	why := ""
	switch {
	case mh == "":
		why = "No reference in the row"
	case mbid == "":
		why = "No building, so a meter cannot be created"
	default:
		why = "Lookup and create both returned nothing"
	}
	return fmt.Sprintf("[Node 9] %s: no meter for this row, so meter_id is null and the row cannot be written — "+
		"meter reference %s, building hint %s, resolved building %s. %s", table, r(mh), r(hint), r(mbid), why)
}
