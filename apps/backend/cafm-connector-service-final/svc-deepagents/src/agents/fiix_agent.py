"""

Fiix CMMS agent tools — live schema fetch + schema mapping flow (Schema Mapper UI parity).



Chat flow:

1. Ask user for Fiix credentials (subdomain, app key, access key, secret key).

2. configure_fiix_credentials(...) — stored per session only, never echoed back.

3. test_fiix_connection() → fetch_fiix_schema() → start_fiix_schema_mapping()

4. Poll get_schema_mapping_status(schema_mapping_id) — same 8-node pipeline as UI.

5. Optional: start_fiix_ingestion() to sync live data after mapping.

"""

from __future__ import annotations



import httpx

import structlog

from langchain_core.tools import tool



from ..config import settings

from .meta_tools import get_session_context



log = structlog.get_logger(__name__)



_TIMEOUT = 120.0

_TIMEOUT_SCHEMA = 300.0

_BASE = settings.migration_base_url



_FIIX_SETUP_FIELDS = [

    ("fiix_subdomain", "Subdomain (e.g. plenumtechnology)"),

    ("fiix_app_key", "App Key"),

    ("fiix_access_key", "Access Key"),

    ("fiix_secret_key", "Secret Key"),

]





def _err(exc: Exception, op: str) -> dict:

    if isinstance(exc, httpx.HTTPStatusError):

        body = (exc.response.text or "")[:400]

        log.error(f"fiix.{op}.http_error", status=exc.response.status_code, body=body[:200])

        return {"error": body, "status_code": exc.response.status_code}

    log.error(f"fiix.{op}.error", error=str(exc)[:300])

    return {"error": str(exc)[:300]}





def _session_id() -> str:

    return get_session_context() or ""





def _creds_body() -> dict:

    from .session_workspace import fiix_credentials_payload



    sid = _session_id()

    if not sid:

        return {}

    return fiix_credentials_payload(sid)





def _record_fiix_ingestion(session_id: str, ingestion_id: str) -> None:

    if not session_id or not ingestion_id:

        return

    from .session_workspace import record_fiix_ingestion_started



    record_fiix_ingestion_started(session_id, ingestion_id)





def _record_schema_mapping(session_id: str, schema_mapping_id: str) -> None:

    if not session_id or not schema_mapping_id:

        return

    from .session_workspace import record_schema_mapping_started



    record_schema_mapping_started(session_id, schema_mapping_id)





@tool

def get_fiix_setup_status() -> dict:

    """Check whether this chat session has Fiix credentials configured.



    Use at the start of any Fiix live schema or sync request. If not configured,

    ask the user for all four fields (same as Schema Mapper start panel) before

    calling configure_fiix_credentials.

    """

    from .session_workspace import fiix_credentials_configured, get_session_state



    sid = _session_id()

    if not sid:

        return {

            "configured": False,

            "missing_fields": [label for _, label in _FIIX_SETUP_FIELDS],

            "message": "No session context — credentials cannot be stored yet.",

        }

    creds = get_session_state(sid).get("fiix_credentials") or {}

    missing = []

    if not (creds.get("subdomain") or "").strip():

        missing.append("Subdomain (e.g. plenumtechnology)")

    if not (creds.get("app_key") or "").strip():

        missing.append("App Key")

    if not (creds.get("access_key") or "").strip():

        missing.append("Access Key")

    if not (creds.get("secret_key") or "").strip():

        missing.append("Secret Key")

    configured = fiix_credentials_configured(sid)

    if not configured:
        from .session_workspace import set_pending_fiix_confirm

        set_pending_fiix_confirm(sid, action="schema_mapping")

    return {

        "configured": configured,

        "subdomain": (creds.get("subdomain") or "") if configured else "",

        "missing_fields": missing,

        "required_prompt": (

            "Please provide Fiix credentials to connect (same as Schema Mapper UI):\n"

            "1. Subdomain (e.g. plenumtechnology)\n"

            "2. App Key\n"

            "3. Access Key\n"

            "4. Secret Key"

            if missing

            else None

        ),

        "next_steps": (

            ["test_fiix_connection", "fetch_fiix_schema", "start_fiix_schema_mapping"]

            if configured

            else ["configure_fiix_credentials"]

        ),

    }





@tool

def configure_fiix_credentials(

    fiix_subdomain: str,

    fiix_app_key: str,

    fiix_access_key: str,

    fiix_secret_key: str,

) -> dict:

    """Save Fiix API credentials for this chat session (required before live schema fetch).



    Args:

        fiix_subdomain: Tenant subdomain without .macmms.com (e.g. plenumtechnology).

        fiix_app_key: Fiix application key.

        fiix_access_key: Fiix access key.

        fiix_secret_key: Fiix secret key (never returned in tool output).

    """

    sid = _session_id()

    if not sid:

        return {"error": "No active session — cannot store credentials."}

    from .session_workspace import set_fiix_credentials



    result = set_fiix_credentials(

        sid,

        fiix_subdomain=fiix_subdomain,

        fiix_app_key=fiix_app_key,

        fiix_access_key=fiix_access_key,

        fiix_secret_key=fiix_secret_key,

    )

    if not result.get("configured"):

        return {

            "configured": False,

            "error": "All four Fiix credential fields are required.",

        }

    from .session_workspace import get_session_state



    pending = bool(get_session_state(sid).get("pending_fiix_confirm"))

    return {

        "configured": True,

        "subdomain": result.get("subdomain"),

        "pending_fiix_confirm": pending,

        "message": (

            f"Fiix credentials saved for this session ({result.get('subdomain')}). "

            + (

                "User already confirmed — run test_fiix_connection, fetch_fiix_schema, "

                "then start_fiix_schema_mapping."

                if pending

                else "Next: test_fiix_connection, then fetch_fiix_schema, then start_fiix_schema_mapping."

            )

        ),

    }





@tool

async def test_fiix_connection() -> dict:

    """Test live Fiix API connectivity using credentials stored in this chat session.



    Call get_fiix_setup_status / configure_fiix_credentials first if not configured.

    Returns status and subdomain only — no secrets.

    """

    body = _creds_body()

    if not all(body.values()):

        return {

            "error": "Fiix credentials not configured in this session.",

            "action": "Ask the user for subdomain, app key, access key, and secret key, "

            "then call configure_fiix_credentials.",

        }

    try:

        async with httpx.AsyncClient(

            base_url=_BASE, timeout=_TIMEOUT, follow_redirects=True

        ) as client:

            resp = await client.post("/api/platforms/fiix/test-connection", json=body)

            resp.raise_for_status()

            data = resp.json()

            return {

                "status": data.get("status", "ok"),

                "message": data.get("message"),

                "subdomain": data.get("subdomain"),

            }

    except Exception as exc:

        return _err(exc, "test_connection")





@tool

async def fetch_fiix_schema() -> dict:

    """Fetch live Fiix CMMS schema summary (same API as Schema Mapper Fiix connector).



    Requires configure_fiix_credentials in this session. Returns object/table counts

    and sample Fiix object names — not the full mapper JSON.

    """

    body = _creds_body()

    if not all(body.values()):

        return {

            "error": "Fiix credentials not configured in this session.",

            "action": "Call configure_fiix_credentials after collecting credentials from the user.",

        }

    try:

        async with httpx.AsyncClient(

            base_url=_BASE, timeout=_TIMEOUT_SCHEMA, follow_redirects=True

        ) as client:

            resp = await client.post("/api/platforms/fiix/fetch-schema", json=body)

            resp.raise_for_status()

            data = resp.json()

            summary = data.get("summary") or {}

            if not summary.get("table_count"):

                mapper = data.get("mapper") or {}

                tables = mapper.get("tables_by_object") or {}

                if isinstance(tables, dict):

                    names = sorted(tables.keys())

                    col_count = sum(len(v) for v in tables.values() if isinstance(v, dict))

                    summary = {

                        "table_count": len(names),

                        "fiix_object_count": len(names),

                        "column_count": col_count,

                        "mapped_field_count": col_count,

                        "sample_tables": names[:25],

                    }

            comparison = data.get("schema_comparison") or {}

            display = data.get("display_summary") or comparison.get("markdown") or ""

            sid = _session_id()

            if sid:

                from .session_workspace import stash_fiix_schema_summary

                stash_fiix_schema_summary(

                    sid,

                    display_summary=display,

                    schema_comparison=comparison if isinstance(comparison, dict) else None,

                )

            return {

                "status": data.get("status", "ok"),

                "summary": summary,

                "schema_comparison": comparison,

                "display_summary": display,

                "api_calls": data.get("api_calls"),

                "message": display or (

                    "Live Fiix schema fetched. Use start_fiix_schema_mapping to run the "

                    "8-node Schema Mapper pipeline (same as the Schema Mapper UI)."

                ),

            }

    except Exception as exc:

        return _err(exc, "fetch_schema")





@tool

async def start_fiix_schema_mapping(

    organization_id: str = "00000000-0000-0000-0000-000000000001",

    external_cmms_name: str = "Fiix",

) -> dict:

    """Start Schema Mapper 8-node pipeline with live Fiix schema (Schema Mapper UI flow).



    Fetches schema from Fiix API using session credentials, then runs ingest → mapping →

    hierarchy gates. Returns schema_mapping_id — poll with get_schema_mapping_status.



    Args:

        organization_id: Tenant UUID.

        external_cmms_name: Label for the external CMMS (default Fiix).

    """

    body = _creds_body()

    if not all(body.values()):

        return {

            "error": "Fiix credentials not configured in this session.",

            "action": "Collect credentials from the user and call configure_fiix_credentials first.",

        }

    payload = {

        "connector_type": "fiix",

        "organization_id": organization_id,

        "external_cmms_name": external_cmms_name or "Fiix",

        "schema_source": "fiix_api",

        "schema_format": "json",

        **body,

    }

    try:

        async with httpx.AsyncClient(

            base_url=_BASE, timeout=_TIMEOUT_SCHEMA, follow_redirects=True

        ) as client:

            resp = await client.post("/api/schema-mapping", json=payload)

            resp.raise_for_status()

            data = resp.json()

            schema_id = str(data.get("schema_mapping_id") or "")

            sid = _session_id()

            if schema_id and sid:

                _record_schema_mapping(sid, schema_id)

            log.info("fiix.schema_mapping.started", schema_mapping_id=schema_id)

            from .session_workspace import get_session_state, set_pending_schema_gate_confirm

            intro = ""

            if sid:

                set_pending_schema_gate_confirm(sid, schema_mapping_id=schema_id)

                intro = str(get_session_state(sid).get("last_fiix_display_summary") or "").strip()

            gate_prompt = (

                f"Schema mapping started (`{schema_id}`).\n\n"

                "Would you like to continue with gate submissions here in chat, "

                "or use the Schema Mapping UI?\n\n"

                "If you reply **yes** here, I will load the current gate status and guide you."

            )

            message = f"{intro}\n\n{gate_prompt}".strip() if intro else gate_prompt

            return {

                **data,

                "schema_mapping_id": schema_id,

                "display_summary": intro or None,

                "pending_schema_gate_confirm": True,

                "message": message,

            }

    except Exception as exc:

        return _err(exc, "start_schema_mapping")





@tool

async def get_schema_mapping_status(schema_mapping_id: str) -> dict:

    """Poll Schema Mapper pipeline status (nodes, gates, progress) for a Fiix session."""

    if not schema_mapping_id:

        return {"error": "schema_mapping_id is required"}

    try:

        async with httpx.AsyncClient(

            base_url=_BASE, timeout=_TIMEOUT, follow_redirects=True

        ) as client:

            resp = await client.get(f"/api/schema-mapping/{schema_mapping_id}/status")

            resp.raise_for_status()

            data = resp.json()

            comparison = data.get("schema_comparison")

            if comparison and isinstance(comparison, dict):

                data["display_summary"] = comparison.get("markdown") or data.get("display_summary")

            return data

    except Exception as exc:

        return _err(exc, "get_schema_mapping_status")





@tool

async def start_fiix_ingestion(

    organization_id: str = "00000000-0000-0000-0000-000000000001",

    schema_mapping_id: str | None = None,

    created_by: str = "deepagents",

) -> dict:

    """Start background Fiix → plenum_cafm data sync after schema mapping is complete.



    Poll with get_fiix_ingestion_status(ingestion_id). Prefer completing schema mapping first.



    Args:

        organization_id: Tenant UUID.

        schema_mapping_id: SchemaMappingJob UUID from start_fiix_schema_mapping.

        created_by: Audit label.

    """

    params: dict[str, str] = {

        "organization_id": organization_id,

        "created_by": created_by,

    }

    if schema_mapping_id:

        params["schema_mapping_id"] = schema_mapping_id

    try:

        async with httpx.AsyncClient(

            base_url=_BASE, timeout=_TIMEOUT, follow_redirects=True

        ) as client:

            resp = await client.post("/api/fiix-ingestion", params=params)

            resp.raise_for_status()

            data = resp.json()

            ingestion_id = str(data.get("ingestion_id") or "")

            sid = _session_id()

            if ingestion_id and sid:

                _record_fiix_ingestion(sid, ingestion_id)

            log.info("fiix.ingestion.started", ingestion_id=ingestion_id)

            return data

    except Exception as exc:

        return _err(exc, "start_ingestion")





@tool

async def get_fiix_ingestion_status(ingestion_id: str) -> dict:

    """Poll a Fiix data ingestion job: status, progress_pct, record counts, errors."""

    if not ingestion_id:

        return {"error": "ingestion_id is required"}

    try:

        async with httpx.AsyncClient(

            base_url=_BASE, timeout=_TIMEOUT, follow_redirects=True

        ) as client:

            resp = await client.get(f"/api/fiix-ingestion/{ingestion_id}")

            resp.raise_for_status()

            return resp.json()

    except Exception as exc:

        return _err(exc, "get_ingestion_status")





@tool

async def list_fiix_ingestion_jobs(

    organization_id: str = "00000000-0000-0000-0000-000000000001",

    limit: int = 20,

) -> dict:

    """List recent Fiix ingestion jobs for an organization (newest first)."""

    try:

        async with httpx.AsyncClient(

            base_url=_BASE, timeout=_TIMEOUT, follow_redirects=True

        ) as client:

            resp = await client.get(

                "/api/fiix-ingestion",

                params={"organization_id": organization_id, "limit": min(max(limit, 1), 100)},

            )

            resp.raise_for_status()

            return resp.json()

    except Exception as exc:

        return _err(exc, "list_ingestion_jobs")


