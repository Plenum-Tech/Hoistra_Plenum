from fastapi import APIRouter, HTTPException, status

from ...services.approval_workflow import ApprovalWorkflowService
from ...config import settings
from ...core.logging import get_logger

router = APIRouter()
log = get_logger(__name__)


@router.post("/{approval_request_id}/respond")
async def respond_to_approval(
    approval_request_id: str,
    approved: bool,
    notes: str | None = None,
):
    if not approval_request_id.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "validation_error", "message": "approval_request_id must not be blank"},
        )

    log.info("approvals.respond.start", approval_request_id=approval_request_id, approved=approved)
    svc = ApprovalWorkflowService(aimms_api_url=settings.aimms_api_url)
    result = await svc.handle_approval_response(approval_request_id, approved, notes=notes)

    if result.get("status") == "not_found":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "approval_request_not_found", "message": f"Approval request '{approval_request_id}' not found"},
        )
    if result.get("status") == "already_processed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "approval_already_processed", "message": f"Approval request '{approval_request_id}' already processed"},
        )
    if result.get("status") == "work_order_missing":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "work_order_not_found", "message": "Work order linked to approval request no longer exists"},
        )
    if result.get("status") == "rejected":
        log.info("approvals.respond.rejected", approval_request_id=approval_request_id)
        return result

    log.info("approvals.respond.complete", approval_request_id=approval_request_id, approved=approved)
    return result
