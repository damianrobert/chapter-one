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
    assert response.json() == {"status": "ok", "service": "inventory"}


async def ok(engine):
    return None


async def fail(engine):
    raise ConnectionError("db down")


@pytest.fixture
def consuming(client, monkeypatch):
    monkeypatch.setattr(client.app.state.consumer, "is_consuming", lambda: True)


def test_ready_returns_ok_when_database_and_broker_reachable(client, monkeypatch, consuming):
    monkeypatch.setattr(db, "ping_database", ok)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "broker": "ok"}}


def test_ready_returns_503_when_database_unreachable(client, monkeypatch, consuming):
    monkeypatch.setattr(db, "ping_database", fail)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"database": "unreachable", "broker": "ok"},
    }


def test_ready_returns_503_while_not_connected_to_rabbitmq(client, monkeypatch):
    # The real consumer, pointed at the dummy URL from conftest.py: never connects.
    monkeypatch.setattr(db, "ping_database", ok)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"database": "ok", "broker": "unreachable"},
    }
