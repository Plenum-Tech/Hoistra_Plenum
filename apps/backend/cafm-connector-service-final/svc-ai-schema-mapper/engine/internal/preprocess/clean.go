package preprocess

import (
	"context"
	"strconv"
	"strings"

	"hoistra/engine/internal/arrowtab"
	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/pdframe"
	"hoistra/engine/internal/protocol"
)

// Job is the preprocess job (steps.preprocess_job): the parse's full data set in; the cleaned
// data set, the renamed full one and — for columns left to Python — their values out.
type Job struct {
	FullDir        string                       `json:"full_dir"`
	CleanedDir     string                       `json:"cleaned_dir"`
	RenamedFullDir string                       `json:"renamed_full_dir"`
	ScratchDir     string                       `json:"scratch_dir"`
	RenameByTable  map[string]map[string]string `json:"rename_by_table"`
	SkipByTable    map[string][]string          `json:"skip_by_table"`
	// EL-4.0 against the destination schema (read with HOIST_ENGINE_DSN, read-only) and the link
	// statistics for the hierarchy step.
	Schema                string              `json:"schema"`
	TableRouting          map[string]string   `json:"table_routing"`
	SystemSuppliedColumns []string            `json:"system_supplied_columns"`
	KeysByTable           map[string][]string `json:"keys_by_table"`
	MaxLinkPairs          int                 `json:"max_link_pairs"`
}

// TableReport is what preprocess did to one table.
type TableReport struct {
	Name               string   `json:"name"`
	RowsIn             int      `json:"rows_in"`
	RowsOut            int      `json:"rows_out"`
	DedupDropped       int      `json:"dedup_dropped"`
	NullColumnsDropped []string `json:"null_columns_dropped"`
	DateColumns        []string `json:"date_columns"`
	Renamed            int      `json:"renamed"`
	Skipped            []string `json:"skipped"`
}

// Deferred is a date column the engine leaves to Python's _coerce_dates: its values (after the
// null fill) are in the scratch "deferred" data set under Column; RenamedTo is the cleaned
// column its result belongs in ("" when the column does not reach the cleaned table).
type Deferred struct {
	Table     string `json:"table"`
	Column    string `json:"column"`
	RenamedTo string `json:"renamed_to"`
}

type Result struct {
	Tables            []TableReport   `json:"tables"`
	PythonDateColumns []Deferred      `json:"python_date_columns"`
	Prewrite          []PrewriteTable `json:"prewrite"`
	Links             []Link          `json:"links"`
}

// Run cleans every table of the full data set as preprocess_tables does.
func Run(ctx context.Context, job Job, dsn string, em *protocol.Emitter) (*Result, error) {
	if job.FullDir == "" || job.CleanedDir == "" || job.RenamedFullDir == "" || job.ScratchDir == "" {
		return nil, protocol.Errorf(protocol.CodeBadJob, "preprocess needs full_dir, cleaned_dir, renamed_full_dir and scratch_dir")
	}
	tables, err := arrowtab.ReadDir(job.FullDir)
	if err != nil {
		return nil, protocol.Errorf(protocol.CodeBadJob, "reading the full data set: %v", err)
	}
	res := &Result{Tables: []TableReport{}, PythonDateColumns: []Deferred{}, Prewrite: []PrewriteTable{}, Links: []Link{}}
	var cleaned, renamed, deferred []*arrowtab.Table
	var cleanedBuilt []built
	for k, t := range tables {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		rows := readRows(t)
		rename := job.RenameByTable[t.Name]
		renamed = append(renamed, mustTable(renameFull(t.Name, t.Columns, rows, rename)))
		if len(rows) == 0 {
			continue // preprocess_tables skips a table with no records
		}
		skip := map[string]bool{}
		for _, f := range job.SkipByTable[t.Name] {
			skip[f] = true
		}
		out := cleanTable(t.Name, t.Columns, rows, rename, skip)
		cleaned = append(cleaned, mustTable(out.table))
		cleanedBuilt = append(cleanedBuilt, out.table)
		res.Tables = append(res.Tables, out.report)
		if len(out.deferred) > 0 {
			res.PythonDateColumns = append(res.PythonDateColumns, out.deferred...)
			deferred = append(deferred, mustTable(out.deferredValues))
		}
		if em != nil {
			em.Progress("preprocess", t.Name, int64(k+1), int64(len(tables)))
		}
	}
	res.Links = measureLinks(cleanedBuilt, job.KeysByTable, job.MaxLinkPairs)
	if pw, err := prewrite(ctx, dsn, job.Schema, cleanedBuilt, job.TableRouting, job.SystemSuppliedColumns); err != nil {
		if em != nil {
			em.Log("warning", "EL-4.0 pre-write check skipped: "+err.Error())
		}
	} else {
		res.Prewrite = pw
	}
	for _, w := range []struct {
		dir string
		ts  []*arrowtab.Table
	}{{job.CleanedDir, cleaned}, {job.RenamedFullDir, renamed}, {job.ScratchDir + "/deferred", deferred}} {
		if err := arrowtab.WriteDir(w.dir, nonNilTables(w.ts)); err != nil {
			return nil, protocol.Errorf(protocol.CodeInternal, "writing %s: %v", w.dir, err)
		}
	}
	return res, nil
}

func nonNilTables(ts []*arrowtab.Table) []*arrowtab.Table {
	if ts == nil {
		return []*arrowtab.Table{}
	}
	return ts
}

type built struct {
	name string
	cols []string
	rows [][]cell.Cell
}

func mustTable(b built) *arrowtab.Table {
	t, err := arrowtab.FromRows(b.name, b.cols, b.rows)
	if err != nil {
		panic(err) // the engine only builds encodable tables (the 0 fill never shares a column with None)
	}
	return t
}

func readRows(t *arrowtab.Table) [][]cell.Cell {
	rows := make([][]cell.Cell, t.Rows)
	for r := range rows {
		row := make([]cell.Cell, len(t.Columns))
		for c := range row {
			row[c] = t.Cell(c, r)
		}
		rows[r] = row
	}
	return rows
}

// renameFull is rename_full_tables for one table: every row, columns renamed, a name two
// columns now share keeping its first place and the later value.
func renameFull(name string, cols []string, rows [][]cell.Cell, rename map[string]string) built {
	if len(rows) == 0 {
		return built{name: name, cols: []string{}, rows: nil}
	}
	names := make([]string, len(cols))
	for i, c := range cols {
		names[i] = c
		if to, ok := rename[c]; ok {
			names[i] = to
		}
	}
	out, outRows := collapse(names, rows)
	return built{name: name, cols: out, rows: outRows}
}

func collapse(names []string, rows [][]cell.Cell) ([]string, [][]cell.Cell) {
	cols, target := pdframe.Collapse(names)
	if len(cols) == len(names) {
		return cols, rows
	}
	out := make([][]cell.Cell, len(rows))
	for i, r := range rows {
		o := make([]cell.Cell, len(cols))
		for j, v := range r {
			o[target[j]] = v
		}
		out[i] = o
	}
	return cols, out
}

type cleanedTable struct {
	table          built
	report         TableReport
	deferred       []Deferred
	deferredValues built
}

// rowKey identifies a row's values exactly (None apart from every string).
func rowKey(r []cell.Cell, sb *strings.Builder) string {
	sb.Reset()
	for _, c := range r {
		if c.IsNone() {
			sb.WriteByte(0)
			continue
		}
		sb.WriteByte(1)
		sb.WriteString(strconv.Itoa(len(c.S)))
		sb.WriteByte(':')
		sb.WriteString(c.S)
	}
	return sb.String()
}

// cleanTable is preprocess_tables' loop body for one table.
func cleanTable(name string, cols []string, rows [][]cell.Cell, rename map[string]string, skip map[string]bool) cleanedTable {
	rep := TableReport{Name: name, RowsIn: len(rows), NullColumnsDropped: []string{}, DateColumns: []string{},
		Skipped: []string{}}
	// 1. dedup: exact duplicate rows, None equal to None, the first kept
	seen := make(map[string]struct{}, len(rows))
	var sb strings.Builder
	kept := rows[:0:0]
	for _, r := range rows {
		k := rowKey(r, &sb)
		if _, dup := seen[k]; dup {
			continue
		}
		seen[k] = struct{}{}
		kept = append(kept, r)
	}
	rep.DedupDropped = len(rows) - len(kept)
	// 2. columns with no value at all
	var live []int
	for c := range cols {
		allNull := true
		for _, r := range kept {
			if !r[c].IsNone() {
				allNull = false
				break
			}
		}
		if allNull {
			rep.NullColumnsDropped = append(rep.NullColumnsDropped, cols[c])
		} else {
			live = append(live, c)
		}
	}
	// 3. the fill by inferred type, then 4. the date coercion of date-named columns
	values := make([][]dval, len(live))
	for j, c := range live {
		var nonNull []string
		for _, r := range kept {
			if !r[c].IsNone() {
				nonNull = append(nonNull, r[c].S)
			}
		}
		fill := uint8(vNone)
		switch {
		case IsPandasNumeric(nonNull):
			fill = vInt0
		case containsDates(nonNull):
			fill = vNone
		default:
			fill = vStr
		}
		vs := make([]dval, len(kept))
		for i, r := range kept {
			switch {
			case !r[c].IsNone():
				vs[i] = dval{kind: vStr, s: r[c].S}
			case fill == vInt0:
				vs[i] = dval{kind: vInt0}
			case fill == vStr:
				vs[i] = dval{kind: vStr}
			}
		}
		values[j] = vs
	}
	var deferredIdx []int
	for j, c := range live {
		if !dateHinted(cols[c]) {
			continue
		}
		out, coerced, deferIt := coerceDates(values[j])
		switch {
		case deferIt:
			deferredIdx = append(deferredIdx, j)
		case coerced:
			values[j] = out
			rep.DateColumns = append(rep.DateColumns, cols[c])
		}
	}
	// 7. rename, 8. skip fields (matched after the rename), then the dict's collapse
	names := make([]string, len(live))
	for j, c := range live {
		names[j] = cols[c]
		if to, ok := rename[cols[c]]; ok {
			names[j] = to
			rep.Renamed++
		}
	}
	var keep []int
	for j, n := range names {
		if skip[n] {
			if !contains(rep.Skipped, n) {
				rep.Skipped = append(rep.Skipped, n)
			}
			continue
		}
		keep = append(keep, j)
	}
	outNames := make([]string, len(keep))
	for k, j := range keep {
		outNames[k] = names[j]
	}
	outRows := make([][]cell.Cell, len(kept))
	for i := range kept {
		r := make([]cell.Cell, len(keep))
		for k, j := range keep {
			r[k] = values[j][i].cell()
		}
		outRows[i] = r
	}
	finalCols, finalRows := collapse(outNames, outRows)
	if len(finalCols) == 0 {
		// to_dict(orient="records") on rows with no columns is []: the writer gets nothing to write
		// (the row count stays the frame's, as Python reports it)
		finalRows = nil
	}
	rep.RowsOut = len(kept)
	ct := cleanedTable{table: built{name: name, cols: finalCols, rows: finalRows}, report: rep}
	if len(deferredIdx) > 0 {
		// the winner of each name: the last kept column with it
		winner := map[string]int{}
		for _, j := range keep {
			winner[names[j]] = j
		}
		dcols := make([]string, len(deferredIdx))
		drows := make([][]cell.Cell, len(kept))
		for i := range drows {
			drows[i] = make([]cell.Cell, len(deferredIdx))
		}
		for k, j := range deferredIdx {
			dcols[k] = cols[live[j]]
			to := ""
			if w, ok := winner[names[j]]; ok && w == j {
				to = names[j]
			}
			ct.deferred = append(ct.deferred, Deferred{Table: name, Column: cols[live[j]], RenamedTo: to})
			for i := range kept {
				drows[i][k] = values[j][i].cell()
			}
		}
		ct.deferredValues = built{name: name, cols: dcols, rows: drows}
	}
	return ct
}

func (v dval) cell() cell.Cell {
	switch v.kind {
	case vStr:
		return cell.Of(v.s)
	case vInt0:
		return cell.OfInt(0)
	}
	return cell.None
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}
