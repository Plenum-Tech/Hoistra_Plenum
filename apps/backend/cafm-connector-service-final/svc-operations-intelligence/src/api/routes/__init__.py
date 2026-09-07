from .approvals import router as approvals_router
from .compliance import router as compliance_router
from .contract_performance import router as contract_performance_router
from .energy import router as energy_router

__all__ = [
    "approvals_router",
    "compliance_router",
    "contract_performance_router",
    "energy_router",
]
