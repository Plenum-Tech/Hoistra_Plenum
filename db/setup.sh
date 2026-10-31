#!/usr/bin/env bash
# Stand up a fresh Hoistra database and load the schema. Idempotent-ish: it removes and
# recreates the container, so running it twice gives you a clean database twice.
#
#   ./db/setup.sh                 container hoistra-db on 5432
#   PORT=5433 NAME=my-db ./db/setup.sh
#   SKIP_REFERENCE=1 ./db/setup.sh    schema + bootstrap only, no regulation packs
#   DEMO=1 ./db/setup.sh              plus a demo portfolio to test against
set -euo pipefail

NAME="${NAME:-hoistra-db}"
PORT="${PORT:-5432}"
DBN="${DBN:-hoistra}"
USER="${USER_NAME:-cafm}"
PASS="${PASS:-cafm}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# pgvector, not plain postgres: the schema uses the vector extension for document
# embeddings. On plain postgres that one statement fails and you get a database that is
# complete apart from document_chunks — which is worse than an obvious failure, because
# everything else works.
IMAGE="${IMAGE:-pgvector/pgvector:pg16}"

echo "==> removing any existing container named $NAME"
docker rm -f "$NAME" >/dev/null 2>&1 || true

echo "==> starting $IMAGE as $NAME on port $PORT"
docker run -d --name "$NAME" \
  -e POSTGRES_USER="$USER" -e POSTGRES_PASSWORD="$PASS" -e POSTGRES_DB="$DBN" \
  -p "$PORT:5432" "$IMAGE" >/dev/null

printf "==> waiting for postgres"
for _ in $(seq 1 60); do
  if docker exec "$NAME" pg_isready -U "$USER" -d "$DBN" >/dev/null 2>&1; then
    echo " ready"
    break
  fi
  printf "."
  sleep 1
done

FILES=(01_schema.sql)
[ "${SKIP_REFERENCE:-0}" = "1" ] || FILES+=(02_reference_data.sql)
FILES+=(03_bootstrap.sql)
# The demo portfolio is opt-IN. An empty database is the right default: it is what you
# want when the next thing to happen is a real import, and demo rows mixed into real ones
# are hard to tell apart afterwards and harder to remove.
[ "${DEMO:-0}" = "1" ] && FILES+=(04_demo_data.sql)

for f in "${FILES[@]}"; do
  echo "==> $f"
  # ON_ERROR_STOP so a broken file fails the script rather than leaving a half-built
  # database that looks fine until something reads the table that did not get made.
  docker exec -i "$NAME" psql -q -U "$USER" -d "$DBN" -v ON_ERROR_STOP=1 -o /dev/null < "$HERE/$f"
done

echo
docker exec "$NAME" psql -U "$USER" -d "$DBN" -tAc "
  SELECT (SELECT count(*) FROM information_schema.tables
          WHERE table_schema='plenum_cafm' AND table_type='BASE TABLE') || ' tables, '
      || (SELECT count(*) FROM information_schema.views
          WHERE table_schema='plenum_cafm') || ' views, '
      || (SELECT count(*) FROM pg_indexes WHERE schemaname='plenum_cafm') || ' indexes'"

cat <<EOF

Ready. Point the service at it:

  DB_URL=postgresql+asyncpg://$USER:$PASS@127.0.0.1:$PORT/$DBN
  AUTH_DEFAULT_ORGANIZATION_ID=00000000-0000-0000-0000-000000000001

and set AUTH_JWT_SECRET and AUTH_OTP_PEPPER to 32+ random characters each — the service
refuses to start in production without them, and generates throwaway ones in development
that change on every restart.

Then create the first account:

  curl -X POST http://localhost:8009/api/auth/register \\
    -H 'Content-Type: application/json' \\
    -d '{"email":"you@example.com","password":"a-long-enough-passphrase","full_name":"Your Name"}'

With EMAIL_DRY_RUN=true (the default) the code is never sent anywhere and never stored —
so on a dev box, read it from the service log or set up SMTP/Graph and turn dry-run off.
EOF
