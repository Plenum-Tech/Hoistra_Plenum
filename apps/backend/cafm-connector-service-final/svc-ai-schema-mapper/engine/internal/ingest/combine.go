package ingest

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"
	"time"
	"unicode"

	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/outputs"
	"hoistra/engine/internal/protocol"
	"hoistra/engine/internal/pystr"
	"hoistra/engine/internal/xlsxread"
)

// CombineJob is a multi-file upload: start-with-upload-multi's _parse_and_combine, which makes
// one workbook (one sheet per CSV, one per worksheet of each workbook) for the run to ingest.
type CombineJob struct {
	Files   []CombineFile `json:"files"`
	OutPath string        `json:"out_path"`
}

// CombineFile is one upload, in upload order. A workbook is read here; a CSV/TSV is read by the
// schema-mapper with pandas (its python engine sniffs the delimiter) and handed over as a frame
// file — JSON lines: the column names, then each row's values — or, when pandas could not read
// it, as an error carrying pandas' message.
type CombineFile struct {
	Path  string `json:"path"`
	Name  string `json:"name"`
	Kind  string `json:"kind"` // "workbook" | "frame" | "error"
	Error string `json:"error,omitempty"`
}

type CombineResult struct {
	Sheets []string `json:"sheets"`
	Bytes  int64    `json:"bytes"`
}

// newSheetNamer is _safe_sheet with its own used set: ':\/?*[]' become '_', the name is stripped
// (empty → "sheet") and cut to 31 characters, and a name already used (case-insensitively) gets
// _2, _3, … within those 31.
func newSheetNamer() func(string) string {
	used := map[string]bool{}
	return func(base string) string {
		var sb strings.Builder
		for _, r := range base {
			if strings.ContainsRune(`:\/?*[]`, r) {
				sb.WriteByte('_')
			} else {
				sb.WriteRune(r)
			}
		}
		cleaned := pystr.Strip(sb.String())
		if cleaned == "" {
			cleaned = "sheet"
		}
		cleaned = firstRunes(cleaned, 31)
		name := cleaned
		for i := 2; used[pystr.Lower(name)]; i++ {
			suffix := fmt.Sprintf("_%d", i)
			name = firstRunes(cleaned, 31-len(suffix)) + suffix
		}
		used[pystr.Lower(name)] = true
		return name
	}
}

func firstRunes(s string, n int) string {
	if rs := []rune(s); len(rs) > n {
		return string(rs[:n])
	}
	return s
}

// pathStem is pathlib.PurePosixPath(name).stem.
func pathStem(name string) string {
	parts := strings.Split(name, "/")
	base := ""
	for i := len(parts) - 1; i >= 0; i-- {
		if p := parts[i]; p != "" && p != "." {
			base = p
			break
		}
	}
	if i := strings.LastIndexByte(base, '.'); i > 0 && i < len(base)-1 {
		return base[:i]
	}
	return base
}

// isGenericSheet is re.match(r"^sheet\s*\d*$", label, re.IGNORECASE): Excel's default names.
func isGenericSheet(label string) bool {
	rs := []rune(label)
	word := []rune("sheet")
	if len(rs) < len(word) {
		return false
	}
	for i, w := range word {
		if !foldEq(rs[i], w) {
			return false
		}
	}
	i := len(word)
	for i < len(rs) && pystr.IsSpace(rs[i]) {
		i++
	}
	for i < len(rs) && unicode.IsDigit(rs[i]) {
		i++
	}
	return i == len(rs) || (i == len(rs)-1 && rs[i] == '\n')
}

func foldEq(a, b rune) bool {
	if a == b {
		return true
	}
	for f := unicode.SimpleFold(a); f != a; f = unicode.SimpleFold(f) {
		if f == b {
			return true
		}
	}
	return false
}

// readFrameFile reads a frame the schema-mapper wrote.
func readFrameFile(path string) ([]string, [][]cell.Cell, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, nil, err
	}
	defer f.Close()
	dec := json.NewDecoder(bufio.NewReaderSize(f, 1<<20))
	var names []string
	if err := dec.Decode(&names); err != nil {
		return nil, nil, fmt.Errorf("frame %s: %w", path, err)
	}
	var rows [][]cell.Cell
	for {
		var vals []*string
		if err := dec.Decode(&vals); err == io.EOF {
			break
		} else if err != nil {
			return nil, nil, fmt.Errorf("frame %s: %w", path, err)
		}
		row := make([]cell.Cell, len(vals))
		for i, v := range vals {
			if v != nil {
				row[i] = cell.Of(*v)
			}
		}
		rows = append(rows, row)
	}
	return names, rows, nil
}

// Combine reads every upload in order and writes the combined workbook: a lone generic sheet
// (Sheet1) is named after its file, every other sheet keeps its name, a CSV takes its file's
// stem — all through _safe_sheet — and every cell is df.fillna("") (empty).
func Combine(ctx context.Context, job CombineJob, em *protocol.Emitter) (*CombineResult, error) {
	if job.OutPath == "" {
		return nil, protocol.Errorf(protocol.CodeBadJob, "combine needs out_path")
	}
	name := newSheetNamer()
	var sheets []outputs.Sheet
	for k, f := range job.Files {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		failed := func(msg string) error {
			return protocol.Errorf(protocol.CodeParse, "Could not parse '%s': %s", f.Name, msg)
		}
		stem := pathStem(f.Name)
		switch f.Kind {
		case "error":
			return nil, failed(f.Error)
		case "frame":
			cols, rows, err := readFrameFile(f.Path)
			if err != nil {
				return nil, protocol.Errorf(protocol.CodeInternal, "%v", err)
			}
			sheets = append(sheets, outputs.Sheet{Title: name(stem), Columns: cols, Rows: rows})
		case "workbook":
			wb, err := xlsxread.Open(f.Path)
			if err != nil {
				return nil, failed(friendlyExcelError(err))
			}
			names := wb.SheetNames()
			read, err := readSheets(ctx, wb, names)
			wb.Close()
			if err != nil {
				return nil, failed(err.Error())
			}
			for i, sh := range names {
				label := pystr.Strip(sh)
				base := label
				if (len(names) == 1 && isGenericSheet(label)) || label == "" {
					base = stem
				}
				sheets = append(sheets, outputs.Sheet{Title: name(base), Columns: read[i].cols, Rows: read[i].rows})
			}
		default:
			return nil, protocol.Errorf(protocol.CodeBadJob, "unknown file kind %q", f.Kind)
		}
		if em != nil {
			em.Progress("combine", f.Name, int64(k+1), int64(len(job.Files)))
		}
	}
	if len(sheets) == 0 {
		return nil, protocol.Errorf(protocol.CodeParse, "No parseable structured data found in uploads")
	}
	out, err := os.Create(job.OutPath)
	if err != nil {
		return nil, protocol.Errorf(protocol.CodeInternal, "writing %s: %v", job.OutPath, err)
	}
	cw := &countWriter{w: out}
	bw := bufio.NewWriterSize(cw, 1<<20)
	why, werr := outputs.WriteFrames(bw, sheets, time.Now().UTC().Format("2006-01-02T15:04:05Z"))
	if werr == nil {
		werr = bw.Flush()
	}
	cerr := out.Close()
	if why != "" {
		os.Remove(job.OutPath)
		return nil, protocol.Errorf(protocol.CodeData, "%s", why) // openpyxl's refusal, raised by to_excel
	}
	if werr != nil || cerr != nil {
		return nil, protocol.Errorf(protocol.CodeInternal, "writing %s: %v %v", job.OutPath, werr, cerr)
	}
	titles := make([]string, len(sheets))
	for i, s := range sheets {
		titles[i] = s.Title
	}
	return &CombineResult{Sheets: titles, Bytes: cw.n}, nil
}

type frame struct {
	cols []string
	rows [][]cell.Cell
}

// readSheets reads a workbook's sheets in parallel; the first that fails, in sheet order, fails.
func readSheets(ctx context.Context, wb *xlsxread.Workbook, names []string) ([]frame, error) {
	out := make([]frame, len(names))
	errs := make([]error, len(names))
	parallel(len(names), func(i int) {
		if ctx.Err() != nil {
			errs[i] = ctx.Err()
			return
		}
		cols, rows, err := xlsxread.Sheet(wb, names[i])
		errs[i] = err
		out[i] = frame{cols, rows}
	})
	for _, err := range errs {
		if err != nil {
			return nil, err
		}
	}
	return out, nil
}

type countWriter struct {
	w io.Writer
	n int64
}

func (c *countWriter) Write(p []byte) (int, error) {
	n, err := c.w.Write(p)
	c.n += int64(n)
	return n, err
}
