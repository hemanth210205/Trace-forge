"""Uniform error responses: ``{"error": {"code", "message", "details", "request_id"}}``."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_var

logger = logging.getLogger("traceforge.errors")


class AppError(Exception):
    def __init__(
        self, status_code: int, code: str, message: str, details: Any = None, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers


class NotFound(AppError):
    def __init__(self, resource: str) -> None:
        super().__init__(404, f"{resource}_not_found", f"{resource.replace('_', ' ').capitalize()} not found.")


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details, "request_id": request_id_var.get()}}


def _validation_details(exc: RequestValidationError) -> list[dict[str, Any]]:
    # Deliberately omit the rejected "input" values: they may contain passwords or keys.
    return [{"loc": list(err.get("loc", ())), "msg": err.get("msg", ""), "type": err.get("type", "")} for err in exc.errors()]


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            error_body(exc.code, exc.message, exc.details), status_code=exc.status_code, headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            error_body("validation_error", "Request validation failed.", _validation_details(exc)), status_code=422
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return JSONResponse(error_body(code, message), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(DataError)
    async def _unstorable(_: Request, exc: DataError) -> JSONResponse:
        # Valid JSON the database cannot store (NaN/Infinity, NUL characters, out-of-range numbers). The detail
        # is not echoed: it can quote the submitted value.
        logger.warning("rejected unstorable value: %s", type(getattr(exc, "orig", exc)).__name__)
        return JSONResponse(
            error_body("unstorable_value", "The request contains a value that cannot be stored, such as NaN, Infinity "
                       "or a NUL character."), status_code=422,
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        if not getattr(exc, "_traceforge_logged", False):  # UnhandledErrorMiddleware logs with the request id
            logger.error("unhandled error: %s", type(exc).__name__, exc_info=exc)
        return JSONResponse(error_body("internal_error", "An internal error occurred."), status_code=500)
