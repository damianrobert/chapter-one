"""Tests for login tokens (JWTs) and who may reach what through the gateway."""

import time

import jwt
import pytest

from app.auth import ALGORITHM, create_token, read_user_id
from app.config import settings
from app.routes import owns_cart_path

ME = "33333333-3333-4333-8333-000000000001"
SOMEONE_ELSE = "33333333-3333-4333-8333-000000000002"
SECRET = settings.jwt_secret


def bearer(token: str) -> str:
    return f"Bearer {token}"


def test_valid_token_gives_the_user_id():
    assert read_user_id(bearer(create_token(ME, SECRET)), SECRET) == ME


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Bearer",
        "Bearer not-a-token",
        "Basic abc123",
        # Signed with a different secret, i.e. forged.
        bearer(create_token(ME, "another-secret-that-is-at-least-32-characters")),
        # Expired 10 seconds ago.
        bearer(create_token(ME, SECRET, lifetime_seconds=-10)),
        # Subject isn't a UUID.
        bearer(create_token("not-a-uuid", SECRET)),
        # No expiry.
        bearer(jwt.encode({"sub": ME}, SECRET, algorithm=ALGORITHM)),
        # No subject.
        bearer(jwt.encode({"exp": int(time.time()) + 60}, SECRET, algorithm=ALGORITHM)),
        # "alg: none" means no signature at all.
        bearer(jwt.encode({"sub": ME, "exp": int(time.time()) + 60}, None, algorithm="none")),
    ],
)
def test_bad_tokens_are_refused(header):
    assert read_user_id(header, SECRET) is None


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        (f"/carts/{ME}", True),
        (f"/carts/{ME}/items/abc", True),
        (f"/carts/{SOMEONE_ELSE}", False),
        ("/carts", False),
        ("/carts/", False),
        # The HTTP client would turn these into /carts/<someone-else>.
        (f"/carts/{ME}/../{SOMEONE_ELSE}", False),
        (f"/carts/{ME}/./../{SOMEONE_ELSE}/items/abc", False),
    ],
)
def test_owns_cart_path(path, allowed):
    assert owns_cart_path(path, ME) is allowed


def test_books_can_be_read_without_a_login(fake_service):
    client, calls, _ = fake_service

    response = client.get("/api/books")

    assert response.status_code == 200
    assert "x-user-id" not in calls[0].headers


def test_changing_books_needs_a_login(fake_service):
    client, calls, _ = fake_service

    response = client.post("/api/books", json={"title": "Emma"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    assert response.headers["www-authenticate"] == "Bearer"
    assert calls == []  # never reached the service


def test_changing_books_with_a_login_passes_on_the_user(fake_service, auth_headers):
    client, calls, _ = fake_service

    client.post("/api/books", json={"title": "Emma"}, headers=auth_headers(ME))

    assert calls[0].headers["x-user-id"] == ME


def test_cart_needs_a_login(fake_service):
    client, calls, _ = fake_service

    response = client.get(f"/api/carts/{ME}")

    assert response.status_code == 401
    assert calls == []


def test_own_cart_is_forwarded_with_the_user(fake_service, auth_headers):
    client, calls, _ = fake_service

    response = client.get(f"/api/carts/{ME}", headers=auth_headers(ME))

    assert response.status_code == 200
    assert calls[0].headers["x-user-id"] == ME


def test_someone_elses_cart_is_forbidden(fake_service, auth_headers):
    client, calls, _ = fake_service

    response = client.get(f"/api/carts/{SOMEONE_ELSE}", headers=auth_headers(ME))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert calls == []


def test_encoded_dot_segments_cannot_reach_someone_elses_cart(fake_service, auth_headers):
    client, calls, _ = fake_service

    response = client.get(f"/api/carts/{ME}/%2e%2e/{SOMEONE_ELSE}", headers=auth_headers(ME))

    assert response.status_code == 403
    assert calls == []


def test_client_cannot_pretend_to_be_someone_else_with_a_header(fake_service, auth_headers):
    client, calls, _ = fake_service

    client.get(f"/api/carts/{ME}", headers={**auth_headers(ME), "X-User-ID": SOMEONE_ELSE})
    client.get("/api/books", headers={"X-User-ID": SOMEONE_ELSE})

    assert calls[0].headers["x-user-id"] == ME
    assert "x-user-id" not in calls[1].headers
