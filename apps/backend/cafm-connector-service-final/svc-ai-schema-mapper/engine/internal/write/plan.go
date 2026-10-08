package write

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"

	"github.com/jackc/pgx/v5"

	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/coerce"
	"hoistra/engine/internal/protocol"
	"hoistra/engine/internal/resolve"
	"hoistra/engine/internal/rules"
)

// plan is the write gate's preview: the write's own preparation in a read-only transaction that
// is rolled back. No DDL runs, no meter is created, nothing is inserted. Links (building, meter,
// reference) need rows written earlier in the run, so they are counted as "to resolve", not resolved.
func (w *writer) plan(ctx context.Context) (*Result, error) {
	tx, err := w.conn.BeginTx(ctx, pgx.TxOptions{AccessMode: pgx.ReadOnly})
	if err != nil {
		return nil, wrapDB(err)
	}
	defer func() { _ = tx.Rollback(context.Background()) }()
	plans, err := w.prepare(ctx, tx, nil)
	if err != nil {
		return nil, err
	}
	w.res = resolve.New(tx, &w.spec, w.org, w.job.Schema, nil, nil)
	pl := &Plan{DDLStatements: len(w.job.DDL), Tables: []PlanTable{}}
	if pl.InputHash, err = w.inputHash(); err != nil {
		return nil, err
	}
	for _, p := range plans {
		pt := PlanTable{Source: p.src.name, Dest: p.dest, Rows: len(p.src.rows), CreatesTable: p.created,
			NewColumns: []string{}, DroppedColumns: append([]string{}, p.dropped...), WidenToText: append([]string{}, p.widened...),
			InvalidValues: []InvalidValue{}, RowsCannotWrite: []ReasonCount{}, ReferencesToResolve: map[string]int{}}
		for _, c := range p.add {
			pt.NewColumns = append(pt.NewColumns, c.name)
		}
		rows, toResolve, noMeter := w.planRows(p)
		pt.ReferencesToResolve = toResolve
		tally := &mismatchTally{}
		items := w.build(p, rows, tally)
		for _, c := range tally.order {
			pt.InvalidValues = append(pt.InvalidValues, InvalidValue{Column: c, Count: tally.count[c],
				DestType: tally.dtype[c], Sample: tally.first[c]})
		}
		if p.dest == "assets" {
			if err := w.prefetchAssets(ctx, tx, p, items); err != nil {
				return nil, err
			}
		}
		known, err := w.prefetchKeys(ctx, tx, p, items)
		if err != nil {
			return nil, err
		}
		reasons := &counts{}
		if noMeter > 0 {
			reasons.add("names no meter, so it cannot be placed", noMeter)
		}
		for _, it := range items {
			if it.skipped {
				continue
			}
			if p.dest == "assets" && it.code != "" {
				id, err := w.existingAsset(ctx, tx, p, it.code)
				if err != nil {
					return nil, err
				}
				if id != "" {
					pt.MergeExistingAssets++
					continue
				}
			}
			dupe := false
			for _, km := range it.keys {
				hit, err := w.alreadyWritten(ctx, tx, p, km, known)
				if err != nil {
					return nil, err
				}
				if hit {
					dupe = true
					break
				}
			}
			if dupe {
				pt.AlreadyPresent++
				continue
			}
			if why := w.cannotWrite(p, it.full); why != "" {
				reasons.add(why, 1)
			}
		}
		for _, r := range reasons.order {
			pt.RowsCannotWrite = append(pt.RowsCannotWrite, ReasonCount{Reason: r, Count: reasons.n[r]})
		}
		pl.Tables = append(pl.Tables, pt)
	}
	if w.job.OutDir != "" {
		if err := os.MkdirAll(w.job.OutDir, 0o755); err != nil {
			return nil, protocol.Errorf(protocol.CodeInternal, "cannot create the plan directory: %v", err)
		}
		b, _ := json.MarshalIndent(pl, "", " ")
		if err := os.WriteFile(filepath.Join(w.job.OutDir, "plan.json"), b, 0o644); err != nil {
			return nil, protocol.Errorf(protocol.CodeInternal, "cannot write plan.json: %v", err)
		}
	}
	return &Result{Plan: pl, OrganizationID: w.org, RowErrors: []string{}, MetersUnlinked: []string{},
		References: map[string]any{}, Tables: []TableResult{}}, nil
}

// planRows is a table's rows with every value the write would resolve taken out — they become
// ids or nothing — and, per such column, how many rows carry something to resolve.
func (w *writer) planRows(p *tablePlan) ([]*rules.Row, map[string]int, int) {
	spec := &w.spec
	t := p.dest
	toResolve := map[string]int{}
	noMeter := 0
	out := make([]*rules.Row, len(p.norm))
	take := func(safe *rules.Row, col string, hint string) {
		if rules.LooksLikeUUID(safe.Get(col)) {
			return
		}
		safe.Pop(col)
		if hint != "" {
			toResolve[col]++
		}
	}
	for i, raw := range p.src.rows {
		safe := p.norm[i].Copy()
		if spec.IsBuildingLinked(t) && p.resolveCols["building_id"] {
			h, _ := rules.BuildingHint(spec, raw)
			take(safe, "building_id", h)
		}
		if t != "building_sections" && p.resolveCols["section_id"] {
			h, _ := rules.SectionHint(spec, raw)
			take(safe, "section_id", h)
		}
		if t == "building_sections" && p.resolveCols["floor_id"] {
			h, _ := rules.FloorHint(spec, raw)
			take(safe, "floor_id", h)
		}
		if t == "energy_meters" {
			if v := safe.Get("is_sub_meter"); v.IsNone() || (v.K == cell.Str && v.S == "") {
				safe.Set("is_sub_meter", cell.OfBool(rules.IsSubMeterFor(spec, raw)))
			}
			if !safe.Get("meter_type").Truthy() {
				safe.Set("meter_type", cell.Of(rules.MeterTypeFor(spec, raw)))
			}
		}
		for _, ref := range spec.References {
			if p.resolveCols[ref.Column] {
				h, _ := rules.ReferenceHint(spec, ref.Column, raw)
				take(safe, ref.Column, h)
			}
		}
		if t == "meter_readings" && !rules.LooksLikeUUID(safe.Get("meter_id")) {
			h, _ := rules.MeterHint(spec, raw)
			safe.Pop("meter_id")
			if h != "" {
				toResolve["meter_id"]++
			} else {
				noMeter++
			}
		}
		out[i] = safe
	}
	return out, toResolve, noMeter
}

// resolvedLater is a column the write fills from a lookup.
func (w *writer) resolvedLater(p *tablePlan, col string) bool {
	switch col {
	case "building_id", "section_id", "floor_id", "meter_id":
		return true
	}
	_, ok := w.spec.Reference(col)
	return ok
}

// cannotWrite predicts why a row would fail: asyncpg refusing a value, Postgres rejecting one
// (too long, out of range, not valid JSON, not a label of the enum), or a required column with
// nothing in it. Constraints between rows (unique, foreign keys) are not predicted.
func (w *writer) cannotWrite(p *tablePlan, f *rules.Filtered) string {
	for i, c := range f.K {
		col := p.colInfo[c]
		if col == nil {
			continue
		}
		if _, why := pyOf(f.V[i]).encode(col); why != "" {
			return fmt.Sprintf("%s: %s", c, why)
		}
		if err := coerce.BindCheck(f.V[i], col); err != nil {
			return fmt.Sprintf("%s: %v", c, err)
		}
	}
	for _, c := range p.required {
		if !hasKey(f, c) && !w.resolvedLater(p, c) {
			return fmt.Sprintf("%s is required and the file has no value for it", c)
		}
	}
	return ""
}

// inputHash identifies what the plan was made from: the cleaned tables and every job field that
// changes the write. The Python side reuses a plan whose hash matches.
func (w *writer) inputHash() (string, error) {
	h := sha256.New()
	entries, err := os.ReadDir(w.job.CleanedDir)
	if err != nil {
		return "", protocol.Errorf(protocol.CodeData, "cannot read the cleaned tables: %v", err)
	}
	names := make([]string, 0, len(entries))
	for _, e := range entries {
		if !e.IsDir() {
			names = append(names, e.Name())
		}
	}
	sort.Strings(names)
	for _, n := range names {
		f, err := os.Open(filepath.Join(w.job.CleanedDir, n))
		if err != nil {
			return "", protocol.Errorf(protocol.CodeData, "cannot read %s: %v", n, err)
		}
		fmt.Fprintf(h, "%s\x00", n)
		_, err = io.Copy(h, f)
		f.Close()
		if err != nil {
			return "", protocol.Errorf(protocol.CodeData, "cannot read %s: %v", n, err)
		}
	}
	j := w.job
	j.Mode, j.OutDir, j.CleanedDir = "", "", ""
	b, _ := json.Marshal(j)
	h.Write(b)
	return hex.EncodeToString(h.Sum(nil)), nil
}
