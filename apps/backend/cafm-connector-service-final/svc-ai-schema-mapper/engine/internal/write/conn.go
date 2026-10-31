package write

import (
	"context"
	"errors"
	"io"
	"net"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"

	"hoistra/engine/internal/protocol"
)

func connect(ctx context.Context, dsn string) (*pgx.Conn, error) {
	cfg, err := pgx.ParseConfig(dsn)
	if err != nil {
		return nil, protocol.Errorf(protocol.CodeBadJob, "invalid database DSN: %v", err)
	}
	cfg.RuntimeParams["application_name"] = "hoist-engine"
	// A naive datetime reaches a timestamptz column as UTC, as asyncpg sends it from a UTC process.
	cfg.RuntimeParams["timezone"] = "UTC"
	conn, err := pgx.ConnectConfig(ctx, cfg)
	if err != nil {
		return nil, protocol.Errorf(protocol.CodeConnLost, "could not connect to the database: %v", err)
	}
	return conn, nil
}

// isConnLost is _is_connection_lost: the database went away, which is not a bad row.
func isConnLost(err error) bool {
	if err == nil {
		return false
	}
	var ne net.Error
	if errors.As(err, &ne) || errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
		return true
	}
	var pe *pgconn.PgError
	if errors.As(err, &pe) {
		switch pe.Code {
		case "57P01", "57P02", "57P03", "08000", "08003", "08006", "08001", "08004":
			return true
		}
		return false
	}
	msg := strings.ToLower(err.Error())
	for _, sign := range []string{"conn closed", "connection was closed", "connection is closed",
		"server closed the connection", "terminating connection", "connection does not exist",
		"broken pipe", "connection reset", "unexpected eof"} {
		if strings.Contains(msg, sign) {
			return true
		}
	}
	return false
}

func connLost(err error) error {
	return protocol.Errorf(protocol.CodeConnLost, "the database connection closed part way through the write: %v", err)
}
