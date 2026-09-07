"""
Finds the best available technician for a work order.

Strategy:
  1. Match technician_skills rows to the required_skills list (ILIKE)
  2. Rank by: skill matches DESC → skill level DESC → performance_score DESC
  3. Fallback: any available technician ordered by performance_score

Queries plenum_cafm.technicians + technician_skills + users directly
(no ORM models for these tables in this service).
"""
from typing import Optional
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger

log = get_logger(__name__)

_SKILL_LEVEL_SQL = """
    CASE ts.skill_level
        WHEN 'expert'       THEN 5
        WHEN 'certified'    THEN 5
        WHEN 'advanced'     THEN 4
        WHEN 'intermediate' THEN 3
        ELSE 2
    END
"""


async def find_best_technician(
    required_skills: list[str],
    asset_category: str,
    session: AsyncSession,
) -> Optional[dict]:
    """
    Return {technician_id, name, email, availability_status, performance_score}
    for the best skill-matched available technician.
    Returns None if no technician is available.
    """
    if not required_skills:
        required_skills = [asset_category]

    # Build parameterised ILIKE conditions — max 6 skills to keep query sane
    skills = required_skills[:6]
    params: dict = {}
    conditions: list[str] = []
    for i, skill in enumerate(skills):
        key = f"sk{i}"
        params[key] = f"%{skill}%"
        conditions.append(f"LOWER(ts.skill_name) LIKE LOWER(:{key})")

    skill_filter = " OR ".join(conditions)

    query = text(f"""
        SELECT
            t.id                  AS technician_id,
            u.full_name           AS name,
            u.email               AS email,
            t.availability_status AS availability_status,
            t.performance_score   AS performance_score,
            COUNT(ts.id)          AS skill_matches,
            COALESCE(MAX({_SKILL_LEVEL_SQL}), 0) AS top_skill_level
        FROM plenum_cafm.technicians t
        JOIN plenum_cafm.users u ON u.id = t.user_id
        LEFT JOIN plenum_cafm.technician_skills ts
            ON ts.technician_id = t.id
            AND ({skill_filter})
        WHERE t.availability_status = 'available'
        GROUP BY t.id, u.full_name, u.email, t.availability_status, t.performance_score
        ORDER BY skill_matches DESC, top_skill_level DESC, t.performance_score DESC
        LIMIT 1
    """)

    result = await session.execute(query, params)
    row = result.fetchone()

    if not row:
        # Fallback: any available technician
        fallback = await session.execute(text("""
            SELECT
                t.id           AS technician_id,
                u.full_name    AS name,
                u.email        AS email,
                t.availability_status,
                t.performance_score
            FROM plenum_cafm.technicians t
            JOIN plenum_cafm.users u ON u.id = t.user_id
            WHERE t.availability_status = 'available'
            ORDER BY t.performance_score DESC
            LIMIT 1
        """))
        row = fallback.fetchone()

    if not row:
        log.warning("technician_service.none_available", required_skills=required_skills)
        return None

    tech = {
        "technician_id":       str(row.technician_id),
        "name":                row.name,
        "email":               row.email,
        "availability_status": row.availability_status,
        "performance_score":   row.performance_score,
        "skill_matches":       getattr(row, "skill_matches", 0),
    }
    log.info(
        "technician_service.assigned",
        technician_id=tech["technician_id"],
        name=tech["name"],
        skill_matches=tech["skill_matches"],
    )
    return tech
