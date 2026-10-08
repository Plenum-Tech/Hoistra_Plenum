package rules

import (
	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/pystr"
)

// Normalize is write_node._normalize_row_for_table.
func Normalize(spec *Spec, table string, row *Row, orgID string) *Row {
	n := row.Copy()
	t := pystr.Lower(table)
	if orgID != "" {
		n.SetDefault("organization_id", cell.Of(orgID))
	}
	switch t {
	case "assets":
		if n.Has("asset_id") {
			if !n.Has("id") {
				v := n.Pop("asset_id")
				n.Set("id", v)
			} else {
				n.Pop("asset_id")
			}
		}
		if n.Has("asset_type") {
			if !n.Get("asset_code").Truthy() {
				v := n.Pop("asset_type")
				n.Set("asset_code", v)
			} else {
				n.Pop("asset_type")
			}
		}
		for _, fk := range []string{"site_id", "location", "location_code", "category"} {
			n.Pop(fk)
		}
		move := func(src, dst string) {
			if n.Has(src) {
				if !n.Has(dst) {
					v := n.Pop(src)
					n.Set(dst, v)
				} else {
					n.Pop(src)
				}
			}
		}
		move("serial", "serial_number")
		move("install_date", "installation_date")
		if !n.Get("asset_name").Truthy() {
			n.Set("asset_name", or(n.Get("name"), n.Get("asset"), n.Get("asset_code"), cell.Of("Unknown Asset")))
		}
	case "locations":
		if n.Has("site_name") && !n.Has("name") {
			n.Set("name", n.Get("site_name"))
		}
		if n.Has("location_name") && !n.Has("name") {
			n.Set("name", n.Get("location_name"))
		}
		if n.Has("site_type") && !n.Has("type") {
			n.Set("type", n.Get("site_type"))
		}
		if !n.Get("type").Truthy() {
			n.Set("type", cell.Of("site"))
		}
	case "energy_meters":
		mpan, mprn := SupplyNumbers(spec, row)
		if mpan != "" && !n.Get("mpan").Truthy() {
			n.Set("mpan", cell.Of(mpan))
		}
		if mprn != "" && !n.Get("mprn").Truthy() {
			n.Set("mprn", cell.Of(mprn))
		}
		if !n.Get("meter_type").Truthy() {
			n.Set("meter_type", cell.Of(MeterTypeFor(spec, row)))
		}
		for _, k := range []string{"fuel", "fuel_type", "supply_type", "utility", "commodity", "energy_type",
			"mpan_mprn", "meter_ref", "meter_reference", "supply_number", "meter_number", "msn"} {
			n.Pop(k)
		}
	case "meter_readings":
		for _, p := range [][2]string{{"timestamp", "reading_at"}, {"read_at", "reading_at"},
			{"datetime", "reading_at"}, {"reading_date", "reading_at"}, {"kwh", "consumption_kwh"},
			{"consumption", "consumption_kwh"}, {"usage", "consumption_kwh"}, {"value", "consumption_kwh"}} {
			src, dst := p[0], p[1]
			if !n.Has(src) {
				continue
			}
			if !n.Get(dst).Truthy() {
				v := n.Pop(src)
				n.Set(dst, v)
			} else {
				n.Pop(src)
			}
		}
		for _, k := range []string{"mpan", "mprn", "mpan_mprn", "meter_ref", "meter_reference",
			"supply_number", "meter_number", "msn", "meter"} {
			n.Pop(k)
		}
	case "work_orders":
		if !n.Get("work_order_id").Truthy() {
			n.Set("work_order_id", or(n.Get("wo_code"), n.Get("work_order_number"), n.Get("wo_number"),
				n.Get("order_number")))
		}
		if !n.Get("title").Truthy() {
			n.Set("title", or(n.Get("description"), n.Get("wo_code"), n.Get("work_order_number"),
				n.Get("work_order_id"), cell.Of("Work Order")))
		}
	}
	return n
}
