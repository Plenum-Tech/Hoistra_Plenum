// Package testdb gives Go tests a fresh copy of the throwaway parity database (scripts/parity_db.sh).
// It never touches anything else: HOIST_PARITY_DSN names parity_run on the hoist-parity network.
package testdb

import (
	"context"
	"os"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5"
)

// Fresh recreates parity_run from parity_template and connects to it; it skips the test when
// HOIST_PARITY_DSN is unset (scripts/dev.sh test-db sets it).
func Fresh(t *testing.T) *pgx.Conn {
	t.Helper()
	dsn := os.Getenv("HOIST_PARITY_DSN")
	if dsn == "" {
		t.Skip("HOIST_PARITY_DSN not set (scripts/dev.sh test-db)")
	}
	ctx := context.Background()
	admin, err := pgx.Connect(ctx, strings.Replace(dsn, "/parity_run", "/parity", 1))
	if err != nil {
		t.Fatal(err)
	}
	for _, q := range []string{
		"DROP DATABASE IF EXISTS parity_run WITH (FORCE)",
		"CREATE DATABASE parity_run TEMPLATE parity_template",
	} {
		if _, err := admin.Exec(ctx, q); err != nil {
			t.Fatal(err)
		}
	}
	admin.Close(ctx)
	conn, err := pgx.Connect(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close(context.Background()) })
	return conn
}

// DSN is the run database's DSN, for code under test that opens its own connection.
func DSN() string { return os.Getenv("HOIST_PARITY_DSN") }
