#!/bin/sh
# Build and test hoist-engine without a local Go toolchain: everything runs in golang:1.25.
#   dev.sh test               go vet + go test (Postgres tests skip)
#   dev.sh test-db [args]     same, joined to the throwaway hoist-parity network (scripts/parity_db.sh);
#                             with args, only `go test -count=1 -p 1 <args>` (e.g. -run X -v ./internal/write/)
#   dev.sh build-linux [arch] static binary at bin/hoist-engine-linux-<arch>
#   dev.sh tidy               go mod tidy
#   dev.sh go <args>          any go command (e.g. go test -run X ./internal/xlsxread/)
set -eu
ENG="$(cd "$(dirname "$0")/.." && pwd)"
IMG=golang:1.25-bookworm
ARCH_DEFAULT="$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')"
run() {
  docker run --rm -v "$ENG":/src -v hoist-go-mod:/go/pkg/mod -v hoist-go-build:/root/.cache/go-build \
    -w /src -e CGO_ENABLED=0 "$@"
}
case "${1:-test}" in
  test) run "$IMG" sh -c 'go vet ./... && go test ./...'
        echo "note: the Postgres tests (writer, resolvers, schema reads, EL-4.0) skipped here;" \
             "run scripts/parity_db.sh up, then dev.sh test-db, before shipping an engine change" >&2 ;;
  test-db)
    shift
    DSN="postgres://parity:parity@hoist-parity-pg:5432/parity_run?sslmode=disable"
    if [ "$#" -eq 0 ]; then
      run --network hoist-parity -e GOFLAGS=-mod=readonly -e GOPROXY=off -e HOIST_PARITY_DSN="$DSN" \
        "$IMG" sh -c 'go vet ./... && go test -count=1 -p 1 ./...'
    else
      run --network hoist-parity -e GOFLAGS=-mod=readonly -e GOPROXY=off -e HOIST_PARITY_DSN="$DSN" \
        "$IMG" go test -count=1 -p 1 "$@"
    fi ;;
  build-linux)
    arch="${2:-$ARCH_DEFAULT}"
    run -e GOOS=linux -e GOARCH="$arch" "$IMG" \
      go build -trimpath -ldflags "-s -w" -o "bin/hoist-engine-linux-$arch" ./cmd/hoist-engine ;;
  tidy) run "$IMG" go mod tidy ;;
  go) shift; run "$IMG" go "$@" ;;
  *) echo "usage: dev.sh test|test-db|build-linux [arch]|tidy|go <args>" >&2; exit 2 ;;
esac
