from fastapi.testclient import TestClient

from app.main import app

USER = "aaaaaaaa-aaaa-4aaa-8aaa-000000000001"


def test_cart_requests_are_503_when_redis_is_unreachable():
    # conftest.py points REDIS_URL at a port where nothing listens.
    with TestClient(app) as client:
        response = client.get(f"/carts/{USER}")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
