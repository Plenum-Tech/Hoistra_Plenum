package testdb

import (
	"context"
	"testing"
)

func TestFreshGivesTheTemplateSchemaWithReproducibleUUIDs(t *testing.T) {
	conn := Fresh(t)
	var n int
	if err := conn.QueryRow(context.Background(),
		"SELECT count(*) FROM information_schema.tables WHERE table_schema = 'plenum_cafm' AND table_name = 'meter_readings'").Scan(&n); err != nil || n != 1 {
		t.Fatalf("meter_readings missing: n=%d err=%v", n, err)
	}
	var id string
	if err := conn.QueryRow(context.Background(), "SELECT gen_random_uuid()::text").Scan(&id); err != nil {
		t.Fatal(err)
	}
	if id != "00000000-0000-4000-8000-000000000001" {
		t.Fatalf("gen_random_uuid is not the parity sequence: %s", id)
	}
}
