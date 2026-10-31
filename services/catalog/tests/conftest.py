import os

# Settings require DATABASE_URL. Set a dummy one before the app is imported.
# Nothing connects to it: the engine connects lazily and these tests fake the DB ping.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
