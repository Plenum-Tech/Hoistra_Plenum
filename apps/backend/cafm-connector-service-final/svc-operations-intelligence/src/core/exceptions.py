"""CAFMError-style exceptions for the operations intelligence service."""
from __future__ import annotations


class OpsIntelligenceError(Exception):
    def __init__(self, message: str, code: str = "ops_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class NotFoundError(OpsIntelligenceError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="not_found")


class ValidationError(OpsIntelligenceError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="validation_error")


class AdversaryRejectedError(OpsIntelligenceError):
    def __init__(self, message: str, reasons: list[str] | None = None) -> None:
        super().__init__(message, code="adversary_rejected")
        self.reasons = reasons or []
