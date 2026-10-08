// Package ingest is ingest_node's steps 1–4 for the engine: read the upload as CSV or as a
// workbook exactly as pandas + python-calamine read it, scan it for nulls, merge identical
// columns, and write the run's full data set.
package ingest

import (
	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/protocol"
)

// Job is the parse job. The source is UTF-8 text or a workbook: for any other text the
// schema-mapper decodes it first (chardet, as today) and Encoding names what it decoded from.
type Job struct {
	Source                  string   `json:"source"`
	OutDir                  string   `json:"out_dir"`
	Encoding                string   `json:"encoding"`
	KnownDestinationColumns []string `json:"known_destination_columns"`
	PostWriteSheets         []string `json:"post_write_sheets"`
	PreviewRows             int      `json:"preview_rows"`
	NanSampleRows           int      `json:"nan_sample_rows"`
}

// Table is one parsed table: its record keys and rows (None for NaN), and the columns the
// duplicate merge keeps.
type Table struct {
	Name    string
	Columns []string
	Rows    [][]cell.Cell
	Kept    []string
}

type TableResult struct {
	Name        string             `json:"name"`
	Rows        int                `json:"rows"`
	Columns     []string           `json:"columns"`
	KeptColumns []string           `json:"kept_columns"`
	Preview     []*protocol.Object `json:"preview"`
}

type Result struct {
	DetectedFileFormat    string           `json:"detected_file_format"`
	SourceDelimiter       string           `json:"source_delimiter"`
	SourceEncoding        string           `json:"source_encoding"`
	Tables                []TableResult    `json:"tables"`
	NanReport             *protocol.Object `json:"nan_report"`
	DuplicateColumnReport *protocol.Object `json:"duplicate_column_report"`
	SetAsideSheets        []string         `json:"set_aside_sheets"`
}

// record is a row as the dict to_dict(orient="records") makes, restricted to cols.
func record(names []string, row []cell.Cell, keep map[string]bool) *protocol.Object {
	o := &protocol.Object{}
	for i, n := range names {
		if keep != nil && !keep[n] {
			continue
		}
		o.Set(n, value(row[i]))
	}
	return o
}

func value(c cell.Cell) any {
	if c.IsNone() {
		return nil
	}
	return c.PyStr()
}
