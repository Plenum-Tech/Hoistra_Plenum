package pgschema

import (
	"context"
	"reflect"
	"sort"
	"strings"
	"testing"

	"hoistra/engine/internal/testdb"
)

func TestLoadReadsTypesKeysAndForeignKeysInViolationOrder(t *testing.T) {
	conn := testdb.Fresh(t)
	ctx := context.Background()
	for _, q := range []string{
		`CREATE TYPE plenum_cafm.pg_kind AS ENUM ('a', 'b')`,
		`CREATE TABLE plenum_cafm.pg_parent (id uuid PRIMARY KEY, code text UNIQUE)`,
		`CREATE TABLE plenum_cafm.pg_t (
			id uuid PRIMARY KEY,
			code varchar(8) UNIQUE,
			qty integer NOT NULL,
			small smallint,
			amt numeric(6,2),
			kind plenum_cafm.pg_kind,
			made timestamp without time zone DEFAULT now(),
			parent uuid,
			other uuid,
			CONSTRAINT zz_parent FOREIGN KEY (parent) REFERENCES plenum_cafm.pg_parent(id),
			CONSTRAINT aa_other FOREIGN KEY (other) REFERENCES plenum_cafm.pg_parent(id)
		)`,
		`CREATE UNIQUE INDEX pg_t_partial ON plenum_cafm.pg_t (qty) WHERE qty > 0`,
	} {
		if _, err := conn.Exec(ctx, q); err != nil {
			t.Fatal(err)
		}
	}
	got, err := Load(ctx, conn, "plenum_cafm", []string{"pg_t", "missing_table"})
	if err != nil {
		t.Fatal(err)
	}
	tab := got["pg_t"]
	if tab == nil || !tab.Exists {
		t.Fatalf("pg_t not loaded: %+v", got)
	}
	if got["missing_table"] == nil || got["missing_table"].Exists {
		t.Fatalf("missing table must be reported as not existing: %+v", got["missing_table"])
	}
	want := map[string]string{"id": "uuid", "code": "character varying", "qty": "integer", "small": "smallint",
		"amt": "numeric", "kind": "USER-DEFINED", "made": "timestamp without time zone", "parent": "uuid"}
	for c, dt := range want {
		if tab.ByName[c] == nil || tab.ByName[c].DataType != dt {
			t.Errorf("%s: data type %+v, want %s", c, tab.ByName[c], dt)
		}
	}
	if order := []string{tab.Columns[0].Name, tab.Columns[1].Name, tab.Columns[2].Name}; !reflect.DeepEqual(order, []string{"id", "code", "qty"}) {
		t.Errorf("column order %v", order)
	}
	if c := tab.ByName["code"]; c.MaxChars == nil || *c.MaxChars != 8 {
		t.Errorf("code max chars %+v", c.MaxChars)
	}
	if c := tab.ByName["amt"]; c.NumPrecision == nil || *c.NumPrecision != 6 || *c.NumScale != 2 {
		t.Errorf("amt precision/scale %+v %+v", c.NumPrecision, c.NumScale)
	}
	if c := tab.ByName["qty"]; c.Nullable || c.IntBits != 32 || c.Default != nil {
		t.Errorf("qty %+v", c)
	}
	if c := tab.ByName["small"]; c.IntBits != 16 {
		t.Errorf("small bits %d", c.IntBits)
	}
	if c := tab.ByName["made"]; c.Default == nil || *c.Default != "now()" {
		t.Errorf("made default %+v", c.Default)
	}
	if c := tab.ByName["kind"]; !reflect.DeepEqual(c.EnumLabels, []string{"a", "b"}) || c.UDT != "pg_kind" {
		t.Errorf("kind enum %+v udt %q", c.EnumLabels, c.UDT)
	}
	var sets []string
	for _, s := range tab.UniqueSets {
		sort.Strings(s)
		sets = append(sets, joinKey(s))
	}
	sort.Strings(sets)
	if !reflect.DeepEqual(sets, []string{"code", "id"}) { // the partial index is not a plain conflict target
		t.Errorf("unique sets %v", sets)
	}
	if len(tab.FKs) != 2 {
		t.Fatalf("fks %+v", tab.FKs)
	}
	// Postgres raises the violation of the RI trigger whose name sorts first.
	if tab.FKs[0].Trigger > tab.FKs[1].Trigger {
		t.Errorf("FKs not in trigger order: %+v", tab.FKs)
	}
	for _, fk := range tab.FKs {
		if fk.RefTable != "pg_parent" || !reflect.DeepEqual(fk.RefColumns, []string{"id"}) || len(fk.Columns) != 1 {
			t.Errorf("fk %+v", fk)
		}
	}
}

func joinKey(s []string) string {
	out := ""
	for i, x := range s {
		if i > 0 {
			out += ","
		}
		out += x
	}
	return out
}

// WidenBlocked predicts, for the write gate's plan, which numeric columns the writer's
// ALTER … TYPE TEXT would fail on. The test checks the prediction against the ALTER itself.
func TestWidenBlockedPredictsWhichColumnsCannotBecomeText(t *testing.T) {
	conn := testdb.Fresh(t)
	ctx := context.Background()
	for _, q := range []string{
		`CREATE TABLE plenum_cafm.wb_parent (id integer PRIMARY KEY)`,
		`CREATE TABLE plenum_cafm.wb_t (
			id uuid PRIMARY KEY,
			free integer,
			indexed integer,
			checked integer CHECK (checked >= 0),
			viewed integer,
			linked integer REFERENCES plenum_cafm.wb_parent(id),
			partial integer
		)`,
		`CREATE INDEX wb_t_indexed ON plenum_cafm.wb_t (indexed)`,
		`CREATE INDEX wb_t_partial ON plenum_cafm.wb_t (free) WHERE partial > 0`,
		`CREATE VIEW plenum_cafm.wb_v AS SELECT id, viewed FROM plenum_cafm.wb_t`,
	} {
		if _, err := conn.Exec(ctx, q); err != nil {
			t.Fatal(err)
		}
	}
	got, err := Load(ctx, conn, "plenum_cafm", []string{"wb_t", "wb_parent"})
	if err != nil {
		t.Fatal(err)
	}
	check := func(table, col string) {
		t.Helper()
		tx, err := conn.Begin(ctx)
		if err != nil {
			t.Fatal(err)
		}
		_, altErr := tx.Exec(ctx, `ALTER TABLE plenum_cafm.`+table+` ALTER COLUMN "`+col+`" TYPE TEXT USING "`+col+`"::text`)
		_ = tx.Rollback(ctx)
		if blocked := got[table].ByName[col].WidenBlocked; blocked != (altErr != nil) {
			t.Errorf("%s.%s: predicted blocked=%v, ALTER error: %v", table, col, blocked, altErr)
		}
	}
	for _, c := range []string{"free", "indexed", "checked", "viewed", "linked", "partial"} {
		check("wb_t", c)
	}
	check("wb_parent", "id")
}

// What decides whether the writer may load a table in bulk: the kind of relation, its user
// triggers and rules (an RI trigger is internal and does not count), and identity columns.
func TestLoadReadsWhatDecidesABulkLoad(t *testing.T) {
	conn := testdb.Fresh(t)
	ctx := context.Background()
	for _, q := range []string{
		`CREATE TABLE plenum_cafm.bk_parent (id uuid PRIMARY KEY)`,
		`CREATE TABLE plenum_cafm.bk_plain (id uuid PRIMARY KEY, p uuid REFERENCES plenum_cafm.bk_parent(id),
			n bigint GENERATED BY DEFAULT AS IDENTITY, s serial)`,
		`CREATE TABLE plenum_cafm.bk_trig (id uuid PRIMARY KEY)`,
		`CREATE FUNCTION plenum_cafm.bk_noop() RETURNS trigger LANGUAGE plpgsql AS 'BEGIN RETURN NEW; END'`,
		`CREATE TRIGGER bk_t BEFORE INSERT ON plenum_cafm.bk_trig FOR EACH ROW EXECUTE FUNCTION plenum_cafm.bk_noop()`,
		`CREATE TABLE plenum_cafm.bk_rule (id uuid PRIMARY KEY)`,
		`CREATE RULE bk_r AS ON UPDATE TO plenum_cafm.bk_rule DO ALSO NOTHING`,
		`CREATE VIEW plenum_cafm.bk_view AS SELECT id FROM plenum_cafm.bk_parent`,
		`CREATE DOMAIN plenum_cafm.bk_dom AS text DEFAULT 'x'`,
		`ALTER TABLE plenum_cafm.bk_rule ADD COLUMN d plenum_cafm.bk_dom`,
		`CREATE SEQUENCE plenum_cafm.bk_seq`,
		`CREATE FUNCTION plenum_cafm."NextRef"() RETURNS text LANGUAGE sql AS $$ SELECT 'R-' || nextval('plenum_cafm.bk_seq') $$`,
		`CREATE FUNCTION plenum_cafm.bk_room(o text) RETURNS boolean LANGUAGE sql STABLE AS $$ SELECT true $$`,
		`CREATE TABLE plenum_cafm.bk_guarded (id uuid PRIMARY KEY, org text CHECK (plenum_cafm.bk_room(org)),
			ref text DEFAULT plenum_cafm."NextRef"(), made timestamptz DEFAULT now(), n int DEFAULT 0,
			s text DEFAULT nextval('plenum_cafm.bk_seq'::regclass)::text)`,
		`CREATE TABLE plenum_cafm.bk_rls (id uuid PRIMARY KEY)`,
		`ALTER TABLE plenum_cafm.bk_rls ENABLE ROW LEVEL SECURITY`,
		`CREATE TABLE plenum_cafm.bk_part (id uuid, org text) PARTITION BY LIST (org)`,
		`CREATE TABLE plenum_cafm.bk_part_a PARTITION OF plenum_cafm.bk_part FOR VALUES IN ('a')`,
		`CREATE TABLE plenum_cafm.bk_child () INHERITS (plenum_cafm.bk_parent)`,
		`CREATE FUNCTION plenum_cafm.bk_below(o text, lim int) RETURNS boolean LANGUAGE sql STABLE AS $$ SELECT true $$`,
		`CREATE OPERATOR plenum_cafm.<# (LEFTARG = text, RIGHTARG = int, FUNCTION = plenum_cafm.bk_below)`,
		`CREATE TABLE plenum_cafm.bk_op (id uuid PRIMARY KEY, org text CHECK (org OPERATOR(plenum_cafm.<#) 2))`,
		`CREATE FUNCTION plenum_cafm.bk_slot(o text) RETURNS text LANGUAGE sql IMMUTABLE AS $$ SELECT o $$`,
		`CREATE TABLE plenum_cafm.bk_idx (id uuid PRIMARY KEY, org text)`,
		`CREATE UNIQUE INDEX bk_idx_slot ON plenum_cafm.bk_idx (plenum_cafm.bk_slot(org))`,
		`CREATE TABLE plenum_cafm.bk_pred (id uuid PRIMARY KEY, org text)`,
		`CREATE UNIQUE INDEX bk_pred_org ON plenum_cafm.bk_pred (org) WHERE plenum_cafm.bk_slot(org) <> ''`,
		`CREATE TABLE plenum_cafm.bk_lower (id uuid PRIMARY KEY, org text)`,
		`CREATE UNIQUE INDEX bk_lower_org ON plenum_cafm.bk_lower (lower(org))`,
	} {
		if _, err := conn.Exec(ctx, q); err != nil {
			t.Fatal(err)
		}
	}
	got, err := Load(ctx, conn, "plenum_cafm", []string{"bk_plain", "bk_trig", "bk_rule", "bk_view", "bk_missing",
		"bk_guarded", "bk_rls", "bk_part", "bk_part_a", "bk_child", "bk_parent", "bk_op", "bk_idx", "bk_pred", "bk_lower"})
	if err != nil {
		t.Fatal(err)
	}
	type facts struct {
		Kind         string
		UserTriggers bool
		Rules        bool
	}
	want := map[string]facts{
		"bk_plain": {"r", false, false}, "bk_trig": {"r", true, false}, "bk_rule": {"r", false, true},
		"bk_view": {"v", false, true}, "bk_missing": {"", false, false},
	}
	for name, w := range want {
		tab := got[name]
		if g := (facts{tab.Kind, tab.UserTriggers, tab.Rules}); g != w {
			t.Errorf("%s: %+v, want %+v", name, g, w)
		}
	}
	plain := got["bk_plain"]
	for col, identity := range map[string]bool{"id": false, "p": false, "n": true, "s": false} {
		if plain.ByName[col].Identity != identity {
			t.Errorf("bk_plain.%s identity %v, want %v", col, plain.ByName[col].Identity, identity)
		}
	}
	if d := plain.ByName["s"].Default; d == nil || !strings.Contains(*d, "nextval(") {
		t.Errorf("serial default %v", d)
	}
	if got := got["bk_rule"].ByName["d"].Domain; got != "bk_dom" {
		t.Errorf("domain column: %q", got)
	}
	if got := plain.ByName["id"].Domain; got != "" {
		t.Errorf("plain column domain: %q", got)
	}
	g := got["bk_guarded"]
	if !g.CallsUserCode || g.RowSecurity || g.Partition || g.Inherits {
		t.Errorf("bk_guarded: check %v rls %v partition %v inherits %v", g.CallsUserCode, g.RowSecurity, g.Partition, g.Inherits)
	}
	for col, user := range map[string]bool{"ref": true, "made": false, "n": false, "s": true, "id": false} {
		if g.ByName[col].DefaultUsesObject != user {
			t.Errorf("bk_guarded.%s: default uses an object %v, want %v", col, g.ByName[col].DefaultUsesObject, user)
		}
	}
	// Everything an insert evaluates per row against the table: a CHECK through a user operator, an
	// index expression or predicate calling a user function. A built-in function (lower) is fine.
	for name, want := range map[string]bool{"bk_op": true, "bk_idx": true, "bk_pred": true, "bk_lower": false} {
		if got[name].CallsUserCode != want {
			t.Errorf("%s: calls user code %v, want %v", name, got[name].CallsUserCode, want)
		}
	}
	if !got["bk_rls"].RowSecurity || got["bk_plain"].RowSecurity || got["bk_plain"].CallsUserCode {
		t.Errorf("row security / check flags wrong")
	}
	if !got["bk_part_a"].Partition || got["bk_part"].Kind != "p" || !got["bk_child"].Inherits || !got["bk_parent"].Inherits ||
		got["bk_plain"].Inherits || got["bk_plain"].Partition {
		t.Errorf("partition/inheritance flags wrong")
	}
}
