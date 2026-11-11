import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app


@pytest.fixture
def client():
    # Using TestClient as a context manager runs the app's lifespan (startup/shutdown).
    with TestClient(app) as c:
        yield c


def test_live_returns_ok(client):
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "order"}


def test_ready_returns_ok_when_database_reachable(client, monkeypatch):
    async def fake_ping(engine):
        return None

    monkeypatch.setattr(db, "ping_database", fake_ping)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok"}}


def test_ready_returns_503_when_database_unreachable(client, monkeypatch):
    async def failing_ping(engine):
        raise ConnectionError("db down")

    monkeypatch.setattr(db, "ping_database", failing_ping)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "checks": {"database": "unreachable"}}
