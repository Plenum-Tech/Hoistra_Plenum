"""Background worker — starts the PPM scheduler loop."""
import asyncio
from .services.ppm_work_order_scheduler import PPMWorkOrderScheduler
from .config import settings
from .core.logging import get_logger, configure_logging

log = get_logger(__name__)


async def main() -> None:
    configure_logging()
    log.info("worker.start", aimms_api_url=settings.aimms_api_url)
    scheduler = PPMWorkOrderScheduler(aimms_api_url=settings.aimms_api_url)
    try:
        await scheduler.run_scheduler()
    except Exception as exc:
        log.error("worker.fatal", exc_info=exc)
        raise
    finally:
        log.info("worker.stop")


if __name__ == "__main__":
    asyncio.run(main())
