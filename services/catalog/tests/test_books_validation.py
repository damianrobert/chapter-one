"""Books API checks that don't need a database.

Validation runs before an endpoint's code, so bad requests are rejected without
ever touching the DB. That makes these fast and Docker-free.
"""

import pytest
from fastapi.testclient import TestClient

from app.books import escape_like
from app.config import settings
from app.main import app

VALID_BOOK = {"title": "Emma", "author": "Jane Austen", "priceMinor": 1150, "currency": "EUR"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ---------- escape_like ----------


def test_escape_like_escapes_wildcards():
    assert escape_like("100%_done") == "100\\%\\_done"


def test_escape_like_escapes_backslash_first():
    assert escape_like("a\\b") == "a\\\\b"


def test_escape_like_leaves_normal_text_alone():
    assert escape_like("Moby-Dick") == "Moby-Dick"


# ---------- POST /books validation ----------


@pytest.mark.parametrize(
    "change, bad_field",
    [
        ({"priceMinor": 9.99}, "body.priceMinor"),  # floats are never money
        ({"priceMinor": 9.0}, "body.priceMinor"),  # not even "whole" floats
        ({"priceMinor": "999"}, "body.priceMinor"),  # nor strings
        ({"priceMinor": -1}, "body.priceMinor"),
        ({"priceMinor": 2_147_483_648}, "body.priceMinor"),  # too big for the DB column
        ({"currency": "eur"}, "body.currency"),
        ({"currency": "EURO"}, "body.currency"),
        ({"title": "   "}, "body.title"),  # only spaces = empty after trimming
        ({"price": 999}, "body.price"),  # unknown field (typo)
    ],
)
def test_create_book_rejects_invalid_body(client, change, bad_field):
    response = client.post("/books", json={**VALID_BOOK, **change})

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert bad_field in [d["field"] for d in error["details"]]


def test_create_book_rejects_missing_field(client):
    body = {k: v for k, v in VALID_BOOK.items() if k != "author"}

    response = client.post("/books", json=body)

    assert response.status_code == 400
    assert "body.author" in [d["field"] for d in response.json()["error"]["details"]]


# ---------- other request errors ----------


def test_get_book_with_non_uuid_id_returns_400(client):
    response = client.get("/books/not-a-uuid")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
def test_list_books_rejects_bad_pagination(client, params):
    response = client.get("/books", params=params)

    assert response.status_code == 400


def test_unknown_path_uses_error_shape(client):
    response = client.get("/nope")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "NOT_FOUND", "message": "Not Found"}}


# ---------- database down ----------


def test_returns_503_when_database_unreachable(monkeypatch):
    # Port 1 on localhost: nothing listens there, so the connection is refused.
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://test:test@127.0.0.1:1/test")
    with TestClient(app) as client:
        response = client.get("/books")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
