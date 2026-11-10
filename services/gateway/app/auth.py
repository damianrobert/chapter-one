"""Checking who is calling (hard rule 8: auth lives at the gateway).

A JWT is like a tamper-proof name badge. It holds a few facts, such as "this is user
<id>" (the `sub` field) and "valid until <time>" (`exp`), plus a signature made with
a secret only we know. Anyone can read a badge, but changing one breaks the signature.

The client sends it on each request:   Authorization: Bearer <token>

The gateway checks it here and tells the services who the user is with an
`X-User-ID` header, so the services don't each need their own login code.

Why HS256: it uses one shared secret for both making and checking tokens, which is
the simplest option while the gateway is the only component that touches tokens.
"""

import time
from uuid import UUID

import jwt

ALGORITHM = "HS256"


def read_user_id(authorization: str | None, secret: str) -> str | None:
    """Return the user id from an `Authorization` header, or None if it isn't valid."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    try:
        claims = jwt.decode(
            token,
            secret,
            # Name the one algorithm we accept. Otherwise a forged token could pick
            # its own, including "none" (no signature at all).
            algorithms=[ALGORITHM],
            # A badge with no expiry would be valid forever, so refuse it.
            options={"require": ["exp", "sub"]},
        )
        # IDs are UUIDs (CLAUDE.md); this also gives one standard spelling of the id.
        return str(UUID(claims["sub"]))
    except (jwt.InvalidTokenError, ValueError):
        return None


def create_token(user_id: str, secret: str, lifetime_seconds: int = 3600) -> str:
    """Make a signed token. Used by tests and `python -m app.dev_token`."""
    claims = {"sub": user_id, "exp": int(time.time()) + lifetime_seconds}
    return jwt.encode(claims, secret, algorithm=ALGORITHM)
