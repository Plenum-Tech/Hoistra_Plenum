// Package resolve ports the writer's lookups — BuildingResolver (building_link.py), MeterResolver
// and the section/floor lookups (meter_link.py), ReferenceResolver (reference_link.py), and the
// asset lookups in write_node.py — with the same SQL, the same ambiguity rules and the same
// caching rules, run inside the engine's transaction so rows written earlier in the run are
// visible. Every lookup runs in its own savepoint: one that fails is logged and treated as no
// match, exactly like write_node's _fetch.
package resolve

import (
	"context"
	"fmt"
	"sort"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"

	"hoistra/engine/internal/pystr"
	"hoistra/engine/internal/rules"
)

// Tx is the slice of pgx the lookups need (a pgx.Tx; Begin makes a savepoint).
type Tx interface {
	Begin(ctx context.Context) (pgx.Tx, error)
	Exec(ctx context.Context, sql string, args ...any) (pgconn.CommandTag, error)
	Query(ctx context.Context, sql string, args ...any) (pgx.Rows, error)
	QueryRow(ctx context.Context, sql string, args ...any) pgx.Row
}

// Logger receives the warnings the Python writer logs (lookup failures, meter create failures).
type Logger func(msg string)

type sectionKey struct{ building, hint string }

// MeterSpec is what MeterResolve needs to create a meter.
type MeterSpec struct {
	BuildingID string
	MeterType  string
	MPAN, MPRN string
	SectionID  string
	IsSubMeter bool
}

// Set holds every resolver for one organisation, one run.
type Set struct {
	tx     Tx
	spec   *rules.Spec
	org    string
	schema string
	sites  map[string]string
	log    Logger

	buildingCache map[string]*string
	sectionCache  map[sectionKey]string
	floorCache    map[sectionKey]*string
	meterCache    map[string]*string
	refCache      map[[2]string]string
	assetIDs      map[string]string
	assetBldg     map[string]string

	BuildingReads     int
	BuildingAmbiguous []string
	MeterReads        int
	MetersCreated     int
	MeterAmbiguous    []string
	MetersUnlinked    []string
	RefReads          int
	RefResolved       int
	RefAmbiguous      []string
	RefUnresolved     map[string]map[string]bool
	Warnings          []string
}

func New(tx Tx, spec *rules.Spec, org, schema string, siteNames map[string]string, log Logger) *Set {
	return &Set{
		tx: tx, spec: spec, org: org, schema: schema, sites: siteNames, log: log,
		buildingCache: map[string]*string{}, sectionCache: map[sectionKey]string{},
		floorCache: map[sectionKey]*string{}, meterCache: map[string]*string{},
		refCache: map[[2]string]string{}, assetIDs: map[string]string{}, assetBldg: map[string]string{},
		RefUnresolved: map[string]map[string]bool{},
	}
}

func (s *Set) warn(msg string) {
	s.Warnings = append(s.Warnings, msg)
	if s.log != nil {
		s.log(msg)
	}
}

func key(hint string) string { return pystr.Lower(pystr.Strip(hint)) }

// fetch runs one lookup in a savepoint; a failure is a warning and no rows (write_node._fetch).
func (s *Set) fetch(ctx context.Context, sql string, args ...any) [][]any {
	sp, err := s.tx.Begin(ctx)
	if err != nil {
		s.warn(fmt.Sprintf("[Node 9] lookup failed (treated as no match): %v", err))
		return nil
	}
	rows, err := sp.Query(ctx, sql, args...)
	var out [][]any
	if err == nil {
		for rows.Next() {
			vals, verr := rows.Values()
			if verr != nil {
				err = verr
				break
			}
			out = append(out, vals)
		}
		rows.Close()
		if err == nil {
			err = rows.Err()
		}
	}
	if err != nil {
		_ = sp.Rollback(ctx)
		s.warn(fmt.Sprintf("[Node 9] lookup failed (treated as no match): %s", truncate(err.Error(), 200)))
		return nil
	}
	if err := sp.Commit(ctx); err != nil {
		s.warn(fmt.Sprintf("[Node 9] lookup failed (treated as no match): %v", err))
		return nil
	}
	return out
}

func truncate(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}

func str(v any) string {
	switch x := v.(type) {
	case nil:
		return ""
	case string:
		return x
	case bool:
		if x {
			return "True"
		}
		return "False"
	}
	return fmt.Sprint(v)
}

func sortedSet(rows [][]any, col int) []string {
	seen := map[string]bool{}
	for _, r := range rows {
		if len(r) > col {
			if v := str(r[col]); v != "" {
				seen[v] = true
			}
		}
	}
	out := make([]string, 0, len(seen))
	for v := range seen {
		out = append(out, v)
	}
	sort.Strings(out)
	return out
}

func (s *Set) sql(t string) string { return strings.ReplaceAll(t, "{schema}", s.schema) }

// ── buildings ────────────────────────────────────────────────────────────────────────────

const buildingsSQL = `SELECT building_id::text, lower(coalesce(building_code, '')), lower(coalesce(site_id::text, '')) FROM {schema}.buildings WHERE organization_id::text = $1 AND (lower(name) = ANY($2) OR lower(coalesce(building_code, '')) = ANY($2) OR lower(coalesce(site_id::text, '')) = ANY($2)) LIMIT 5`

const sitesSQL = `SELECT coalesce(site_name, ''), coalesce(building_name, ''), coalesce(name, '') FROM {schema}.sites WHERE organization_id::text = $1 AND (lower(coalesce(site_id::text, '')) = $2 OR lower(coalesce(site_code, '')) = $2 OR lower(coalesce(site_name, '')) = $2 OR lower(coalesce(name, '')) = $2) LIMIT 5`

func (s *Set) buildingsMatching(ctx context.Context, names []string) []string {
	set := map[string]bool{}
	for _, n := range names {
		if v := key(n); v != "" {
			set[v] = true
		}
	}
	if len(set) == 0 {
		return nil
	}
	wanted := make([]string, 0, len(set))
	for v := range set {
		wanted = append(wanted, v)
	}
	sort.Strings(wanted)
	s.BuildingReads++
	rows := s.fetch(ctx, s.sql(buildingsSQL), s.org, wanted)
	ids := sortedSet(rows, 0)
	byIdent := map[string]bool{}
	for _, r := range rows {
		if len(r) < 3 || str(r[0]) == "" {
			continue
		}
		for _, c := range []string{str(r[1]), str(r[2])} {
			if c != "" && set[c] {
				byIdent[str(r[0])] = true
			}
		}
	}
	if len(ids) > 1 && len(byIdent) == 1 {
		for id := range byIdent {
			return []string{id}
		}
	}
	return ids
}

// Building is BuildingResolver.resolve: hits and misses cached by the lower-cased hint.
func (s *Set) Building(ctx context.Context, hint string) (string, bool) {
	k := key(hint)
	if k == "" {
		return "", false
	}
	if v, ok := s.buildingCache[k]; ok {
		if v == nil {
			return "", false
		}
		return *v, true
	}
	found := s.resolveBuilding(ctx, k)
	s.buildingCache[k] = found
	if found == nil {
		return "", false
	}
	return *found, true
}

func (s *Set) resolveBuilding(ctx context.Context, k string) *string {
	candidates := []string{k}
	if name, ok := s.sites[k]; ok {
		candidates = append(candidates, name)
	}
	ids := s.buildingsMatching(ctx, candidates)
	if len(ids) == 1 {
		return &ids[0]
	}
	if len(ids) > 1 {
		s.BuildingAmbiguous = append(s.BuildingAmbiguous, k)
		return nil
	}
	s.BuildingReads++
	rows := s.fetch(ctx, s.sql(sitesSQL), s.org, k)
	var names []string
	seen := map[string]bool{}
	for _, r := range rows {
		for _, c := range r {
			if v := pystr.Strip(str(c)); v != "" && !seen[v] {
				seen[v] = true
				names = append(names, v)
			}
		}
	}
	if len(names) == 0 {
		return nil
	}
	ids = s.buildingsMatching(ctx, names)
	if len(ids) == 1 {
		return &ids[0]
	}
	if len(ids) > 1 {
		s.BuildingAmbiguous = append(s.BuildingAmbiguous, k)
	}
	return nil
}

// ── sections and floors ──────────────────────────────────────────────────────────────────

const sectionSQL = `
SELECT s.section_id::text, (lower(s.name) = $2) AS by_name
  FROM {schema}.building_sections s
  LEFT JOIN {schema}.floors f ON f.floor_id = s.floor_id
 WHERE s.building_id = CAST($1 AS uuid)
   AND (lower(s.name) = $2
     OR lower(coalesce(s.section_type, '')) = $2
     OR lower(coalesce(f.name, '')) = $2
     OR coalesce(f.level::text, '') = $2)
 LIMIT 8
`

const floorSQL = `
SELECT f.floor_id::text
  FROM {schema}.floors f
 WHERE f.building_id = CAST($1 AS uuid)
   AND (lower(f.name) = $2 OR f.level::text = $2)
 LIMIT 2
`

// Section is write_node._section_for with meter_link.pick_section; only hits are cached.
func (s *Set) Section(ctx context.Context, buildingID, hint string) (string, bool) {
	if buildingID == "" || hint == "" {
		return "", false
	}
	sk := sectionKey{buildingID, key(hint)}
	if v, ok := s.sectionCache[sk]; ok {
		return v, true
	}
	rows := s.fetch(ctx, s.sql(sectionSQL), sk.building, sk.hint)
	ids := sortedSet(rows, 0)
	pick := ""
	if len(ids) == 1 {
		pick = ids[0]
	} else {
		named := map[string]bool{}
		for _, r := range rows {
			if len(r) > 1 && str(r[0]) != "" {
				if b, ok := r[1].(bool); ok && b {
					named[str(r[0])] = true
				}
			}
		}
		if len(named) == 1 {
			for id := range named {
				pick = id
			}
		}
	}
	if pick == "" {
		return "", false
	}
	s.sectionCache[sk] = pick
	return pick, true
}

// Floor is write_node._floor_for: hits and misses cached.
func (s *Set) Floor(ctx context.Context, buildingID, hint string) (string, bool) {
	if buildingID == "" || hint == "" {
		return "", false
	}
	sk := sectionKey{buildingID, key(hint)}
	if v, ok := s.floorCache[sk]; ok {
		if v == nil {
			return "", false
		}
		return *v, true
	}
	ids := sortedSet(s.fetch(ctx, s.sql(floorSQL), sk.building, sk.hint), 0)
	if len(ids) == 1 {
		s.floorCache[sk] = &ids[0]
		return ids[0], true
	}
	s.floorCache[sk] = nil
	return "", false
}

// ── meters ───────────────────────────────────────────────────────────────────────────────

const meterSQL = `
SELECT id::text FROM {schema}.energy_meters
 WHERE organization_id::text = $1
   AND (lower(coalesce(mpan, '')) = $2
     OR lower(coalesce(mprn, '')) = $2
     OR lower(coalesce(dcc_device_id, '')) = $2
     OR id::text = $2)
 LIMIT 2
`

const createMeterSQL = `
INSERT INTO {schema}.energy_meters
       (id, organization_id, building_id, meter_type, mpan, mprn, active,
        is_sub_meter, section_id)
VALUES (gen_random_uuid(), CAST($1 AS uuid), CAST($2 AS uuid), $3, $4, $5, true,
        $6, CAST($7 AS uuid))
RETURNING id::text
`

func nullable(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func (s *Set) meterExisting(ctx context.Context, k string) (string, bool) {
	s.MeterReads++
	ids := sortedSet(s.fetch(ctx, s.sql(meterSQL), s.org, k), 0)
	if len(ids) == 1 {
		return ids[0], false
	}
	if len(ids) > 1 {
		s.MeterAmbiguous = append(s.MeterAmbiguous, k)
		return "", true
	}
	return "", false
}

// MeterFind is MeterResolver.find: never creates; only a hit is cached.
func (s *Set) MeterFind(ctx context.Context, hint string) (string, bool) {
	k := key(hint)
	if k == "" {
		return "", false
	}
	if v, ok := s.meterCache[k]; ok {
		if v == nil {
			return "", false
		}
		return *v, true
	}
	found, _ := s.meterExisting(ctx, k)
	if found != "" {
		s.meterCache[k] = &found
		return found, true
	}
	return "", false
}

// MeterResolve is MeterResolver.resolve: hits and misses cached; creates a meter only for a hint
// that names no meter and comes with a building, never for an ambiguous one.
func (s *Set) MeterResolve(ctx context.Context, hint string, m MeterSpec) (string, bool) {
	k := key(hint)
	if k == "" {
		return "", false
	}
	if v, ok := s.meterCache[k]; ok {
		if v == nil {
			return "", false
		}
		return *v, true
	}
	found, ambiguous := s.meterExisting(ctx, k)
	if ambiguous {
		s.meterCache[k] = nil
		return "", false
	}
	if found == "" {
		if m.BuildingID != "" {
			found = s.createMeter(ctx, m)
			if found != "" {
				s.MetersCreated++
			}
		} else {
			s.MetersUnlinked = append(s.MetersUnlinked, hint)
		}
	}
	if found == "" {
		s.meterCache[k] = nil
		return "", false
	}
	s.meterCache[k] = &found
	return found, true
}

func (s *Set) createMeter(ctx context.Context, m MeterSpec) string {
	sp, err := s.tx.Begin(ctx)
	if err != nil {
		s.warn(fmt.Sprintf("[Node 9] meter create failed: %v", err))
		return ""
	}
	var id string
	err = sp.QueryRow(ctx, s.sql(createMeterSQL), s.org, m.BuildingID, m.MeterType, nullable(m.MPAN),
		nullable(m.MPRN), m.IsSubMeter, nullable(m.SectionID)).Scan(&id)
	if err != nil {
		_ = sp.Rollback(ctx)
		s.warn(fmt.Sprintf("[Node 9] meter create failed for mpan=%q mprn=%q building=%q: %s",
			m.MPAN, m.MPRN, m.BuildingID, truncate(err.Error(), 200)))
		return ""
	}
	if err := sp.Commit(ctx); err != nil {
		s.warn(fmt.Sprintf("[Node 9] meter create failed: %v", err))
		return ""
	}
	return id
}

// ── references ───────────────────────────────────────────────────────────────────────────

// Reference is ReferenceResolver.resolve: only hits are cached (a miss may be written by a later
// table), two matches resolve to nothing.
func (s *Set) Reference(ctx context.Context, column, hint string) (string, bool) {
	ref, ok := s.spec.Reference(column)
	if !ok || hint == "" {
		return "", false
	}
	ck := [2]string{column, key(hint)}
	if ck[1] == "" {
		return "", false
	}
	if v, ok := s.refCache[ck]; ok {
		return v, true
	}
	parts := make([]string, 0, len(ref.MatchColumns)+1)
	for _, c := range ref.MatchColumns {
		parts = append(parts, fmt.Sprintf("lower(coalesce(%s::text, '')) = $2", c))
	}
	parts = append(parts, "id::text = $2")
	sql := fmt.Sprintf("\nSELECT id::text FROM %s.%s\n WHERE organization_id::text = $1\n   AND (%s)\n LIMIT 2\n",
		s.schema, ref.Table, strings.Join(parts, " OR "))
	s.RefReads++
	ids := sortedSet(s.fetch(ctx, sql, s.org, ck[1]), 0)
	if len(ids) == 1 {
		s.RefResolved++
		s.refCache[ck] = ids[0]
		return ids[0], true
	}
	if len(ids) > 1 {
		s.RefAmbiguous = append(s.RefAmbiguous, column+"="+hint)
	} else {
		if s.RefUnresolved[column] == nil {
			s.RefUnresolved[column] = map[string]bool{}
		}
		s.RefUnresolved[column][hint] = true
	}
	return "", false
}

// ReferenceReport is ReferenceResolver.report().
func (s *Set) ReferenceReport() map[string]any {
	amb := uniqSorted(s.RefAmbiguous)
	if len(amb) > 10 {
		amb = amb[:10]
	}
	unres := map[string][]string{}
	for col, hints := range s.RefUnresolved {
		list := make([]string, 0, len(hints))
		for h := range hints {
			list = append(list, h)
		}
		sort.Strings(list)
		if len(list) > 10 {
			list = list[:10]
		}
		unres[col] = list
	}
	return map[string]any{"reads": s.RefReads, "resolved": s.RefResolved, "ambiguous": amb, "unresolved": unres}
}

func uniqSorted(xs []string) []string {
	seen := map[string]bool{}
	out := []string{}
	for _, x := range xs {
		if !seen[x] {
			seen[x] = true
			out = append(out, x)
		}
	}
	sort.Strings(out)
	return out
}

// ── assets ───────────────────────────────────────────────────────────────────────────────

const assetLookupSQL = `SELECT id::text FROM {schema}.assets WHERE organization_id::text = $1 AND (asset_code = $2 OR id::text = $2) LIMIT 1`
const assetBuildingSQL = `SELECT building_id::text FROM {schema}.assets WHERE organization_id::text = $1 AND (id::text = $2 OR asset_code = $2) AND building_id IS NOT NULL LIMIT 1`

// ExistingAsset is write_node._existing_asset_id: hits and misses cached by the exact code.
func (s *Set) ExistingAsset(ctx context.Context, code string) (string, bool) {
	v, ok := s.assetIDs[code]
	if !ok {
		rows := s.fetch(ctx, s.sql(assetLookupSQL), s.org, code)
		if len(rows) > 0 && len(rows[0]) > 0 {
			v = str(rows[0][0])
		}
		s.assetIDs[code] = v
	}
	return v, v != ""
}

// AssetBuilding is write_node._asset_building: hits and misses cached by the exact reference.
func (s *Set) AssetBuilding(ctx context.Context, ref string) (string, bool) {
	if ref == "" {
		return "", false
	}
	v, ok := s.assetBldg[ref]
	if !ok {
		rows := s.fetch(ctx, s.sql(assetBuildingSQL), s.org, ref)
		if len(rows) > 0 && len(rows[0]) > 0 {
			v = str(rows[0][0])
		}
		s.assetBldg[ref] = v
	}
	return v, v != ""
}
