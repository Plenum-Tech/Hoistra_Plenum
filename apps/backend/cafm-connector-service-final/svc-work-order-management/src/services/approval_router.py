"""
Looks up who should approve a work order based on asset category.
Falls back to the '*' wildcard row if no specific category match exists.
Email and name are pulled from plenum_cafm.users via user_id FK.
"""
from typing import Optional
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger

log = get_logger(__name__)

_LOOKUP_SQL = """
    SELECT
        u.full_name  AS approver_name,
        u.email      AS approver_email,
        r.approver_role
    FROM plenum_cafm.wo_approver_routing r
    JOIN plenum_cafm.users u ON u.id = r.user_id
    WHERE r.active = true
      AND (
            LOWER(r.asset_category) LIKE LOWER(:category_like)
         OR r.asset_category = '*'
      )
    ORDER BY
        CASE WHEN LOWER(r.asset_category) LIKE LOWER(:category_like)
                  AND r.asset_category != '*'
             THEN 0 ELSE 1 END,
        r.id
    LIMIT 1
"""


async def get_approver(
    asset_category: str,
    session: AsyncSession,
) -> Optional[dict]:
    """
    Return {approver_name, approver_email, approver_role} for the given asset
    category. Specific ILIKE match takes priority over the '*' wildcard row.
    Name and email are resolved live from plenum_cafm.users.
    Returns None if no routing rule is configured.
    """
    result = await session.execute(
        text(_LOOKUP_SQL),
        {"category_like": f"%{asset_category}%"},
    )
    row = result.fetchone()

    if not row:
        log.warning("approval_router.no_rule", asset_category=asset_category)
        return None

    log.info(
        "approval_router.resolved",
        asset_category=asset_category,
        approver_email=row.approver_email,
        approver_role=row.approver_role,
    )
    return {
        "approver_name":  row.approver_name,
        "approver_email": row.approver_email,
        "approver_role":  row.approver_role,
    }
