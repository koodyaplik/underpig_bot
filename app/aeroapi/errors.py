from __future__ import annotations


class AeroApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "unknown_error",
        http_status: int | None = None,
        transient: bool = False,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.transient = transient
        self.retry_after = retry_after


class QuotaExceededError(AeroApiError):
    def __init__(self, message: str = "AeroAPI request budget is exhausted") -> None:
        super().__init__(message, code="local_quota_exceeded", transient=False)
