from fastapi import APIRouter
from ...core.logging import get_logger
from ...services.ppm_work_order_scheduler import PPMWorkOrderScheduler
from ...config import settings

router = APIRouter()
log = get_logger(__name__)


@router.get("/due")
async def get_due_schedules():
    log.debug("ppm.due.start")
    scheduler = PPMWorkOrderScheduler(aimms_api_url=settings.aimms_api_url)
    due = await scheduler.get_due_ppm_schedules()
    log.info("ppm.due.result", due_count=len(due))
    return due


@router.post("/run")
async def trigger_scheduler_run():
    log.info("ppm.run.start")
    scheduler = PPMWorkOrderScheduler(aimms_api_url=settings.aimms_api_url)
    due = await scheduler.get_due_ppm_schedules()
    results = []
    for schedule in due:
        wo = await scheduler.create_ppm_work_order(schedule)
        results.append(wo["work_order_id"])
    log.info("ppm.run.complete", created_count=len(results), work_order_ids=results)
    return {"created": results}
