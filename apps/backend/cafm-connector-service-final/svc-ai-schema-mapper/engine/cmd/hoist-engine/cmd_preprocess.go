package main

import (
	"context"
	"os"

	"hoistra/engine/internal/preprocess"
	"hoistra/engine/internal/protocol"
)

// preprocess: preprocess_node's cleaning of every table (dedupe, fully-null columns, the null
// fill, date coercion, rename, skip fields) — the cleaned and renamed full data sets.
func init() {
	register("preprocess", func(ctx context.Context, jobPath string, em *protocol.Emitter) (any, error) {
		var job preprocess.Job
		if err := protocol.ReadJob(jobPath, &job); err != nil {
			return nil, err
		}
		// the destination schema is read (never written) for EL-4.0 when the run gives a database
		return preprocess.Run(ctx, job, os.Getenv("HOIST_ENGINE_DSN"), em)
	})
}
