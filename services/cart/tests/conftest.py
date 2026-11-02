import os

# Settings require REDIS_URL and CATALOG_URL. Set them before the app is imported.
# Nothing listens on port 1, so a test that really talks to this "Redis" gets a quick
# "connection refused". Tests that need a working Redis use the `cart_client` fixture
# (test_carts_api.py), which points the app at a throwaway Redis container. Catalog is
# never really called: tests swap the HTTP client for a fake.
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
os.environ.setdefault("CATALOG_URL", "http://catalog.test:8001")
