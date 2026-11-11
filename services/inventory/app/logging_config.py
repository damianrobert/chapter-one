"""Structured JSON logging to stdout.

Copied from the catalog service on purpose (see docs/progress.md): each service is
built as its own Docker image from its own folder, and copying a small file is
simpler than sharing it. If the copies start to drift, it can move to `shared/`.

Why JSON: in Docker and Kubernetes, logs are collected from each container's stdout
and shipped to a central place (e.g. Loki, Elasticsearch). If every line is one JSON
object, those tools can filter by field ("level=ERROR and service=inventory") instead
of parsing free text with regexes.

Why stdout (not a file): the container filesystem is throwaway and services must be
stateless. The platform (Docker, Kubernetes) owns log storage and rotation.

Example line:

    {"timestamp": "2026-11-02T10:15:00.123Z", "level": "INFO", "service": "inventory",
     "logger": "app.requests", "message": "request completed", "method": "GET",
     "path": "/stock/1111...", "status": 200, "durationMs": 4.2}

Extra fields come from `logger.info("...", extra={"path": "/stock"})`. In roadmap
step 6 a `correlationId` field will be added to every line the same way.

This uses only the standard library, so there's no magic: a Formatter turns a
LogRecord into a string, and one handler writes that string to stdout.
"""

import json
import logging
import sys
from datetime import UTC, datetime

# Attributes every LogRecord has. Anything else on a record was passed via `extra=`
# and becomes its own JSON field.
_STANDARD_RECORD_ATTRS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
# Extras we drop on purpose: uvicorn adds "color_message", a copy of the message with
# terminal colour codes in it — useful for a terminal, just noise in JSON.
_IGNORED_EXTRAS = {"color_message"}

HANDLER_NAME = "json-stdout"


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=UTC)
        entry: dict = {
            # UTC with a "Z", same style as the event envelope's occurredAt.
            "timestamp": timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": record.getMessage(),  # fills in %s arguments
        }

        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_ATTRS or key in _IGNORED_EXTRAS or key.startswith("_"):
                continue
            # An extra called e.g. "level" must not overwrite the real level field above.
            if key not in entry:
                entry[key] = value

        # A traceback goes into one field. As plain text it would be split across
        # many lines, and log tools would treat each line as a separate log entry.
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)

        # default=str: values JSON can't encode (UUIDs, datetimes) become strings
        # instead of crashing the logging call.
        return json.dumps(entry, default=str)


def configure_logging(level: str, service: str) -> None:
    """Send every log record (ours, uvicorn's, alembic's...) as JSON to stdout.

    Libraries log to named loggers ("uvicorn.error", "alembic.runtime.migration") that
    pass their records up to the root logger, so one handler on root covers them all.
    """
    root = logging.getLogger()

    # Remove our handler if it's already there (e.g. configure_logging called twice),
    # so lines aren't printed twice. Other handlers (like pytest's) are left alone.
    for existing in list(root.handlers):
        if existing.get_name() == HANDLER_NAME:
            root.removeHandler(existing)

    handler = logging.StreamHandler(sys.stdout)
    handler.set_name(HANDLER_NAME)
    handler.setFormatter(JsonFormatter(service))
    root.addHandler(handler)
    root.setLevel(level.upper())

    # SQLAlchemy logs every SQL statement at INFO level. That's far too noisy at our
    # default INFO, so only show its warnings and errors.
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
