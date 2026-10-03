package rules

// Reference is one reference_link.REFERENCES entry: the column a code or name resolves into.
type Reference struct {
	Column       string   `json:"column"`
	HintKeys     []string `json:"hint_keys"`
	Table        string   `json:"table"`
	MatchColumns []string `json:"match_columns"`
}

// Spec is src/engine/rules_spec.write_rules(): the rule tables, read from the Python modules.
type Spec struct {
	NaturalKeys               map[string][][]string `json:"natural_keys"`
	CoreParents               map[string][]string   `json:"core_parents"`
	References                []Reference           `json:"references"`
	BuildingLinkedTables      []string              `json:"building_linked_tables"`
	BuildingHintTables        []string              `json:"building_hint_tables"`
	BuildingViaAssetTables    []string              `json:"building_via_asset_tables"`
	KnownCoreTables           []string              `json:"known_core_tables"`
	SystemSuppliedColumns     []string              `json:"system_supplied_columns"`
	BuildingHintKeys          []string              `json:"building_hint_keys"`
	SiteTables                []string              `json:"site_tables"`
	MergeKeep                 []string              `json:"merge_keep"`
	MeterHintKeys             []string              `json:"meter_hint_keys"`
	MPANKeys                  []string              `json:"mpan_keys"`
	MPRNKeys                  []string              `json:"mprn_keys"`
	EitherKeys                []string              `json:"either_keys"`
	SectionKeys               []string              `json:"section_keys"`
	SubMeterKeys              []string              `json:"sub_meter_keys"`
	FuelKeys                  []string              `json:"fuel_keys"`
	FloorKeys                 []string              `json:"floor_keys"`
	GasWords                  []string              `json:"gas_words"`
	ElecWords                 []string              `json:"elec_words"`
	WriteChunk                int                   `json:"write_chunk"`
	MaxConsecutiveRowFailures int                   `json:"max_consecutive_row_failures"`
	WidenScanCap              int                   `json:"widen_scan_cap"`
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

func (s *Spec) IsBuildingLinked(t string) bool   { return contains(s.BuildingLinkedTables, t) }
func (s *Spec) IsBuildingHintTable(t string) bool { return contains(s.BuildingHintTables, t) }
func (s *Spec) IsBuildingViaAsset(t string) bool  { return contains(s.BuildingViaAssetTables, t) }
func (s *Spec) IsKnownCore(t string) bool         { return contains(s.KnownCoreTables, t) }
func (s *Spec) IsSystemSupplied(c string) bool    { return contains(s.SystemSuppliedColumns, c) }
func (s *Spec) IsMergeKeep(c string) bool         { return contains(s.MergeKeep, c) }

// Reference returns the REFERENCES entry for a column.
func (s *Spec) Reference(column string) (Reference, bool) {
	for _, r := range s.References {
		if r.Column == column {
			return r, true
		}
	}
	return Reference{}, false
}
