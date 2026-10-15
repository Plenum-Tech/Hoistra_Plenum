package main

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"strings"
	"testing"

	"hoistra/engine/internal/protocol"
)

func lastEvent(t *testing.T, out *bytes.Buffer) map[string]any {
	t.Helper()
	ls := strings.Split(strings.TrimSpace(out.String()), "\n")
	var m map[string]any
	if err := json.Unmarshal([]byte(ls[len(ls)-1]), &m); err != nil {
		t.Fatal(err)
	}
	return m
}

func TestVersionCommand(t *testing.T) {
	var out, errb bytes.Buffer
	if code := run([]string{"version"}, &out, &errb); code != 0 {
		t.Fatalf("exit %d, stderr %s", code, errb.String())
	}
	ev := lastEvent(t, &out)
	if ev["type"] != "result" || ev["result"].(map[string]any)["version"] != Version {
		t.Fatalf("bad result: %v", ev)
	}
}

func TestUnknownCommandIsAUsageError(t *testing.T) {
	var out, errb bytes.Buffer
	if code := run([]string{"nope"}, &out, &errb); code != 2 {
		t.Fatalf("want exit 2, got %d", code)
	}
	if ev := lastEvent(t, &out); ev["type"] != "error" || ev["code"] != protocol.CodeBadJob {
		t.Fatalf("bad event: %v", ev)
	}
}

func TestAPanicBecomesAnInternalError(t *testing.T) {
	register("test-panic", func(ctx context.Context, job string, em *protocol.Emitter) (any, error) {
		panic("kaboom")
	})
	var out, errb bytes.Buffer
	if code := run([]string{"test-panic"}, &out, &errb); code != 1 {
		t.Fatalf("want exit 1, got %d", code)
	}
	ev := lastEvent(t, &out)
	if ev["code"] != protocol.CodeInternal || !strings.Contains(ev["message"].(string), "kaboom") {
		t.Fatalf("bad event: %v", ev)
	}
	if !strings.Contains(errb.String(), "goroutine") {
		t.Fatalf("stack trace missing from stderr")
	}
}

func TestCheckDecodesTheJobAndStops(t *testing.T) {
	// The schema-mapper's tests send each job a node builds through --check: the job decodes
	// against the command's own struct (unknown fields refused) and nothing runs.
	dir := t.TempDir()
	good := dir + "/write.job.json"
	if err := os.WriteFile(good, []byte(`{"mode":"apply","schema":"plenum_cafm","cleaned_dir":"/nowhere"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	var out, errb bytes.Buffer
	if code := run([]string{"write", "--job", good, "--check"}, &out, &errb); code != 0 {
		t.Fatalf("exit %d: %s %s", code, out.String(), errb.String())
	}
	if ev := lastEvent(t, &out); ev["type"] != "result" || ev["result"].(map[string]any)["job"] != "ok" {
		t.Fatalf("bad event: %v", ev)
	}
	bad := dir + "/bad.job.json"
	if err := os.WriteFile(bad, []byte(`{"mode":"apply","not_a_field":1}`), 0o600); err != nil {
		t.Fatal(err)
	}
	out.Reset()
	if code := run([]string{"write", "--job", bad, "--check"}, &out, &errb); code != 1 {
		t.Fatalf("want exit 1 for an unknown field, got %d", code)
	}
	if ev := lastEvent(t, &out); ev["code"] != protocol.CodeBadJob {
		t.Fatalf("bad event: %v", ev)
	}
}
