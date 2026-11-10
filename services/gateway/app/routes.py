"""Routing table: which public path goes to which service.

    /api/books        -> catalog  /books
    /api/books/<...>  -> catalog  /books/<...>
    /api/carts        -> cart     /carts
    /api/carts/<...>  -> cart     /carts/<...>

The gateway strips the /api prefix (CLAUDE.md, API conventions). It doesn't need to
know each service's individual endpoints. Anything under /api/books goes to catalog,
anything under /api/carts goes to cart, and the service itself answers 404/405 for
paths or methods it doesn't support.
"""

from fastapi import APIRouter, Request, Response

from app.config import settings
from app.proxy import forward

router = APIRouter()

ALL_METHODS = ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]


@router.api_route("/api/books", methods=ALL_METHODS)
@router.api_route("/api/books/{rest:path}", methods=ALL_METHODS)
async def books(request: Request) -> Response:
    path = request.url.path.removeprefix("/api")
    return await forward(
        request, request.app.state.http_client, "catalog", settings.catalog_url, path
    )


@router.api_route("/api/carts", methods=ALL_METHODS)
@router.api_route("/api/carts/{rest:path}", methods=ALL_METHODS)
async def carts(request: Request) -> Response:
    path = request.url.path.removeprefix("/api")
    return await forward(request, request.app.state.http_client, "cart", settings.cart_url, path)
