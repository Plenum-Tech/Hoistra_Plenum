// Package outputs writes the output step's files — output.json, output.sql, one CSV per
// destination table and output.xlsx — exactly as output_generator_node builds them, streamed
// table by table and gzip-encoded (the workbook is a zip already).
package outputs

import "encoding/json"

// Hierarchy is one confirmed hierarchy, with the keys the exporters read.
type Hierarchy struct {
	SourceTable      *string `json:"source_table"`
	TargetTable      *string `json:"target_table"`
	RelationshipType *string `json:"relationship_type"`
	SourceColumn     *string `json:"source_column"`
	TargetColumn     *string `json:"target_column"`
}

func sp(p *string) (string, bool) {
	if p == nil {
		return "", false
	}
	return *p, true
}

// Job is what the output step's Go branch sends (src/engine/steps.py).
type Job struct {
	FullDir              string            `json:"full_dir"`
	CleanedDir           string            `json:"cleaned_dir"`
	OutDir               string            `json:"out_dir"`
	Schema               string            `json:"schema"`
	Routing              map[string]string `json:"table_routing"`
	Hierarchies          []Hierarchy       `json:"confirmed_hierarchies"`
	ContainmentHierarchy json.RawMessage   `json:"containment_hierarchy,omitempty"` // read by nothing, as in Python
	LookupDDLBlocks      []string          `json:"lookup_ddl_blocks"`
	GeneratedAt          string            `json:"generated_at"`
}

type File struct {
	Name     string `json:"name"`
	Path     string `json:"path"`
	Gzip     bool   `json:"gzip"`
	RawBytes int64  `json:"raw_bytes"`
}

type TableInfo struct {
	Name    string   `json:"name"`
	Rows    int      `json:"rows"`
	Columns []string `json:"columns"`
}

type RoutedInfo struct {
	Dest string `json:"dest"`
	Rows int    `json:"rows"`
}

type Result struct {
	Files             []File       `json:"files"`
	RecordsTables     []TableInfo  `json:"records_tables"`
	Routed            []RoutedInfo `json:"routed"`
	XLSXSkippedReason string       `json:"xlsx_skipped_reason"`
}
