"""Consistent error responses.

Same shape as every other service (see CLAUDE.md):

    { "error": { "code": "NOT_FOUND", "message": "..." } }

The gateway is what the frontend talks to, so it matters most here that every
error — even "no such path" — looks the same. FastAPI's built-in errors look
different (`{"detail": ...}`), so the handlers below convert them.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


def error_response(
    status_code: int, code: str, message: str, details: list | None = None
) -> JSONResponse:
    error: dict = {"code": code, "message": message}
    if details:
        error["details"] = details
    return JSONResponse(status_code=status_code, content={"error": error})


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


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
