package ingest

import (
	"bytes"
	"context"
	"os"
	"runtime"
	"strings"
	"sync"
	"unicode"

	"hoistra/engine/internal/arrowtab"
	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/csvread"
	"hoistra/engine/internal/protocol"
	"hoistra/engine/internal/pystr"
	"hoistra/engine/internal/xlsxread"
)

var zipMagic = []byte("PK\x03\x04")

// Run parses the source, writes the full data set to OutDir (every parsed column in the files;
// the manifest lists the columns the duplicate merge keeps) and reports what ingest reports.
func Run(ctx context.Context, job Job, em *protocol.Emitter) (*Result, error) {
	if job.Source == "" || job.OutDir == "" {
		return nil, protocol.Errorf(protocol.CodeBadJob, "parse needs source and out_dir")
	}
	data, err := os.ReadFile(job.Source)
	if err != nil {
		return nil, protocol.Errorf(protocol.CodeBadJob, "cannot read the source: %v", err)
	}
	res := &Result{SourceEncoding: job.Encoding, SetAsideSheets: []string{}}
	if res.SourceEncoding == "" {
		res.SourceEncoding = "utf-8"
	}
	var tables []*Table
	if bytes.HasPrefix(data, zipMagic) {
		res.DetectedFileFormat = "excel"
		res.SourceDelimiter = string(csvread.Delimiter(string(data)))
		data = nil
		post := map[string]bool{}
		for _, s := range job.PostWriteSheets {
			post[s] = true
		}
		tables, res.SetAsideSheets, err = readWorkbook(ctx, job.Source, post, em)
		if err != nil {
			return nil, err
		}
	} else {
		text := strings.TrimPrefix(string(data), "\ufeff")
		data = nil
		delim := csvread.Delimiter(text)
		cols, rows, err := csvread.Read(text, delim)
		if err != nil {
			// ingest falls back to the Excel reader, which cannot read text
			return nil, protocol.Errorf(protocol.CodeParse, "Could not read the Excel file: Cannot detect file format")
		}
		res.DetectedFileFormat = "csv"
		res.SourceDelimiter = string(delim)
		tables = []*Table{{Name: "data", Columns: cols, Rows: rows}}
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	res.NanReport = scanNaN(tables, job.NanSampleRows)
	known := make(map[string]bool, len(job.KnownDestinationColumns))
	for _, c := range job.KnownDestinationColumns {
		known[c] = true
	}
	_, res.DuplicateColumnReport = mergeDuplicateColumns(tables, known)
	if err := writeFull(job.OutDir, tables); err != nil {
		return nil, err
	}
	res.Tables = make([]TableResult, len(tables))
	for i, t := range tables {
		// The preview is the first rows as parsed, before the merge: ingest measures each table's
		// health on it, then applies the merge report to it.
		preview := []*protocol.Object{}
		for _, r := range t.Rows[:min(len(t.Rows), job.PreviewRows)] {
			preview = append(preview, record(t.Columns, r, nil))
		}
		res.Tables[i] = TableResult{Name: t.Name, Rows: len(t.Rows), Columns: nonNil(t.Columns),
			KeptColumns: nonNil(t.Kept), Preview: preview}
	}
	return res, nil
}

func nonNil(s []string) []string {
	if s == nil {
		return []string{}
	}
	return s
}

// isPostWriteSheet is ingest_node._is_post_write_sheet: the name lower-cased, letters and digits
// only, is one the post-write engines read.
func isPostWriteSheet(name string, post map[string]bool) bool {
	var sb strings.Builder
	for _, r := range pystr.Lower(name) {
		if unicode.IsLetter(r) || unicode.IsNumber(r) {
			sb.WriteRune(r)
		}
	}
	return post[sb.String()]
}

// readWorkbook reads every worksheet that is not set aside, in parallel, keeping workbook order;
// the first sheet (in that order) that fails fails the parse, as ingest's loop does.
func readWorkbook(ctx context.Context, path string, post map[string]bool, em *protocol.Emitter) ([]*Table, []string, error) {
	wb, err := xlsxread.Open(path)
	if err != nil {
		return nil, nil, protocol.Errorf(protocol.CodeParse, "%s", friendlyExcelError(err))
	}
	defer wb.Close()
	setAside := []string{}
	var names []string
	seen := map[string]bool{}
	for _, n := range wb.SheetNames() {
		if isPostWriteSheet(n, post) {
			setAside = append(setAside, n)
			continue
		}
		if !seen[n] { // a repeated name reads the first sheet so named, into the same table
			seen[n] = true
			names = append(names, n)
		}
	}
	tables := make([]*Table, len(names))
	errs := make([]error, len(names))
	var mu sync.Mutex
	done := int64(0)
	parallel(len(names), func(i int) {
		n := names[i]
		if ctx.Err() != nil {
			errs[i] = ctx.Err()
			return
		}
		cols, rows, err := xlsxread.Frame(wb, n)
		if err != nil {
			errs[i] = protocol.Errorf(protocol.CodeParse, "%s", err.Error())
			return
		}
		tables[i] = &Table{Name: n, Columns: cols, Rows: rows}
		mu.Lock()
		done++
		if em != nil {
			em.Progress("parse", n, done, int64(len(names)))
		}
		mu.Unlock()
	})
	for _, err := range errs {
		if err != nil {
			return nil, nil, err
		}
	}
	return tables, setAside, nil
}

// parallel runs fn(0..n-1) on up to GOMAXPROCS goroutines and waits for them.
func parallel(n int, fn func(i int)) {
	sem := make(chan struct{}, max(1, runtime.GOMAXPROCS(0)))
	var wg sync.WaitGroup
	for i := 0; i < n; i++ {
		wg.Add(1)
		sem <- struct{}{}
		go func() {
			defer wg.Done()
			defer func() { <-sem }()
			fn(i)
		}()
	}
	wg.Wait()
}

// friendlyExcelError is what excel_parser._friendly_error makes of a workbook calamine cannot
// open: python-calamine says "Cannot detect file format" for every zip it cannot read as a
// workbook (probed: a broken zip, a zip of a text file, a .docx, a workbook part without its
// relationships), which _friendly_error passes through.
func friendlyExcelError(error) string {
	return "Could not read the Excel file: Cannot detect file format"
}

// writeFull writes the full data set: every parsed column in the files, the kept ones listed.
func writeFull(dir string, tables []*Table) error {
	out := make([]*arrowtab.Table, len(tables))
	for i, t := range tables {
		rows := t.Rows
		if rows == nil {
			rows = [][]cell.Cell{}
		}
		at, err := arrowtab.FromRows(t.Name, t.Columns, rows)
		if err != nil {
			return protocol.Errorf(protocol.CodeInternal, "%v", err)
		}
		at.ListOnly(t.Kept)
		out[i] = at
	}
	if err := arrowtab.WriteDir(dir, out); err != nil {
		return protocol.Errorf(protocol.CodeInternal, "writing the full data set: %v", err)
	}
	return nil
}
