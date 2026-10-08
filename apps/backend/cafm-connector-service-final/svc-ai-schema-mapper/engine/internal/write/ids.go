package write

import (
	"crypto/rand"
	"fmt"
)

// idGen stands in for the uuid.uuid4() write_node calls for a row with no id. Parity runs use a
// counter the Python side's patched uuid4 counts too; its 9 variant nibble keeps these ids apart
// from the parity database's own gen_random_uuid() counter (…-4000-8000-…).
type idGen struct {
	deterministic bool
	n             int64
}

func (g *idGen) next() string {
	if g.deterministic {
		g.n++
		return fmt.Sprintf("00000000-0000-4000-9000-%012d", g.n)
	}
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(err)
	}
	b[6] = b[6]&0x0f | 0x40
	b[8] = b[8]&0x3f | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}
