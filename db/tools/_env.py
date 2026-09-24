"""The test database's DSN, from the environment or from the repo's own .env.

Every tool here needs `HOISTRA_TEST_DSN` and each one used to require the caller to have put it
in the environment first. That works in bash (`set -a && . .env && set +a`) and is a mouthful in
PowerShell, so the usual first run of any of these scripts is "HOISTRA_TEST_DSN is not set;
source the repo .env first" — a message that tells you what is missing and not how to supply it.

The value is sitting in the repo's gitignored `.env`, which is where it belongs and where these
tools are always run from. Read it. The environment still wins, so a caller who has exported a
different DSN — pointing at a scratch database, say — is not overridden by a file.

Nothing here reads any other key, and the DSN is never logged: it carries the password.
"""
from __future__ import annotations

import os
import pathlib

VAR = "HOISTRA_TEST_DSN"

#: The repo root, four levels up from db/tools/_env.py.
_REPO = pathlib.Path(__file__).resolve().parents[2]


def _from_dotenv(name: str) -> str | None:
    """`name`'s value from the repo .env, or None. Plain KEY=value, optionally quoted."""
    path = _REPO / ".env"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() != name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value or None
    return None


def hoistra_test_dsn() -> str:
    """The DSN asyncpg wants: no `+asyncpg`, which is SQLAlchemy's driver suffix.

    Raises with the command to run rather than an instruction to go and find one.
    """
    raw = os.environ.get(VAR) or _from_dotenv(VAR)
    if not raw:
        raise SystemExit(
            f"{VAR} is not set and the repo .env does not carry it.\n"
            f"  Looked in: {_REPO / '.env'}\n"
            f"  PowerShell: $env:{VAR} = (Get-Content .env | "
            f"Where-Object {{ $_ -like '{VAR}=*' }}) -replace '^{VAR}=',''\n"
            f"  bash:       set -a && . .env && set +a"
        )
    return raw.replace("+asyncpg", "")
