#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
# Validate privately before Compose starts a model-backed service.
python3 - <<'PY'
from pathlib import Path
env = Path('runtime/.env')
if not env.is_file():
    raise SystemExit('Missing runtime/.env; run setup first.')
values = dict(line.split('=', 1) for line in env.read_text().splitlines()
              if '=' in line and not line.lstrip().startswith('#'))
if not values.get('OPENAI_API_KEY', '').strip():
    raise SystemExit('Add OPENAI_API_KEY privately to homework/module-1/runtime/.env first.')
if not values.get('HW1_POSTGRES_PASSWORD', '').strip():
    raise SystemExit('The homework database password is missing.')
PY
docker compose --env-file runtime/.env -p hoistra-hw1 -f docker-compose.hw1.yml up -d --build
docker compose --env-file runtime/.env -p hoistra-hw1 -f docker-compose.hw1.yml ps
