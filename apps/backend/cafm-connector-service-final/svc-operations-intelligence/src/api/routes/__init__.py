from .approvals import router as approvals_router
from .admin import router as admin_router
from .auth import router as auth_router
from .compliance import router as compliance_router
from .contract_performance import router as contract_performance_router
from .crons import router as crons_router
from .energy import router as energy_router
from .ingestion import router as ingestion_router
from .maintenance_watch import router as maintenance_watch_router
from .reports import router as reports_router
from .superadmin import router as superadmin_router
from .value import router as value_router

__all__ = [
    "admin_router",
    "approvals_router",
    "auth_router",
    "compliance_router",
    "contract_performance_router",
    "crons_router",
    "energy_router",
    "ingestion_router",
    "maintenance_watch_router",
    "reports_router",
    "superadmin_router",
    "value_router",
]
