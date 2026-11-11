import os

import httpx2
import pytest
from fastapi.testclient import TestClient

# Settings require these values. Set dummy ones before the app is imported (which is
# why the fixtures below import app code inside the function, not at the top).
# Tests never really call the services: they swap the HTTP client for a fake.
os.environ.setdefault("CATALOG_URL", "http://catalog.test:8001")
os.environ.setdefault("CART_URL", "http://cart.test:8002")
os.environ.setdefault("ORDER_URL", "http://order.test:8004")
os.environ.setdefault("JWT_SECRET", "test-secret-that-is-at-least-32-characters")


@pytest.fixture
def fake_service():
    """Start the app with a fake service. `calls` records what the gateway sent."""
    from app.main import app

    calls: list[httpx2.Request] = []
    state = {"handler": lambda request: httpx2.Response(200, json={"ok": True})}

    def transport_handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return state["handler"](request)

    with TestClient(app) as client:
        # Replace the real client created at startup with one that talks to our fake.
        app.state.http_client = httpx2.AsyncClient(
            transport=httpx2.MockTransport(transport_handler)
        )

        def set_handler(handler):
            state["handler"] = handler

        yield client, calls, set_handler


@pytest.fixture
def auth_headers():
    """Returns a function giving the headers of a user with a valid login token."""
    from app.auth import create_token
    from app.config import settings

    def make(user_id: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_token(user_id, settings.jwt_secret)}"}

    return make
