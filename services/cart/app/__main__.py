"""Entry point: `python -m app`.

Starting uvicorn from Python (instead of the `uvicorn` CLI) lets the port come from
our validated Settings. Uvicorn handles SIGTERM itself: it stops accepting new
connections, lets in-flight requests finish, then exits.
"""

import uvicorn

from app.config import settings
from app.logging_config import configure_logging

if __name__ == "__main__":
    configure_logging(settings.log_level, settings.service_name)
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # listen on all interfaces so it's reachable inside a container
        port=settings.port,
        log_level=settings.log_level.lower(),
        # log_config=None: don't let uvicorn install its own plain-text log handlers;
        # its records then go up to our JSON handler on the root logger.
        log_config=None,
        # Uvicorn's access log is one pre-formatted text line per request. We log
        # requests ourselves instead (middleware in main.py), with separate JSON fields.
        access_log=False,
    )
