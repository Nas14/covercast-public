"""One exception type for every refusal of the approval workflow.

The API (Phase 8) turns it into the agreed shape:
    {"error": {"code": "...", "message_ar": "...", "details": {...}}}
"""

from __future__ import annotations

from coverage.codes import ErrorCode

HTTP_STATUS = {
    ErrorCode.INVALID_INPUT: 400,
    ErrorCode.OFFER_INVALID: 403,
    ErrorCode.AUTH_REQUIRED: 403,
    ErrorCode.NOT_SUPERVISOR: 403,
    ErrorCode.CSRF_FAILED: 403,
    ErrorCode.EMPLOYEE_NOT_FOUND: 404,
    ErrorCode.NOT_FOUND: 404,
    # Everything else is "your request was valid, but the world changed": 409.
}


class CoverCastError(Exception):
    def __init__(self, code: ErrorCode, **details):
        self.code = ErrorCode(code)
        self.details = details
        super().__init__(f"{self.code.value}: {details}")

    @property
    def message_ar(self) -> str:
        return self.code.label

    @property
    def http_status(self) -> int:
        return HTTP_STATUS.get(self.code, 409)

    def as_dict(self) -> dict:
        return {"error": {"code": self.code.value, "message_ar": self.message_ar, "details": self.details}}
