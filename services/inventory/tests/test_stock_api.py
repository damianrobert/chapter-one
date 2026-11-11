import uuid

import pytest

pytestmark = pytest.mark.db

DRACULA = "11111111-1111-4111-8111-000000000005"


def test_get_stock_returns_quantity(db_client):
    response = db_client.get(f"/stock/{DRACULA}")

    assert response.status_code == 200
    assert response.json() == {"bookId": DRACULA, "quantity": 2}


def test_get_stock_for_unknown_book_is_404(db_client):
    response = db_client.get(f"/stock/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "STOCK_NOT_FOUND"


def test_put_stock_creates_a_record_for_a_new_book(db_client):
    book_id = str(uuid.uuid4())

    response = db_client.put(f"/stock/{book_id}", json={"quantity": 7})

    assert response.status_code == 200
    assert response.json() == {"bookId": book_id, "quantity": 7}
    assert db_client.get(f"/stock/{book_id}").json()["quantity"] == 7


def test_put_stock_overwrites_and_is_safe_to_repeat(db_client):
    for _ in range(2):
        response = db_client.put(f"/stock/{DRACULA}", json={"quantity": 20})
        assert response.json()["quantity"] == 20  # set to 20, not 2 + 20 + 20


@pytest.mark.parametrize("quantity", [-1, 1.5, "3", 1_000_001])
def test_put_stock_rejects_bad_quantity(db_client, quantity):
    response = db_client.put(f"/stock/{DRACULA}", json={"quantity": quantity})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
