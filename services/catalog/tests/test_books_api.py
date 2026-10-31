"""Books API against a real Postgres (started by the `database_url` fixture).

These need Docker running. To run only the fast tests: pytest -m "not db"
"""

import pytest

pytestmark = pytest.mark.db  # marks every test in this file

PRIDE_AND_PREJUDICE_ID = "11111111-1111-4111-8111-000000000001"  # from seed migration
SEED_BOOK_COUNT = 6

NEW_BOOK = {"title": "Emma", "author": "Jane Austen", "priceMinor": 1150, "currency": "EUR"}


# ---------- GET /books ----------


def test_list_books_returns_seed_books_sorted_by_title(db_client):
    response = db_client.get("/books")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == SEED_BOOK_COUNT
    assert body["limit"] == 20
    assert body["offset"] == 0
    titles = [book["title"] for book in body["items"]]
    assert titles == sorted(titles)
    assert set(body["items"][0]) == {
        "id", "title", "author", "priceMinor", "currency", "createdAt", "updatedAt",
    }  # fmt: skip


def test_list_books_paginates(db_client):
    first = db_client.get("/books", params={"limit": 4, "offset": 0}).json()
    second = db_client.get("/books", params={"limit": 4, "offset": 4}).json()

    assert len(first["items"]) == 4
    assert len(second["items"]) == SEED_BOOK_COUNT - 4
    # Every book appears exactly once across the pages.
    ids = [b["id"] for b in first["items"] + second["items"]]
    assert len(set(ids)) == SEED_BOOK_COUNT
    assert first["total"] == second["total"] == SEED_BOOK_COUNT


def test_search_matches_title_case_insensitively(db_client):
    body = db_client.get("/books", params={"q": "dracula"}).json()

    assert body["total"] == 1
    assert body["items"][0]["title"] == "Dracula"


def test_search_matches_author(db_client):
    body = db_client.get("/books", params={"q": "Shelley"}).json()

    assert [b["title"] for b in body["items"]] == ["Frankenstein"]


def test_search_treats_percent_sign_literally(db_client):
    # Unescaped, "%" would be a LIKE wildcard and match every book.
    body = db_client.get("/books", params={"q": "%"}).json()

    assert body["total"] == 0


def test_search_with_no_match_returns_empty_page(db_client):
    body = db_client.get("/books", params={"q": "no such book"}).json()

    assert body == {"items": [], "total": 0, "limit": 20, "offset": 0}


# ---------- GET /books/{id} ----------


def test_get_book_returns_the_book(db_client):
    response = db_client.get(f"/books/{PRIDE_AND_PREJUDICE_ID}")

    assert response.status_code == 200
    book = response.json()
    assert book["id"] == PRIDE_AND_PREJUDICE_ID
    assert book["title"] == "Pride and Prejudice"
    assert book["priceMinor"] == 999
    assert book["currency"] == "EUR"


def test_get_unknown_book_returns_404(db_client):
    missing_id = "00000000-0000-4000-8000-000000000000"

    response = db_client.get(f"/books/{missing_id}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "BOOK_NOT_FOUND"


# ---------- POST /books ----------


def test_create_book_returns_201_and_can_be_fetched(db_client):
    response = db_client.post("/books", json=NEW_BOOK)

    assert response.status_code == 201
    created = response.json()
    assert created["title"] == "Emma"
    assert created["priceMinor"] == 1150
    assert response.headers["Location"] == f"/books/{created['id']}"

    fetched = db_client.get(f"/books/{created['id']}").json()
    assert fetched == created


def test_create_book_trims_whitespace(db_client):
    response = db_client.post("/books", json={**NEW_BOOK, "title": "  Emma  "})

    assert response.json()["title"] == "Emma"


def test_created_book_is_cleaned_up_between_tests(db_client):
    # Relies on the db_client fixture deleting books created by earlier tests.
    assert db_client.get("/books").json()["total"] == SEED_BOOK_COUNT
