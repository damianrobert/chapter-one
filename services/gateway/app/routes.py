"""Routing table: which public path goes to which service, and who may use it.

    /api/books        -> catalog  /books        reading is public, changing needs a login
    /api/books/<...>  -> catalog  /books/<...>
    /api/carts/<id>   -> cart     /carts/<id>   login needed, and only your own cart
    /api/carts/<...>  -> cart     /carts/<...>

The gateway strips the /api prefix (CLAUDE.md, API conventions). It doesn't need to
know each service's individual endpoints. Anything under /api/books goes to catalog,
anything under /api/carts goes to cart, and the service itself answers 404/405 for
paths or methods it doesn't support.
"""

from fastapi import APIRouter, Request, Response

from app.auth import read_user_id
from app.config import settings
from app.errors import error_response
from app.proxy import SAFE_METHODS, forward

router = APIRouter()

ALL_METHODS = ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]


def current_user_id(request: Request) -> str | None:
    return read_user_id(request.headers.get("authorization"), settings.jwt_secret)


def unauthorized() -> Response:
    response = error_response(401, "UNAUTHORIZED", "A valid login token is required")
    response.headers["WWW-Authenticate"] = "Bearer"  # the standard hint for a 401
    return response


def owns_cart_path(path: str, user_id: str) -> bool:
    """Is `path` (e.g. /carts/<id>/items/<book>) inside this user's own cart?

    Anything that isn't clearly the user's own cart is refused, including /carts alone.
    Paths with "." or ".." segments are refused too: the HTTP client tidies those up
    before sending, so /carts/<me>/../<someone-else> would really reach
    /carts/<someone-else> even though it starts with the caller's own id.
    """
    segments = path.split("/")  # "/carts/<id>/items" -> ["", "carts", "<id>", "items"]
    if "." in segments or ".." in segments:
        return False
    return len(segments) > 2 and segments[2] == user_id


@router.api_route("/api/books", methods=ALL_METHODS)
@router.api_route("/api/books/{rest:path}", methods=ALL_METHODS)
async def books(request: Request) -> Response:
    # Anyone may browse the shop. Changing it needs a login. There are no roles yet,
    # so any logged-in user can add a book for now.
    user_id = None
    if request.method not in SAFE_METHODS:
        user_id = current_user_id(request)
        if user_id is None:
            return unauthorized()

    path = request.url.path.removeprefix("/api")
    return await forward(
        request, request.app.state.http_client, "catalog", settings.catalog_url, path, user_id
    )


@router.api_route("/api/carts", methods=ALL_METHODS)
@router.api_route("/api/carts/{rest:path}", methods=ALL_METHODS)
async def carts(request: Request) -> Response:
    user_id = current_user_id(request)
    if user_id is None:
        return unauthorized()

    path = request.url.path.removeprefix("/api")
    if not owns_cart_path(path, user_id):
        return error_response(403, "FORBIDDEN", "You can only access your own cart")

    return await forward(
        request, request.app.state.http_client, "cart", settings.cart_url, path, user_id
    )
