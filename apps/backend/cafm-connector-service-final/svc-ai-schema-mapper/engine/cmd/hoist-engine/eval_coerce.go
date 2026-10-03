package main

import (
	"context"

	"hoistra/engine/internal/cell"
	"hoistra/engine/internal/coerce"
	"hoistra/engine/internal/protocol"
)

// jsonCell is how the oracle tests send a cell: null, {"s": "text"} or {"i": 0}.
type jsonCell struct {
	S *string `json:"s,omitempty"`
	I *int64  `json:"i,omitempty"`
	B *bool   `json:"b,omitempty"`
}

func (j *jsonCell) cell() cell.Cell {
	switch {
	case j == nil:
		return cell.None
	case j.S != nil:
		return cell.Of(*j.S)
	case j.I != nil:
		return cell.OfInt(*j.I)
	case j.B != nil:
		return cell.OfBool(*j.B)
	}
	return cell.None
}

// coerce-eval: coerce.ForType over a list of cases, for tests/test_engine_coerce_oracle.py.
func init() {
	register("coerce-eval", func(_ context.Context, jobPath string, _ *protocol.Emitter) (any, error) {
		var job struct {
			Cases []struct {
				Value  *jsonCell `json:"value"`
				DBType string    `json:"db_type"`
			} `json:"cases"`
		}
		if err := protocol.ReadJob(jobPath, &job); err != nil {
			return nil, err
		}
		results := make([]map[string]string, len(job.Cases))
		for i, c := range job.Cases {
			v := coerce.ForType(c.Value.cell(), c.DBType)
			r := map[string]string{"k": v.K.String()}
			if v.K != coerce.Null && v.K != coerce.Mismatch {
				r["s"] = v.S
			}
			results[i] = r
		}
		return map[string]any{"results": results}, nil
	})
}
