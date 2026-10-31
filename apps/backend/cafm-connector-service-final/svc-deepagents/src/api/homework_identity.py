"""Server-owned test sessions for the isolated Homework 1 environment."""
import hashlib
import os
import re
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

from fastapi import HTTPException
from starlette.responses import JSONResponse

ROLES = {"general_user", "property_manager", "portfolio_manager", "compliance_officer"}
PURPOSES = {"building_read", "certificate_management", "certificate_vendor_actions"}


@dataclass(frozen=True)
class TestPrincipal:
    user_id: str
    role: str
    organization_id: str
    building_ids: tuple[str, ...]
    portfolio_id: str | None = None


class TestIdentityStore:
    """Load private fixture assignments; no client can supply or alter a role."""

    def __init__(self, path: str):
        source = Path(path)
        if not source.is_file() or source.stat().st_mode & 0o077:
            raise ValueError("Homework identity file must exist and be owner-only")
        data = json.loads(source.read_text())
        buildings = data["buildings"]
        users = {}
        for row in data["users"]:
            role = row["role"]
            assigned = tuple(row["building_ids"])
            if role not in ROLES or row["user_id"] in users:
                raise ValueError("Invalid role or duplicate test user")
            if any(buildings.get(b) != row["organization_id"] for b in assigned):
                raise ValueError("Building assignment must belong to user's organization")
            if role == "property_manager" and len(assigned) != 1:
                raise ValueError("Property Manager must have exactly one building")
            if role == "compliance_officer":
                # Organization-wide visibility is still limited to compliance functions.
                assigned = tuple(b for b, org in buildings.items() if org == row["organization_id"])
            users[row["user_id"]] = TestPrincipal(
                row["user_id"], role, row["organization_id"], assigned,
                (row.get("portfolio_id") or f"{row['organization_id']}-default-portfolio")
                if role == "portfolio_manager" else None,
            )
        sessions = {}
        for row in data["sessions"]:
            digest = row["token_sha256"]
            expiry = datetime.fromisoformat(row["expires_at"])
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("Session token must be stored as a SHA-256 hash")
            if expiry.tzinfo is None or row["user_id"] not in users or digest in sessions:
                raise ValueError("Invalid session assignment or expiry")
            sessions[digest] = (users[row["user_id"]], expiry)
        if not users or not sessions or not buildings:
            raise ValueError("Homework identities, sessions and buildings are required")
        self.users = MappingProxyType(users)
        self.sessions = MappingProxyType(sessions)

    def authenticate(self, authorization: str) -> TestPrincipal:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token or len(token) > 512:
            raise HTTPException(401, "Valid test session required")
        digest = hashlib.sha256(token.encode()).hexdigest()
        # Constant-time comparison avoids exposing token hashes through timing.
        match = next((value for key, value in self.sessions.items()
                      if secrets.compare_digest(key, digest)), None)
        if match is None or match[1] <= datetime.now(timezone.utc):
            raise HTTPException(401, "Invalid or expired test session")
        return match[0]


def check_building_scope(principal: TestPrincipal, building_ids: list[str], purpose: str) -> None:
    if purpose not in PURPOSES:
        raise HTTPException(422, "Unknown scope-check purpose")
    if not building_ids or any(b not in principal.building_ids for b in building_ids):
        # Same response for unknown and inaccessible buildings avoids disclosing inventory.
        raise HTTPException(403, "Building access denied")
    if principal.role == "compliance_officer" and purpose == "building_read":
        raise HTTPException(403, "Compliance Officer access is limited to certificate functions")
    if principal.role == "general_user" and purpose != "building_read":
        raise HTTPException(403, "General User cannot perform certificate management or vendor actions")


async def expand_portfolio_scope(principal: TestPrincipal, store_factory=None) -> TestPrincipal:
    """Refresh dynamic portfolio/org building membership from trusted scoped records."""
    if (principal.role not in {"portfolio_manager", "compliance_officer"}
            or os.environ.get("HW1_COMPLIANCE_ENABLED", "false").lower() != "true"):
        return principal
    if store_factory is None:
        from ..services.compliance_hw1.store import runtime_store
        store_factory = runtime_store
    async with store_factory().transaction(principal, repeatable=True) as repo:
        if principal.role == "portfolio_manager" and principal.portfolio_id:
            portfolios = [row for row in await repo.list("records", "portfolio")
                          if row["data"].get("portfolio_id") == principal.portfolio_id]
            if len(portfolios) > 1:
                raise ValueError("Portfolio assignment is ambiguous")
            if not portfolios:
                return principal
            portfolio = portfolios[0]
            members = set(portfolio["data"].get("building_ids", portfolio["buildings"]))
            org_buildings={row["id"] for row in await repo.list("records", "building")}
            if (portfolio["org"] != principal.organization_id
                    or not set(portfolio["buildings"]).issubset(members)
                    or not set(principal.building_ids).issubset(members)
                    or not members.issubset(org_buildings)):
                raise ValueError("Portfolio assignment is inconsistent")
            return TestPrincipal(principal.user_id, principal.role, principal.organization_id,
                tuple(sorted(set(principal.building_ids) | members)), principal.portfolio_id)
        if principal.role == "compliance_officer":
            buildings = {row["id"] for row in await repo.list("records", "building")}
            return TestPrincipal(principal.user_id, principal.role, principal.organization_id,
                tuple(sorted(set(principal.building_ids) | buildings)), None)
    return principal


class HomeworkIdentityMiddleware:
    """Fail closed on legacy paths until their tools implement scoped authorization."""

    def __init__(self, app, store: TestIdentityStore, audit_callback=None):
        self.app = app
        self.store = store
        self.audit_callback = audit_callback

    async def audit(self, principal, request_id, entry_path, outcome):
        if self.audit_callback is not None:
            await self.audit_callback(principal, request_id, entry_path, outcome)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            try:
                await self.audit(None, str(uuid4()), "legacy_websocket", "blocked_path")
            except Exception:
                await send({"type": "websocket.close", "code": 1011})
                return
            await send({"type": "websocket.close", "code": 1008})
            return
        path = scope["path"]
        if path in {"/health", "/health/"}:
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        original_send = send

        async def send(message):
            if (message["type"] == "http.response.start" and message.get("status",200)>=400
                    and scope.get("state",{}).get("test_principal") is not None
                    and any(path.startswith("/api/homework/"+d+"/") for d in ("ingestion","usage","energy","contract","communications","compliance","agent"))):
                from ..services.usage_hw1.events import record_failure
                await record_failure(scope['state']['test_principal'],request_id,path.split('/')[3],message['status'])
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await original_send(message)
        try:
            principal = self.store.authenticate(headers.get(b"authorization", b"").decode("latin1"))
        except HTTPException as exc:
            try:
                await self.audit(None, request_id, "authentication", "invalid_or_missing_session")
            except Exception:
                return await JSONResponse({"detail": "Audit store unavailable"}, status_code=503)(scope, receive, send)
            return await JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                                      headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
        if principal.role in {"portfolio_manager", "compliance_officer"} and os.environ.get(
                "HW1_COMPLIANCE_ENABLED", "false").lower() == "true":
            try:
                principal = await expand_portfolio_scope(principal)
            except Exception:
                try:
                    await self.audit(principal, request_id, "portfolio_scope", "scope_unavailable")
                except Exception:
                    return await JSONResponse({"detail": "Audit store unavailable"}, status_code=503)(scope, receive, send)
                return await JSONResponse({"detail": "Portfolio scope unavailable"}, status_code=503)(scope, receive, send)
        scope.setdefault("state", {})["test_principal"] = principal
        read_paths = {"/api/homework/tools", "/api/homework/tools/execute", "/api/homework/ask",
                      "/api/homework/agent/ask","/api/homework/agent/status"}
        unified_path = bool(re.fullmatch(r'/api/homework/agent/(?:conversations|evaluations)/[A-Za-z0-9-]{1,160}',path)) or path=='/api/homework/agent/source-compatibility'
        document_path = path.startswith("/api/homework/documents/")
        # Only reviewed new routes reopen; no broad prefix grants a legacy endpoint access.
        compliance_path = os.environ.get('HW1_COMPLIANCE_ENABLED','false') == 'true' and (
            path in {'/api/homework/compliance/status','/api/homework/compliance/tools',
                     '/api/homework/compliance/tools/execute','/api/homework/compliance/proposals',
                     '/api/homework/compliance/ask','/api/homework/compliance/events/drain',
                     '/api/homework/compliance/traces'}
            or re.fullmatch(r'/api/homework/compliance/(?:proposals|conversations)/[A-Za-z0-9-]{1,160}',path)
            or re.fullmatch(r'/api/homework/actions/[A-Za-z0-9-]{1,160}/confirm',path))
        communications_path = os.environ.get('HW1_COMPLIANCE_ENABLED','false') == 'true' and (
            path in {'/api/homework/communications/tools','/api/homework/communications/tools/execute',
                     '/api/homework/communications/previews'}
            or re.fullmatch(r'/api/homework/communications/proposals/[A-Za-z0-9-]{1,160}(?:/confirm)?',path)
            or re.fullmatch(r'/api/homework/communications/outbox/[A-Za-z0-9-]{1,160}/recover',path))
        contract_path = os.environ.get('HW1_CONTRACT_ENABLED','false') == 'true' and (
            path in {'/api/homework/contract/status','/api/homework/contract/tools',
                     '/api/homework/contract/tools/execute','/api/homework/contract/ask'}
            or re.fullmatch(r'/api/homework/contract/scorecards/[A-Za-z0-9-]{1,160}',path)
            or re.fullmatch(r'/api/homework/contract/configuration/proposals/[A-Za-z0-9-]{1,160}/confirm',path))
        energy_path = os.environ.get('HW1_ENERGY_ENABLED','false') == 'true' and path in {
            '/api/homework/energy/tools', '/api/homework/energy/tools/execute', '/api/homework/energy/ask'}
        ingestion_path = os.environ.get('HW1_INGESTION_ENABLED','false') == 'true' and (
            path in {'/api/homework/ingestion/previews','/api/homework/ingestion/corrections/previews',
                     '/api/homework/ingestion/tools','/api/homework/ingestion/tools/execute',
                     '/api/homework/ingestion/buildings/previews'}
            or re.fullmatch(r'/api/homework/ingestion/(?:previews|corrections/previews)/[A-Za-z0-9-]{1,160}/confirm',path)
            or re.fullmatch(r'/api/homework/ingestion/buildings/previews/[A-Za-z0-9-]{1,160}/confirm',path)
            or re.fullmatch(r'/api/homework/ingestion/conflicts/[A-Za-z0-9-]{1,160}/resolve',path))
        usage_path = os.environ.get('HW1_USAGE_ENABLED','false') == 'true' and (
            path in {'/api/homework/usage/reports/weekly','/api/homework/usage/reports/monthly',
                     '/api/homework/usage/tools','/api/homework/usage/tools/execute'}
            or re.fullmatch(r'/api/homework/usage/reports/[A-Za-z0-9a-f-]{1,160}',path))

        if (path not in {"/api/homework/identity", "/api/homework/scope-check", "/api/homework/traces"} | read_paths
                and not document_path and not unified_path and not compliance_path and not contract_path and not energy_path and not ingestion_path and not usage_path and not communications_path):
            try:
                await self.audit(principal, request_id, "legacy_http", "blocked_path")
            except Exception:
                return await JSONResponse({"detail": "Audit store unavailable"}, status_code=503)(scope, receive, send)
            # Identity alone cannot safely authorize the legacy query/mutation tools.
            return await JSONResponse(
                {"detail": "Legacy tools unavailable; use verified homework read endpoints"}, status_code=503
            )(scope, receive, send)
        await self.app(scope, receive, send)
