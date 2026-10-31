"""Entry point: `python -m app`.

Starting uvicorn from Python (instead of the `uvicorn` CLI) lets the port come from
our validated Settings. Uvicorn handles SIGTERM itself: it stops accepting new
connections, lets in-flight requests finish, then exits.
"""

import uvicorn

from app.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # listen on all interfaces so it's reachable inside a container
        port=settings.port,
        log_level=settings.log_level.lower(),
    )
