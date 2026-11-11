import json
import logging
import sys
import uuid

import pytest
from fastapi.testclient import TestClient

from app.logging_config import HANDLER_NAME, JsonFormatter, configure_logging
from app.main import app


def make_record(**kwargs) -> logging.LogRecord:
    defaults = {
        "name": "app.test",
        "levelno": logging.INFO,
        "levelname": "INFO",
        "msg": "hello %s",
        "args": ("world",),
    }
    return logging.makeLogRecord(defaults | kwargs)


def test_formatter_outputs_one_json_object_with_standard_fields():
    line = JsonFormatter("order").format(make_record())

    entry = json.loads(line)  # fails if it isn't valid JSON
    assert entry["level"] == "INFO"
    assert entry["service"] == "order"
    assert entry["logger"] == "app.test"
    assert entry["message"] == "hello world"
    assert entry["timestamp"].endswith("Z")
    assert "\n" not in line  # one log entry = one line


def test_formatter_includes_extra_fields_and_stringifies_unusual_values():
    book_id = uuid.uuid4()
    record = make_record(path="/books", status=200, bookId=book_id)

    entry = json.loads(JsonFormatter("order").format(record))

    assert entry["path"] == "/books"
    assert entry["status"] == 200
    assert entry["bookId"] == str(book_id)


def test_extra_fields_cannot_overwrite_standard_fields():
    record = make_record(level="FAKE", service="someone-else")

    entry = json.loads(JsonFormatter("order").format(record))

    assert entry["level"] == "INFO"
    assert entry["service"] == "order"


def test_uvicorn_color_message_is_dropped():
    record = make_record(color_message="\x1b[36mhello\x1b[0m")

    entry = json.loads(JsonFormatter("order").format(record))

    assert "color_message" not in entry


def test_formatter_puts_traceback_in_one_field():
    try:
        raise ValueError("boom")
    except ValueError:
        record = make_record(exc_info=sys.exc_info())

    line = JsonFormatter("order").format(record)

    assert "\n" not in line  # the traceback's newlines are escaped inside the JSON string
    assert "ValueError: boom" in json.loads(line)["exception"]


@pytest.fixture
def restore_root_logger():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield root
    root.handlers, root.level = handlers, level


def test_configure_logging_twice_adds_only_one_handler(restore_root_logger):
    configure_logging("INFO", "order")
    configure_logging("DEBUG", "order")

    ours = [h for h in restore_root_logger.handlers if h.get_name() == HANDLER_NAME]
    assert len(ours) == 1
    assert restore_root_logger.level == logging.DEBUG


def test_each_request_is_logged_with_method_path_status_and_duration(caplog):
    caplog.set_level(logging.DEBUG, logger="app.requests")

    with TestClient(app) as client:
        client.get("/no-such-path")

    [record] = [r for r in caplog.records if r.name == "app.requests"]
    assert record.levelno == logging.INFO
    assert record.getMessage() == "request completed"
    assert record.method == "GET"
    assert record.path == "/no-such-path"
    assert record.status == 404
    assert record.durationMs >= 0


def test_health_check_requests_are_logged_at_debug(caplog):
    caplog.set_level(logging.DEBUG, logger="app.requests")

    with TestClient(app) as client:
        client.get("/health/live")

    [record] = [r for r in caplog.records if r.name == "app.requests"]
    assert record.levelno == logging.DEBUG
