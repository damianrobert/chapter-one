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


async def ok():
    return None


async def fail(*args):
    raise ConnectionError("down")


@pytest.fixture
def broker_ok(client, monkeypatch):
    monkeypatch.setattr(client.app.state.publisher, "check", ok)
    monkeypatch.setattr(client.app.state.consumer, "is_consuming", lambda: True)


def test_ready_returns_ok_when_database_and_broker_reachable(client, monkeypatch, broker_ok):
    monkeypatch.setattr(db, "ping_database", lambda engine: ok())

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok", "broker": "ok", "consumer": "ok"},
    }


def test_ready_returns_503_when_database_unreachable(client, monkeypatch, broker_ok):
    monkeypatch.setattr(db, "ping_database", fail)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"database": "unreachable", "broker": "ok", "consumer": "ok"},
    }


def test_ready_returns_503_when_broker_unreachable(client, monkeypatch):
    monkeypatch.setattr(db, "ping_database", lambda engine: ok())
    # The real publisher and consumer, pointed at the dummy URL from conftest.py (nothing
    # listens on port 1), so this also checks that a refused connection is reported,
    # not raised.

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"database": "ok", "broker": "unreachable", "consumer": "unreachable"},
    }
