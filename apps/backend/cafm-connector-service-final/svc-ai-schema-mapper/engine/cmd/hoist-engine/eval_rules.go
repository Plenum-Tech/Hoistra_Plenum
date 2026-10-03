package main

import (
	"context"
	"encoding/json"
	"fmt"

	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/coerce"
	"hoistra/engine/internal/protocol"
	"hoistra/engine/internal/rules"
)

func encodeCell(c cell.Cell) any {
	switch c.K {
	case cell.Str:
		return map[string]any{"s": c.S}
	case cell.Int:
		return map[string]any{"i": c.I}
	case cell.Bool:
		return map[string]any{"b": c.I != 0}
	}
	return nil
}

func encodeValue(v coerce.Value) map[string]string {
	out := map[string]string{"k": v.K.String()}
	if v.K != coerce.Null && v.K != coerce.Mismatch {
		out["s"] = v.S
	}
	return out
}

func decodeRow(raw [][2]json.RawMessage) (*rules.Row, error) {
	row := rules.NewRow(len(raw))
	for _, kv := range raw {
		var k string
		if err := json.Unmarshal(kv[0], &k); err != nil {
			return nil, err
		}
		var c *jsonCell
		if err := json.Unmarshal(kv[1], &c); err != nil {
			return nil, err
		}
		row.Set(k, c.cell())
	}
	return row, nil
}

func strOrNil(s string, ok bool) any {
	if !ok || s == "" {
		return nil
	}
	return s
}

type ruleCase struct {
	Fn          string               `json:"fn"`
	Table       string               `json:"table"`
	Row         [][2]json.RawMessage `json:"row"`
	Org         string               `json:"org"`
	Column      string               `json:"column"`
	Raw         string               `json:"raw"`
	DBCols      []string             `json:"db_cols"`
	Filtered    [][3]json.RawMessage `json:"filtered"`
	Col         string               `json:"col"`
	DBType      string               `json:"db_type"`
	Sources     []string             `json:"sources"`
	Routing     map[string]string    `json:"routing"`
	Hierarchies []rules.Edge         `json:"hierarchies"`
	Tables      [][2]json.RawMessage `json:"tables"`
	Value       *jsonCell            `json:"value"`
}

func filteredOf(raw [][3]json.RawMessage) (*rules.Filtered, error) {
	f := &rules.Filtered{}
	for _, e := range raw {
		var k, t string
		var c *jsonCell
		if err := json.Unmarshal(e[0], &k); err != nil {
			return nil, err
		}
		if err := json.Unmarshal(e[1], &c); err != nil {
			return nil, err
		}
		if err := json.Unmarshal(e[2], &t); err != nil {
			return nil, err
		}
		v := c.cell()
		if v.IsNone() || v.PyStr() == "" {
			continue
		}
		cv := coerce.ForType(v, t)
		if cv.K == coerce.Mismatch || cv.K == coerce.Null {
			continue
		}
		f.Set(k, cv)
	}
	return f, nil
}

func evalRule(spec *rules.Spec, c ruleCase) (any, error) {
	switch c.Fn {
	case "normalize":
		row, err := decodeRow(c.Row)
		if err != nil {
			return nil, err
		}
		n := rules.Normalize(spec, c.Table, row, c.Org)
		out := make([][2]any, n.Len())
		for i := range n.K {
			out[i] = [2]any{n.K[i], encodeCell(n.V[i])}
		}
		return out, nil
	case "building_hint", "meter_hint", "section_hint", "floor_hint", "meter_type_for", "is_sub_meter_for",
		"supply_numbers", "reference_hint":
		row, err := decodeRow(c.Row)
		if err != nil {
			return nil, err
		}
		switch c.Fn {
		case "building_hint":
			return strOrNil(rules.BuildingHint(spec, row)), nil
		case "meter_hint":
			return strOrNil(rules.MeterHint(spec, row)), nil
		case "section_hint":
			return strOrNil(rules.SectionHint(spec, row)), nil
		case "floor_hint":
			return strOrNil(rules.FloorHint(spec, row)), nil
		case "meter_type_for":
			return rules.MeterTypeFor(spec, row), nil
		case "is_sub_meter_for":
			return rules.IsSubMeterFor(spec, row), nil
		case "supply_numbers":
			mpan, mprn := rules.SupplyNumbers(spec, row)
			return []any{strOrNil(mpan, true), strOrNil(mprn, true)}, nil
		default:
			return strOrNil(rules.ReferenceHint(spec, c.Column, row)), nil
		}
	case "safe_ident":
		return strOrNil(rules.SafeIdent(c.Raw)), nil
	case "natural_keys", "asset_match_code":
		f, err := filteredOf(c.Filtered)
		if err != nil {
			return nil, err
		}
		if c.Fn == "asset_match_code" {
			return strOrNil(rules.AssetMatchCode(f)), nil
		}
		db := map[string]bool{}
		for _, d := range c.DBCols {
			db[d] = true
		}
		out := []any{}
		for _, m := range rules.NaturalKeys(spec, c.Table, f, db) {
			vals := make([]any, len(m.Vals))
			for i, v := range m.Vals {
				vals[i] = encodeValue(v)
			}
			out = append(out, []any{m.Cols, vals})
		}
		return out, nil
	case "system_default":
		v, ok := rules.SystemDefault(c.Col, c.DBType, c.Org)
		if !ok {
			return nil, nil
		}
		return encodeCell(v), nil
	case "write_order":
		return rules.WriteOrder(spec, c.Sources, c.Routing, c.Hierarchies), nil
	case "site_names":
		var names []string
		tables := map[string][]*rules.Row{}
		for _, t := range c.Tables {
			var name string
			var rows [][][2]json.RawMessage
			if err := json.Unmarshal(t[0], &name); err != nil {
				return nil, err
			}
			if err := json.Unmarshal(t[1], &rows); err != nil {
				return nil, err
			}
			names = append(names, name)
			for _, r := range rows {
				row, err := decodeRow(r)
				if err != nil {
					return nil, err
				}
				tables[name] = append(tables[name], row)
			}
		}
		order, m := rules.SiteNamesFromRun(spec, names, tables, c.Routing)
		out := make([][2]string, len(order))
		for i, k := range order {
			out[i] = [2]string{k, m[k]}
		}
		return out, nil
	case "looks_like_uuid":
		return rules.LooksLikeUUID(c.Value.cell()), nil
	}
	return nil, fmt.Errorf("unknown rule %q", c.Fn)
}

// rules-eval: the ported write rules over a list of cases, for tests/test_engine_rules_oracle.py.
func init() {
	register("rules-eval", func(_ context.Context, jobPath string, _ *protocol.Emitter) (any, error) {
		var job struct {
			Spec  rules.Spec `json:"spec"`
			Cases []ruleCase `json:"cases"`
		}
		if err := protocol.ReadJob(jobPath, &job); err != nil {
			return nil, err
		}
		results := make([]any, len(job.Cases))
		for i, c := range job.Cases {
			r, err := evalRule(&job.Spec, c)
			if err != nil {
				return nil, protocol.Errorf(protocol.CodeBadJob, "case %d: %v", i, err)
			}
			results[i] = r
		}
		return map[string]any{"results": results}, nil
	})
}
