import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_live_returns_ok(client):
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "gateway"}


def test_ready_returns_ok_without_checking_other_services(client):
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {}}


def test_unknown_path_uses_standard_error_shape(client):
    response = client.get("/api/no-such-thing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
