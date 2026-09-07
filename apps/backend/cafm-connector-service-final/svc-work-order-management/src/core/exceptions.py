"""
BE1-13: Custom exception hierarchy for the Work Order service.
Every exception maps to a specific HTTP status code and error code string.
"""
from fastapi import HTTPException, status


class WorkOrderNotFound(HTTPException):
    def __init__(self, work_order_id: str):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "work_order_not_found", "message": f"Work order '{work_order_id}' not found"},
        )


class InvalidStatusTransition(HTTPException):
    def __init__(self, current: str, requested: str, allowed: list[str]):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_status_transition",
                "message": f"Cannot transition '{current}' → '{requested}'. Allowed: {allowed or ['none (terminal)']}",
                "field": "new_status",
            },
        )


class WorkOrderAlreadyClosed(HTTPException):
    def __init__(self, work_order_id: str):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "work_order_closed", "message": f"Work order '{work_order_id}' is already closed"},
        )


class ApprovalNotPending(HTTPException):
    def __init__(self, work_order_id: str, current_status: str):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "approval_not_pending",
                "message": f"Work order '{work_order_id}' cannot be approved — current status is '{current_status}'",
            },
        )


class DatabaseError(HTTPException):
    def __init__(self, detail: str = "A database error occurred"):
        super().__init__(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "database_error", "message": detail},
        )


class AIExtractionError(HTTPException):
    def __init__(self, detail: str = "AI extraction failed"):
        super().__init__(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "ai_extraction_error", "message": detail},
        )


class CMMSIntegrationError(HTTPException):
    def __init__(self, detail: str = "CMMS integration failed"):
        super().__init__(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "cmms_error", "message": detail},
        )
