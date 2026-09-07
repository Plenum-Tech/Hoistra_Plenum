"""Admin endpoints for dynamic approval rule tuning."""
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...core.logging import get_logger

router = APIRouter()
log = get_logger(__name__)


@router.get("/approval-rules")
async def list_approval_rules(session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        text(
            "SELECT * FROM plenum_cafm.wo_approval_rules "
            "WHERE active = TRUE ORDER BY dimension, rule_id"
        )
    )
    return {"rules": [dict(r._mapping) for r in result.fetchall()]}


@router.patch("/approval-rules/{rule_id}")
async def patch_approval_rule(
    rule_id: int,
    weight: int,
    session: AsyncSession = Depends(get_session),
):
    await session.execute(
        text("""
            UPDATE plenum_cafm.wo_approval_rules
            SET weight = :weight
            WHERE rule_id = :rule_id AND active = TRUE
        """),
        {"weight": weight, "rule_id": rule_id},
    )
    await session.commit()
    log.info("approval.admin.rule_updated", rule_id=rule_id, weight=weight)
    return {"rule_id": rule_id, "weight": weight}
