// Package protocol is the contract between the schema-mapper (Python) and hoist-engine:
// a job file in, newline-delimited JSON events out, exactly one result or error at the end.
package protocol

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"sync"
	"time"
)

const (
	CodeBadJob      = "bad_job"
	CodeUnsupported = "unsupported"
	CodeParse       = "parse_error"
	CodeData        = "data_error"
	CodeDB          = "db_error"
	CodeConnLost    = "connection_lost"
	CodeDDL         = "ddl_failed"
	CodeCancelled   = "cancelled"
	CodeInternal    = "internal"
)

// Error is an engine failure the Python side can act on by its code.
type Error struct{ Code, Message string }

func (e *Error) Error() string { return e.Code + ": " + e.Message }

func Errorf(code, format string, a ...any) error {
	return &Error{Code: code, Message: fmt.Sprintf(format, a...)}
}

// Classify turns any error into (code, message). A cancelled context wins: the run was stopped.
func Classify(ctx context.Context, err error) (string, string) {
	if ctx.Err() != nil {
		return CodeCancelled, "the step was cancelled"
	}
	var e *Error
	if errors.As(err, &e) {
		return e.Code, e.Message
	}
	return CodeInternal, err.Error()
}

type event struct {
	Type    string `json:"type"`
	Stage   string `json:"stage,omitempty"`
	Table   string `json:"table,omitempty"`
	Done    *int64 `json:"done,omitempty"`
	Total   *int64 `json:"total,omitempty"`
	Level   string `json:"level,omitempty"`
	Code    string `json:"code,omitempty"`
	Message string `json:"message,omitempty"`
	Result  any    `json:"result,omitempty"`
}

// Emitter writes events; it is safe for concurrent use. Progress is throttled per
// (stage, table) to one event per minGap, except the tick that reaches total.
type Emitter struct {
	mu     sync.Mutex
	w      io.Writer
	now    func() time.Time
	minGap time.Duration
	last   map[string]time.Time
}

func NewEmitter(w io.Writer) *Emitter {
	return &Emitter{w: w, now: time.Now, minGap: 250 * time.Millisecond, last: map[string]time.Time{}}
}

func (e *Emitter) write(ev event) error {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(ev); err != nil {
		return err
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	_, err := e.w.Write(buf.Bytes())
	return err
}

func (e *Emitter) Progress(stage, table string, done, total int64) {
	key := stage + "\x00" + table
	now := e.now()
	e.mu.Lock()
	last, seen := e.last[key]
	due := !seen || now.Sub(last) >= e.minGap || done >= total
	if due {
		e.last[key] = now
	}
	e.mu.Unlock()
	if due {
		_ = e.write(event{Type: "progress", Stage: stage, Table: table, Done: &done, Total: &total})
	}
}

func (e *Emitter) Log(level, msg string) { _ = e.write(event{Type: "log", Level: level, Message: msg}) }

func (e *Emitter) Result(v any) error {
	if v == nil {
		v = map[string]any{}
	}
	return e.write(event{Type: "result", Result: v})
}

func (e *Emitter) Error(code, msg string) { _ = e.write(event{Type: "error", Code: code, Message: msg}) }

// ReadJob decodes a job file strictly: an unknown field means Python and Go disagree about
// the contract, which must fail loudly rather than be ignored.
// CheckOnly makes ReadJob stop once a job has decoded (hoist-engine <command> --check): the
// schema-mapper's contract tests send every job a node builds through it.
var CheckOnly bool

// ErrJobChecked is ReadJob's answer in check mode for a job that decoded.
var ErrJobChecked = errors.New("job checked")

func ReadJob(path string, v any) error {
	f, err := os.Open(path)
	if err != nil {
		return Errorf(CodeBadJob, "cannot open job file: %v", err)
	}
	defer f.Close()
	dec := json.NewDecoder(f)
	dec.DisallowUnknownFields()
	if err := dec.Decode(v); err != nil {
		return Errorf(CodeBadJob, "invalid job file: %v", err)
	}
	if CheckOnly {
		return ErrJobChecked
	}
	return nil
}
