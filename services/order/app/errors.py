"""Consistent error responses.

Every error from this service has the same JSON shape (see CLAUDE.md):

    { "error": { "code": "BOOK_NOT_FOUND", "message": "..." } }

Clients (the gateway, the frontend, other services) can then check `error.code`
without parsing messages. FastAPI's built-in errors look different
(`{"detail": ...}`), so the handlers below convert them to our shape.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """Raise this from an endpoint to return an error in our standard shape."""

    def __init__(self, status_code: int, code: str, message: str, details: list | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        # Optional extra info for the client, e.g. which book was unavailable.
        self.details = details


def error_response(
    status_code: int, code: str, message: str, details: list | None = None
) -> JSONResponse:
    error: dict = {"code": code, "message": message}
    if details:
        error["details"] = details
    return JSONResponse(status_code=status_code, content={"error": error})


async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message, exc.details)


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI's default is 422; CLAUDE.md says 400 for validation errors.
    # `details` says which field failed and why, e.g. {"field": "body.priceMinor", ...}.
    details = [
        {"field": ".".join(str(part) for part in err["loc"]), "message": err["msg"]}
        for err in exc.errors()
    ]
    return error_response(400, "VALIDATION_ERROR", "Request is invalid", details)


async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # Covers errors raised by the framework itself, e.g. an unknown path (404) or the
    # wrong method (405), so even those use our shape.
    codes = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
    code = codes.get(exc.status_code, "HTTP_ERROR")
    return error_response(exc.status_code, code, str(exc.detail))


async def handle_database_unavailable(request: Request, exc: Exception) -> JSONResponse:
    # The database is down or unreachable: that's our dependency failing, not a bug in
    # our code, so answer 503 ("try again later") instead of a generic 500.
    logger.warning("database unavailable while handling %s", request.url.path, exc_info=True)
    return error_response(503, "DATABASE_UNAVAILABLE", "The order database is unavailable")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, handle_api_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    # Errors that mean "can't reach the DB": SQLAlchemy's wrappers for driver errors,
    # plus refused connections (OSError) and our connect timeout (TimeoutError).
    for exc_type in (OperationalError, InterfaceError, OSError, TimeoutError):
        app.add_exception_handler(exc_type, handle_database_unavailable)
