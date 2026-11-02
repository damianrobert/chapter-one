import os

# Settings require REDIS_URL. Set one before the app is imported. Nothing listens on
# port 1, so any test that really talks to "Redis" gets a quick "connection refused";
# tests that need a working Redis swap in a fake (see test_health.py).
os.environ.setdefault("REDIS_URL", "redis://localhost:1/0")
