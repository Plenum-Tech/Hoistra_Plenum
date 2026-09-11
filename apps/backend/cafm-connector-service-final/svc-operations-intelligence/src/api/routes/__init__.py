from .approvals import router as approvals_router
from .admin import router as admin_router
from .auth import router as auth_router
from .compliance import router as compliance_router
from .contract_performance import router as contract_performance_router
from .energy import router as energy_router
from .superadmin import router as superadmin_router

__all__ = [
    "admin_router",
    "approvals_router",
    "auth_router",
    "compliance_router",
    "contract_performance_router",
    "energy_router",
    "superadmin_router",
]
