package resolve

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5"

	"hoistra/engine/internal/rules"
)

// Deleting a vendor that has history RETIRES it (Hussain, 12 Oct 2026): the row keeps its id, its
// status becomes 'deleted' and its name gains " (deleted)". A migration must never link new work to
// it — by code, name or id, or by an id the file already carries — which is what these tests hold
// the resolver to. They need no database: register below answers the resolver's two vendor
// questions as Postgres would, applying the status filter only when the SQL carries it.

// liveSQL is ops-intelligence's live_vendor_sql('t'), written out by hand.
const liveSQL = "COALESCE(lower(to_jsonb(t)->>'status'), 'active') <> 'deleted'"

const (
	retiredOrg = "22222222-2222-4222-8222-222222222222"
	retiredID  = "aaaa1111-2222-4333-8444-555566667777"
	liveID     = "bbbb1111-2222-4333-8444-555566667777"
	unknownID  = "cccc1111-2222-4333-8444-555566667777"
)

var retiredSpec = &rules.Spec{
	References: []rules.Reference{
		{Column: "asset_id", Table: "assets", MatchColumns: []string{"asset_code", "serial_number", "inventory_code"}},
		{Column: "vendor_id", Table: "vendors", MatchColumns: []string{"vendor_code", "vendor_name"}},
		{Column: "contract_id", Table: "vendor_contracts", MatchColumns: []string{"contract_name"}},
	},
}

type vendorRow struct{ id, code, name, status string }

func (r vendorRow) live() bool { return strings.ToLower(r.status) != "deleted" }

type register struct {
	rows  []vendorRow
	sqls  []string
	fails int // the next n queries fail, as a statement timeout would
}

func (g *register) answer(sql string, args []any) ([][]any, error) {
	g.sqls = append(g.sqls, sql)
	if g.fails > 0 {
		g.fails--
		return nil, errors.New("canceling statement due to statement timeout")
	}
	if strings.Contains(sql, "count(*)") {
		k := args[0].(string)
		n := int64(0)
		for _, r := range g.rows {
			if strings.ToLower(r.id) == k && (!strings.Contains(sql, "NOT ("+liveSQL+")") || !r.live()) {
				n++
			}
		}
		return [][]any{{n}}, nil
	}
	k := args[1].(string)
	var out [][]any
	for _, r := range g.rows {
		if k != strings.ToLower(r.code) && k != strings.ToLower(r.name) && k != r.id {
			continue
		}
		if strings.Contains(sql, liveSQL) && !r.live() {
			continue
		}
		out = append(out, []any{r.id})
	}
	return out, nil
}

// fakeTx is the slice of a pgx.Tx the resolver uses — a savepoint per lookup, one query in it.
type fakeTx struct {
	pgx.Tx
	g *register
}

func (t *fakeTx) Begin(context.Context) (pgx.Tx, error) { return t, nil }
func (t *fakeTx) Commit(context.Context) error          { return nil }
func (t *fakeTx) Rollback(context.Context) error        { return nil }
func (t *fakeTx) Query(_ context.Context, sql string, args ...any) (pgx.Rows, error) {
	data, err := t.g.answer(sql, args)
	if err != nil {
		return nil, err
	}
	return &fakeRows{data: data}, nil
}

type fakeRows struct {
	pgx.Rows
	data [][]any
	i    int
}

func (r *fakeRows) Next() bool             { r.i++; return r.i <= len(r.data) }
func (r *fakeRows) Values() ([]any, error) { return r.data[r.i-1], nil }
func (r *fakeRows) Close()                 {}
func (r *fakeRows) Err() error             { return nil }

func retiredSet(g *register) *Set {
	return New(&fakeTx{g: g}, retiredSpec, retiredOrg, "plenum_cafm", nil, nil)
}

func unresolved(s *Set) map[string][]string {
	return s.ReferenceReport()["unresolved"].(map[string][]string)
}

func TestTheMarkedNameAnExportedHistorySheetCarriesFindsNoVendor(t *testing.T) {
	g := &register{rows: []vendorRow{{id: retiredID, name: "Apex Mechanical Ltd (deleted)", status: "deleted"}}}
	s := retiredSet(g)
	if id, ok := s.Reference(context.Background(), "vendor_id", "Apex Mechanical Ltd (deleted)"); ok {
		t.Fatalf("linked new work to the retired vendor %s", id)
	}
	if got := unresolved(s); !reflect.DeepEqual(got, map[string][]string{"vendor_id": {"Apex Mechanical Ltd (deleted)"}}) {
		t.Fatalf("unresolved %v", got)
	}
}

func TestACodeOrALegacyIDOnARetiredRowFindsNothing(t *testing.T) {
	g := &register{rows: []vendorRow{
		{id: retiredID, code: "APX-01", name: "Apex (deleted)", status: "Deleted"},
		{id: "ven-0042", name: "Brook (deleted)", status: "deleted"},
	}}
	s := retiredSet(g)
	for _, hint := range []string{"APX-01", "VEN-0042"} {
		if id, ok := s.Reference(context.Background(), "vendor_id", hint); ok {
			t.Fatalf("%s linked to the retired vendor %s", hint, id)
		}
	}
}

func TestASupplierTakenOnAgainIsFoundRatherThanAmbiguous(t *testing.T) {
	g := &register{rows: []vendorRow{
		{id: retiredID, name: "Apex Mechanical Ltd", status: "deleted"},
		{id: liveID, name: "Apex Mechanical Ltd", status: "active"},
	}}
	s := retiredSet(g)
	if id, ok := s.Reference(context.Background(), "vendor_id", "Apex Mechanical Ltd"); !ok || id != liveID {
		t.Fatalf("got %q %v, want the live vendor", id, ok)
	}
	if len(s.RefAmbiguous) != 0 {
		t.Fatalf("ambiguous %v", s.RefAmbiguous)
	}
}

func TestOnlyTheVendorLookupCarriesTheStatusFilter(t *testing.T) {
	g := &register{}
	s := retiredSet(g)
	for _, col := range []string{"asset_id", "contract_id", "vendor_id"} {
		s.Reference(context.Background(), col, "X-1")
	}
	var got []bool
	for _, q := range g.sqls {
		got = append(got, strings.Contains(q, liveSQL))
	}
	if !reflect.DeepEqual(got, []bool{false, false, true}) {
		t.Fatalf("filter per lookup %v", got)
	}
	want := "\nSELECT id::text FROM plenum_cafm.vendors t\n WHERE organization_id::text = $1\n" +
		"   AND (lower(coalesce(vendor_code::text, '')) = $2 OR lower(coalesce(vendor_name::text, '')) = $2 OR id::text = $2)\n" +
		"   AND " + liveSQL + "\n LIMIT 2\n"
	if g.sqls[2] != want {
		t.Fatalf("vendor lookup\n got %q\nwant %q", g.sqls[2], want)
	}
}

func TestAnOldIDNamingARetiredVendorIsRefusedAndReported(t *testing.T) {
	g := &register{rows: []vendorRow{{id: retiredID, status: "deleted"}, {id: liveID, status: "active"}}}
	s := retiredSet(g)
	ctx := context.Background()
	if s.Linkable(ctx, "vendor_id", retiredID) {
		t.Fatal("an id naming a retired vendor was kept")
	}
	if s.Linkable(ctx, "vendor_id", strings.ToUpper(retiredID)) {
		t.Fatal("the same id in capitals was kept")
	}
	if !s.Linkable(ctx, "vendor_id", liveID) {
		t.Fatal("an id naming a live vendor was refused")
	}
	if !s.Linkable(ctx, "vendor_id", unknownID) {
		t.Fatal("an id naming no vendor yet was refused — it may be one this file writes")
	}
	// Reported as the file wrote them, sorted as Python sorts them: capitals first.
	want := map[string][]string{"vendor_id": {strings.ToUpper(retiredID), retiredID}}
	if got := unresolved(s); !reflect.DeepEqual(got, want) {
		t.Fatalf("unresolved %v", got)
	}
}

func TestACheckThatCouldNotBeReadRefusesAndIsAskedAgain(t *testing.T) {
	g := &register{rows: []vendorRow{{id: liveID, status: "active"}}, fails: 1}
	s := retiredSet(g)
	ctx := context.Background()
	if s.Linkable(ctx, "vendor_id", liveID) {
		t.Fatal("an unreadable check let the link through")
	}
	if len(s.Warnings) != 1 {
		t.Fatalf("the failed lookup must be reported: %v", s.Warnings)
	}
	if !s.Linkable(ctx, "vendor_id", liveID) {
		t.Fatal("a failed check was remembered as a refusal")
	}
}

func TestEachCarriedIDIsCheckedOncePerRun(t *testing.T) {
	g := &register{rows: []vendorRow{{id: retiredID, status: "deleted"}, {id: liveID, status: "active"}}}
	s := retiredSet(g)
	ctx := context.Background()
	for i := 0; i < 200; i++ {
		if s.Linkable(ctx, "vendor_id", retiredID) || !s.Linkable(ctx, "vendor_id", liveID) {
			t.Fatal("answer changed")
		}
	}
	if len(g.sqls) != 2 || s.RefReads != 2 {
		t.Fatalf("queries %d reads %d", len(g.sqls), s.RefReads)
	}
}

func TestAColumnWithNoRetiredStateIsNotChecked(t *testing.T) {
	g := &register{}
	s := retiredSet(g)
	if !s.Linkable(context.Background(), "asset_id", retiredID) || !s.Linkable(context.Background(), "contract_id", retiredID) {
		t.Fatal("refused a column with no retired state")
	}
	if len(g.sqls) != 0 {
		t.Fatalf("asked %v", g.sqls)
	}
}

func TestLiveRowIsTheVendorPredicateOnly(t *testing.T) {
	if LiveRow("vendors") != liveSQL {
		t.Fatalf("vendors: %q", LiveRow("vendors"))
	}
	for _, table := range []string{"assets", "vendor_contracts", "spare_parts", ""} {
		if LiveRow(table) != "" {
			t.Fatalf("%s: %q", table, LiveRow(table))
		}
	}
}
