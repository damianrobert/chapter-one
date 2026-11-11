"""The cart API: look at a cart, set how many of a book it holds, remove a book.

## How a cart is stored in Redis

Each user's cart is one Redis **hash** (a small dictionary stored under one key):

    key:    cart:<userId>
    fields: <bookId> -> '{"title": "...", "author": "...", "priceMinor": 999,
                         "currency": "EUR", "quantity": 2}'

One key per cart means one command reads the whole cart (HGETALL), and one TTL
("time to live") on that key deletes the whole cart when it runs out. Every change
resets the TTL to 7 days, so a cart disappears 7 days after it was last changed.
Nobody has to write a clean-up job: Redis deletes expired keys by itself.

A cart that doesn't exist and an empty cart look the same to clients: an empty list.

## Why we copy the price and title into the cart

The order service will read the cart at checkout and copy prices into the order
(hard rule 2). The cart keeps the price the user saw when they last set the
quantity, and a title to show in the cart page without asking catalog for every
item. The copy is refreshed on every PUT. It can go stale if catalog changes a price
afterwards — that's a known trade-off, written down in docs/progress.md.

## Why PUT ("set quantity to 2") instead of "add one more"

Sending the same PUT twice leaves the cart the same as sending it once. That makes
retries safe: if the response got lost, the client can just send it again. "Add one
more" sent twice would add two.
"""

import json
from typing import Annotated
from uuid import UUID

import httpx2
from fastapi import APIRouter, Depends, Request, Response
from redis.asyncio import Redis

from app import catalog_client
from app.config import settings
from app.errors import ApiError
from app.schemas import Cart, CartItem, SetQuantity

router = APIRouter(prefix="/carts", tags=["carts"])

CART_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def get_http_client(request: Request) -> httpx2.AsyncClient:
    return request.app.state.http_client


RedisClient = Annotated[Redis, Depends(get_redis)]
HttpClient = Annotated[httpx2.AsyncClient, Depends(get_http_client)]


def cart_key(user_id: UUID) -> str:
    return f"cart:{user_id}"


async def load_cart(redis: Redis, user_id: UUID) -> Cart:
    stored = await redis.hgetall(cart_key(user_id))  # {} if the key doesn't exist

    items = []
    for book_id, raw in stored.items():
        data = json.loads(raw)
        items.append(
            CartItem(
                book_id=book_id,
                title=data["title"],
                author=data["author"],
                price_minor=data["priceMinor"],
                currency=data["currency"],
                quantity=data["quantity"],
                line_total_minor=data["priceMinor"] * data["quantity"],
            )
        )
    # A hash has no order; sort so the cart looks the same on every request.
    items.sort(key=lambda item: (item.title, str(item.book_id)))

    return Cart(
        user_id=user_id,
        items=items,
        total_minor=sum(item.line_total_minor for item in items),
        currency=items[0].currency if items else None,
    )


@router.get("/{user_id}", response_model=Cart)
async def get_cart(user_id: UUID, redis: RedisClient) -> Cart:
    return await load_cart(redis, user_id)


@router.put("/{user_id}/items/{book_id}", response_model=Cart)
async def set_item_quantity(
    user_id: UUID, book_id: UUID, body: SetQuantity, redis: RedisClient, http: HttpClient
) -> Cart:
    # 1. Ask catalog: does the book exist, and what does it cost right now?
    try:
        book = await catalog_client.get_book(http, settings.catalog_url, book_id)
    except catalog_client.CatalogUnavailable as exc:
        raise ApiError(
            503, "CATALOG_UNAVAILABLE", "Can't check the book right now, try again later"
        ) from exc
    if book is None:
        raise ApiError(404, "BOOK_NOT_FOUND", f"Book {book_id} not found")

    # 2. One currency per cart. (This reads the cart, then writes it. Two requests from
    # the same user at the same moment, with books in different currencies, could
    # both pass. For a single user's cart that's unlikely enough to accept.)
    cart = await load_cart(redis, user_id)
    other_currencies = {item.currency for item in cart.items if item.book_id != book_id}
    if other_currencies and book["currency"] not in other_currencies:
        raise ApiError(
            409,
            "CURRENCY_MISMATCH",
            f"The cart is in {cart.currency}; this book is priced in {book['currency']}",
        )

    # 3. Save the item and reset the 7-day expiry. transaction=True wraps both commands
    # in MULTI/EXEC: Redis runs them together, so there's never a moment where the item
    # is saved but the cart has no expiry.
    item = {
        "title": book["title"],
        "author": book["author"],
        "priceMinor": book["priceMinor"],
        "currency": book["currency"],
        "quantity": body.quantity,
    }
    key = cart_key(user_id)
    async with redis.pipeline(transaction=True) as pipe:
        pipe.hset(key, str(book_id), json.dumps(item))
        pipe.expire(key, CART_TTL_SECONDS)
        await pipe.execute()

    return await load_cart(redis, user_id)


# --- emptying a cart after an order (called by app/consumer.py, not by an endpoint) ---

# Marks that the cart was already emptied for an order: `cart-emptied-for:<orderId>`.
# Kept 7 days, as long as a cart lives; a repeat of the same event days later is
# very unlikely, and the marker shouldn't pile up in Redis forever.
EMPTIED_MARKER_TTL_SECONDS = 7 * 24 * 60 * 60

# A Lua script runs inside Redis as ONE step: nothing else happens in between.
# "Set the marker if it isn't there yet (NX); only if we set it, delete the cart."
# Returns 1 if this call emptied the cart, 0 if the order was handled before.
#
# Why not two separate commands? If cart crashed after setting the marker but before
# deleting, the repeat would see the marker and never empty the cart. And MULTI/EXEC
# (used above) can't help here: it runs every command, it can't say "delete only if
# the marker was new".
_EMPTY_ONCE = """
if redis.call('SET', KEYS[1], '1', 'NX', 'EX', ARGV[1]) then
    redis.call('DEL', KEYS[2])
    return 1
end
return 0
"""


async def empty_cart_for_order(redis: Redis, user_id: UUID, order_id: UUID) -> bool:
    """Empty the user's cart because `order_id` was confirmed. Safe to repeat.

    Returns True if this call emptied it, False if this order was handled before
    (a repeated OrderConfirmed). Why not just DEL the cart every time? A repeat
    arriving after the user filled a *new* cart would delete those new books.
    """
    marker = f"cart-emptied-for:{order_id}"
    emptied = await redis.eval(
        _EMPTY_ONCE, 2, marker, cart_key(user_id), EMPTIED_MARKER_TTL_SECONDS
    )
    return emptied == 1


@router.delete("/{user_id}/items/{book_id}", status_code=204)
async def remove_item(user_id: UUID, book_id: UUID, redis: RedisClient) -> Response:
    # Removing a book that isn't in the cart is not an error: the result the client
    # wanted ("this book is not in my cart") is true either way. That also makes it
    # safe to repeat. Catalog isn't needed here, so this works even if catalog is down.
    key = cart_key(user_id)
    async with redis.pipeline(transaction=True) as pipe:
        pipe.hdel(key, str(book_id))
        # The cart was changed, so restart its 7 days. (If the last item was removed,
        # Redis has already deleted the empty hash and EXPIRE simply does nothing.)
        pipe.expire(key, CART_TTL_SECONDS)
        await pipe.execute()
    return Response(status_code=204)
