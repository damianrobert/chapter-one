"""Consistent error responses.

Same shape as every other service (see CLAUDE.md):

    { "error": { "code": "BOOK_NOT_FOUND", "message": "..." } }

FastAPI's built-in errors look different (`{"detail": ...}`), so the handlers below
convert them. Same approach as services/catalog/app/errors.py.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """Raise this from an endpoint to return an error in our standard shape."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def error_response(
    status_code: int, code: str, message: str, details: list | None = None
) -> JSONResponse:
    error: dict = {"code": code, "message": message}
    if details:
        error["details"] = details
    return JSONResponse(status_code=status_code, content={"error": error})


async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message)


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI's default is 422; CLAUDE.md says 400 for validation errors.
    details = [
        {"field": ".".join(str(part) for part in err["loc"]), "message": err["msg"]}
        for err in exc.errors()
    ]
    return error_response(400, "VALIDATION_ERROR", "Request is invalid", details)


async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # Errors raised by the framework itself, e.g. an unknown path (404) or the wrong
    # method (405), so even those use our shape.
    codes = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
    code = codes.get(exc.status_code, "HTTP_ERROR")
    return error_response(exc.status_code, code, str(exc.detail))


async def handle_redis_unavailable(request: Request, exc: Exception) -> JSONResponse:
    # Redis is down or too slow: our dependency failing, not a bug, so 503 ("try again
    # later") rather than 500. Same code as catalog uses for its database.
    logger.warning("redis unavailable while handling %s", request.url.path, exc_info=True)
    return error_response(503, "DATABASE_UNAVAILABLE", "The cart database is unavailable")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, handle_api_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    # Only "can't reach Redis" errors. Other Redis errors (e.g. a wrong command) are
    # bugs in our code and should show up as a 500.
    for exc_type in (RedisConnectionError, RedisTimeoutError):
        app.add_exception_handler(exc_type, handle_redis_unavailable)
