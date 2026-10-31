"""svc-udr on localhost:8006 for validating the orchestrator's UDR agent end to end, locally.

    python scripts/serve_for_validation.py

The role gate is replaced by a fixed superadmin principal (there is no OTP sign-in locally) and
the startup DDL is skipped. Reads DB_URL from the environment or the repo root .env, and
OPENAI_API_KEY (the catalogue's embedding model) from the environment — the deployed container
app has it; read it into your shell, never into a file. Read-only use intended: point the
deepagents validator (svc-deepagents/scripts/validate_udr_agent.py) at this.

svc-udr and svc-deepagents are both a package called `src`, which is why this is a separate
process rather than an in-process fixture.
"""
from __future__ import annotations

import os
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

HERE = Path(__file__).resolve().parent.parent
ROOT = HERE.parents[3]
for candidate in (HERE / ".env", ROOT / ".env"):
    if candidate.exists():
        for line in candidate.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
if not os.environ.get("DB_URL"):
    sys.exit("DB_URL is not set (environment or .env)")
os.environ["DB_URL"] = re.sub(r"^postgresql(\+\w+)?://", "postgresql+asyncpg://", os.environ["DB_URL"])
if not os.environ.get("OPENAI_API_KEY"):
    print("warning: OPENAI_API_KEY is not set — catalogue search falls back to word matching", file=sys.stderr)

os.chdir(HERE)
sys.path.insert(0, str(HERE))
from src.app import app  # noqa: E402
from src.services.principal import Principal, require_admin  # noqa: E402


@asynccontextmanager
async def _no_lifespan(_app):
    yield


app.router.lifespan_context = _no_lifespan
app.dependency_overrides[require_admin] = lambda: Principal(
    user_id=UUID("00000000-0000-0000-0000-000000000001"), email="validation@local",
    organization_id=None, role="superadmin", building_ids=None,
)

if __name__ == "__main__":
    import uvicorn

    print("svc-udr for validation: db =", re.sub(r"//.*@", "//***@", os.environ["DB_URL"]), flush=True)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8006")), log_level="warning")
