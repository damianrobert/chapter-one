"""Routing table: which public path goes to which service, and who may use it.

    /api/books        -> catalog  /books        reading is public, changing needs a login
    /api/books/<...>  -> catalog  /books/<...>
    /api/carts/<id>   -> cart     /carts/<id>   login needed, and only your own cart
    /api/carts/<...>  -> cart     /carts/<...>
    /api/orders       -> order    /orders       login needed; order only shows your own
    /api/orders/<...> -> order    /orders/<...>

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

# How long the gateway waits for each service, retries included (hard rule 6).
# Rule: a caller waits longer than everything the service behind it may do. Cart
# spends at most 1.5s asking catalog, so 2s is enough for cart. Placing an order
# asks cart (max 1.5s), then inventory (max 1.5s), plus database writes, so order
# gets 5s. See docs/decisions/0002-timeout-budgets.md.
BUDGETS = {"catalog": 2.0, "cart": 2.0, "order": 5.0}


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
        request,
        request.app.state.http_client,
        "catalog",
        settings.catalog_url,
        path,
        user_id,
        budget_seconds=BUDGETS["catalog"],
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
        request,
        request.app.state.http_client,
        "cart",
        settings.cart_url,
        path,
        user_id,
        budget_seconds=BUDGETS["cart"],
    )


@router.api_route("/api/orders", methods=ALL_METHODS)
@router.api_route("/api/orders/{rest:path}", methods=ALL_METHODS)
async def orders(request: Request) -> Response:
    # Login needed for everything. Unlike carts, order paths don't contain the user's
    # id (/orders/<orderId>), so the gateway can't check ownership from the path.
    # Order does it itself: it only ever returns orders of the X-User-ID we send.
    user_id = current_user_id(request)
    if user_id is None:
        return unauthorized()

    path = request.url.path.removeprefix("/api")
    return await forward(
        request,
        request.app.state.http_client,
        "order",
        settings.order_url,
        path,
        user_id,
        budget_seconds=BUDGETS["order"],
    )
