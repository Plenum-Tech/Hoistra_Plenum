package outputs

import (
	"bufio"
	"compress/gzip"
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"hoistra/engine/internal/protocol"
)

type countWriter struct {
	w io.Writer
	n int64
}

func (c *countWriter) Write(p []byte) (int, error) {
	n, err := c.w.Write(p)
	c.n += int64(n)
	return n, err
}

// writeGz streams one text artefact into a gzip file (level 6, as encode_artefact sends it).
func writeGz(path string, fill func(*bufio.Writer)) (int64, error) {
	f, err := os.Create(path)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	gz, err := gzip.NewWriterLevel(f, 6)
	if err != nil {
		return 0, err
	}
	cw := &countWriter{w: gz}
	bw := bufio.NewWriterSize(cw, 1<<20)
	fill(bw)
	if err := bw.Flush(); err != nil {
		return 0, err
	}
	if err := gz.Close(); err != nil {
		return 0, err
	}
	return cw.n, f.Close()
}

// Run is one `hoist-engine outputs` job.
func Run(ctx context.Context, job Job, em *protocol.Emitter) (*Result, error) {
	if job.OutDir == "" {
		return nil, protocol.Errorf(protocol.CodeBadJob, "out_dir is required")
	}
	if job.Schema == "" {
		job.Schema = "plenum_cafm"
	}
	full, err := readDir(job.FullDir)
	if err != nil {
		return nil, protocol.Errorf(protocol.CodeData, "cannot read the full tables: %v", err)
	}
	cleaned, err := readDir(job.CleanedDir)
	if err != nil {
		return nil, protocol.Errorf(protocol.CodeData, "cannot read the cleaned tables: %v", err)
	}
	records := recordsTables(cleaned, full)
	routes := routeTables(records, job.Routing)
	if err := os.MkdirAll(job.OutDir, 0o755); err != nil {
		return nil, protocol.Errorf(protocol.CodeInternal, "cannot create %s: %v", job.OutDir, err)
	}
	res := &Result{Files: []File{}, RecordsTables: []TableInfo{}, Routed: []RoutedInfo{}}
	steps := 3
	for _, r := range routes {
		if r.rows() > 0 {
			steps++
		}
	}
	done := 0
	tick := func(name string) {
		done++
		if em != nil {
			em.Progress("outputs", name, int64(done), int64(steps))
		}
	}
	gz := func(name, local string, fill func(*bufio.Writer)) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		path := filepath.Join(job.OutDir, local)
		n, err := writeGz(path, fill)
		if err != nil {
			return protocol.Errorf(protocol.CodeInternal, "writing %s: %v", name, err)
		}
		res.Files = append(res.Files, File{Name: name, Path: path, Gzip: true, RawBytes: n})
		tick(name)
		return nil
	}
	if err := gz("output.json", "output.json.gz", func(w *bufio.Writer) {
		writeOutputJSON(w, records, job.Hierarchies, job.GeneratedAt)
	}); err != nil {
		return nil, err
	}
	if err := gz("output.sql", "output.sql.gz", func(w *bufio.Writer) {
		writeSQL(w, routes, job.Hierarchies, job.Schema, job.LookupDDLBlocks)
	}); err != nil {
		return nil, err
	}
	sheets, why := planWorkbook(full, records)
	if why != "" {
		res.XLSXSkippedReason = why
	} else {
		path := filepath.Join(job.OutDir, "output.xlsx")
		f, err := os.Create(path)
		if err != nil {
			return nil, protocol.Errorf(protocol.CodeInternal, "writing output.xlsx: %v", err)
		}
		created := strings.SplitN(job.GeneratedAt, ".", 2)[0]
		if created != "" && !strings.HasSuffix(created, "Z") {
			created += "Z"
		}
		cw := &countWriter{w: f}
		werr := writeWorkbook(cw, sheets, created)
		cerr := f.Close()
		if werr != nil || cerr != nil {
			return nil, protocol.Errorf(protocol.CodeInternal, "writing output.xlsx: %v %v", werr, cerr)
		}
		res.Files = append(res.Files, File{Name: "output.xlsx", Path: path, RawBytes: cw.n})
	}
	tick("output.xlsx")
	for i, r := range routes {
		if r.rows() == 0 {
			continue
		}
		rr := r
		if err := gz("table_"+r.dest+".csv", fmt.Sprintf("table_%04d.csv.gz", i), func(w *bufio.Writer) {
			writeCSV(w, rr)
		}); err != nil {
			return nil, err
		}
	}
	for _, t := range records {
		cols := []string{}
		if t.rows() > 0 {
			cols = append(cols, t.cols()...)
		}
		res.RecordsTables = append(res.RecordsTables, TableInfo{Name: t.name, Rows: t.rows(), Columns: cols})
	}
	for _, r := range routes {
		res.Routed = append(res.Routed, RoutedInfo{Dest: r.dest, Rows: r.rows()})
	}
	return res, nil
}
