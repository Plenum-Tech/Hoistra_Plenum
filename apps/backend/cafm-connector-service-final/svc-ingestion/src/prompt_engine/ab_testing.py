"""
svc-ingestion/src/prompt_engine/ab_testing.py

Task 2.7 — A/B Test Framework for prompt templates.

Provides helpers for:
  - Recording per-document eval_score outcomes against an A/B test variant
  - Checking whether a winner can be declared (simple threshold: Δ ≥ 0.03
    with ≥ 30 documents processed per variant)
  - Promoting the winner (sets winner_id, status=completed on PromptAbTest;
    sets is_active=False on the losing template)

Called by:
  - eval_layer.py after EL-2.3 completes (records eval_score + variant)
  - A background cron / weekly refinement task (checks for winners)
"""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cafm_shared.logging import get_logger

logger = get_logger(__name__)

# Minimum docs processed per variant before declaring a winner
_MIN_DOCS_PER_VARIANT = 30

# Minimum accuracy gap (absolute) to declare a winner
_MIN_ACCURACY_GAP = 0.03


async def record_outcome(
    session: AsyncSession,
    ab_test_id: UUID,
    variant: str,            # "a" or "b"
    eval_score: float,
) -> None:
    """
    Record one document's eval_score outcome for the given A/B test variant.

    Updates the running accuracy (simple rolling average via:
      new_avg = (old_avg * (n-1) + eval_score) / n
    where n = docs_processed + 1).

    This is called from eval_layer.py after EL-2.3 completes.
    """
    from models.ingestion import PromptAbTest  # noqa: PLC0415

    result = await session.execute(
        select(PromptAbTest).where(PromptAbTest.id == ab_test_id)
    )
    test = result.scalar_one_or_none()
    if test is None or test.status != "running":
        return

    n = (test.docs_processed or 0) + 1

    if variant == "a":
        old_acc = float(test.accuracy_a or 0.0)
        new_acc = (old_acc * (n - 1) + eval_score) / n
        await session.execute(
            update(PromptAbTest)
            .where(PromptAbTest.id == ab_test_id)
            .values(accuracy_a=round(new_acc, 4), docs_processed=n)
        )
    else:
        old_acc = float(test.accuracy_b or 0.0)
        new_acc = (old_acc * (n - 1) + eval_score) / n
        await session.execute(
            update(PromptAbTest)
            .where(PromptAbTest.id == ab_test_id)
            .values(accuracy_b=round(new_acc, 4), docs_processed=n)
        )

    await session.commit()
    logger.debug(
        "ab_test.outcome_recorded",
        ab_test_id=str(ab_test_id),
        variant=variant,
        eval_score=eval_score,
        docs_processed=n,
    )


async def check_for_winner(
    session: AsyncSession,
    ab_test_id: UUID,
) -> str | None:
    """
    Check if a winner can be declared for the given A/B test.

    Winner criteria (both must hold):
      1. docs_processed >= _MIN_DOCS_PER_VARIANT * 2  (enough data)
      2. |accuracy_a - accuracy_b| >= _MIN_ACCURACY_GAP

    Returns "a", "b", or None if no winner yet.
    Does NOT commit — caller must commit after reviewing result.
    """
    from models.ingestion import PromptAbTest  # noqa: PLC0415

    result = await session.execute(
        select(PromptAbTest).where(PromptAbTest.id == ab_test_id)
    )
    test = result.scalar_one_or_none()
    if test is None or test.status != "running":
        return None

    if (test.docs_processed or 0) < _MIN_DOCS_PER_VARIANT * 2:
        return None  # not enough data

    acc_a = float(test.accuracy_a or 0.0)
    acc_b = float(test.accuracy_b or 0.0)
    gap = abs(acc_a - acc_b)

    if gap < _MIN_ACCURACY_GAP:
        return None  # not statistically meaningful yet

    return "a" if acc_a >= acc_b else "b"


async def promote_winner(
    session: AsyncSession,
    ab_test_id: UUID,
) -> str | None:
    """
    Declare a winner for the given A/B test and update DB state:
      - Sets PromptAbTest.winner_id to the winning template's id
      - Sets PromptAbTest.status = "completed"
      - Sets the losing PromptTemplate.is_active = False

    Returns the winning variant ("a" or "b"), or None if no winner yet.
    Commits the session on success.
    """
    from models.ingestion import PromptAbTest, PromptTemplate  # noqa: PLC0415

    winner_variant = await check_for_winner(session, ab_test_id)
    if winner_variant is None:
        return None

    result = await session.execute(
        select(PromptAbTest).where(PromptAbTest.id == ab_test_id)
    )
    test = result.scalar_one_or_none()
    if test is None:
        return None

    winner_tpl_id = test.template_a_id if winner_variant == "a" else test.template_b_id
    loser_tpl_id = test.template_b_id if winner_variant == "a" else test.template_a_id

    from sqlalchemy import func  # noqa: PLC0415

    # Mark test complete
    await session.execute(
        update(PromptAbTest)
        .where(PromptAbTest.id == ab_test_id)
        .values(
            winner_id=winner_tpl_id,
            status="completed",
            completed_at=func.now(),
        )
    )

    # Deactivate the losing template
    await session.execute(
        update(PromptTemplate)
        .where(PromptTemplate.id == loser_tpl_id)
        .values(is_active=False)
    )

    await session.commit()

    logger.info(
        "ab_test.winner_promoted",
        ab_test_id=str(ab_test_id),
        winner_variant=winner_variant,
        winner_template_id=str(winner_tpl_id),
        loser_template_id=str(loser_tpl_id),
    )

    return winner_variant


async def get_active_tests(session: AsyncSession) -> list[dict[str, Any]]:
    """
    Return a summary of all currently running A/B tests.
    Used by the weekly refinement loop (Task 3.5).
    """
    from models.ingestion import PromptAbTest, PromptTemplate  # noqa: PLC0415

    result = await session.execute(
        select(PromptAbTest)
        .where(PromptAbTest.status == "running")
        .order_by(PromptAbTest.created_at.desc())
    )
    tests = result.scalars().all()

    summaries = []
    for t in tests:
        summaries.append({
            "ab_test_id": str(t.id),
            "template_a_id": str(t.template_a_id),
            "template_b_id": str(t.template_b_id),
            "accuracy_a": float(t.accuracy_a or 0.0),
            "accuracy_b": float(t.accuracy_b or 0.0),
            "docs_processed": t.docs_processed,
            "gap": abs(float(t.accuracy_a or 0.0) - float(t.accuracy_b or 0.0)),
            "can_declare_winner": (
                (t.docs_processed or 0) >= _MIN_DOCS_PER_VARIANT * 2
                and abs(float(t.accuracy_a or 0.0) - float(t.accuracy_b or 0.0)) >= _MIN_ACCURACY_GAP
            ),
        })

    return summaries
