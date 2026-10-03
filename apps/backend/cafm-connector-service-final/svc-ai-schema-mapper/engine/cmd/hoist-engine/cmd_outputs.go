package main

import (
	"context"

	"hoistra/engine/internal/outputs"
	"hoistra/engine/internal/protocol"
)

// outputs: the output step's files (output.json, output.sql, table_<dest>.csv gzip-encoded, and
// output.xlsx), from the run's full and cleaned tables.
func init() {
	register("outputs", func(ctx context.Context, jobPath string, em *protocol.Emitter) (any, error) {
		var job outputs.Job
		if err := protocol.ReadJob(jobPath, &job); err != nil {
			return nil, err
		}
		return outputs.Run(ctx, job, em)
	})
}
