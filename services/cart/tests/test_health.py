import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app


class FakeRedis:
    """Stands in for the Redis client: answers PING, nothing else (yet)."""

    async def ping(self):
        return True

    async def aclose(self):
        pass


@pytest.fixture
def client():
    # Uses the real Redis client, pointed at a port where nothing listens (conftest.py).
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_with_redis(monkeypatch):
    monkeypatch.setattr(main.redis_client, "create_client", lambda url: FakeRedis())
    with TestClient(app) as c:
        # Pretend the event consumer is connected (it never is in these tests).
        monkeypatch.setattr(c.app.state.consumer, "is_consuming", lambda: True)
        yield c


def test_live_returns_ok(client):
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "cart"}


def test_ready_returns_ok_when_redis_answers(client_with_redis):
    response = client_with_redis.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"redis": "ok", "consumer": "ok"}}


def test_ready_returns_503_when_redis_unreachable(client, monkeypatch):
    monkeypatch.setattr(client.app.state.consumer, "is_consuming", lambda: True)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"redis": "unreachable", "consumer": "ok"},
    }


def test_ready_returns_503_while_not_connected_to_rabbitmq(client_with_redis, monkeypatch):
    # Undo the fixture's "connected": the real consumer never connects in tests.
    monkeypatch.setattr(client_with_redis.app.state.consumer, "is_consuming", lambda: False)

    response = client_with_redis.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"redis": "ok", "consumer": "unreachable"},
    }


def test_unknown_path_uses_standard_error_shape(client):
    response = client.get("/no-such-thing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
