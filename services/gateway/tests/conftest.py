import os

# Settings require the service addresses. Set dummy ones before the app is imported.
# Tests never really call them: they swap the HTTP client for a fake (see test_proxy.py).
os.environ.setdefault("CATALOG_URL", "http://catalog.test:8001")
os.environ.setdefault("CART_URL", "http://cart.test:8002")
