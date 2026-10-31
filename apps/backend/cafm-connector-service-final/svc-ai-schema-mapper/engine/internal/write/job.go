// Package write is the engine's bulk writer: the port of write_node._apply_records_with_schema_alignment
// and _insert_rows. It builds the rows the Python writer builds, decides the same merges, duplicates,
// failures and conflicts, and sends each batch the way asyncpg's executemany sends it — every row of a
// batch pipelined in one round trip — with Python's own row-by-row recovery where a batch fails.
//
// Two modes:
//   - "plan": a read-only transaction, rolled back — what the write is expected to do, for the
//     write gate (no DDL, no meter is created, nothing is inserted);
//   - "apply": DDL in its own transaction, then every row in one transaction.
package write

import "hoistra/engine/internal/rules"

type DDLStatement struct {
	SQL         string `json:"sql"`
	Description string `json:"description"`
}

// Rename is write_node's new-column collision safety net: in source table Table, the values under
// From move to To. Applied in order, as the Python loop applies them.
type Rename struct {
	Table string `json:"table"`
	From  string `json:"from"`
	To    string `json:"to"`
}

// Job is what write_node's Go branch sends (src/engine/steps.py).
type Job struct {
	Mode               string              `json:"mode"`
	Schema             string              `json:"schema"`
	OrganizationID     string              `json:"organization_id"`
	DefaultBuildingID  string              `json:"default_building_id"`
	CleanedDir         string              `json:"cleaned_dir"`
	OutDir             string              `json:"out_dir"`
	Routing            map[string]string   `json:"table_routing"`
	ApprovedNewColumns map[string][]string `json:"approved_new_columns"`
	Hierarchies        []rules.Edge        `json:"confirmed_hierarchies"`
	DDL                []DDLStatement      `json:"ddl_statements"`
	ColumnRenames      []Rename            `json:"column_renames"`
	Rules              rules.Spec          `json:"rules"`
	DeterministicIDs   bool                `json:"deterministic_ids,omitempty"`
	// DeterministicIDsFrom is where a parity run's id counter starts (each run of a re-run test
	// draws its own block, so a row written twice shows up instead of colliding on its first id).
	DeterministicIDsFrom int64 `json:"deterministic_ids_from,omitempty"`
	// BulkMinRows is the shortest run of rows the bulk load takes (bulk.go): 0 is the default,
	// a negative number turns the bulk load off.
	BulkMinRows int `json:"bulk_min_rows,omitempty"`
}

type TableResult struct {
	Source         string  `json:"source"`
	Dest           string  `json:"dest"`
	Inserted       int     `json:"inserted"`
	Merged         int     `json:"merged"`
	Skipped        int     `json:"skipped"`
	AlreadyPresent int     `json:"already_present"`
	Seconds        float64 `json:"seconds"`
	Mode           string  `json:"mode"`
}

// Result carries the Python writer's keys, so the node, the job row and the UDR node read it unchanged.
type Result struct {
	RowsInserted    int            `json:"rows_inserted"`
	TablesWritten   int            `json:"tables_written"`
	RowsSkipped     int            `json:"rows_skipped"`
	RowsMerged      int            `json:"rows_merged"`
	BuildingsLinked int            `json:"buildings_linked"`
	MetersLinked    int            `json:"meters_linked"`
	MetersCreated   int            `json:"meters_created"`
	MetersMatched   int            `json:"meters_matched"`
	MetersUnlinked  []string       `json:"meters_unlinked"`
	References      map[string]any `json:"references"`
	RowErrors       []string       `json:"row_errors"`
	Tables          []TableResult  `json:"tables"`
	OrganizationID  string         `json:"organization_id"`
	BulkRows        int            `json:"bulk_rows"`  // rows that went in by the bulk load
	ServerIDs       int            `json:"server_ids"` // of those, rows whose random id the server drew
	Plan            *Plan          `json:"plan,omitempty"`
}

type InvalidValue struct {
	Column   string `json:"column"`
	Count    int    `json:"count"`
	DestType string `json:"dest_type"`
	Sample   string `json:"sample"`
}

type ReasonCount struct {
	Reason string `json:"reason"`
	Count  int    `json:"count"`
}

type PlanTable struct {
	Source              string         `json:"source"`
	Dest                string         `json:"dest"`
	Rows                int            `json:"rows"`
	MergeExistingAssets int            `json:"merge_existing_assets"`
	AlreadyPresent      int            `json:"already_present"`
	InvalidValues       []InvalidValue `json:"invalid_values"`
	RowsCannotWrite     []ReasonCount  `json:"rows_cannot_write"`
	NewColumns          []string       `json:"new_columns"`
	DroppedColumns      []string       `json:"dropped_columns"`
	WidenToText         []string       `json:"widen_to_text"`
	ReferencesToResolve map[string]int `json:"references_to_resolve"`
	CreatesTable        bool           `json:"creates_table"`
}

type Plan struct {
	Tables        []PlanTable `json:"tables"`
	DDLStatements int         `json:"ddl_statements"`
	InputHash     string      `json:"input_hash"`
}
