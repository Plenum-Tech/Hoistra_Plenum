package main

import (
	"context"

	"hoistra/engine/internal/ingest"
	"hoistra/engine/internal/protocol"
)

// combine: a multi-file upload's combined.xlsx (start-with-upload-multi's _parse_and_combine).
func init() {
	register("combine", func(ctx context.Context, jobPath string, em *protocol.Emitter) (any, error) {
		var job ingest.CombineJob
		if err := protocol.ReadJob(jobPath, &job); err != nil {
			return nil, err
		}
		return ingest.Combine(ctx, job, em)
	})
}
