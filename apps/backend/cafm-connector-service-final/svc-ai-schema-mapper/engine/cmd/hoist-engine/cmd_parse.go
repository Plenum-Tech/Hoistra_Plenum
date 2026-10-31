package main

import (
	"context"

	"hoistra/engine/internal/ingest"
	"hoistra/engine/internal/protocol"
)

// parse: ingest's read of the upload (CSV or workbook), its null scan and duplicate-column
// merge, and the run's full data set.
func init() {
	register("parse", func(ctx context.Context, jobPath string, em *protocol.Emitter) (any, error) {
		var job ingest.Job
		if err := protocol.ReadJob(jobPath, &job); err != nil {
			return nil, err
		}
		return ingest.Run(ctx, job, em)
	})
}
