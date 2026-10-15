package main

import (
	"context"

	"hoistra/engine/internal/arrowtab"
	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/protocol"
)

// arrow-selftest writes a fixed data set so the Python tests can prove they read what Go writes.
func init() {
	register("arrow-selftest", func(_ context.Context, jobPath string, _ *protocol.Emitter) (any, error) {
		var job struct {
			OutDir string `json:"out_dir"`
		}
		if err := protocol.ReadJob(jobPath, &job); err != nil {
			return nil, err
		}
		wo := arrowtab.NewBuilder("Work Orders", []string{"wo", "qty", "note"})
		wo.Append([]cell.Cell{cell.Of("W1"), cell.OfInt(0), cell.None})
		wo.Append([]cell.Cell{cell.Of("W2"), cell.Of("5"), cell.Of("")})
		uni := arrowtab.NewBuilder("Ünïcode ✓", []string{"naïve"})
		uni.Append([]cell.Cell{cell.Of("é")})
		tables := []*arrowtab.Table{wo.Build(), arrowtab.NewBuilder("Empty", nil).Build(), uni.Build()}
		if err := arrowtab.WriteDir(job.OutDir, tables); err != nil {
			return nil, protocol.Errorf(protocol.CodeInternal, "%v", err)
		}
		return map[string]any{"tables": len(tables)}, nil
	})
}
