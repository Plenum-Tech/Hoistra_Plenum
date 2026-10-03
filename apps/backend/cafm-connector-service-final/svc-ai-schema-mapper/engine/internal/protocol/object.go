package protocol

import (
	"bytes"
	"encoding/json"
)

// Object is a JSON object that keeps its keys in the order they were set (a Go map marshals them
// sorted), so a report reads as the Python dict it replaces.
type Object struct {
	keys []string
	vals []any
}

// Set adds a key at the end, or replaces its value where it already is.
func (o *Object) Set(k string, v any) *Object {
	for i, x := range o.keys {
		if x == k {
			o.vals[i] = v
			return o
		}
	}
	o.keys = append(o.keys, k)
	o.vals = append(o.vals, v)
	return o
}

func (o *Object) Len() int { return len(o.keys) }

func (o *Object) MarshalJSON() ([]byte, error) {
	var b bytes.Buffer
	b.WriteByte('{')
	for i, k := range o.keys {
		if i > 0 {
			b.WriteByte(',')
		}
		kb, err := json.Marshal(k)
		if err != nil {
			return nil, err
		}
		b.Write(kb)
		b.WriteByte(':')
		vb, err := json.Marshal(o.vals[i])
		if err != nil {
			return nil, err
		}
		b.Write(vb)
	}
	b.WriteByte('}')
	return b.Bytes(), nil
}
