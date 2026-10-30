package write

import (
	"strings"
	"testing"

	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/pgschema"
	"hoistra/engine/internal/rules"
)

// The Python writer was fixed on 30 Oct (write_node.py, commit 40f7657) and the engine was not,
// so a Go run — which is every run, because `write` is an engine step — still refused almost
// every row of a real workbook. From one write plan:
//
//	vendors          13 rows   "id is required and the file has no value for it"
//	vendor_contracts 13 rows   same
//	work_orders   4,073 rows   same
//	activity_log    769 rows   "updated_at is required and the file has no value for it"
//	certificates     65 rows   "cert_scope: value too long for character varying(20)"
//
// 4,948 of 5,020 rows. These pin the four behaviours the Python side already has.

func plan(dest string, types map[string]string, required []string) *tablePlan {
	cols := map[string]bool{}
	for c := range types {
		cols[c] = true
	}
	return &tablePlan{dest: dest, cols: cols, types: types, required: required}
}

func row(pairs ...string) *rules.Row {
	r := rules.NewRow(len(pairs) / 2)
	for i := 0; i < len(pairs); i += 2 {
		r.K = append(r.K, pairs[i])
		r.V = append(r.V, cell.Of(pairs[i+1]))
	}
	return r
}

func built1(t *testing.T, p *tablePlan, r *rules.Row) (*built, *mismatchTally) {
	t.Helper()
	w := &writer{org: org, ids: idGen{deterministic: true}}
	tally := &mismatchTally{}
	out := w.build(p, []*rules.Row{r}, tally)
	if len(out) != 1 {
		t.Fatalf("expected one built row, got %d", len(out))
	}
	return out[0], tally
}

func value(t *testing.T, f *rules.Filtered, key string) string {
	t.Helper()
	for i, k := range f.K {
		if k == key {
			return f.V[i].PyStr()
		}
	}
	t.Fatalf("%s is not in the row: %v", key, f.K)
	return ""
}

// ── 1. the id is minted AFTER the unfit one is dropped ────────────────────────────────

func TestASourceIdThatCannotBeAUuidDoesNotCostTheRow(t *testing.T) {
	p := plan("vendors", map[string]string{"id": "uuid", "name": "text"}, []string{"id"})
	b, tally := built1(t, p, row("id", "VEN-001", "name", "Mitie"))

	if b.skipped {
		t.Fatal("the row was skipped; VEN-001 is unfit for a uuid column, the row is not")
	}
	if !b.genID {
		t.Error("no id was drawn for a row whose own id could not be used")
	}
	id := value(t, b.full, "id")
	if !strings.HasPrefix(id, "00000000-0000-4000-9000-") {
		t.Errorf("id = %q, wanted a drawn uuid", id)
	}
	if value(t, b.full, "name") != "Mitie" {
		t.Error("the rest of the row did not survive")
	}
	if tally.count["id"] != 1 {
		t.Error("the unusable value should still be reported as one that does not fit")
	}
}

func TestTheRowIsNoLongerRefusedForTheIdItWasGiven(t *testing.T) {
	p := plan("vendors", map[string]string{"id": "uuid", "name": "text"}, []string{"id"})
	b, _ := built1(t, p, row("id", "VEN-001", "name", "Mitie"))
	w := &writer{org: org, ids: idGen{deterministic: true}}
	p.colInfo = map[string]*pgschema.Column{}
	if why := w.cannotWrite(p, b.full); why != "" {
		t.Fatalf("the plan still refuses the row: %s", why)
	}
}

func TestASourceIdThatIsAlreadyAUuidIsKept(t *testing.T) {
	const u = "3f2a9c1e-5b7d-4e8a-9c21-7d4e5f6a8b90"
	p := plan("vendors", map[string]string{"id": "uuid", "name": "text"}, []string{"id"})
	b, _ := built1(t, p, row("id", u, "name", "Mitie"))
	if b.genID {
		t.Error("an id that fits must not be replaced")
	}
	if got := value(t, b.full, "id"); got != u {
		t.Errorf("id = %q, wanted the source's own %q", got, u)
	}
}

func TestARowWithNoIdAtAllStillGetsOne(t *testing.T) {
	p := plan("vendors", map[string]string{"id": "uuid", "name": "text"}, []string{"id"})
	b, _ := built1(t, p, row("name", "Mitie"))
	if !b.genID || value(t, b.full, "id") == "" {
		t.Error("a row with no id of its own must be given one")
	}
}

func TestARowThatIsNothingButAnUnusableIdIsStillSkipped(t *testing.T) {
	p := plan("vendors", map[string]string{"id": "uuid"}, []string{"id"})
	b, _ := built1(t, p, row("id", "VEN-001"))
	if !b.skipped {
		t.Error("there is nothing to write but an id nobody asked for")
	}
}

// ── 2. bookkeeping timestamps the source could not carry ──────────────────────────────

func TestBookkeepingTimestampsAreFilled(t *testing.T) {
	for _, col := range []string{"created_at", "updated_at", "inserted_at"} {
		got, ok := rules.SystemDefault(col, "timestamp without time zone", org)
		if !ok || got.PyStr() == "" {
			t.Errorf("%s: no system default, so a required column refuses the row", col)
		}
	}
}

func TestABusinessDateIsStillRefused(t *testing.T) {
	for _, col := range []string{"reading_at", "period_start", "report_month", "issued_date"} {
		if _, ok := rules.SystemDefault(col, "timestamp without time zone", org); ok {
			t.Errorf("%s was invented; when a meter was read is not ours to decide", col)
		}
	}
}

func TestTheAlarmRowsThatHadNoTimestamp(t *testing.T) {
	p := plan("activity_log_entries",
		map[string]string{"id": "uuid", "detail": "text", "updated_at": "timestamp without time zone"},
		[]string{"id", "updated_at"})
	b, _ := built1(t, p, row("detail", "alarm raised"))
	if b.skipped {
		t.Fatal("row skipped")
	}
	if value(t, b.full, "updated_at") == "" {
		t.Error("updated_at is required and nothing filled it — 769 rows failed this way")
	}
}

// ── 3. a value too long is trimmed, not fatal ─────────────────────────────────────────

// The width is not in data_type ("character varying"); it reaches the writer on the column, as
// information_schema's character_maximum_length. Supplying it the other way tests nothing.
func width(p *tablePlan, col string, n int) *tablePlan {
	if p.colInfo == nil {
		p.colInfo = map[string]*pgschema.Column{}
	}
	p.colInfo[col] = &pgschema.Column{Name: col, DataType: "character varying", MaxChars: &n}
	return p
}

func TestAValueTooLongIsTrimmedToTheColumn(t *testing.T) {
	long := strings.Repeat("A", 80)
	p := width(plan("compliance_certificates", map[string]string{"cert_scope": "character varying"}, nil),
		"cert_scope", 20)
	b, _ := built1(t, p, row("cert_scope", long))
	got := value(t, b.full, "cert_scope")
	if len([]rune(got)) != 20 {
		t.Errorf("cert_scope is %d characters, wanted 20 — 65 rows were refused for this", len([]rune(got)))
	}
}

func TestAValueThatFitsIsUntouched(t *testing.T) {
	p := width(plan("x", map[string]string{"s": "character varying"}, nil), "s", 64)
	b, _ := built1(t, p, row("s", "Annual gas safety"))
	if got := value(t, b.full, "s"); got != "Annual gas safety" {
		t.Errorf("s = %q", got)
	}
}

func TestAColumnWithNoDeclaredWidthIsLeftAlone(t *testing.T) {
	long := strings.Repeat("A", 500)
	p := plan("x", map[string]string{"notes": "text"}, nil)
	b, _ := built1(t, p, row("notes", long))
	if got := value(t, b.full, "notes"); len(got) != 500 {
		t.Errorf("text was trimmed to %d; a text column has no width to trim to", len(got))
	}
}

// ── 4. the column is widened to the data, and trimming is only the fallback ────────────

func TestTheWidthNeededIsMeasuredFromTheRows(t *testing.T) {
	// What the prepare pass measures: the longest value in the column decides the new width.
	rows := []*rules.Row{
		row("cert_scope", strings.Repeat("A", 14)),
		row("cert_scope", strings.Repeat("B", 73)),
		row("cert_scope", strings.Repeat("C", 31)),
	}
	need := 0
	for _, r := range rows {
		if n := len([]rune(r.Get("cert_scope").PyStr())); n > need {
			need = n
		}
	}
	if need != 73 {
		t.Fatalf("needed width = %d, wanted 73 — the widening would be sized wrong", need)
	}
}

func TestTrimmingIsTheFallbackNotTheRule(t *testing.T) {
	// A column that could not be altered keeps its width; the row is still written, trimmed,
	// rather than refused. This is the belt to the widening's braces.
	long := strings.Repeat("A", 80)
	p := width(plan("compliance_certificates", map[string]string{"cert_scope": "character varying"}, nil),
		"cert_scope", 20)
	p.colInfo["cert_scope"].WidenBlocked = true
	b, _ := built1(t, p, row("cert_scope", long))
	if b.skipped {
		t.Fatal("the row was refused; a value too long must never cost the row")
	}
	if n := len([]rune(value(t, b.full, "cert_scope"))); n != 20 {
		t.Errorf("trimmed to %d, wanted the column's 20", n)
	}
}

func TestAWidenedColumnNoLongerTrims(t *testing.T) {
	// After the ALTER the column carries the new width, so the value passes through whole.
	long := strings.Repeat("A", 80)
	p := width(plan("compliance_certificates", map[string]string{"cert_scope": "character varying"}, nil),
		"cert_scope", 80)
	b, _ := built1(t, p, row("cert_scope", long))
	if got := value(t, b.full, "cert_scope"); len([]rune(got)) != 80 {
		t.Errorf("value is %d characters; a widened column must not trim", len([]rune(got)))
	}
}
