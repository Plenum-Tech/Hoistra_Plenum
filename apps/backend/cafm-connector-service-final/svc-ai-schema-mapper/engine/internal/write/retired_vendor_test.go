package write

import (
	"context"
	"reflect"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5"

	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/coerce"
	"hoistra/engine/internal/pgschema"
	"hoistra/engine/internal/resolve"
	"hoistra/engine/internal/rules"
)

// Deleting a vendor that has history RETIRES it (Hussain, 12 Oct 2026): the row stays for its
// history with status 'deleted'. The writer must neither count it as "already written" — a supplier
// taken on again would be skipped as a twin of the vendor that was deleted — nor write an id a row
// carries when that id names it. These tests need no database: vendorTable answers the writer's
// questions about plenum_cafm.vendors as Postgres would, filtering on status only when the SQL does.

// liveVendorSQL is ops-intelligence's live_vendor_sql('t'), written out by hand.
const liveVendorSQL = "COALESCE(lower(to_jsonb(t)->>'status'), 'active') <> 'deleted'"

const (
	vOrg     = "33333333-3333-4333-8333-333333333333"
	vRetired = "aaaa1111-2222-4333-8444-555566667777"
	vLive    = "bbbb1111-2222-4333-8444-555566667777"
)

type vendorOnFile struct{ id, name, status string }

type vendorTable struct {
	rows []vendorOnFile
	sqls []string
}

func (v *vendorTable) answer(sql string, args []any) [][]any {
	v.sqls = append(v.sqls, sql)
	live := func(r vendorOnFile) bool { return strings.ToLower(r.status) != "deleted" }
	named := func(name string) bool {
		for _, r := range v.rows {
			if r.name == name && (!strings.Contains(sql, liveVendorSQL) || live(r)) {
				return true
			}
		}
		return false
	}
	switch {
	case strings.Contains(sql, "unnest("): // prefetchKeys: the ordinals of the names on file
		var out [][]any
		for i, n := range args[0].([]*string) {
			if n != nil && named(*n) {
				out = append(out, []any{int64(i + 1)})
			}
		}
		return out
	case strings.Contains(sql, "count(*)"): // resolve.Linkable
		n := int64(0)
		for _, r := range v.rows {
			if r.id == args[0].(string) && (!strings.Contains(sql, "NOT ("+liveVendorSQL+")") || !live(r)) {
				n++
			}
		}
		return [][]any{{n}}
	default: // keyLookup
		if named(args[0].(string)) {
			return [][]any{{int32(1)}}
		}
		return nil
	}
}

// vendorTx is the slice of a pgx.Tx the writer's lookups use: a savepoint, one query, a release.
type vendorTx struct {
	pgx.Tx
	tab *vendorTable
}

func (t *vendorTx) Begin(context.Context) (pgx.Tx, error) { return t, nil }
func (t *vendorTx) Commit(context.Context) error          { return nil }
func (t *vendorTx) Rollback(context.Context) error        { return nil }
func (t *vendorTx) Query(_ context.Context, sql string, args ...any) (pgx.Rows, error) {
	return &vendorRows{data: t.tab.answer(sql, args)}, nil
}

type vendorRows struct {
	pgx.Rows
	data [][]any
	i    int
}

func (r *vendorRows) Next() bool             { r.i++; return r.i <= len(r.data) }
func (r *vendorRows) Values() ([]any, error) { return r.data[r.i-1], nil }
func (r *vendorRows) Close()                 {}
func (r *vendorRows) Err() error             { return nil }

func vendorKeyWriter() (*writer, *tablePlan) {
	w := &writer{job: Job{Schema: "plenum_cafm"}, org: vOrg, nkSeen: map[string]bool{},
		spec: rules.Spec{NaturalKeys: map[string][][]string{"vendors": {{"vendor_code"}, {"vendor_name"}}}}}
	text := func(name string) *pgschema.Column {
		return &pgschema.Column{Name: name, DataType: "character varying", FormatType: "character varying(255)",
			BaseType: "character varying", Nullable: true}
	}
	p := &tablePlan{dest: "vendors",
		cols:    map[string]bool{"id": true, "organization_id": true, "vendor_code": true, "vendor_name": true, "status": true},
		colInfo: map[string]*pgschema.Column{"vendor_code": text("vendor_code"), "vendor_name": text("vendor_name")}}
	return w, p
}

func nameKey(name string) rules.KeyMatch {
	return rules.KeyMatch{Cols: []string{"vendor_name"}, Vals: []coerce.Value{{K: coerce.Text, S: name}}}
}

// A retired row whose name was never marked: the status decides, the mark only defends in depth.
func retiredAndLive() *vendorTable {
	return &vendorTable{rows: []vendorOnFile{
		{id: vRetired, name: "Apex Mechanical Ltd", status: "deleted"},
		{id: vLive, name: "Brook Lifts", status: "active"},
	}}
}

func TestARetiredVendorIsNotAlreadyWrittenOneKeyAtATime(t *testing.T) {
	tab := retiredAndLive()
	w, p := vendorKeyWriter()
	ctx := context.Background()
	if dup, err := w.keyLookup(ctx, &vendorTx{tab: tab}, p, nameKey("Apex Mechanical Ltd")); err != nil || dup {
		t.Fatalf("the retired vendor counted as already written: %v %v", dup, err)
	}
	if dup, err := w.keyLookup(ctx, &vendorTx{tab: tab}, p, nameKey("Brook Lifts")); err != nil || !dup {
		t.Fatalf("a live vendor on file was not found: %v %v", dup, err)
	}
	want := "SELECT 1 FROM plenum_cafm.vendors t WHERE vendor_name = $1 AND organization_id::text = $2 AND " +
		liveVendorSQL + " LIMIT 1"
	if tab.sqls[0] != want {
		t.Fatalf("lookup\n got %q\nwant %q", tab.sqls[0], want)
	}
}

func TestThePrefetchDoesNotCountARetiredVendorAsWritten(t *testing.T) {
	tab := retiredAndLive()
	w, p := vendorKeyWriter()
	items := []*built{{keys: []rules.KeyMatch{nameKey("Apex Mechanical Ltd")}}, {keys: []rules.KeyMatch{nameKey("Brook Lifts")}}}
	known, err := w.prefetchKeys(context.Background(), &vendorTx{tab: tab}, p, items)
	if err != nil {
		t.Fatal(err)
	}
	got := map[string]bool{
		"Apex Mechanical Ltd": known[nkCacheKey("vendors", nameKey("Apex Mechanical Ltd"))],
		"Brook Lifts":         known[nkCacheKey("vendors", nameKey("Brook Lifts"))],
	}
	if !reflect.DeepEqual(got, map[string]bool{"Apex Mechanical Ltd": false, "Brook Lifts": true}) {
		t.Fatalf("already written %v", got)
	}
	if len(tab.sqls) != 1 || !strings.HasSuffix(tab.sqls[0], "AND t.organization_id::text = $2 AND "+liveVendorSQL+")") {
		t.Fatalf("prefetch %q", tab.sqls)
	}
}

func TestAnotherTablesKeyLookupIsUnfiltered(t *testing.T) {
	tab := &vendorTable{}
	w, p := vendorKeyWriter()
	p.dest = "buildings"
	p.colInfo["building_code"] = p.colInfo["vendor_code"]
	km := rules.KeyMatch{Cols: []string{"building_code"}, Vals: []coerce.Value{{K: coerce.Text, S: "Brook Lifts"}}}
	if _, err := w.keyLookup(context.Background(), &vendorTx{tab: tab}, p, km); err != nil {
		t.Fatal(err)
	}
	want := "SELECT 1 FROM plenum_cafm.buildings t WHERE building_code = $1 AND organization_id::text = $2 LIMIT 1"
	if tab.sqls[0] != want {
		t.Fatalf("lookup\n got %q\nwant %q", tab.sqls[0], want)
	}
}

func TestAnIDARowCarriesForARetiredVendorIsDroppedAndReported(t *testing.T) {
	tab := &vendorTable{rows: []vendorOnFile{{id: vRetired, status: "deleted"}, {id: vLive, status: "active"}}}
	w := &writer{org: vOrg, spec: rules.Spec{References: []rules.Reference{{Column: "vendor_id",
		HintKeys: []string{"vendor_id", "vendor_name"}, Table: "vendors", MatchColumns: []string{"vendor_code", "vendor_name"}}}}}
	w.res = resolve.New(&vendorTx{tab: tab}, &w.spec, vOrg, "plenum_cafm", nil, nil)
	row := func(code, vendor string) *rules.Row {
		r := rules.NewRow(2)
		r.Set("wo_code", cell.Of(code))
		r.Set("vendor_id", cell.Of(vendor))
		return r
	}
	p := &tablePlan{dest: "work_orders",
		src:         &source{rows: []*rules.Row{row("WO-1", vRetired), row("WO-2", vLive)}},
		norm:        []*rules.Row{row("WO-1", vRetired), row("WO-2", vLive)},
		resolveCols: map[string]bool{"wo_code": true, "vendor_id": true}}
	out := w.resolveRows(context.Background(), p)
	if out[0].Has("vendor_id") {
		t.Fatalf("new work linked to the retired vendor: %v", out[0].Get("vendor_id"))
	}
	if got := out[1].Get("vendor_id"); got.S != vLive {
		t.Fatalf("the live vendor's id was dropped: %v", got)
	}
	unres := w.res.ReferenceReport()["unresolved"].(map[string][]string)
	if !reflect.DeepEqual(unres, map[string][]string{"vendor_id": {vRetired}}) {
		t.Fatalf("unresolved %v", unres)
	}
}
