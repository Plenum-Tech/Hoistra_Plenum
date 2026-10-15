package outputs

import (
	"bufio"
	"sort"
	"strconv"
	"strings"

	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/pystr"
)

var defaultNesting = []string{"sites", "locations", "assets", "work_orders", "tasks", "parts", "maintenance_plans"}

// nestingOrder is json_builder._infer_nesting_order.
func nestingOrder(hier []Hierarchy) []string {
	extracted := map[string]bool{}
	for _, h := range hier {
		if rt, ok := sp(h.RelationshipType); ok && rt == "CONTAINMENT" {
			if s, ok := sp(h.SourceTable); ok {
				extracted[s] = true
			}
			if t, ok := sp(h.TargetTable); ok {
				extracted[t] = true
			}
		}
	}
	if len(extracted) > 0 {
		var order []string
		for _, t := range defaultNesting {
			if extracted[t] {
				order = append(order, t)
			}
		}
		if len(order) > 0 {
			return order
		}
	}
	return defaultNesting
}

// node is one record of the nested hierarchy; with listKey set it is the record's copy with the
// next level's records under listKey (json_builder._build_nested_level).
type node struct {
	t       *table
	row     int
	listKey string
	list    []*node
}

type nester struct {
	byName map[string]*table
	order  []string
	hier   []Hierarchy
}

func get(t *table, row int, col *string) cell.Cell {
	c, ok := sp(col)
	if !ok {
		return cell.None
	}
	i := t.t.ColumnIndex(c)
	if i < 0 {
		return cell.None
	}
	return t.at(i, row)
}

func (n *nester) level(t *table, row, lvl int) *node {
	nd := &node{t: t, row: row}
	if lvl >= len(n.order)-1 {
		return nd
	}
	cur, next := n.order[lvl], n.order[lvl+1]
	nt, ok := n.byName[next]
	if !ok {
		return nd
	}
	var fk *Hierarchy
	for i := range n.hier {
		h := &n.hier[i]
		s, ok1 := sp(h.SourceTable)
		tt, ok2 := sp(h.TargetTable)
		rt, ok3 := sp(h.RelationshipType)
		if ok1 && ok2 && ok3 && s == next && tt == cur && rt == "CONTAINMENT" {
			fk = h
			break
		}
	}
	if fk == nil {
		return nd
	}
	pk := get(t, row, fk.TargetColumn)
	if !pk.Truthy() {
		return nd
	}
	want := pystr.Lower(pk.PyStr())
	nd.listKey = next + "_list"
	nd.list = []*node{}
	for r := 0; r < nt.rows(); r++ {
		v := get(nt, r, fk.SourceColumn)
		if v.Truthy() && pystr.Lower(v.PyStr()) == want {
			nd.list = append(nd.list, n.level(nt, r, lvl+1))
		}
	}
	return nd
}

// nestedSites is build_nested_json(...)["sites"].
func nestedSites(records []*table, hier []Hierarchy) []*node {
	n := &nester{byName: map[string]*table{}, order: nestingOrder(hier), hier: hier}
	for _, t := range records {
		n.byName[t.name] = t
	}
	sites := ""
	for _, t := range n.order {
		l := pystr.Lower(t)
		if strings.Contains(l, "site") || strings.Contains(l, "location") {
			sites = t
			break
		}
	}
	var out []*node
	if st, ok := n.byName[sites]; ok && sites != "" {
		for r := 0; r < st.rows(); r++ {
			out = append(out, n.level(st, r, 0))
		}
	}
	return out
}

// ── output.json ───────────────────────────────────────────────────────────────────────────

func writeRecord(w *bufio.Writer, t *table, row, depth int, listKey string, list []*node) {
	cols := t.cols()
	extra := listKey != "" && t.t.ColumnIndex(listKey) < 0
	if len(cols) == 0 && !extra {
		w.WriteString("{}")
		return
	}
	w.WriteByte('{')
	first := true
	item := func(k string) {
		if !first {
			w.WriteByte(',')
		}
		first = false
		indent(w, depth+1)
		writeJSONString(w, k)
		w.WriteString(": ")
	}
	for c, name := range cols {
		item(name)
		if listKey != "" && name == listKey {
			writeNodes(w, list, depth+1)
			continue
		}
		writeJSONCell(w, t.at(c, row))
	}
	if extra {
		item(listKey)
		writeNodes(w, list, depth+1)
	}
	indent(w, depth)
	w.WriteByte('}')
}

func writeNodes(w *bufio.Writer, nodes []*node, depth int) {
	if len(nodes) == 0 {
		w.WriteString("[]")
		return
	}
	w.WriteByte('[')
	for i, nd := range nodes {
		if i > 0 {
			w.WriteByte(',')
		}
		indent(w, depth+1)
		writeRecord(w, nd.t, nd.row, depth+1, nd.listKey, nd.list)
	}
	indent(w, depth)
	w.WriteByte(']')
}

// writeOutputJSON is json.dumps({"nested_hierarchy", "tables", "table_count", "tables_included",
// "generated_at"}, indent=2), written one table at a time.
func writeOutputJSON(w *bufio.Writer, records []*table, hier []Hierarchy, generatedAt string) {
	w.WriteByte('{')
	indent(w, 1)
	w.WriteString(`"nested_hierarchy": {`)
	indent(w, 2)
	w.WriteString(`"sites": `)
	writeNodes(w, nestedSites(records, hier), 2)
	indent(w, 1)
	w.WriteString("},")
	indent(w, 1)
	w.WriteString(`"tables": `)
	if len(records) == 0 {
		w.WriteString("{}")
	} else {
		w.WriteByte('{')
		for i, t := range records {
			if i > 0 {
				w.WriteByte(',')
			}
			indent(w, 2)
			writeJSONString(w, t.name)
			w.WriteString(": ")
			if t.rows() == 0 {
				w.WriteString("[]")
				continue
			}
			w.WriteByte('[')
			for r := 0; r < t.rows(); r++ {
				if r > 0 {
					w.WriteByte(',')
				}
				indent(w, 3)
				writeRecord(w, t, r, 3, "", nil)
			}
			indent(w, 2)
			w.WriteByte(']')
		}
		indent(w, 1)
		w.WriteByte('}')
	}
	w.WriteByte(',')
	indent(w, 1)
	w.WriteString(`"table_count": ` + strconv.Itoa(len(records)) + ",")
	indent(w, 1)
	w.WriteString(`"tables_included": `)
	names := make([]string, len(records))
	for i, t := range records {
		names[i] = t.name
	}
	sort.Strings(names)
	if len(names) == 0 {
		w.WriteString("[]")
	} else {
		w.WriteByte('[')
		for i, n := range names {
			if i > 0 {
				w.WriteByte(',')
			}
			indent(w, 2)
			writeJSONString(w, n)
		}
		indent(w, 1)
		w.WriteByte(']')
	}
	w.WriteByte(',')
	indent(w, 1)
	w.WriteString(`"generated_at": `)
	writeJSONString(w, generatedAt)
	indent(w, 0)
	w.WriteByte('}')
}
