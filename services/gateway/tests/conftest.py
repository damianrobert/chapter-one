import os

# Settings require CATALOG_URL. Set a dummy one before the app is imported.
# Tests never really call it: they swap the HTTP client for a fake (see test_proxy.py).
os.environ.setdefault("CATALOG_URL", "http://catalog.test:8001")
