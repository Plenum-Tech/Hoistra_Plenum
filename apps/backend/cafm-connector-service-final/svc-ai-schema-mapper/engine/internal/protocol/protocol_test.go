package protocol

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func lines(t *testing.T, b *bytes.Buffer) []map[string]any {
	t.Helper()
	var out []map[string]any
	for _, l := range strings.Split(strings.TrimSpace(b.String()), "\n") {
		if l == "" {
			continue
		}
		var m map[string]any
		if err := json.Unmarshal([]byte(l), &m); err != nil {
			t.Fatalf("not JSON: %q (%v)", l, err)
		}
		out = append(out, m)
	}
	return out
}

func TestEmitterWritesOneJSONObjectPerLine(t *testing.T) {
	var b bytes.Buffer
	em := NewEmitter(&b)
	em.Log("info", "hello")
	em.Progress("parse", "Assets", 10, 10)
	if err := em.Result(map[string]any{"ok": true}); err != nil {
		t.Fatal(err)
	}
	got := lines(t, &b)
	if len(got) != 3 || got[0]["type"] != "log" || got[1]["type"] != "progress" || got[2]["type"] != "result" {
		t.Fatalf("unexpected events: %v", got)
	}
	if got[1]["table"] != "Assets" || got[1]["done"].(float64) != 10 {
		t.Fatalf("progress fields lost: %v", got[1])
	}
}

func TestProgressIsThrottledButTheLastTickAlwaysGoesOut(t *testing.T) {
	var b bytes.Buffer
	em := NewEmitter(&b)
	now := time.Unix(0, 0)
	em.now = func() time.Time { return now }
	for i := int64(1); i < 100; i++ {
		em.Progress("write", "assets", i, 100)
	}
	em.Progress("write", "assets", 100, 100)
	got := lines(t, &b)
	if len(got) != 2 {
		t.Fatalf("want first + final tick, got %d: %v", len(got), got)
	}
	if got[1]["done"].(float64) != 100 {
		t.Fatalf("final tick missing: %v", got)
	}
}

func TestReadJobRejectsUnknownFields(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "job.json")
	if err := os.WriteFile(p, []byte(`{"input":"a","surprise":1}`), 0o600); err != nil {
		t.Fatal(err)
	}
	var job struct {
		Input string `json:"input"`
	}
	err := ReadJob(p, &job)
	var pe *Error
	if !errors.As(err, &pe) || pe.Code != CodeBadJob {
		t.Fatalf("want bad_job, got %v", err)
	}
}

func TestClassifyMapsCancellationAndUnknownErrors(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if code, _ := Classify(ctx, errors.New("boom")); code != CodeCancelled {
		t.Fatalf("cancelled ctx: got %s", code)
	}
	if code, _ := Classify(context.Background(), errors.New("boom")); code != CodeInternal {
		t.Fatalf("plain error: got %s", code)
	}
	if code, msg := Classify(context.Background(), Errorf(CodeData, "bad %d", 3)); code != CodeData || msg != "bad 3" {
		t.Fatalf("engine error: got %s %q", code, msg)
	}
}
