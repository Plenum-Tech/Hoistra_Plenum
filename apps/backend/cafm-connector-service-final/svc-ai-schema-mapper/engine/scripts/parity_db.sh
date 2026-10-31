#!/bin/sh
# A throwaway Postgres for writer parity tests. Never the stack database, never Azure:
# its own container on an --internal network (no route out), data on tmpfs, removed by `down`.
#   parity_db.sh up | reset | psql [db] | down
set -eu
ROOT="$(cd "$(dirname "$0")/../../../../../.." && pwd)"   # repository root
NET=hoist-parity; PG=hoist-parity-pg
SQL="docker exec -i $PG psql -v ON_ERROR_STOP=0 -q -U parity"
case "${1:-up}" in
  up)
    docker network inspect $NET >/dev/null 2>&1 || docker network create --internal $NET >/dev/null
    docker inspect $PG >/dev/null 2>&1 || docker run -d --name $PG --network $NET \
      -e POSTGRES_USER=parity -e POSTGRES_PASSWORD=parity -e POSTGRES_DB=parity \
      --tmpfs /var/lib/postgresql/data:rw postgres:16-alpine >/dev/null
    until docker exec $PG pg_isready -U parity -q 2>/dev/null; do sleep 1; done
    sleep 1
    $SQL -d parity -c "DROP DATABASE IF EXISTS parity_run WITH (FORCE)" >/dev/null
    $SQL -d parity -c "DROP DATABASE IF EXISTS parity_template WITH (FORCE)" >/dev/null
    $SQL -d parity -c "CREATE DATABASE parity_template" >/dev/null
    $SQL -d parity_template < "$ROOT/db/01_schema.sql" >/dev/null 2>&1 || true
    OPS="$ROOT/apps/backend/cafm-connector-service-final/svc-operations-intelligence/migrations"
    for pass in 1 2; do   # alphabetical order skips ALTERs on tables created later; a second pass applies them
      for f in $(ls "$OPS"/*.sql | sort); do $SQL -d parity_template < "$f" >/dev/null 2>&1 || true; done
    done
    $SQL -d parity_template < "$(dirname "$0")/parity_extras.sql" >/dev/null
    missing=$($SQL -d parity_template -At -c "SELECT string_agg(t, ',') FROM unnest(ARRAY['organizations','sites','buildings','building_sections','floors','assets','work_orders','vendors','vendor_contracts','energy_meters','meter_readings','compliance_certificates','ppm_visits','spare_parts','resources','inspections']) t WHERE to_regclass('plenum_cafm.'||t) IS NULL")
    [ -z "$missing" ] || { echo "parity schema is missing: $missing" >&2; exit 1; }
    $SQL -d parity -c "CREATE DATABASE parity_run TEMPLATE parity_template" >/dev/null
    echo "parity database ready (template: parity_template, run: parity_run)" ;;
  reset)
    $SQL -d parity -c "DROP DATABASE IF EXISTS parity_run WITH (FORCE)" >/dev/null
    $SQL -d parity -c "CREATE DATABASE parity_run TEMPLATE parity_template" >/dev/null ;;
  psql) exec docker exec -it $PG psql -U parity -d "${2:-parity_run}" ;;
  down)
    docker rm -f $PG >/dev/null 2>&1 || true
    docker network rm $NET >/dev/null 2>&1 || true
    echo "parity database removed" ;;
  *) echo "usage: parity_db.sh up|reset|psql [db]|down" >&2; exit 2 ;;
esac
