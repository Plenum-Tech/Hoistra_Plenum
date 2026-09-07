"""
Plenum-CAFM CRUD router.

Import and include in app.py:

    from cafm_connector.api.routes.plenum_cafm import plenum_router
    app.include_router(plenum_router, prefix="/api/v1/plenum")
"""

from fastapi import APIRouter

from cafm_connector.api.routes.plenum_cafm.org_users import router as org_users_router
from cafm_connector.api.routes.plenum_cafm.rbac import router as rbac_router
from cafm_connector.api.routes.plenum_cafm.assets import router as assets_router
from cafm_connector.api.routes.plenum_cafm.maintenance_vendors import router as mv_router
from cafm_connector.api.routes.plenum_cafm.work_orders import router as wo_router
from cafm_connector.api.routes.plenum_cafm.inventory_notifications import router as inv_router

plenum_router = APIRouter()

plenum_router.include_router(org_users_router)
plenum_router.include_router(rbac_router)
plenum_router.include_router(assets_router)
plenum_router.include_router(mv_router)
plenum_router.include_router(wo_router)
plenum_router.include_router(inv_router)

__all__ = ["plenum_router"]
