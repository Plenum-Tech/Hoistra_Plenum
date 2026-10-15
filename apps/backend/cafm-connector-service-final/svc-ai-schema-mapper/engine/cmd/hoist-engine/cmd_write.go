package main

import (
	"context"
	"os"

	"hoistra/engine/internal/protocol"
	"hoistra/engine/internal/write"
)

// write: the migration's bulk write ("plan" before the write gate, "apply" after it). The
// database DSN comes from HOIST_ENGINE_DSN, never from the job file or the command line.
func init() {
	register("write", func(ctx context.Context, jobPath string, em *protocol.Emitter) (any, error) {
		var job write.Job
		if err := protocol.ReadJob(jobPath, &job); err != nil {
			return nil, err
		}
		dsn := os.Getenv("HOIST_ENGINE_DSN")
		if dsn == "" {
			return nil, protocol.Errorf(protocol.CodeBadJob, "HOIST_ENGINE_DSN is not set")
		}
		return write.Run(ctx, job, dsn, em)
	})
}
