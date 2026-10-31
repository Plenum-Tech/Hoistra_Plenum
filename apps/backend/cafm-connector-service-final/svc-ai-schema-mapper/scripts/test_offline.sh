#!/bin/sh
# Offline schema-mapper tests in the worker image: no network, tree mounted read-only.
#   scripts/test_offline.sh [pytest args]       (run from svc-ai-schema-mapper)
#   HOIST_PARITY=1 scripts/test_offline.sh …    joins the throwaway hoist-parity network instead
#   PT_EXTRA_ARGS="-v ~/Downloads:/data:ro" …   extra docker run arguments (e.g. real files, read-only)
set -eu
SVC="$(cd "$(dirname "$0")/.." && pwd)"
ARCH="$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')"
BIN="$SVC/engine/bin/hoist-engine-linux-$ARCH"
IMG="${HOIST_TEST_IMAGE:-hoistra_plenum-schema-mapper-worker}"
docker volume inspect hoist-pytest-deps >/dev/null 2>&1 || {
  docker volume create hoist-pytest-deps >/dev/null
  docker run --rm -v hoist-pytest-deps:/pydeps "$IMG" pip install -q --target /pydeps pytest pytest-asyncio
}
NET="--network none"
[ "${HOIST_PARITY:-0}" = "1" ] && NET="--network hoist-parity -e HOIST_PARITY_DSN=postgresql://parity:parity@hoist-parity-pg:5432/parity_run -e DB_URL=postgresql+asyncpg://parity:parity@hoist-parity-pg:5432/parity_run"
ENGINE_MOUNT=""
[ -x "$BIN" ] && ENGINE_MOUNT="-v $BIN:/usr/local/bin/hoist-engine:ro"
# shellcheck disable=SC2086
exec docker run --rm $NET $ENGINE_MOUNT ${PT_EXTRA_ARGS:-} \
  -v "$SVC":/work:ro -v hoist-pytest-deps:/pydeps:ro -w /work \
  -e PYTHONPATH=/work:/pydeps -e USE_SQLITE_DEV=true -e PYTHONDONTWRITEBYTECODE=1 \
  -e HOIST_ENGINE_DIR=/tmp/hoist-engine \
  "$IMG" python -m pytest -q -p no:cacheprovider -o log_cli=false "$@"
