package preprocess

import (
	"context"
	"sort"
	"strings"

	"github.com/jackc/pgx/v5"

	"hoistra/engine/internal/coerce"
	"hoistra/engine/internal/pgschema"
)

// PrewriteIssue is one column's values the write would drop (not the column's type) or refuse
// (the bind or input check fails), with up to three of them.
type PrewriteIssue struct {
	Column  string   `json:"column"`
	Reason  string   `json:"reason"`
	Count   int      `json:"count"`
	Samples []string `json:"samples"`
}

// PrewriteTable is EL-4.0 for one destination table, from its cleaned source tables.
type PrewriteTable struct {
	Dest            string          `json:"dest"`
	Sources         []string        `json:"sources"`
	Rows            int             `json:"rows"`
	Exists          bool            `json:"exists"`
	InvalidValues   []PrewriteIssue `json:"invalid_values"`
	RequiredMissing []string        `json:"required_missing"`
}

// writerFills is write_node._system_default_for_db_type having a value (and the generated id):
// a required column the writer fills itself when the source has nothing for it.
func writerFills(col, dbType string, systemSupplied map[string]bool) bool {
	c, t := strings.ToLower(col), strings.ToLower(dbType)
	if c == "id" || systemSupplied[c] {
		return true
	}
	switch {
	case strings.Contains(t, "bool"), strings.Contains(t, "json"):
		return true
	case c == "source" || c == "origin" || c == "created_by" || c == "source_system":
		return true
	case strings.Contains(t, "int"), strings.Contains(t, "numeric"), strings.Contains(t, "double"), strings.Contains(t, "real"):
		return true
	case strings.Contains(t, "char"), strings.Contains(t, "text"):
		return true
	}
	return false
}

// prewrite is EL-4.0 before the write: for each destination, the cleaned values its columns would
// drop or refuse (coerce.ForType / coerce.BindCheck, the writer's own rules) and the required
// columns no row fills and the writer cannot. It reads the destination schema (read-only) when a
// DSN is given; without one there is nothing to check against.
func prewrite(ctx context.Context, dsn, schema string, tables []built, routing map[string]string,
	systemSupplied []string) ([]PrewriteTable, error) {
	out := []PrewriteTable{}
	if dsn == "" || len(tables) == 0 {
		return out, nil
	}
	var dests []string
	sources := map[string][]built{}
	for _, t := range tables {
		d := t.name
		if r, ok := routing[t.name]; ok && r != "" {
			d = r
		}
		if _, seen := sources[d]; !seen {
			dests = append(dests, d)
		}
		sources[d] = append(sources[d], t)
	}
	conn, err := pgx.Connect(ctx, dsn)
	if err != nil {
		return nil, err
	}
	defer conn.Close(context.Background())
	tx, err := conn.BeginTx(ctx, pgx.TxOptions{AccessMode: pgx.ReadOnly})
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(context.Background())
	schemas, err := pgschema.Load(ctx, tx, schema, dests)
	if err != nil {
		return nil, err
	}
	sys := map[string]bool{}
	for _, c := range systemSupplied {
		sys[strings.ToLower(c)] = true
	}
	for _, d := range dests {
		pt := PrewriteTable{Dest: d, InvalidValues: []PrewriteIssue{}, RequiredMissing: []string{}}
		st := schemas[d]
		pt.Exists = st != nil && st.Exists
		filled := map[string]bool{}
		issues := map[string]*PrewriteIssue{}
		var order []string
		for _, t := range sources[d] {
			pt.Sources = append(pt.Sources, t.name)
			pt.Rows += len(t.rows)
			if !pt.Exists {
				continue
			}
			for c, name := range t.cols {
				col := st.ByName[name]
				if col == nil {
					continue
				}
				for _, r := range t.rows {
					v := coerce.ForType(r[c], col.DataType)
					reason := ""
					switch {
					case v.K == coerce.Mismatch:
						reason = "not a " + col.DataType
					case !v.IsNull():
						filled[name] = true
						if err := coerce.BindCheck(v, col); err != nil {
							reason = err.Error()
						}
					}
					if reason == "" {
						continue
					}
					key := name + "\x00" + reason
					iv := issues[key]
					if iv == nil {
						iv = &PrewriteIssue{Column: name, Reason: reason, Samples: []string{}}
						issues[key] = iv
						order = append(order, key)
					}
					iv.Count++
					if sample := r[c].PyStr(); len(iv.Samples) < 3 && !containsString(iv.Samples, sample) {
						iv.Samples = append(iv.Samples, sample)
					}
				}
			}
		}
		for _, k := range order {
			pt.InvalidValues = append(pt.InvalidValues, *issues[k])
		}
		if pt.Exists {
			for _, col := range st.Columns {
				if col.Nullable || col.Default != nil || filled[col.Name] || writerFills(col.Name, col.DataType, sys) {
					continue
				}
				pt.RequiredMissing = append(pt.RequiredMissing, col.Name)
			}
			sort.Strings(pt.RequiredMissing)
		}
		out = append(out, pt)
	}
	return out, nil
}

func containsString(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}
